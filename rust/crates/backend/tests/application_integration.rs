use anyhow::{Context, Result, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::{Notify, broadcast},
    time::{sleep, timeout},
};
use tokio_tungstenite::{
    accept_hdr_async,
    tungstenite::{
        Message,
        handshake::server::{Request, Response},
    },
};
use vocal_more_backend::{
    application::{Application, Options},
    catalog::CONTRACT,
    provider::Endpoints,
};

struct Fixture {
    http: String,
    ws: String,
    requests: Arc<Mutex<Vec<Value>>>,
    sessions: Arc<Mutex<Vec<Value>>>,
    fail_ws: Arc<AtomicBool>,
    stall: Arc<AtomicBool>,
    arrived: Arc<Notify>,
    tasks: Vec<tokio::task::JoinHandle<()>>,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        for task in &self.tasks {
            task.abort();
        }
    }
}
async fn request(stream: &mut TcpStream) -> Result<(String, Value)> {
    let mut bytes = vec![];
    let mut buffer = [0u8; 4096];
    let end;
    loop {
        let n = stream.read(&mut buffer).await?;
        ensure!(n > 0, "request closed");
        bytes.extend_from_slice(&buffer[..n]);
        ensure!(bytes.len() < 2 * 1024 * 1024, "oversized request");
        if let Some(index) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
            end = index + 4;
            break;
        }
    }
    let headers = String::from_utf8(bytes[..end].to_vec())?;
    ensure!(!headers.to_lowercase().contains("authorization:"));
    let length: usize = headers
        .lines()
        .find_map(|l| {
            l.to_lowercase()
                .strip_prefix("content-length:")
                .map(|s| s.trim().parse())
        })
        .context("missing content length")??;
    while bytes.len() < end + length {
        let n = stream.read(&mut buffer).await?;
        ensure!(n > 0);
        bytes.extend_from_slice(&buffer[..n]);
    }
    Ok((headers, serde_json::from_slice(&bytes[end..end + length])?))
}
impl Fixture {
    async fn new() -> Result<Self> {
        let http = TcpListener::bind("127.0.0.1:0").await?;
        let ws = TcpListener::bind("127.0.0.1:0").await?;
        let mut fixture = Self {
            http: format!("http://{}", http.local_addr()?),
            ws: format!("ws://{}", ws.local_addr()?),
            requests: Arc::new(Mutex::new(vec![])),
            sessions: Arc::new(Mutex::new(vec![])),
            fail_ws: Arc::new(AtomicBool::new(false)),
            stall: Arc::new(AtomicBool::new(false)),
            arrived: Arc::new(Notify::new()),
            tasks: vec![],
        };
        let requests = fixture.requests.clone();
        let stall = fixture.stall.clone();
        let arrived = fixture.arrived.clone();
        fixture.tasks.push(tokio::spawn(async move {
            let mut clients=tokio::task::JoinSet::new();
            loop {tokio::select!{client=http.accept()=>{let Ok((mut stream,_))=client else{break};let requests=requests.clone();let stall=stall.clone();let arrived=arrived.clone();
                clients.spawn(async move {
                    let (headers,payload)=request(&mut stream).await.unwrap();requests.lock().unwrap().push(payload.clone());arrived.notify_one();
                    if stall.load(Ordering::Relaxed){let mut byte=[0];let _=stream.read(&mut byte).await;return}
                    let classify=payload["model"]=="qwen3.7-plus" && payload["response_format"]["type"]=="json_object";
                    let compatible=headers.contains("compatible-mode");
                    let text=if classify {json!({"decision":"add","term":"GitHub","aliases":["github"],"term_type":"proper_name","confidence":0.02}).to_string()}
                        else if matches!(payload["model"].as_str(), Some("qwen3.5-plus" | "qwen3.8-flash" | "qwen3.8-max")) {"润色后的Rust文本".into()}else{"文件里的rust文本".into()};
                    let body=if compatible {json!({"choices":[{"message":{"content":text},"finish_reason":"stop"}],"usage":{"input_tokens":40,"output_tokens":8}})}
                        else {json!({"output":{"choices":[{"message":{"content":[{"text":text}]},"finish_reason":"stop"}]},"usage":{"input_tokens":40,"output_tokens":8}})}.to_string();
                    let response=format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len());
                    let _=stream.write_all(response.as_bytes()).await;
                });},_=clients.join_next(),if !clients.is_empty()=>{}}
            }
        }));
        let broken = fixture.fail_ws.clone();
        let sessions = fixture.sessions.clone();
        fixture.tasks.push(tokio::spawn(async move {
            let mut clients=tokio::task::JoinSet::new();
            loop {tokio::select!{client=ws.accept()=>{let Ok((stream,_))=client else{break};let broken=broken.clone();let sessions=sessions.clone();
                clients.spawn(async move {
                    let mut socket=accept_hdr_async(stream,|r:&Request,response:Response|{assert!(!r.headers().contains_key("authorization"));Ok(response)}).await.unwrap();
                    let first=socket.next().await.unwrap().unwrap();let first:Value=serde_json::from_str(first.to_text().unwrap()).unwrap();
                    sessions.lock().unwrap().push(first.clone());
                    let recognition=first["header"]["action"]=="run-task";let task=first["header"]["task_id"].clone();
                    let ready=if recognition {json!({"header":{"event":"task-started","task_id":task}})}else{json!({"type":"session.updated"})};
                    socket.send(Message::Text(ready.to_string().into())).await.unwrap();
                    while let Some(Ok(message))=socket.next().await {
                        if broken.load(Ordering::Relaxed) && (message.is_binary() || message.to_text().is_ok_and(|t|t.contains("input_audio_buffer.append"))) {
                            let _=socket.send(Message::Text(json!({"type":"error","error":{"code":"FixtureNetworkDown"}}).to_string().into())).await;break
                        }
                        let Ok(raw)=message.to_text() else{continue};let Ok(value)=serde_json::from_str::<Value>(raw) else{continue};
                        let replies=if value["type"]=="input_audio_buffer.commit" {vec![json!({"type":"conversation.item.input_audio_transcription.completed","item_id":"final","transcript":"原始的rust文本"})]}
                            else if value["type"]=="response.create" {vec![json!({"type":"response.done","response":{"status":"completed","output":[{"content":[{"type":"text","text":"生成的rust文本"}]}],"usage":{"input_tokens":20,"output_tokens":6}}})]}
                            else if value["header"]["action"]=="finish-task" {vec![json!({"header":{"event":"result-generated","task_id":task},"payload":{"output":{"sentence":{"sentence_end":true,"sentence_id":1,"text":"原始的rust文本"}}}}),json!({"header":{"event":"task-finished","task_id":task}})]}
                            else{vec![]};
                        for reply in replies {if socket.send(Message::Text(reply.to_string().into())).await.is_err(){return}}
                    }
                });},_=clients.join_next(),if !clients.is_empty()=>{}}
            }
        }));
        Ok(fixture)
    }
    async fn app(&self, path: &Path) -> Result<Application> {
        let mut options = Options::new(path.into());
        options.endpoints = Endpoints::loopback(&self.http, &self.ws)?;
        options.allow_test_sources = true;
        options.environment_key = Some("synthetic-secret-must-never-leave-process".into());
        Application::open(options).await
    }
}
async fn event(events: &mut broadcast::Receiver<Value>, method: &str) -> Result<Value> {
    timeout(Duration::from_secs(5), async {
        loop {
            let event = events.recv().await?;
            if event["method"] == method {
                return Ok::<_, anyhow::Error>(event["params"].clone());
            }
        }
    })
    .await?
}
async fn idle(app: &Application) -> Result<()> {
    timeout(Duration::from_secs(4), async {
        loop {
            if app.call("status", json!({})).await?["state"] == "idle" {
                return Ok::<_, anyhow::Error>(());
            }
            sleep(Duration::from_millis(5)).await;
        }
    })
    .await?
}
async fn audio(app: &Application) -> Result<Value> {
    let started = app
        .call("start", json!({"source":{"kind":"stream"}}))
        .await?;
    for _ in 0..5 {
        app.call("append",json!({"generation":started["generation"],"pcm_base64":STANDARD.encode(vec![1u8;1280])})).await?;
    }
    app.call("finish", json!({})).await?;
    Ok(started)
}

