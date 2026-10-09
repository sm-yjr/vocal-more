// SPDX-License-Identifier: GPL-3.0-only
//! Product bridge acceptance: every intent goes through CommandSink -> UiEvent
//! -> BackendDriver::send. No Cocoa, microphone, provider or real credential.
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    collections::{HashSet, VecDeque},
    fs,
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant},
};
use vocal_more_backend::{application::Options, config::ConfigRepository, history::History};
use vocal_more_core::recording::RecordingStore;
use vocal_more_desktop::bridge::{
    BackendDriver, COMMAND_QUEUE_CAPACITY, CommandSink, Request, UI_QUEUE_CAPACITY, UiEvent,
};

const DEADLINE: Duration = Duration::from_secs(5);

#[derive(Debug, Clone)]
struct Event {
    method: String,
    params: Value,
}
struct Host {
    driver: BackendDriver,
    sink: CommandSink,
    events: async_channel::Receiver<UiEvent>,
    backlog: VecDeque<Event>,
    seen: Vec<String>,
}
impl Host {
    fn start(path: &Path) -> Result<Self> {
        let mut options = Options::new(path.into());
        options.allow_test_sources = true;
        // Options::new does not read the process environment. Keep native=None,
        // endpoints unused and environment_key=None even on a configured Mac.
        let (driver, sink, events) = BackendDriver::start(options)?;
        let mut host = Self {
            driver,
            sink,
            events,
            backlog: VecDeque::new(),
            seen: vec![],
        };
        let initialized = host.event("initialized", |_| true)?;
        ensure!(initialized["runtime"] == "rust");
        ensure!(initialized["config"]["api_key"] == "");
        host.backlog.push_back(Event {
            method: "initialized".into(),
            params: initialized,
        });
        Ok(host)
    }
    fn receive(&mut self, deadline: Instant) -> Result<Event> {
        loop {
            match self.events.try_recv() {
                Ok(UiEvent::Request(request)) => self.driver.send(request),
                Ok(UiEvent::Backend { method, params, .. }) => {
                    ensure!(
                        method != "backend_disconnected",
                        "backend disconnected: {}",
                        params["message"]
                    );
                    if method == "state_changed" {
                        self.sink.update_session(
                            params["state"].as_str().unwrap_or("idle"),
                            params["generation"].as_u64().unwrap_or_default(),
                        );
                    }
                    self.seen.push(method.clone());
                    return Ok(Event { method, params });
                }
                Err(async_channel::TryRecvError::Empty) => {
                    ensure!(
                        Instant::now() < deadline,
                        "timed out waiting for bridge event; observed {:?}",
                        self.seen
                    );
                    thread::sleep(Duration::from_millis(2));
                }
                Err(async_channel::TryRecvError::Closed) => {
                    bail!("bridge event channel closed while awaiting a response")
                }
            }
        }
    }
    fn matching(&mut self, predicate: impl Fn(&Event) -> bool) -> Result<Event> {
        if let Some(index) = self.backlog.iter().position(&predicate) {
            return Ok(self.backlog.remove(index).unwrap());
        }
        let deadline = Instant::now() + DEADLINE;
        loop {
            let event = self.receive(deadline)?;
            if predicate(&event) {
                return Ok(event);
            }
            self.backlog.push_back(event);
        }
    }
    fn event(&mut self, method: &str, predicate: impl Fn(&Value) -> bool) -> Result<Value> {
        Ok(self
            .matching(|event| event.method == method && predicate(&event.params))?
            .params)
    }
    fn response(&mut self, method: &str, params: Value) -> Result<Event> {
        let id = self.sink.request(method, params);
        let response = self.matching(|event| {
            matches!(event.method.as_str(), "rpc_response" | "rpc_error")
                && event.params["request_id"] == id
        })?;
        ensure!(
            response.params["method"] == method,
            "RPC method correlation changed"
        );
        Ok(response)
    }
    fn call(&mut self, method: &str, params: Value) -> Result<Value> {
        let reply = self.response(method, params)?;
        ensure!(
            reply.method == "rpc_response",
            "{method}: {}",
            reply.params["message"]
        );
        Ok(reply.params["result"].clone())
    }
    fn action(&mut self, action: &str, mut params: Value) -> Result<Value> {
        params["action"] = json!(action);
        self.call("ui_action", params)
    }
    /// Return an admitted UI intent without forwarding it yet, to exercise
    /// queue ownership deterministically while retaining the real entry path.
    fn admit(&mut self, method: &str, params: Value) -> Result<Request> {
        let id = self.sink.request(method, params);
        let deadline = Instant::now() + DEADLINE;
        loop {
            match self.events.try_recv() {
                Ok(UiEvent::Request(request)) if request.id == id => return Ok(request),
                Ok(UiEvent::Request(request)) => self.driver.send(request),
                Ok(UiEvent::Backend { method, params, .. }) => {
                    self.backlog.push_back(Event { method, params });
                }
                Err(async_channel::TryRecvError::Empty) => {
                    ensure!(Instant::now() < deadline, "UI request was not admitted");
                    thread::sleep(Duration::from_millis(2));
                }
                Err(async_channel::TryRecvError::Closed) => bail!("UI request channel closed"),
            }
        }
    }
    fn close(&mut self) -> Result<()> {
        self.driver.close();
        let deadline = Instant::now() + DEADLINE;
        while !self.driver.finished() {
            ensure!(Instant::now() < deadline, "driver did not finish shutdown");
            thread::sleep(Duration::from_millis(2));
        }
        ensure!(
            !self
                .sink
                .can_paste(self.sink.paste_epoch(), self.sink.generation()),
            "closed bridge still authorized paste"
        );
        Ok(())
    }
    fn report(&self, name: &str, extra: Value) -> Result<()> {
        let path =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../.build/bridge-lifecycle");
        fs::create_dir_all(&path)?;
        let data = json!({"test":name,"runtime":"real BackendDriver + Application","native_audio":false,"provider_requests":false,"real_credentials":false,
            "driver_finished":self.driver.finished(),"observed_events":self.seen,"evidence":extra});
        fs::write(
            path.join(format!("{name}.json")),
            serde_json::to_vec_pretty(&data)?,
        )?;
        Ok(())
    }
}
impl Drop for Host {
    fn drop(&mut self) {
        self.driver.close();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !self.driver.finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(2));
        }
    }
}

