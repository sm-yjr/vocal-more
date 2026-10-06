// SPDX-License-Identifier: GPL-3.0-only
//! Isolated real-backend acceptance for full UI/backend queues and quit. No
//! audio device, provider request, installation, or user's config is touched.
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    path::Path,
    thread,
    time::{Duration, Instant},
};
use vocal_more_backend::{application::Options, config::ConfigRepository, dictionary::Dictionary};
use vocal_more_desktop::bridge::{
    BackendDriver, COMMAND_QUEUE_CAPACITY, CommandSink, REQUEST_CAPACITY, Request,
    ShutdownFailures, UI_QUEUE_CAPACITY, UiEvent,
};

const DEADLINE: Duration = Duration::from_secs(20);

struct Host {
    driver: BackendDriver,
    sink: CommandSink,
    events: async_channel::Receiver<UiEvent>,
}
impl Host {
    fn start(data: &Path) -> Result<Self> {
        let (driver, sink, events) = BackendDriver::start(Options::new(data.into()))?;
        let host = Self {
            driver,
            sink,
            events,
        };
        let deadline = Instant::now() + DEADLINE;
        loop {
            match host.events.try_recv() {
                Ok(UiEvent::Backend { method, params }) if method == "initialized" => {
                    ensure!(params["config"]["api_key"] == "");
                    return Ok(host);
                }
                Ok(UiEvent::Backend { method, params }) if method == "backend_disconnected" => {
                    bail!("{}", params["message"])
                }
                Err(async_channel::TryRecvError::Empty) => {
                    ensure!(Instant::now() < deadline);
                    thread::sleep(Duration::from_millis(1));
                }
                _ => {}
            }
        }
    }
    fn admit(&self, method: &str, params: Value, checked: bool) -> Result<Request> {
        let id = if checked {
            self.sink.request_checked(method, params)?
        } else {
            self.sink.request(method, params)
        };
        let deadline = Instant::now() + DEADLINE;
        loop {
            match self.events.try_recv() {
                Ok(UiEvent::Request(request)) => {
                    ensure!(request.id == id, "unexpected queued UI request");
                    return Ok(request);
                }
                Ok(UiEvent::Backend { method, params }) if method == "backend_disconnected" => {
                    bail!("{}", params["message"])
                }
                Err(async_channel::TryRecvError::Empty) => {
                    ensure!(Instant::now() < deadline);
                    thread::sleep(Duration::from_millis(1));
                }
                _ => {}
            }
        }
    }
    fn fill_ui(&self) {
        for _ in 0..UI_QUEUE_CAPACITY {
            self.sink.emit("test_filler", json!({}));
        }
        assert_eq!(self.events.len(), UI_QUEUE_CAPACITY);
    }
    fn await_finished(&self) -> Result<()> {
        let deadline = Instant::now() + DEADLINE;
        while !self.driver.finished() {
            ensure!(
                Instant::now() < deadline,
                "backend did not finish bounded shutdown"
            );
            thread::sleep(Duration::from_millis(1));
        }
        Ok(())
    }
    fn await_clean_finish(&self) -> Result<()> {
        self.await_finished()?;
        assert_eq!(self.driver.shutdown_failures(), ShutdownFailures::default());
        Ok(())
    }
}

/// The durable write proves the driver reached a response blocked by the full
/// UI queue. Its 64 backend slots can now be filled deterministically.
fn wait_checkpoint(data: &Path) -> Result<()> {
    let deadline = Instant::now() + DEADLINE;
    loop {
        if ConfigRepository::open(&data.join("config.yaml"))?
            .config
            .get("ui.language")
            == "en"
        {
            return Ok(());
        }
        ensure!(Instant::now() < deadline, "checkpoint did not persist");
        thread::sleep(Duration::from_millis(1));
    }
}

#[test]
fn checked_ui_rejections_are_direct_and_untracked_callbacks_coalesce() -> Result<()> {
    let data = tempfile::tempdir()?;
    let host = Host::start(data.path())?;
    host.fill_ui();
    let mut rejected = HashSet::new();
    for value in 0..4 {
        let error = host
            .sink
            .request_checked("set_config", json!({"key":"audio.gain","value":value}))
            .unwrap_err();
        assert!(rejected.insert(error.request.id));
        assert_eq!(error.request.params["value"], value);
        assert!(error.message.contains("full"));
        let params = error.into_params();
        assert_eq!(params["method"], "set_config");
        assert!(params["message"].as_str().unwrap().contains("not accepted"));
    }
    assert!(
        host.sink.take_overflow().is_none(),
        "checked failures must not depend on a later overflow poll"
    );
    let mut last = 0;
    for _ in 0..1000 {
        last = host.sink.request("cancel", json!({}));
    }
    let summary = host
        .sink
        .take_overflow()
        .context("missing aggregated callback rejection")?;
    assert_eq!(summary["request_id"], last);
    assert_eq!(summary["rejected_count"], 1000);
    assert!(host.sink.take_overflow().is_none());
    assert_eq!(host.events.len(), UI_QUEUE_CAPACITY);
    host.driver.close();
    assert!(
        host.sink
            .request_checked("status", json!({}))
            .unwrap_err()
            .message
            .contains("closed")
    );
    host.await_clean_finish()
}

