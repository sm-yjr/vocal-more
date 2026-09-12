// SPDX-License-Identifier: GPL-3.0-only
use anyhow::{Result, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::{
    net::{TcpListener, TcpStream},
    time::{sleep, timeout},
};
use tokio_tungstenite::{
    WebSocketStream, accept_hdr_async,
    tungstenite::{
        Message,
        handshake::server::{Request, Response},
    },
};
use vocal_more_backend::{
    catalog::CONTRACT,
    config::Config,
    provider::{Endpoints, Provider},
};
use vocal_more_core::{
    audio::Source,
    recording::RecordingStore,
    runtime::{Host, Phase, wait_terminal},
};

fn provider(config: Config, endpoint: &str) -> Provider {
    Provider::new(
        config,
        vec![],
        "",
        Endpoints::loopback("http://127.0.0.1:1", endpoint).unwrap(),
        Some("must-never-be-sent"),
    )
}

#[test]
fn session_parameters_match_installed_python_sdk_for_every_conversation_model() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/realtime-session.json")).unwrap();
    for case in cases {
        let provider = provider(Config::from_persisted(&case["config"]), "ws://127.0.0.1:1");
        assert_eq!(
            provider.session_update()["session"],
            case["session"],
            "{} polish={}",
            provider.model(),
            provider.config.get("enable_polish")
        );
    }
}

async fn send(socket: &mut WebSocketStream<TcpStream>, value: Value) -> Result<()> {
    socket.send(Message::Text(value.to_string().into())).await?;
    Ok(())
}
async fn next(socket: &mut WebSocketStream<TcpStream>) -> Result<Message> {
    loop {
        let message = socket
            .next()
            .await
            .ok_or_else(|| anyhow::anyhow!("fixture client closed"))??;
        if matches!(message, Message::Ping(_) | Message::Pong(_)) {
            socket.flush().await?;
            continue;
        }
        return Ok(message);
    }
}
async fn serve(
    listener: TcpListener,
    recognition: bool,
    wants_response: bool,
    broken: bool,
) -> Result<Vec<u8>> {
    let (stream, _) = listener.accept().await?;
    let auth = Arc::new(AtomicBool::new(false));
    let observed = auth.clone();
    let mut socket = accept_hdr_async(stream, move |request: &Request, response: Response| {
        observed.store(
            request.headers().contains_key("Authorization"),
            Ordering::Relaxed,
        );
        Ok(response)
    })
    .await?;
    ensure!(
        !auth.load(Ordering::Relaxed),
        "fixture received a credential"
    );
    let first: Value = serde_json::from_str(next(&mut socket).await?.to_text()?)?;
    let task = first["header"]["task_id"].clone();
    if recognition {
        ensure!(first["header"]["action"] == "run-task" && first["payload"]["task"] == "asr");
        ensure!(first["payload"]["parameters"]["sample_rate"] == 16000);
        send(
            &mut socket,
            json!({"header":{"event":"task-started","task_id":task},"payload":{}}),
        )
        .await?;
    } else {
        ensure!(first["type"] == "session.update");
        send(&mut socket, json!({"type":"session.updated"})).await?;
    }
    let mut pcm = Vec::new();
    loop {
        match next(&mut socket).await? {
            Message::Binary(block) => pcm.extend_from_slice(&block),
            Message::Text(raw) => {
                let event: Value = serde_json::from_str(&raw)?;
                if event["type"] == "input_audio_buffer.append" {
                    pcm.extend(STANDARD.decode(event["audio"].as_str().unwrap())?);
                } else if event["type"] == "input_audio_buffer.commit" {
                    send(&mut socket,json!({"type":"conversation.item.input_audio_transcription.completed","item_id":"input_1","transcript":"原始听写"})).await?;
                    if wants_response {
                        continue;
                    } else {
                        break;
                    }
                } else if event["type"] == "response.create" {
                    send(&mut socket, json!({"type":"response.created"})).await?;
                    send(
                        &mut socket,
                        json!({"type":"response.text.delta","delta":"完成"}),
                    )
                    .await?;
                    send(
                        &mut socket,
                        json!({"type":"response.text.done","text":"完成文本"}),
                    )
                    .await?;
                    send(&mut socket,json!({"type":"response.done","response":{"status":"completed","usage":{"input_tokens":10,"output_tokens":2}}})).await?;
                    break;
                } else if event["header"]["action"] == "finish-task" {
                    ensure!(event["header"]["task_id"] == task);
                    let final_event = json!({"header":{"event":"result-generated","task_id":task},"payload":{"output":{"sentence":{"sentence_id":1,"sentence_end":true,"text":"原始听写"}},"usage":{"duration":1}}});
                    send(&mut socket, final_event.clone()).await?;
                    send(&mut socket, final_event).await?; // duplicate delivery must not duplicate the final result
                    send(
                        &mut socket,
                        json!({"header":{"event":"task-finished","task_id":task},"payload":{}}),
                    )
                    .await?;
                    break;
                }
            }
            other => anyhow::bail!("unexpected client message: {other:?}"),
        }
        if broken && !pcm.is_empty() {
            send(&mut socket,json!({"type":"error","error":{"code":"fixture_disconnected","message":"must-not-echo-sensitive-provider-detail"}})).await?;
            return Ok(pcm);
        }
    }
    Ok(pcm)
}