fn seed_recording(path: &Path) -> Result<(String, PathBuf)> {
    tokio::runtime::Runtime::new()?.block_on(async {
        let store = RecordingStore::open(path.join("recordings")).await?;
        let history = History::open(store).await?;
        let mut writer = history
            .store()
            .create(1, "qwen3.5-omni-plus-realtime")
            .await?;
        writer.append(&[1, 0].repeat(3200)).await?;
        let record = writer
            .finish("completed", "synthetic bridge transcript".into(), None)
            .await?;
        history.register(&record, "realtime_long", "en")?;
        history.update(
            &record.id.to_string(),
            "success",
            Some("synthetic bridge transcript"),
            None,
            Some(json!({"total_cost_cny":0.0})),
        )?;
        Ok((
            record.id.to_string(),
            history.store().directory().join(record.filename),
        ))
    })
}

#[test]
fn settings_config_readback_redaction_rejection_and_restart_are_real() -> Result<()> {
    let data = tempfile::tempdir()?;
    let mut host = Host::start(data.path())?;
    let initial = host.event("initialized", |_| true)?;
    assert_eq!(initial["api_key_set"], false);
    assert!(
        initial["asr_models"]
            .as_array()
            .is_some_and(|models| !models.is_empty())
    );
    host.action("setConfig", json!({"key":"ui.language","value":"en"}))?;
    let changed = host.event("config_changed", |params| {
        params["config"]["ui"]["language"] == "en"
    })?;
    assert_eq!(changed["config"]["api_key"], "");
    let disk = ConfigRepository::open(&data.path().join("config.yaml"))?;
    assert_eq!(disk.config.get("ui.language"), "en");
    host.call(
        "set_config",
        json!({"key":"api_key","value":"bridge-fixture-not-a-real-credential"}),
    )?;
    let public = host.call("snapshot", json!({}))?;
    assert_eq!(public["config"]["api_key"], "");
    assert_eq!(public["api_key_set"], true);
    assert!(
        !public
            .to_string()
            .contains("bridge-fixture-not-a-real-credential")
    );
    host.action("revealApiKey", json!({}))?;
    let reveal = host.event("api_key_revealed", |_| true)?;
    assert_eq!(reveal["value"], "bridge-fixture-not-a-real-credential");
    let rejected = host.response(
        "set_config",
        json!({"key":"network.proxy_url","value":"https://invalid:80"}),
    )?;
    assert_eq!(rejected.method, "rpc_error");
    assert!(
        rejected.params["message"]
            .as_str()
            .is_some_and(|message| !message.is_empty())
    );
    let recovered = host.call("snapshot", json!({}))?;
    assert_eq!(recovered["config"]["network"]["proxy_url"], "");
    assert_eq!(recovered["config"]["ui"]["language"], "en");
    // Flush of an unchanged masked Key follows the retained legacy contract.
    host.action(
        "syncFormState",
        json!({"state":{"api_key":"","ui":{"language":"en"}}}),
    )?;
    assert_eq!(host.call("snapshot", json!({}))?["api_key_set"], true);
    host.call("set_config", json!({"key":"api_key","value":""}))?;
    assert_eq!(host.call("snapshot", json!({}))?["api_key_set"], false);
    host.close()?;
    host.report("config_readback",json!({"durable_language":"en","public_key_masked":true,"explicit_reveal_only":true,"rejected_write_readback_unchanged":true,"masked_form_preserves_key":true,"explicit_clear_works":true}))?;
    let mut restarted = Host::start(data.path())?;
    assert_eq!(
        restarted.call("snapshot", json!({}))?["config"]["ui"]["language"],
        "en"
    );
    restarted.close()?;
    Ok(())
}