#[test]
fn checked_request_budget_is_bounded_and_released_when_requests_are_consumed() -> Result<()> {
    let data = tempfile::tempdir()?;
    let host = Host::start(data.path())?;
    let mut held = Vec::with_capacity(REQUEST_CAPACITY);
    for _ in 0..REQUEST_CAPACITY {
        held.push(host.admit("status", json!({}), true)?);
    }
    assert!(
        host.sink
            .request_checked("status", json!({}))
            .unwrap_err()
            .message
            .contains("awaiting completion")
    );
    drop(held.pop());
    held.push(host.admit("status", json!({}), true)?);
    assert_eq!(held.len(), REQUEST_CAPACITY);
    drop(held);
    host.admit("status", json!({}), true)?;
    host.driver.close();
    host.await_clean_finish()
}

#[test]
fn full_backend_and_ui_queues_retain_every_checked_rejection_id() -> Result<()> {
    let data = tempfile::tempdir()?;
    let host = Host::start(data.path())?;
    let checkpoint = host.admit(
        "set_config",
        json!({"key":"ui.language","value":"en"}),
        false,
    )?;
    let pending = (0..COMMAND_QUEUE_CAPACITY)
        .map(|_| host.admit("status", json!({}), true))
        .collect::<Result<Vec<_>>>()?;
    let rejected = (0..4)
        .map(|value| host.admit("status", json!({"correlation":value}), true))
        .collect::<Result<Vec<_>>>()?;
    let expected = rejected
        .iter()
        .map(|request| request.id)
        .collect::<Vec<_>>();
    host.fill_ui();
    host.driver.send(checkpoint);
    wait_checkpoint(data.path())?;
    for request in pending {
        host.driver.send(request);
    }
    for request in rejected {
        host.driver.send(request);
    }
    assert_eq!(host.events.len(), UI_QUEUE_CAPACITY);
    for (value, id) in expected.into_iter().enumerate() {
        let error = host
            .sink
            .take_overflow()
            .context("a rejected pending ID was lost while UI queue was full")?;
        assert_eq!(error["request_id"], id);
        assert_eq!(error["params"]["correlation"], value);
        assert!(
            error["message"]
                .as_str()
                .unwrap()
                .contains("Backend command queue")
        );
    }
    assert!(host.sink.take_overflow().is_none());
    host.driver.close();
    host.await_clean_finish()
}

#[test]
fn quit_handoff_preserves_ui_edits_after_a_full_backend_queue_in_order() -> Result<()> {
    let data = tempfile::tempdir()?;
    let host = Host::start(data.path())?;
    let checkpoint = host.admit(
        "set_config",
        json!({"key":"ui.language","value":"en"}),
        false,
    )?;
    let backend = (0..COMMAND_QUEUE_CAPACITY)
        .map(|_| {
            host.admit(
                "set_config",
                json!({"key":"ui.language","value":"zh"}),
                true,
            )
        })
        .collect::<Result<Vec<_>>>()?;
    let handoff = (0..UI_QUEUE_CAPACITY).map(|index| host.admit("ui_action",
        json!({"action":"setConfig","key":"ui.language","value":if index + 1 == UI_QUEUE_CAPACITY { "en" } else { "zh" }}), true)).collect::<Result<Vec<_>>>()?;
    assert_eq!(handoff.len(), UI_QUEUE_CAPACITY);
    host.fill_ui();
    host.driver.send(checkpoint);
    wait_checkpoint(data.path())?;
    for request in backend {
        host.driver.send(request);
    }
    assert!(
        host.sink.take_overflow().is_none(),
        "backend slots were not all available"
    );
    let close = Instant::now();
    host.driver.close_with_requests(handoff)?;
    let close_elapsed = close.elapsed();
    assert!(
        close.elapsed() < Duration::from_millis(100),
        "close waited for backend writes on the caller thread"
    );
    host.await_clean_finish()?;
    println!(
        "queue_shutdown: {} backend + {} UI writes; caller {:.3} ms; durable completion {:.3} s",
        COMMAND_QUEUE_CAPACITY,
        UI_QUEUE_CAPACITY,
        close_elapsed.as_secs_f64() * 1000.0,
        close.elapsed().as_secs_f64()
    );
    assert_eq!(
        ConfigRepository::open(&data.path().join("config.yaml"))?
            .config
            .get("ui.language"),
        "en",
        "last accepted UI edit was dropped or executed before the older backend queue"
    );
    Ok(())
}

