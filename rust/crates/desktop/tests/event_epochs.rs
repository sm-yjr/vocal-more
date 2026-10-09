// SPDX-License-Identifier: GPL-3.0-only
//! Real backend results crossing queued cancel/start intents. All audio and
//! provider responses are synthetic, local, and independent of user settings.
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    io::{Read, Write},
    net::TcpListener,
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};
use vocal_more_backend::{application::Options, provider::Endpoints};
use vocal_more_desktop::bridge::{BackendDriver, CommandSink, Request, UiEvent};

const DEADLINE: Duration = Duration::from_secs(5);

struct Host {
    driver: BackendDriver,
    sink: CommandSink,
    events: async_channel::Receiver<UiEvent>,
    backlog: VecDeque<(String, Value)>,
}
impl Host {
    fn start(options: Options) -> Result<Self> {
        let (driver, sink, events) = BackendDriver::start(options)?;
        let mut host = Self {
            driver,
            sink,
            events,
            backlog: VecDeque::new(),
        };
        let initialized = host.event("initialized", |_| true)?;
        host.sink
            .update_session("idle", initialized["generation"].as_u64().unwrap_or(0));
        Ok(host)
    }
    fn receive(
        &mut self,
        dispatch: bool,
        held: &mut Vec<Request>,
        deadline: Instant,
    ) -> Result<(String, Value)> {
        loop {
            match self.events.try_recv() {
                Ok(UiEvent::Request(request)) => {
                    if dispatch {
                        self.driver.send(request);
                    } else {
                        held.push(request);
                    }
                }
                Ok(UiEvent::Backend { method, params, .. }) => {
                    ensure!(method != "backend_disconnected", "{}", params["message"]);
                    if method == "state_changed" {
                        self.sink.update_session(
                            params["state"].as_str().unwrap_or("idle"),
                            params["generation"].as_u64().unwrap_or(0),
                        );
                    }
                    return Ok((method, params));
                }
                Err(async_channel::TryRecvError::Empty) => {
                    ensure!(
                        Instant::now() < deadline,
                        "timed out waiting for bridge event"
                    );
                    thread::sleep(Duration::from_millis(1));
                }
                Err(async_channel::TryRecvError::Closed) => bail!("bridge closed"),
            }
        }
    }
    fn matching(&mut self, predicate: impl Fn(&str, &Value) -> bool) -> Result<Value> {
        if let Some(index) = self
            .backlog
            .iter()
            .position(|(method, params)| predicate(method, params))
        {
            return Ok(self.backlog.remove(index).unwrap().1);
        }
        let deadline = Instant::now() + DEADLINE;
        loop {
            let (method, params) = self.receive(true, &mut vec![], deadline)?;
            if predicate(&method, &params) {
                return Ok(params);
            }
            self.backlog.push_back((method, params));
        }
    }
    fn event(&mut self, method: &str, predicate: impl Fn(&Value) -> bool) -> Result<Value> {
        self.matching(|name, params| name == method && predicate(params))
    }
    fn response(&mut self, id: u64) -> Result<Value> {
        let response = self.matching(|method, params| {
            matches!(method, "rpc_response" | "rpc_error") && params["request_id"] == id
        })?;
        ensure!(response.get("message").is_none(), "{}", response["message"]);
        Ok(response["result"].clone())
    }
    fn call(&mut self, method: &str, params: Value) -> Result<Value> {
        let id = self.sink.request(method, params);
        self.response(id)
    }
    fn close(&self) -> Result<()> {
        self.driver.close();
        let deadline = Instant::now() + DEADLINE;
        while !self.driver.finished() {
            ensure!(Instant::now() < deadline, "backend shutdown timed out");
            thread::sleep(Duration::from_millis(1));
        }
        Ok(())
    }
}

