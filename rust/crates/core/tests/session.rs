// SPDX-License-Identifier: GPL-3.0-only
use anyhow::{Context, Result};
use base64::{Engine, engine::general_purpose::STANDARD};
use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::time::Duration;
use tokio::{net::TcpListener, time::timeout};
use tokio_tungstenite::{
    accept_hdr_async,
    tungstenite::{
        Message,
        handshake::server::{Request, Response},
    },
};
use vocal_more_core::{
    AUDIO_QUEUE_BLOCKS, BLOCK_BYTES,
    audio::Source,
    protocol::RealtimeConfig,
    recording::RecordingStore,
    runtime::{Host, Phase, StartRequest, wait_terminal},
};

fn asr(endpoint: String) -> RealtimeConfig {
    RealtimeConfig {
        endpoint,
        model: "qwen3.5-omni-plus-realtime".into(),
        instructions: "只输出听写文字".into(),
    }
}

fn write_wav(path: &std::path::Path, samples: &[i16], sample_rate: u32) -> Result<()> {
    let mut writer = hound::WavWriter::create(
        path,
        hound::WavSpec {
            channels: 1,
            sample_rate,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )?;
    for sample in samples {
        writer.write_sample(*sample)?;
    }
    writer.finalize()?;
    Ok(())
}

#[tokio::test]
async fn real_websocket_preserves_audio_tail_and_commits_only_final_text() -> Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let endpoint = format!("ws://{}/realtime", listener.local_addr()?);
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await?;
        let mut socket = accept_hdr_async(stream, |request: &Request, response: Response| {
            assert!(!request.headers().contains_key("Authorization"));
            assert_eq!(
                request.uri().query(),
                Some("model=qwen3.5-omni-plus-realtime")
            );
            Ok(response)
        })
        .await?;
        let update: Value =
            serde_json::from_str(socket.next().await.context("missing update")??.to_text()?)?;
        assert_eq!(update["session"]["modalities"], json!(["text"]));
        assert_eq!(
            update["session"]["audio"]["input"]["format"]["sample_rate"],
            16000
        );
        socket
            .send(Message::Text(
                json!({"type":"session.updated"}).to_string().into(),
            ))
            .await?;
        let mut audio = Vec::new();
        let mut sizes = Vec::new();
        let mut committed = false;
        while let Some(message) = socket.next().await {
            let message = message?;
            if !message.is_text() {
                continue;
            }
            let event: Value = serde_json::from_str(message.to_text()?)?;
            match event["type"].as_str().context("event type")? {
                "input_audio_buffer.append" => {
                    assert!(!committed);
                    let chunk = STANDARD.decode(event["audio"].as_str().context("audio")?)?;
                    sizes.push(chunk.len());
                    audio.extend(chunk);
                }
                "input_audio_buffer.commit" => {
                    assert!(!committed);
                    committed = true;
                }
                "response.create" => {
                    assert!(committed);
                    for value in [
                        json!({"type":"response.text.delta","delta":"partial"}),
                        json!({"type":"response.text.done","text":"中文 final ✓"}),
                        json!({"type":"response.done","response":{"status":"completed"}}),
                    ] {
                        socket.send(Message::Text(value.to_string().into())).await?;
                    }
                    break;
                }
                other => anyhow::bail!("unexpected client event: {other}"),
            }
        }
        // The host must close a consumed session instead of retaining context.
        let close = timeout(Duration::from_secs(2), socket.next()).await?;
        assert!(matches!(close, Some(Ok(Message::Close(_)))));
        Ok::<_, anyhow::Error>((audio, sizes))
    });
    let temp = tempfile::tempdir()?;
    let samples: Vec<i16> = (0..5001).map(|n| (n * 31) as i16).collect();
    let input = temp.path().join("input.wav");
    write_wav(&input, &samples, 16000)?;
    let mut host = Host::new(
        RecordingStore::open(temp.path().join("recordings")).await?,
        None,
        Some("secret-must-not-reach-loopback".into()),
    );
    host.start(StartRequest {
        source: Source::Wav {
            path: input,
            paced: false,
        },
        asr: Some(asr(endpoint)),
    })
    .await?;
    let state = wait_terminal(&host, Duration::from_secs(5)).await?;
    assert_eq!(state.phase, Phase::Completed, "{state:?}");
    assert_eq!(state.transcript, "中文 final ✓");
    assert_eq!(state.pcm_bytes, 10002);
    let (audio, sizes) = server.await??;
    assert_eq!(sizes, [3200, 3200, 3200, 402]);
    assert_eq!(
        audio,
        samples
            .iter()
            .flat_map(|s| s.to_le_bytes())
            .collect::<Vec<_>>()
    );
    let record = host.store().list().await?.remove(0);
    assert_eq!(record.transcript, state.transcript);
    assert_eq!(
        hound::WavReader::open(host.store().directory().join(record.filename))?
            .into_samples::<i16>()
            .collect::<Result<Vec<_>, _>>()?,
        samples
    );
    host.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn bounded_admission_cancel_retains_every_accepted_block_and_rejects_old_generation()