#[test]
fn dictionary_and_history_actions_round_trip_through_the_bridge() -> Result<()> {
    let data = tempfile::tempdir()?;
    let (id, wav) = seed_recording(data.path())?;
    let mut host = Host::start(data.path())?;
    host.action(
        "addDictEntry",
        json!({"term":"GPUI Kit","aliases":["gpui kit","GPUI"]}),
    )?;
    assert!(
        host.event("dictionary_changed", |params| params
            .as_array()
            .is_some_and(|entries| entries
                .iter()
                .any(|entry| entry["term"] == "GPUI Kit")))?
            .is_array()
    );
    let dictionary = host.call("get_dictionary", json!({}))?;
    assert_eq!(dictionary[0]["aliases"], json!(["gpui kit", "GPUI"]));
    host.action("removeDictEntry", json!({"term":"GPUI Kit"}))?;
    assert!(
        host.call("get_dictionary", json!({}))?
            .as_array()
            .unwrap()
            .is_empty()
    );
    let recordings = host.action("getRecordings", json!({}))?;
    assert_eq!(recordings[0]["id"], id);
    assert_eq!(recordings[0]["transcript"], "synthetic bridge transcript");
    let updated = host.event("recordings_changed", |params| {
        params["recordings"][0]["id"] == id
    })?;
    assert_eq!(updated["storage"]["recording_count"], 1);
    host.action("copyTranscript", json!({"id":id}))?;
    assert_eq!(
        host.event("copy_transcript", |params| params["id"] == id)?["text"],
        "synthetic bridge transcript"
    );
    let playback = host.action("playRecording", json!({"id":id}))?;
    assert_eq!(
        Path::new(playback["path"].as_str().context("playback path missing")?),
        wav
    );
    assert!(wav.is_file());
    assert_eq!(
        host.event("play_recording", |params| params["id"] == id)?["path"],
        playback["path"]
    );
    host.action("stopRecording", json!({"id":id}))?;
    assert_eq!(
        host.event("stop_recording", |params| params["id"] == id)?["id"],
        id
    );
    // A missing recording rejects before spawning a transcription provider.
    let rejected = host.response(
        "ui_action",
        json!({"action":"retryTranscription","id":"missing-fixture-recording"}),
    )?;
    assert_eq!(rejected.method, "rpc_error");
    host.action("compactRecordingHistory", json!({}))?;
    assert_eq!(
        host.event("recording_compaction_complete", |_| true)?["storage"]["recording_count"],
        1
    );
    host.action("deleteRecording", json!({"id":id}))?;
    host.event("recording_deleted", |params| params["id"] == id)?;
    assert!(!wav.exists());
    assert!(
        host.action("getRecordings", json!({}))?
            .as_array()
            .unwrap()
            .is_empty()
    );
    host.close()?;
    host.report("dictionary_history",json!({"dictionary_add_remove":true,"list_storage_readback":true,"playback_path_is_real_wav":true,"copy_text_event":true,"missing_retry_rejected":true,"compression_event":true,"recording_deleted_on_disk":true}))?;
    Ok(())
}