/// Exactly one provider request; a gate separates backend completion from UI
/// admission without sleeps determining the cancellation race.
type ProviderFixture = (
    Endpoints,
    mpsc::Receiver<()>,
    mpsc::Sender<()>,
    thread::JoinHandle<Result<()>>,
);
fn provider_fixture() -> Result<ProviderFixture> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    listener.set_nonblocking(true)?;
    let address = listener.local_addr()?;
    let endpoints = Endpoints::loopback(&format!("http://{address}"), &format!("ws://{address}"))?;
    let (arrived, ready) = mpsc::channel();
    let (release, gate) = mpsc::channel();
    let task = thread::spawn(move || {
        let deadline = Instant::now() + DEADLINE;
        let mut stream = loop {
            match listener.accept() {
                Ok((stream, _)) => break stream,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    ensure!(Instant::now() < deadline, "provider request never arrived");
                    thread::sleep(Duration::from_millis(1));
                }
                Err(error) => return Err(error.into()),
            }
        };
        // Darwin may inherit O_NONBLOCK from the accepting socket. This fixture
        // uses bounded blocking reads after accept; a read timeout alone does
        // not clear O_NONBLOCK and can fail before the HTTP headers arrive.
        stream.set_nonblocking(false)?;
        stream.set_read_timeout(Some(DEADLINE))?;
        stream.set_write_timeout(Some(DEADLINE))?;
        let mut bytes = vec![];
        let mut buffer = [0; 4096];
        let end = loop {
            let count = stream
                .read(&mut buffer)
                .context("fixture failed reading request headers")?;
            ensure!(count > 0, "provider request closed before complete headers");
            bytes.extend_from_slice(&buffer[..count]);
            ensure!(bytes.len() < 2 * 1024 * 1024, "oversized provider request");
            if let Some(index) = bytes.windows(4).position(|part| part == b"\r\n\r\n") {
                break index + 4;
            }
        };
        let headers = std::str::from_utf8(&bytes[..end])?;
        ensure!(
            !headers.to_ascii_lowercase().contains("authorization:"),
            "fixture leaked authorization"
        );
        let length: usize = headers
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length:")
                    .map(|value| value.trim().parse())
            })
            .context("missing content length")??;
        while bytes.len() < end + length {
            let count = stream.read(&mut buffer)?;
            ensure!(count > 0);
            bytes.extend_from_slice(&buffer[..count]);
        }
        let request: Value = serde_json::from_slice(&bytes[end..end + length])?;
        ensure!(
            request["model"] == "qwen3.5-omni-plus",
            "unexpected fixture request model"
        );
        arrived.send(())?;
        gate.recv_timeout(DEADLINE)?;
        let body = json!({"choices":[{"message":{"content":"old queued transcription"},"finish_reason":"stop"}],
            "usage":{"input_tokens":1,"output_tokens":1}}).to_string();
        stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes())?;
        Ok(())
    });
    Ok((endpoints, ready, release, task))
}