-> Result<()> {
    let temp = tempfile::tempdir()?;
    let mut host = Host::new(RecordingStore::open(temp.path()).await?, None, None);
    let first = host.start(StartRequest::default()).await?.generation;
    // This current-thread test admits without yielding, deterministically filling the queue.
    for _ in 0..AUDIO_QUEUE_BLOCKS {
        host.append(first, Bytes::from(vec![7; BLOCK_BYTES]))?;
    }
    assert!(host.append(first, Bytes::from_static(&[0, 0])).is_err());
    assert!(host.finish(first).is_err());
    host.cancel(first)?;
    let state = wait_terminal(&host, Duration::from_secs(3)).await?;
    assert_eq!(state.phase, Phase::Cancelled);
    assert_eq!(state.pcm_bytes, (AUDIO_QUEUE_BLOCKS * BLOCK_BYTES) as u64);
    assert_eq!(state.input_queue_high_watermark, AUDIO_QUEUE_BLOCKS);
    assert!(state.transcript.is_empty());
    let second = host.start(StartRequest::default()).await?.generation;
    assert!(host.append(first, Bytes::from_static(&[0, 0])).is_err());
    assert!(host.cancel(first).is_err());
    host.append(second, Bytes::from_static(&[2, 0]))?;
    host.finish(second)?;
    assert_eq!(
        wait_terminal(&host, Duration::from_secs(3)).await?.phase,
        Phase::Completed
    );
    host.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn cancel_interrupts_blocked_handshake_and_shutdown_finishes_files() -> Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let endpoint = format!("ws://{}/realtime", listener.local_addr()?);
    let temp = tempfile::tempdir()?;
    let store_dir = temp.path().join("store");
    let mut host = Host::new(RecordingStore::open(&store_dir).await?, None, None);
    let generation = host
        .start(StartRequest {
            source: Source::Stream,
            asr: Some(asr(endpoint)),
        })
        .await?
        .generation;
    let (_blocked_stream, _) = timeout(Duration::from_secs(2), listener.accept()).await??;
    host.append(generation, Bytes::from(vec![3; BLOCK_BYTES]))?;
    host.cancel(generation)?;
    let state = wait_terminal(&host, Duration::from_secs(1)).await?;
    assert_eq!(state.phase, Phase::Cancelled);
    assert_eq!(state.pcm_bytes, BLOCK_BYTES as u64);
    let generation = host.start(StartRequest::default()).await?.generation;
    host.append(generation, Bytes::from_static(&[4, 0]))?;
    host.shutdown().await?;
    drop(host);
    let records = RecordingStore::open(&store_dir).await?.list().await?;
    assert_eq!(records.len(), 2);
    assert!(
        records
            .iter()
            .all(|r| r.status == "cancelled" && r.transcript.is_empty())
    );
    Ok(())
}

#[tokio::test]
async fn provider_error_keeps_recording_but_never_publishes_partial_text() -> Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let endpoint = format!("ws://{}/realtime", listener.local_addr()?);
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await?;
        let mut socket = tokio_tungstenite::accept_async(stream).await?;
        socket.next().await.context("session update")??;
        socket.send(Message::Text(json!({"type":"error","error":{"code":"quota_exceeded","message":"sensitive-provider-message"}}).to_string().into())).await?;
        Ok::<_, anyhow::Error>(())
    });
    let temp = tempfile::tempdir()?;
    let mut host = Host::new(RecordingStore::open(temp.path()).await?, None, None);
    let generation = host
        .start(StartRequest {
            source: Source::Stream,
            asr: Some(asr(endpoint)),
        })
        .await?
        .generation;
    host.append(generation, Bytes::from(vec![1; BLOCK_BYTES]))?;
    let state = wait_terminal(&host, Duration::from_secs(3)).await?;
    assert_eq!(state.phase, Phase::Failed);
    assert_eq!(
        state.error.as_deref(),
        Some("provider error: quota_exceeded")
    );
    assert!(state.transcript.is_empty());
    assert_eq!(state.pcm_bytes, BLOCK_BYTES as u64);
    server.await??;
    host.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn repeated_sessions_and_unsupported_wav_fail_without_poisoning_host() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let bad = temp.path().join("bad.wav");
    write_wav(&bad, &[1, 2], 48000)?;
    let mut host = Host::new(
        RecordingStore::open(temp.path().join("store")).await?,
        None,
        None,
    );
    host.start(StartRequest {
        source: Source::Wav {
            path: bad,
            paced: false,
        },
        asr: None,
    })
    .await?;
    assert_eq!(
        wait_terminal(&host, Duration::from_secs(2)).await?.phase,
        Phase::Failed
    );
    for _ in 0..50 {
        let generation = host.start(StartRequest::default()).await?.generation;
        host.append(generation, Bytes::from_static(&[3, 0, 4, 0]))?;
        host.finish(generation)?;
        let state = wait_terminal(&host, Duration::from_secs(2)).await?;
        assert_eq!(state.phase, Phase::Completed);
        assert_eq!(state.pcm_bytes, 4);
    }
    assert_eq!(host.store().list().await?.len(), 51);
    host.shutdown().await?;
    Ok(())
}