#[test]
fn synthetic_pcm_cancel_revokes_paste_and_isolates_the_next_generation() -> Result<()> {
    let data = tempfile::tempdir()?;
    let mut host = Host::start(data.path())?;
    let first = host.action("startMicTest", json!({"source":{"kind":"stream"}}))?;
    let generation = first["generation"].as_u64().unwrap();
    host.call(
        "append",
        json!({"generation":generation,"pcm_base64":STANDARD.encode(vec![1u8;1280])}),
    )?;
    host.event("mic_test_started", |params| {
        params["generation"] == generation
    })?;
    host.event("state_changed", |params| {
        params["generation"] == generation && params["state"] == "recording"
    })?;
    let epoch = host.sink.paste_epoch();
    assert!(host.sink.can_paste(epoch, generation));
    let id = host.sink.request("cancel", json!({}));
    // Main-thread or backend progress is unnecessary for this revocation.
    assert!(!host.sink.can_paste(epoch, generation));
    host.matching(|event| event.method == "rpc_response" && event.params["request_id"] == id)?;
    host.event("state_changed", |params| {
        params["state"] == "idle" && params["generation"] == generation
    })?;
    let stale = host.response(
        "append",
        json!({"generation":generation,"pcm_base64":STANDARD.encode([0,0])}),
    )?;
    assert_eq!(stale.method, "rpc_error");
    let second = host.action("startMicTest", json!({"source":{"kind":"stream"}}))?;
    let next = second["generation"].as_u64().unwrap();
    assert!(next > generation);
    let stale = host.response(
        "append",
        json!({"generation":generation,"pcm_base64":STANDARD.encode([0,0])}),
    )?;
    assert_eq!(stale.method, "rpc_error");
    assert!(
        stale.params["message"]
            .as_str()
            .unwrap()
            .contains("stale session")
    );
    host.call(
        "append",
        json!({"generation":next,"pcm_base64":STANDARD.encode(vec![1u8;1280])}),
    )?;
    host.event("mic_test_started", |params| params["generation"] == next)?;
    assert!(!host.sink.can_paste(epoch, generation));
    host.action("stopMicTest", json!({}))?;
    host.event("mic_test_complete", |params| params["generation"] == next)?;
    host.event("state_changed", |params| {
        params["state"] == "idle" && params["generation"] == next
    })?;
    let playback = host.action("playMicTest", json!({}))?;
    let wav = STANDARD.decode(playback["wav_base64"].as_str().unwrap())?;
    assert!(wav.starts_with(b"RIFF") && &wav[8..12] == b"WAVE");
    let snapshot = host.call("snapshot", json!({}))?;
    assert_eq!(snapshot["config"]["api_key"], "");
    assert_eq!(snapshot["api_key_set"], false);
    assert!(snapshot["pending_pastes"].as_array().unwrap().is_empty());
    assert!(snapshot["recordings"].as_array().unwrap().is_empty());
    assert!(
        !host
            .seen
            .iter()
            .any(|method| matches!(method.as_str(), "paste_requested" | "final_result"))
    );
    host.close()?;
    host.report("pcm_cancel_generation",json!({"first_generation":generation,"next_generation":next,"cancel_immediately_revokes_paste":true,"stale_pcm_rejected":true,"preview_wav_bytes":wav.len(),"no_history_or_paste_from_preview":true}))?;
    Ok(())
}