#[tokio::test]
async fn every_shipped_model_runs_through_application_dictionary_history_and_paste() -> Result<()> {
    let fixture = Fixture::new().await?;
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;
    let mut events = app.subscribe();
    app.call("add_dict_entry", json!({"term":"Rust","aliases":["rust"]}))
        .await?;
    for model in CONTRACT["all_asr_models"].as_array().unwrap() {
        app.call("set_config", json!({"key":"asr.model","value":model["id"]}))
            .await?;
        let started = audio(&app).await?;
        let result = event(&mut events, "final_result").await?;
        assert_eq!(result["generation"], started["generation"]);
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("Rust"), "{model}: {result}");
        let paste = event(&mut events, "paste_requested").await?;
        assert_eq!(paste["text"], result["text"]);
        let claim = app
            .call("claim_paste", json!({"token":paste["token"]}))
            .await?;
        assert_eq!(claim["text"], result["text"]);
        assert_eq!(
            app.call("claim_paste", json!({"token":paste["token"]}))
                .await?["cancelled"],
            true
        );
        idle(&app).await?;
        let history = app.call("list_recordings", json!({})).await?;
        let record = &history[0];
        assert_eq!(record["status"], "success");
        assert_eq!(record["transcript"], result["raw_text"]);
        assert_eq!(record["duration_seconds"], 0.2);
        assert!(record["billing"].is_object());
    }
    assert_eq!(
        app.call("list_recordings", json!({}))
            .await?
            .as_array()
            .unwrap()
            .len(),
        CONTRACT["all_asr_models"].as_array().unwrap().len()
    );
    app.call("shutdown", json!({})).await?;
    Ok(())
}