#[tokio::test]
async fn all_realtime_models_exchange_real_pcm_and_complete_under_existing_polish_modes()
-> Result<()> {
    timeout(Duration::from_secs(20), async {
        for model in CONTRACT["all_asr_models"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|m| m["transport"] == "realtime_ws")
        {
            for enable in [false, true] {
                let listener = TcpListener::bind("127.0.0.1:0").await?;
                let endpoint = format!("ws://{}", listener.local_addr()?);
                let mut config = Config::default();
                config.apply_update("asr.model", &model["id"])?;
                config.apply_update("enable_polish", &json!(enable))?;
                let provider = provider(config, &endpoint);
                let recognition = model["protocol"] == "audio_recognition";
                let response = provider.wants_response();
                let server = tokio::spawn(serve(listener, recognition, response, false));
                let dir = tempfile::tempdir()?;
                let mut host = Host::new(RecordingStore::open(dir.path()).await?, None, None);
                let generation = host
                    .start_external(Source::Stream, provider.external()?)
                    .await?
                    .generation;
                while !host.status().asr_ready {
                    sleep(Duration::from_millis(2)).await;
                }
                let pcm = (0..3840).map(|n| (n % 251) as u8).collect::<Vec<_>>();
                for block in pcm.chunks(1280) {
                    host.append(generation, block.to_vec().into())?;
                }
                host.finish(generation)?;
                let status = wait_terminal(&host, Duration::from_secs(3)).await?;
                assert_eq!(
                    status.phase,
                    Phase::Completed,
                    "{} polish={enable}: {:?}",
                    model["id"],
                    status.error
                );
                assert_eq!(
                    status.transcript,
                    if response && !recognition {
                        "完成文本"
                    } else {
                        "原始听写"
                    }
                );
                assert_eq!(status.raw_transcript, "原始听写");
                let received = server.await??;
                assert_eq!(&received[..pcm.len()], pcm);
                if recognition {
                    assert_eq!(received.len(), 4480);
                    assert!(received[pcm.len()..].iter().all(|b| *b == 0));
                } else {
                    assert_eq!(received.len(), pcm.len());
                }
                let records = host.store().list().await?;
                assert_eq!(records[0].pcm_bytes, pcm.len() as u64);
                host.shutdown().await?;
            }
        }
        Ok::<_, anyhow::Error>(())
    })
    .await?
}

#[tokio::test]
async fn degraded_transport_retains_subsequent_audio_until_user_finishes() -> Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let endpoint = format!("ws://{}", listener.local_addr()?);
    let server = tokio::spawn(serve(listener, false, true, true));
    let dir = tempfile::tempdir()?;
    let mut host = Host::new(RecordingStore::open(dir.path()).await?, None, None);
    let generation = host
        .start_external(
            Source::Stream,
            provider(Config::default(), &endpoint).external()?,
        )
        .await?
        .generation;
    for _ in 0..3 {
        host.append(generation, vec![1; 1280].into())?;
    }
    server.await??;
    timeout(Duration::from_secs(2), async {
        while host.status().error.is_none() {
            sleep(Duration::from_millis(2)).await;
        }
    })
    .await?;
    assert_eq!(host.status().phase, Phase::Recording);
    for _ in 0..7 {
        host.append(generation, vec![2; 1280].into())?;
    }
    host.finish(generation)?;
    let status = wait_terminal(&host, Duration::from_secs(3)).await?;
    assert_eq!(status.phase, Phase::Failed);
    assert_eq!(status.pcm_bytes, 12800);
    assert!(!status.error.unwrap().contains("sensitive-provider-detail"));
    assert_eq!(host.store().list().await?[0].pcm_bytes, 12800);
    host.shutdown().await?;
    Ok(())
}