#[test]
fn ui_queue_is_bounded_and_overflow_remains_explicit() -> Result<()> {
    let data = tempfile::tempdir()?;
    let mut host = Host::start(data.path())?;
    for _ in 0..UI_QUEUE_CAPACITY {
        host.sink.request("status", json!({}));
    }
    assert_eq!(host.events.len(), UI_QUEUE_CAPACITY);
    let rejected = host.sink.request("cancel", json!({}));
    assert_eq!(host.events.len(), UI_QUEUE_CAPACITY);
    let overflow = host
        .sink
        .take_overflow()
        .context("overflow was not surfaced")?;
    assert_eq!(overflow["request_id"], rejected);
    assert_eq!(overflow["method"], "cancel");
    assert!(
        overflow["message"]
            .as_str()
            .unwrap()
            .contains("not accepted")
    );
    assert!(host.sink.take_overflow().is_none());
    host.close()?;
    host.report("ui_queue",json!({"capacity":UI_QUEUE_CAPACITY,"over_capacity_rejected":true,"cancellation_epoch_revoked_before_dispatch":host.sink.paste_epoch()>0}))?;
    Ok(())
}

#[test]
fn backend_queue_rejects_excess_and_correlates_every_admitted_intent() -> Result<()> {
    let data = tempfile::tempdir()?;
    let mut host = Host::start(data.path())?;
    let mut requests = Vec::with_capacity(UI_QUEUE_CAPACITY);
    for _ in 0..UI_QUEUE_CAPACITY {
        requests.push(host.admit("status", json!({}))?);
    }
    let ids = requests
        .iter()
        .map(|request| request.id)
        .collect::<HashSet<_>>();
    for request in requests {
        host.driver.send(request);
    }
    assert!(host.events.len() <= UI_QUEUE_CAPACITY);
    let mut completed = HashSet::new();
    let mut rejected = 0;
    let mut successful = 0;
    while completed.len() < ids.len() {
        let response =
            host.matching(|event| matches!(event.method.as_str(), "rpc_response" | "rpc_error"))?;
        let id = response.params["request_id"]
            .as_u64()
            .context("missing correlated id")?;
        assert!(ids.contains(&id));
        assert!(completed.insert(id), "duplicate terminal response for {id}");
        if response.method == "rpc_error" {
            assert!(
                response.params["message"]
                    .as_str()
                    .unwrap()
                    .contains("queue")
            );
            rejected += 1;
        } else {
            assert_eq!(response.params["result"]["state"], "idle");
            successful += 1;
        }
    }
    assert!(rejected > 0, "burst should exercise bounded command queue");
    assert!(successful > 0);
    host.close()?;
    host.report("backend_queue",json!({"capacity":COMMAND_QUEUE_CAPACITY,"burst":ids.len(),"successes":successful,"explicit_rejections":rejected,"terminal_response_for_each_id":true}))?;
    Ok(())
}

#[test]
fn close_drains_configuration_commands_already_accepted_by_driver() -> Result<()> {
    let data = tempfile::tempdir()?;
    let mut host = Host::start(data.path())?;
    let checkpoint = host.admit("set_config", json!({"key":"ui.language","value":"en"}))?;
    let pending = host.admit(
        "set_config",
        json!({"key":"network.proxy_url","value":"http://127.0.0.1:7890"}),
    )?;
    // Block only outbound presentation. The first durable write proves the
    // driver is inside its response send; the second command is then admitted
    // to its otherwise empty bounded queue before close is requested.
    for _ in 0..UI_QUEUE_CAPACITY {
        host.sink.emit("acceptance_queue_filler", json!({}));
    }
    assert_eq!(host.events.len(), UI_QUEUE_CAPACITY);
    host.driver.send(checkpoint);
    let deadline = Instant::now() + DEADLINE;
    loop {
        if ConfigRepository::open(&data.path().join("config.yaml"))?
            .config
            .get("ui.language")
            == "en"
        {
            break;
        }
        ensure!(
            Instant::now() < deadline,
            "checkpoint config write did not finish"
        );
        thread::sleep(Duration::from_millis(2));
    }
    host.driver.send(pending);
    host.close()?;
    let persisted = ConfigRepository::open(&data.path().join("config.yaml"))?
        .config
        .get("network.proxy_url")
        .clone();
    host.report("shutdown_pending_config",json!({"outbound_queue_was_full":true,"checkpoint_write_durable":true,"pending_write_before_close":true,"persisted_proxy":persisted,"expected_proxy":"http://127.0.0.1:7890"}))?;
    assert_eq!(
        persisted, "http://127.0.0.1:7890",
        "close discarded a configuration command already admitted through the UI and backend queues"
    );
    Ok(())
}