#[tokio::test]
async fn dictation_scene_is_persisted_and_snapshotted_for_realtime_and_fallback() -> Result<()> {
    let fixture = Fixture::new().await?;
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;
    let mut events = app.subscribe();
    let description = "我在玩《巫师 3》，会提到杰洛特和昆特牌。";

    app.call(
        "set_asr_model",
        json!({"model":"qwen3.5-omni-flash-realtime"}),
    )
    .await?;
    app.call("set_config", json!({"key":"enable_polish","value":false}))
        .await?;
    app.call(
        "save_dictation_scene",
        json!({"name":"巫师 3","description":description}),
    )
    .await?;
    assert!(
        app.call(
            "save_dictation_scene",
            json!({"name":"无效","description":"词".repeat(401)})
        )
        .await
        .is_err()
    );
    let config = app.call("get_config", json!({})).await?;
    let scene_id = &config["asr"]["scenes"]["entries"][0]["id"];
    assert!(config["asr"]["scenes"]["active_id"].is_null());
    app.call("select_dictation_scene", json!({"id":scene_id}))
        .await?;
    assert_eq!(
        vocal_more_backend::config::ConfigRepository::open(&temp.path().join("config.yaml"))?
            .config
            .scenes()
            .active()
            .unwrap()
            .description,
        description
    );

    let started = app
        .call(
            "start",
            json!({"source":{"kind":"stream"},"context":"本地应用语境"}),
        )
        .await?;
    // The next selection must not affect the session in progress or its recovery.
    app.call("select_dictation_scene", json!({"id":null}))
        .await?;
    for _ in 0..5 {
        app.call("append", json!({"generation":started["generation"],"pcm_base64":STANDARD.encode(vec![1u8;1280])})).await?;
    }
    app.call("finish", json!({})).await?;
    let result = event(&mut events, "final_result").await?;
    assert_eq!(result["raw_text"], "生成的rust文本");
    let session = fixture.sessions.lock().unwrap()[0].clone();
    let instructions = session["session"]["instructions"].as_str().unwrap();
    assert!(instructions.contains(description));
    assert!(instructions.contains("本地应用语境"));
    assert!(instructions.contains("不要复述场景"));
    idle(&app).await?;

    audio(&app).await?;
    let result = event(&mut events, "final_result").await?;
    assert_eq!(result["raw_text"], "原始的rust文本");
    assert!(fixture.sessions.lock().unwrap()[1]["session"]["instructions"].is_null());
    idle(&app).await?;

    app.call(
        "set_asr_model",
        json!({"model":"qwen-audio-3.0-asr-flash-streaming"}),
    )
    .await?;
    app.call("select_dictation_scene", json!({"id":scene_id}))
        .await?;
    fixture.fail_ws.store(true, Ordering::Relaxed);
    audio(&app).await?;
    event(&mut events, "final_result").await?;
    assert_eq!(
        fixture.sessions.lock().unwrap()[2]["payload"]["input"]["context"][0]["content"][0]["text"],
        description
    );
    assert!(
        fixture.requests.lock().unwrap()[0]["input"]["messages"][0]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains(description)
    );
    idle(&app).await?;

    app.call("delete_dictation_scene", json!({"id":scene_id}))
        .await?;
    assert!(
        app.call("select_dictation_scene", json!({"id":scene_id}))
            .await
            .is_err()
    );
    let config = app.call("get_config", json!({})).await?;
    assert_eq!(
        config["asr"]["scenes"],
        json!({"entries":[],"active_id":null})
    );
    app.call("shutdown", json!({})).await?;
    Ok(())
}