#[test]
fn close_handoff_is_bounded_and_wakes_an_idle_driver() -> Result<()> {
    let data = tempfile::tempdir()?;
    let host = Host::start(data.path())?;
    let mut oversized = Vec::with_capacity(UI_QUEUE_CAPACITY + 1);
    for _ in 0..=UI_QUEUE_CAPACITY {
        oversized.push(host.admit(
            "set_config",
            json!({"key":"ui.language","value":"en"}),
            false,
        )?);
    }
    assert!(
        host.driver
            .close_with_requests(oversized)
            .unwrap_err()
            .to_string()
            .contains("capacity")
    );
    // A rejected oversize handoff must leave normal bounded admission open.
    let save = host.admit(
        "set_config",
        json!({"key":"ui.language","value":"en"}),
        true,
    )?;
    host.driver.close_with_requests(vec![save])?;
    host.await_clean_finish()?;
    assert_eq!(
        ConfigRepository::open(&data.path().join("config.yaml"))?
            .config
            .get("ui.language"),
        "en"
    );
    Ok(())
}

#[test]
fn shutdown_reports_config_write_failures_and_continues_both_durable_queues() -> Result<()> {
    let data = tempfile::tempdir()?;
    let host = Host::start(data.path())?;
    assert!(!host.driver.finished());
    assert_eq!(host.driver.shutdown_failures(), ShutdownFailures::default());
    let checkpoint = host.admit(
        "set_config",
        json!({"key":"ui.language","value":"en"}),
        false,
    )?;
    let mut backend = vec![
        host.admit(
            "set_config",
            json!({"key":"api_key","value":"isolated-secret-must-not-leak"}),
            true,
        )?,
        host.admit(
            "add_dict_entry",
            json!({"term":"backend saved after failure"}),
            true,
        )?,
        host.admit(
            "set_config",
            json!({"key":"ui.language","value":"zh"}),
            true,
        )?,
    ];
    while backend.len() < COMMAND_QUEUE_CAPACITY {
        backend.push(host.admit("status", json!({}), true)?);
    }
    let handoff = vec![
        host.admit(
            "ui_action",
            json!({"action":"setConfig","key":"llm.prompt_overrides","value":{
                "tone":{"enabled":true,"prompt":"private prompt must not leak"}}}),
            true,
        )?,
        host.admit(
            "ui_action",
            json!({"action":"addDictEntry","term":"UI saved after failure"}),
            true,
        )?,
        host.admit("set_config", json!({"key":"audio.gain","value":23}), true)?,
        host.admit(
            "add_dict_entry",
            json!({"term":"final accepted edit saved"}),
            true,
        )?,
    ];
    host.fill_ui();
    host.driver.send(checkpoint);
    wait_checkpoint(data.path())?;
    // The backend is already open and a successful RPC is blocked by the UI
    // queue. Replacing only this fixture's destination forces atomic rename
    // to fail; permissions/root behavior cannot accidentally bypass it.
    let config = data.path().join("config.yaml");
    std::fs::remove_file(&config)?;
    std::fs::create_dir(&config)?;
    for request in backend {
        host.driver.send(request);
    }
    assert!(host.sink.take_overflow().is_none());
    host.driver.close_with_requests(handoff)?;
    host.await_finished()?;
    let failures = host.driver.shutdown_failures();
    assert_eq!(
        failures,
        ShutdownFailures {
            failed_durable_requests: 4,
            cleanup_failed: false,
        }
    );
    // Exact aggregate output and fixed layout rule out requests, keys, API
    // credentials, prompts, file paths and error text leaking via this API.
    assert_eq!(
        format!("{failures:?}"),
        "ShutdownFailures { failed_durable_requests: 4, cleanup_failed: false }"
    );
    assert!(std::mem::size_of::<ShutdownFailures>() <= 2 * std::mem::size_of::<usize>());
    assert_eq!(host.driver.shutdown_failures(), failures);
    assert!(config.is_dir());
    let entries = Dictionary::open(&data.path().join("dictionary.yaml"))?.entries;
    assert_eq!(
        entries
            .iter()
            .map(|entry| entry.term.as_str())
            .collect::<Vec<_>>(),
        [
            "backend saved after failure",
            "UI saved after failure",
            "final accepted edit saved"
        ],
        "a failed save stopped later durable requests or reordered the handoff"
    );
    Ok(())
}