#[test]
fn close_preserves_queued_ui_alias_edits_and_history_deletion() -> Result<()> {
    let data = tempfile::tempdir()?;
    let (recording, wav) = seed_recording(data.path())?;
    let mut host = Host::start(data.path())?;
    let checkpoint = host.admit("set_config", json!({"key":"ui.language","value":"en"}))?;
    let intents = [
        (
            "ui_action",
            json!({"action":"setConfig","key":"audio.gain","value":4.0}),
        ),
        (
            "ui_action",
            json!({"action":"syncFormState","state":{"auto_paste":false,"api_key":""}}),
        ),
        (
            "ui_action",
            json!({"action":"setDevice","device":"synthetic-device"}),
        ),
        (
            "ui_action",
            json!({"action":"setActiveHotkeys","hotkeys":["fn"]}),
        ),
        (
            "ui_action",
            json!({"action":"addDictEntry","term":"Keep on close","aliases":["saved alias"]}),
        ),
        (
            "ui_action",
            json!({"action":"addDictEntry","term":"Remove on close","aliases":[]}),
        ),
        (
            "ui_action",
            json!({"action":"removeDictEntry","term":"Remove on close"}),
        ),
        (
            "ui_action",
            json!({"action":"deleteRecording","id":recording}),
        ),
        ("set_mode", json!({"mode":"walkie_talkie"})),
        // Preview changes must never become durable configuration on quit.
        (
            "ui_action",
            json!({"action":"previewConfig","key":"audio.gain","value":9.0}),
        ),
        (
            "ui_action",
            json!({"action":"startMicTest","source":{"kind":"stream"}}),
        ),
    ];
    let pending = intents
        .into_iter()
        .map(|(method, params)| host.admit(method, params))
        .collect::<Result<Vec<_>>>()?;
    for _ in 0..UI_QUEUE_CAPACITY {
        host.sink.emit("acceptance_queue_filler", json!({}));
    }
    host.driver.send(checkpoint);
    let deadline = Instant::now() + DEADLINE;
    while ConfigRepository::open(&data.path().join("config.yaml"))?
        .config
        .get("ui.language")
        != "en"
    {
        ensure!(Instant::now() < deadline, "checkpoint did not persist");
        thread::sleep(Duration::from_millis(2));
    }
    for request in pending {
        host.driver.send(request);
    }
    host.close()?;
    let config = ConfigRepository::open(&data.path().join("config.yaml"))?.config;
    assert_eq!(config.get("audio.gain"), 4.0);
    assert_eq!(config.get("auto_paste"), false);
    assert_eq!(config.get("audio.input_device"), "synthetic-device");
    assert_eq!(config.get("hotkey.active_hotkeys"), &json!(["fn"]));
    assert_eq!(config.get("default_mode"), "walkie_talkie");
    let dictionary =
        vocal_more_backend::dictionary::Dictionary::open(&data.path().join("dictionary.yaml"))?;
    assert_eq!(dictionary.entries.len(), 1);
    assert_eq!(dictionary.entries[0].term, "Keep on close");
    assert_eq!(dictionary.entries[0].aliases, ["saved alias"]);
    assert!(!wav.exists(), "accepted delete was discarded on close");
    let mut restarted = Host::start(data.path())?;
    let snapshot = restarted.call("snapshot", json!({}))?;
    assert_eq!(snapshot["state"], "idle");
    assert!(snapshot["recordings"].as_array().unwrap().is_empty());
    assert_eq!(snapshot["dictionary"][0]["term"], "Keep on close");
    restarted.close()?;
    host.report("shutdown_durable_aliases",json!({"persisted_gain":config.get("audio.gain"),"preview_not_persisted":true,"dictionary_add_remove_survived":true,"history_file_deleted":true,"restart_idle":true}))?;
    Ok(())
}