#[tokio::test]
async fn broken_realtime_falls_back_without_losing_audio_and_retry_is_independent() -> Result<()> {
    let fixture = Fixture::new().await?;
    fixture.fail_ws.store(true, Ordering::Relaxed);
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;
    let mut events = app.subscribe();
    audio(&app).await?;
    let result = event(&mut events, "final_result").await?;
    assert!(result["raw_text"].as_str().unwrap().contains("文件"));
    idle(&app).await?;
    let record = app.call("list_recordings", json!({})).await?[0].clone();
    assert_eq!(
        app.call("retry_transcription", json!({"id":record["id"]}))
            .await?["status"],
        "accepted"
    );
    event(&mut events, "retry_completed").await?;
    {
        let requests = fixture.requests.lock().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(requests[1]["model"], "qwen3.5-omni-plus");
    }
    app.call("shutdown", json!({})).await?;
    Ok(())
}

#[tokio::test]
async fn cancel_stalled_processing_keeps_commands_responsive_and_discards_paste() -> Result<()> {
    let fixture = Fixture::new().await?;
    fixture.stall.store(true, Ordering::Relaxed);
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;
    let mut events = app.subscribe();
    app.call(
        "set_config",
        json!({"key":"asr.model","value":"qwen3.5-omni-plus"}),
    )
    .await?;
    let started = audio(&app).await?;
    timeout(Duration::from_secs(3), fixture.arrived.notified()).await?;
    timeout(
        Duration::from_millis(500),
        app.call("set_config", json!({"key":"ui.language","value":"en"})),
    )
    .await??;
    timeout(Duration::from_millis(500), app.call("cancel", json!({}))).await??;
    idle(&app).await?;
    assert!(
        app.call(
            "append",
            json!({"generation":started["generation"],"pcm_base64":STANDARD.encode([0,0])})
        )
        .await
        .is_err()
    );
    while let Ok(message) = events.try_recv() {
        assert_ne!(message["method"], "final_result");
        assert_ne!(message["method"], "paste_requested");
    }
    fixture.stall.store(false, Ordering::Relaxed);
    audio(&app).await?;
    event(&mut events, "final_result").await?;
    app.call("shutdown", json!({})).await?;
    Ok(())
}

