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
                        else if payload["model"]=="qwen3.5-plus" {"润色后的Rust文本".into()}else{"文件里的rust文本".into()};
                    let body=if compatible {json!({"choices":[{"message":{"content":text},"finish_reason":"stop"}],"usage":{"input_tokens":40,"output_tokens":8}})}
                        else {json!({"output":{"choices":[{"message":{"content":[{"text":text}]},"finish_reason":"stop"}]},"usage":{"input_tokens":40,"output_tokens":8}})}.to_string();
                    let response=format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len());
                    let _=stream.write_all(response.as_bytes()).await;
                });},_=clients.join_next(),if !clients.is_empty()=>{}}
            }
        }));
        let broken = fixture.fail_ws.clone();
        fixture.tasks.push(tokio::spawn(async move {
            let mut clients=tokio::task::JoinSet::new();
            loop {tokio::select!{client=ws.accept()=>{let Ok((stream,_))=client else{break};let broken=broken.clone();
                clients.spawn(async move {
                    let mut socket=accept_hdr_async(stream,|r:&Request,response:Response|{assert!(!r.headers().contains_key("authorization"));Ok(response)}).await.unwrap();
                    let first=socket.next().await.unwrap().unwrap();let first:Value=serde_json::from_str(first.to_text().unwrap()).unwrap();
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
        10
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
    assert_eq!(models[0]["status"], "ok");
    assert_eq!(models[1]["status"], "ok");
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