#[test]
fn real_final_and_pending_paste_keep_original_epoch_when_cancel_and_restart_are_queued()
-> Result<()> {
    let data = tempfile::tempdir()?;
    let (endpoints, ready, release, provider) = provider_fixture()?;
    let mut provider = Some(provider);
    let mut options = Options::new(data.path().into());
    options.allow_test_sources = true;
    options.environment_key = Some("synthetic-local-fixture-key".into());
    options.endpoints = endpoints;
    let mut host = Host::start(options)?;
    host.call(
        "set_config",
        json!({"key":"asr.model","value":"qwen3.5-omni-plus"}),
    )?;
    host.call("set_config", json!({"key":"enable_polish","value":false}))?;
    let start = host.call("start", json!({"source":{"kind":"stream"}}))?;
    let generation = start["generation"].as_u64().unwrap();
    let original = start["_ui_epoch"]
        .as_u64()
        .context("start lost admitted epoch")?;
    host.event("state_changed", |params| params["state"] == "starting")?;
    for _ in 0..5 {
        host.call(
            "append",
            json!({"generation":generation,"pcm_base64":STANDARD.encode(vec![1;1280])}),
        )?;
    }
    host.call("finish", json!({}))?;
    if let Err(error) = ready.recv_timeout(DEADLINE) {
        let server = provider
            .take()
            .unwrap()
            .join()
            .map_err(|_| anyhow::anyhow!("provider fixture panicked"))?;
        server.context(format!(
            "provider fixture exited before request gate: {error}"
        ))?;
        return Err(error.into());
    }

    // Keyboard admission revokes immediately. Main-thread dispatch is delayed
    // while the old provider finishes, so backend generation has not changed.
    let cancel = host.sink.request("cancel", json!({}));
    let next = host
        .sink
        .request("start", json!({"source":{"kind":"stream"}}));
    let new_epoch = host.sink.paste_epoch();
    assert!(new_epoch > original);
    assert!(
        host.sink.can_paste(new_epoch, generation),
        "reproduction needs the reopened UI epoch before the new backend generation"
    );
    release.send(())?;
    let mut held = vec![];
    let deadline = Instant::now() + DEADLINE;
    let final_result = loop {
        let (method, params) = host.receive(false, &mut held, deadline)?;
        if method == "final_result" {
            break params;
        }
        host.backlog.push_back((method, params));
    };
    assert_eq!(
        held.iter().map(|request| request.id).collect::<Vec<_>>(),
        [cancel, next]
    );
    assert_eq!(held[0].epoch, original + 1);
    assert_eq!(held[1].epoch, original + 2);
    assert_eq!(final_result["text"], "old queued transcription");
    assert_eq!(final_result["_ui_epoch"], original);
    assert!(
        !host
            .sink
            .can_paste(final_result["_ui_epoch"].as_u64().unwrap(), generation)
    );
    let paste = host.event("paste_requested", |_| true)?;
    assert_eq!(paste["_ui_epoch"], original);
    assert!(
        !host
            .sink
            .can_paste(paste["_ui_epoch"].as_u64().unwrap(), generation)
    );

    let snapshot = host.call("snapshot", json!({}))?;
    assert_eq!(snapshot["pending_pastes"][0]["_ui_epoch"], original);
    assert_eq!(snapshot["last_result"]["_ui_epoch"], original);
    let claim = host.call("claim_paste", json!({"token":paste["token"]}))?;
    assert_eq!(
        claim["_ui_epoch"], original,
        "claim must not relabel the result with its later request epoch"
    );

    for request in held {
        host.driver.send(request);
    }
    host.response(cancel)?;
    let restarted = host.response(next)?;
    assert_eq!(restarted["_ui_epoch"], new_epoch);
    assert!(restarted["generation"].as_u64().unwrap() > generation);
    host.close()?;
    provider
        .take()
        .unwrap()
        .join()
        .map_err(|_| anyhow::anyhow!("provider fixture panicked"))??;
    Ok(())
}

#[test]
fn every_start_alias_binds_generation_and_cancel_state_events_still_arrive() -> Result<()> {
    let data = tempfile::tempdir()?;
    let mut options = Options::new(data.path().into());
    options.allow_test_sources = true;
    options.environment_key = Some("unused-synthetic-fixture-key".into());
    let mut host = Host::start(options)?;
    host.call(
        "set_config",
        json!({"key":"asr.model","value":"qwen3.5-omni-plus"}),
    )?;
    let mut last_generation = 0;
    for method in [
        "start",
        "toggle_recording",
        "hotkey_pressed",
        "start_mic_test",
        "ui_action",
    ] {
        let start = host.call(
            method,
            json!({"source":{"kind":"stream"},"action":"startMicTest"}),
        )?;
        let generation = start["generation"].as_u64().unwrap();
        let epoch = start["_ui_epoch"].as_u64().unwrap();
        assert!(generation > last_generation);
        assert_eq!(epoch, host.sink.paste_epoch());
        let state = host.event("state_changed", |params| {
            params["state"] == "starting" && params["generation"] == generation
        })?;
        assert_eq!(state["_ui_epoch"], epoch);
        host.call("cancel", json!({}))?;
        let idle = host.event("state_changed", |params| {
            params["state"] == "idle" && params["generation"] == generation
        })?;
        assert_eq!(
            idle["_ui_epoch"], epoch,
            "cancel must preserve the session's provenance for state cleanup"
        );
        assert!(!host.sink.can_paste(epoch, generation));
        last_generation = generation;
    }
    host.close()?;
    Ok(())
}