#[tokio::test]
async fn settings_restart_model_check_and_learning_use_only_rust_services() -> Result<()> {
    let fixture = Fixture::new().await?;
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;
    let mut events = app.subscribe();
    app.call(
        "ui_action",
        json!({"action":"setConfig","key":"api_key","value":"synthetic-key"}),
    )
    .await?;
    app.call("ui_action",json!({"action":"syncFormState","state":{"api_key":"","ui":{"language":"en"},"dictionary_learning":{"enabled":true}}})).await?;
    assert_eq!(app.call("get_config", json!({})).await?["api_key"], "");
    app.call("ui_action", json!({"action":"checkDashScopeModels"}))
        .await?;
    let models = event(&mut events, "model_check_complete").await?;
    assert_eq!(
        models.as_array().unwrap().len(),
        CONTRACT["asr_models"].as_array().unwrap().len()
            + CONTRACT["llm_models"].as_array().unwrap().len()
    );
    assert!(
        models
            .as_array()
            .unwrap()
            .iter()
            .all(|model| model["status"] == "ok")
    );
    assert_eq!(
        models
            .as_array()
            .unwrap()
            .iter()
            .filter(|model| model["family"] == "asr")
            .count(),
        CONTRACT["asr_models"].as_array().unwrap().len()
    );
    assert_eq!(
        models
            .as_array()
            .unwrap()
            .iter()
            .filter(|model| model["family"] == "llm")
            .count(),
        CONTRACT["llm_models"].as_array().unwrap().len()
    );
    app.call("submit_correction",json!({"evidence":{"raw_text":"github","pasted_text":"github","baseline_text":"github","edited_text":"GitHub","recording_id":"r1","observation_id":"o1"}})).await?;
    timeout(Duration::from_secs(4), async {
        loop {
            if app
                .call("get_dictionary", json!({}))
                .await?
                .as_array()
                .unwrap()
                .iter()
                .any(|d| d["term"] == "GitHub")
            {
                break;
            }
            sleep(Duration::from_millis(10)).await;
        }
        Ok::<_, anyhow::Error>(())
    })
    .await??;
    let records = app.call("get_dictionary_learning", json!({})).await?;
    let id = records[0]["id"].clone();
    assert_eq!(
        app.call(
            "ui_action",
            json!({"action":"undoDictionaryLearning","id":id})
        )
        .await?["ok"],
        true
    );
    app.call("shutdown", json!({})).await?;
    drop(app);
    let app = fixture.app(temp.path()).await?;
    assert_eq!(
        app.call("get_config", json!({})).await?["ui"]["language"],
        "en"
    );
    assert!(std::fs::read_to_string(temp.path().join("config.yaml"))?.contains("synthetic-key"));
    assert_eq!(
        app.call("get_dictionary_learning", json!({})).await?[0]["status"],
        "reverted"
    );
    app.call("shutdown", json!({})).await?;
    Ok(())
}

#[tokio::test]
async fn failed_durable_commit_never_publishes_realtime_success() -> Result<()> {
    failed_commit(false).await
}

#[tokio::test]
async fn failed_history_commit_returns_idle_without_publishing_success() -> Result<()> {
    failed_commit(true).await
}

async fn failed_commit(history_failure: bool) -> Result<()> {
    let fixture = Fixture::new().await?;
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;
    let mut events = app.subscribe();
    app.call("set_config", json!({"key":"enable_polish","value":false}))
        .await?;
    app.call("set_asr_model", json!({"model":"qwen3-asr-flash-realtime"}))
        .await?;
    let started = app
        .call("start", json!({"source":{"kind":"stream"}}))
        .await?;
    for _ in 0..5 {
        app.call("append", json!({"generation":started["generation"],"pcm_base64":STANDARD.encode(vec![1u8;1280])})).await?;
    }
    let status = timeout(Duration::from_secs(3), async {
        loop {
            let status = app.call("status", json!({})).await?;
            if status["core"]["pcm_bytes"].as_u64().unwrap() >= 6400 {
                return Ok::<_, anyhow::Error>(status);
            }
            sleep(Duration::from_millis(5)).await;
        }
    })
    .await??;
    let id = status["core"]["recording_id"].as_str().unwrap();
    // Make only the final atomic replacement fail after the initial receipt.
    let failure = if history_failure {
        let path = temp.path().join("recordings/history.json");
        std::fs::remove_file(&path)?;
        path
    } else {
        temp.path()
            .join("recordings")
            .join(format!("{id}.json.tmp"))
    };
    std::fs::create_dir(&failure)?;
    app.call("finish", json!({})).await?;
    idle(&app).await?;
    let mut saw_error = false;
    while let Ok(event) = events.try_recv() {
        assert_ne!(event["method"], "final_result");
        assert_ne!(event["method"], "paste_requested");
        if event["method"] == "error" {
            saw_error |= history_failure
                || event["params"]["message"]
                    .as_str()
                    .unwrap()
                    .contains("recording commit failed");
        }
    }
    assert!(saw_error);
    let history = app.call("list_recordings", json!({})).await?;
    if history_failure {
        assert!(history.as_array().unwrap().is_empty());
    } else {
        assert_eq!(history[0]["status"], "failed");
        assert!(history[0]["transcript"].is_null());
    }
    assert!(
        fixture.requests.lock().unwrap().is_empty(),
        "commit failure must not trigger a second cloud transcription"
    );
    std::fs::remove_dir(failure)?;
    app.call("shutdown", json!({})).await?;
    Ok(())
}

/// Offline latency control: generated PCM, loopback provider, disposable history.
/// Prints only elapsed milliseconds; no provider text, settings or credentials.
#[tokio::test]
async fn offline_dictation_latency_probe() -> Result<()> {
    let fixture = Fixture::new().await?;
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;
    app.call(
        "set_config",
        json!({"key":"default_mode","value":"walkie_talkie"}),
    )
    .await?;
    let mut events = app.subscribe();
    let mut start_ms = Vec::new();
    let mut final_ms = Vec::new();
    let mut claim_ms = Vec::new();
    for _ in 0..8 {
        let start = std::time::Instant::now();
        let session = app
            .call("hotkey_pressed", json!({"source":{"kind":"stream"}}))
            .await?;
        start_ms.push(start.elapsed().as_secs_f64() * 1000.0);
        for _ in 0..5 {
            app.call("append",json!({"generation":session["generation"],"pcm_base64":STANDARD.encode(vec![0u8;1280])})).await?;
        }
        let finish = std::time::Instant::now();
        app.call("hotkey_released", json!({})).await?;
        let result = event(&mut events, "final_result").await?;
        final_ms.push(finish.elapsed().as_secs_f64() * 1000.0);
        assert_eq!(result["generation"], session["generation"]);
        let paste = event(&mut events, "paste_requested").await?;
        let claim = std::time::Instant::now();
        let claimed = app
            .call("claim_paste", json!({"token":paste["token"]}))
            .await?;
        assert_ne!(claimed["cancelled"], true);
        assert_eq!(claimed["token"], paste["token"]);
        app.call(
            "prepare_paste_observation",
            json!({"token":paste["token"],"snapshot":null}),
        )
        .await?;
        claim_ms.push(claim.elapsed().as_secs_f64() * 1000.0);
        idle(&app).await?;
    }
    for (stage, mut values) in [
        ("start_rpc", start_ms),
        ("stop_to_durable_final", final_ms),
        ("claim_prepare", claim_ms),
    ] {
        values.sort_by(f64::total_cmp);
        println!(
            "offline_latency stage={stage} samples={} median_ms={:.3} max_ms={:.3}",
            values.len(),
            values[values.len() / 2],
            values[values.len() - 1]
        );
    }
    app.call("shutdown", json!({})).await?;
    Ok(())
}

#[tokio::test]
async fn message_asr_dual_track_and_cross_turn_context_work_end_to_end() -> Result<()> {
    let fixture = Fixture::new().await?;
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;
    let mut events = app.subscribe();

    // Turn 1: default qwen-audio-3.1-asr-flash-message with default minimal polish runs single-pass (no HTTP LLM call).
    let started = app
        .call(
            "start",
            json!({
                "source": {"kind": "stream"},
                "cursor_context": {
                    "app_name": "Cursor",
                    "bundle_id": "com.todesktop.230313mzl4w4u92",
                    "window_title": "provider.rs — vocal-more",
                    "before_cursor": "let route = needs_second_stage_polish(&config);",
                    "after_cursor": "",
                    "selected_text": "ButterworthHighpass4"
                }
            }),
        )
        .await?;
    for _ in 0..5 {
        app.call(
            "append",
            json!({"generation": started["generation"], "pcm_base64": STANDARD.encode(vec![1u8; 1280])}),
        )
        .await?;
    }
    app.call("finish", json!({})).await?;
    let first = event(&mut events, "final_result").await?;
    assert_eq!(first["text"], "原始的 rust 文本");
    assert!(
        fixture.requests.lock().unwrap().is_empty(),
        "default minimal mode on qwen-audio-3.1-asr-flash-message must skip second-stage HTTP LLM"
    );
    let session_1 = fixture.sessions.lock().unwrap()[0].clone();
    assert_eq!(
        session_1["payload"]["model"],
        "qwen-audio-3.1-asr-flash-message"
    );
    assert_eq!(
        session_1["payload"]["parameters"]["disfluency_removal_enabled"],
        true
    );
    let vocab = session_1["payload"]["parameters"]["vocabulary"]
        .as_object()
        .unwrap();
    assert_eq!(
        vocab.get("ButterworthHighpass4"),
        Some(&json!(50)),
        "selected identifier must be promoted as dynamic super hotword: {vocab:?}"
    );
    idle(&app).await?;

    // Turn 2: switch to balanced polish; hybrid_asr cascades to qwen3.8-flash and carries turn 1 in recent history.
    app.call(
        "set_config",
        json!({"key": "llm.level", "value": "balanced"}),
    )
    .await?;
    let started_2 = app
        .call(
            "start",
            json!({
                "source": {"kind": "stream"},
                "cursor_context": {
                    "app_name": "Cursor",
                    "bundle_id": "com.todesktop.230313mzl4w4u92",
                    "window_title": "provider.rs — vocal-more",
                    "before_cursor": "",
                    "after_cursor": "",
                    "selected_text": ""
                }
            }),
        )
        .await?;
    for _ in 0..5 {
        app.call(
            "append",
            json!({"generation": started_2["generation"], "pcm_base64": STANDARD.encode(vec![1u8; 1280])}),
        )
        .await?;
    }
    app.call("finish", json!({})).await?;
    let second = event(&mut events, "final_result").await?;
    assert_eq!(second["text"], "润色后的 Rust 文本");
    let requests = fixture.requests.lock().unwrap().clone();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0]["model"], "qwen3.8-flash");
    let session_2 = fixture.sessions.lock().unwrap()[1].clone();
    let turns = session_2["payload"]["input"]["context"].as_array().unwrap();
    assert!(
        turns.iter().any(|t| {
            t["content"][0]["text"]
                .as_str()
                .is_some_and(|s| s.contains("原始的 rust 文本"))
        }),
        "turn 2 must carry recent dictation history for the same app: {turns:?}"
    );
    idle(&app).await?;

    // Turn 3: disabling cursor_context_enabled immediately clears recent_history and rejects late context updates.
    app.call(
        "set_config",
        json!({"key": "cursor_context_enabled", "value": false}),
    )
    .await?;
    let started_3 = app
        .call("start", json!({"source": {"kind": "stream"}}))
        .await?;
    let rejected = app
        .call(
            "update_session_context",
            json!({
                "generation": started_3["generation"],
                "snapshot": {
                    "app_name": "Cursor",
                    "app_bundle_id": "com.todesktop.230313mzl4w4u92",
                    "window_title": "provider.rs",
                    "before_cursor": "let x = 1;"
                }
            }),
        )
        .await?;
    assert_eq!(rejected["accepted"], false);
    for _ in 0..5 {
        app.call(
            "append",
            json!({"generation": started_3["generation"], "pcm_base64": STANDARD.encode(vec![1u8; 1280])}),
        )
        .await?;
    }
    app.call("finish", json!({})).await?;
    let _ = event(&mut events, "final_result").await?;
    let session_3 = fixture.sessions.lock().unwrap()[2].clone();
    assert!(
        session_3["payload"]["input"].get("context").is_none(),
        "disabled cursor context must clear prior recent history: {session_3}"
    );
    idle(&app).await?;

    app.call("shutdown", json!({})).await?;
    Ok(())
}

#[tokio::test]
async fn dictionary_edit_batch_restore_reload_and_learning_clear_work_end_to_end() -> Result<()> {
    let fixture = Fixture::new().await?;
    let temp = tempfile::tempdir()?;
    let app = fixture.app(temp.path()).await?;

    let initial = app.call("snapshot", json!({})).await?;
    assert_eq!(
        initial["dictionary_path"].as_str(),
        Some(
            temp.path()
                .join("dictionary.yaml")
                .to_string_lossy()
                .as_ref()
        )
    );

    // Batch add: 2 new entries + 1 merge + 1 empty skipped.
    let batch = app
        .call(
            "ui_action",
            json!({
                "action": "batchAddDictEntries",
                "entries": [
                    {"term": "Kubernetes", "aliases": ["k8s", "库伯内蒂斯"]},
                    {"term": "GPUI", "aliases": ["gp ui"]},
                    {"term": "Kubernetes", "aliases": ["k8s", "K8S"]},
                    {"term": "   ", "aliases": ["ignored"]}
                ]
            }),
        )
        .await?;
    assert_eq!(batch["summary"]["added_terms"], 2);
    assert_eq!(batch["summary"]["updated_terms"], 1);
    assert_eq!(batch["summary"]["added_aliases"], 4);
    assert_eq!(batch["summary"]["skipped"], 1);

    // Update in place with expected_aliases precondition and rename conflict rejection.
    assert!(
        app.call(
            "ui_action",
            json!({
                "action": "updateDictEntry",
                "old_term": "GPUI",
                "term": "GPUI Kit",
                "aliases": ["gp ui", "gpui kit"],
                "expected_aliases": ["stale alias"]
            })
        )
        .await
        .is_err()
    );
    assert!(
        app.call(
            "ui_action",
            json!({
                "action": "updateDictEntry",
                "old_term": "GPUI",
                "term": "Kubernetes",
                "aliases": ["gp ui"]
            })
        )
        .await
        .is_err()
    );
    app.call(
        "ui_action",
        json!({
            "action": "updateDictEntry",
            "old_term": "GPUI",
            "term": "GPUI Kit",
            "aliases": ["gp ui", "gpui kit"],
            "expected_aliases": ["gp ui"]
        }),
    )
    .await?;
    let entries = app.call("get_dictionary", json!({})).await?;
    assert_eq!(entries[0]["term"], "Kubernetes");
    assert_eq!(entries[1]["term"], "GPUI Kit");
    assert_eq!(entries[1]["aliases"], json!(["gp ui", "gpui kit"]));

    // Immediate remove returns authoritative removed metadata; restore puts it back at original index.
    let removed = app
        .call(
            "ui_action",
            json!({"action": "removeDictEntry", "term": "Kubernetes"}),
        )
        .await?;
    assert_eq!(removed["removed"][0]["index"], 0);
    assert_eq!(
        app.call("get_dictionary", json!({})).await?[0]["term"],
        "GPUI Kit"
    );

    app.call(
        "ui_action",
        json!({"action": "restoreDictEntries", "entries": removed["removed"]}),
    )
    .await?;
    let restored = app.call("get_dictionary", json!({})).await?;
    assert_eq!(restored[0]["term"], "Kubernetes");
    assert_eq!(restored[1]["term"], "GPUI Kit");

    // Strict reload rejects malformed YAML without emptying live memory, and accepts valid edits & empty list.
    let dict_path = temp.path().join("dictionary.yaml");
    std::fs::write(&dict_path, "not_entries: []\n")?;
    assert!(
        app.call("ui_action", json!({"action": "reloadDict"}))
            .await
            .is_err()
    );
    std::fs::write(&dict_path, "entries:\n  - term: {unexpected: value}\n")?;
    assert!(
        app.call("ui_action", json!({"action": "reloadDict"}))
            .await
            .is_err()
    );
    std::fs::write(
        &dict_path,
        "entries:\n  - term: Valid\n    aliases:\n      - {unexpected: value}\n",
    )?;
    assert!(
        app.call("ui_action", json!({"action": "reloadDict"}))
            .await
            .is_err()
    );
    assert_eq!(
        app.call("get_dictionary", json!({}))
            .await?
            .as_array()
            .unwrap()
            .len(),
        2
    );
    std::fs::write(
        &dict_path,
        "entries:\n  - term: Vocal More\n    aliases:\n      - vocalmore\n",
    )?;
    let reloaded = app
        .call("ui_action", json!({"action": "reloadDict"}))
        .await?;
    assert_eq!(reloaded["count"], 1);
    assert_eq!(
        app.call("get_dictionary", json!({})).await?[0]["term"],
        "Vocal More"
    );

    std::fs::write(&dict_path, "entries: []\n")?;
    let emptied = app
        .call("ui_action", json!({"action": "reloadDict"}))
        .await?;
    assert_eq!(emptied["count"], 0);

    std::fs::remove_file(&dict_path)?;
    assert!(
        app.call("ui_action", json!({"action": "reloadDict"}))
            .await
            .is_err()
    );

    // Clear learning history RPC succeeds cleanly even when empty.
    let cleared = app
        .call(
            "ui_action",
            json!({"action": "clearDictionaryLearningHistory"}),
        )
        .await?;
    assert_eq!(cleared["cleared"], 0);

    app.call("shutdown", json!({})).await?;
    Ok(())
}
