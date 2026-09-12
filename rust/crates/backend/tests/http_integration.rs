// SPDX-License-Identifier: GPL-3.0-only
use anyhow::{Context, Result, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    io::Cursor,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    time::timeout,
};
use tokio_util::sync::CancellationToken;
use vocal_more_backend::{
    config::Config,
    http::SseDecoder,
    provider::{Endpoints, Provider},
    wave::{WaveChunks, wav_bytes},
};

async fn read_request(stream: &mut tokio::net::TcpStream) -> Result<(String, Value)> {
    let mut bytes = Vec::new();
    let header_end;
    loop {
        let mut buffer = [0; 4096];
        let n = stream.read(&mut buffer).await?;
        ensure!(n > 0, "client closed before headers");
        bytes.extend_from_slice(&buffer[..n]);
        ensure!(bytes.len() < 512 * 1024, "test request too large");
        if let Some(end) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
            header_end = end + 4;
            break;
        }
    }
    let headers = String::from_utf8(bytes[..header_end].to_vec())?;
    let length: usize = headers
        .lines()
        .find_map(|line| {
            line.to_lowercase()
                .strip_prefix("content-length:")
                .map(|s| s.trim().parse::<usize>())
        })
        .context("no content length")??;
    while bytes.len() < header_end + length {
        let mut buffer = [0; 4096];
        let n = stream.read(&mut buffer).await?;
        ensure!(n > 0, "client closed before body");
        bytes.extend_from_slice(&buffer[..n]);
    }
    ensure!(
        !headers.to_lowercase().contains("authorization:"),
        "fixture received an API key"
    );
    Ok((
        headers,
        serde_json::from_slice(&bytes[header_end..header_end + length])?,
    ))
}

async fn http_fixture(
    status: u16,
    body: String,
    sse: bool,
) -> Result<(String, tokio::task::JoinHandle<Result<(String, Value)>>)> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let endpoint = format!("http://{}", listener.local_addr()?);
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await?;
        let request = read_request(&mut stream).await?;
        stream.write_all(format!("HTTP/1.1 {status} Test\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",if sse {"text/event-stream"} else {"application/json"},body.len()).as_bytes()).await?;
        // Deliberately split inside JSON and UTF-8 code points.
        for chunk in body.as_bytes().chunks(7) {
            if let Err(error) = stream.write_all(chunk).await {
                // A rejected HTTP status need not consume a private error body.
                if status >= 400
                    && matches!(
                        error.kind(),
                        std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::ConnectionReset
                    )
                {
                    break;
                }
                return Err(error.into());
            }
            tokio::task::yield_now().await;
        }
        Ok(request)
    });
    Ok((endpoint, task))
}
fn provider(endpoint: &str) -> Provider {
    Provider::new(
        Config::default(),
        vec![],
        "",
        Endpoints::loopback(endpoint, "ws://127.0.0.1:1").unwrap(),
        Some("must-not-leave-process"),
    )
}
fn event(value: Value) -> String {
    format!("data: {value}\r\n\r\n")
}

#[tokio::test]
async fn streamed_polish_handles_fragmented_unicode_and_matches_dashscope_request() -> Result<()> {
    let body = event(
        json!({"output":{"choices":[{"message":{"content":[{"text":"整理"}]},"finish_reason":"null"}]}}),
    ) + &event(
        json!({"output":{"choices":[{"message":{"content":[{"text":"文本"}]},"finish_reason":"stop"}]},"usage":{"input_tokens":12,"output_tokens":3}}),
    );
    let (endpoint, server) = http_fixture(200, body, true).await?;
    let partials = Arc::new(Mutex::new(Vec::new()));
    let capture = partials.clone();
    let response = provider(&endpoint)
        .polish(
            "需要整理的口述",
            &CancellationToken::new(),
            Some(Arc::new(move |text| {
                capture.lock().unwrap().push(text.to_owned())
            })),
        )
        .await?;
    assert_eq!(response.text, "整理文本");
    assert_eq!(response.usage["input_tokens"], 12);
    assert_eq!(*partials.lock().unwrap(), ["整理", "整理文本"]);
    let (headers, request) = server.await??;
    assert!(headers.contains("/api/v1/services/aigc/multimodal-generation/generation"));
    assert!(headers.to_lowercase().contains("x-dashscope-sse: enable"));
    assert_eq!(request["model"], "qwen3.5-plus");
    assert_eq!(request["parameters"]["incremental_output"], true);
    assert!(request["parameters"].get("stream").is_none());
    assert_eq!(
        request["input"]["messages"][1]["content"][0]["text"],
        "需要整理的口述"
    );
    Ok(())
}

#[tokio::test]
async fn all_file_models_send_complete_wav_and_parse_their_response_envelopes() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("fixture.wav");
    let pcm = (0..6400).map(|n| (n % 251) as u8).collect::<Vec<_>>();
    std::fs::write(&path, wav_bytes(&pcm)?)?;
    for model in ["qwen3-asr-flash", "qwen3.5-omni-plus", "qwen3.5-omni-flash"] {
        let omni = model.contains("omni");
        let body = if omni {
            event(json!({"choices":[{"delta":{"content":"文件结果"},"finish_reason":"stop"}]}))
                + &event(json!({"choices":[],"usage":{"prompt_tokens":20,"completion_tokens":4}}))
                + "data: [DONE]\n\n"
        } else {
            json!({"output":{"choices":[{"message":{"content":[{"text":"文件结果"}]}}]},"usage":{"input_tokens":20,"output_tokens":4}}).to_string()
        };
        let (endpoint, server) = http_fixture(200, body, omni).await?;
        let result = provider(&endpoint)
            .transcribe_file(&path, model, &CancellationToken::new(), None)
            .await?;
        assert_eq!(result.text, "文件结果");
        assert_eq!(result.parts.len(), 1);
        let (_, request) = server.await??;
        assert_eq!(request["model"], model);
        let data = if omni {
            request["messages"][1]["content"][0]["input_audio"]["data"]
                .as_str()
                .unwrap()
        } else {
            request["input"]["messages"][0]["content"][0]["audio"]
                .as_str()
                .unwrap()
        };
        let wav = STANDARD.decode(data.split_once(',').unwrap().1)?;
        let mut reader = hound::WavReader::new(Cursor::new(wav))?;
        let decoded = reader
            .samples::<i16>()
            .collect::<Result<Vec<_>, _>>()?
            .iter()
            .flat_map(|s| s.to_le_bytes())
            .collect::<Vec<_>>();
        assert_eq!(decoded, pcm);
        assert_eq!(reader.spec().sample_rate, 16000);
    }
    Ok(())
}

#[tokio::test]
async fn failed_or_truncated_provider_output_is_never_a_success() -> Result<()> {
    for (status, body, sse) in [
        (401, "private-provider-detail".into(), false),
        (
            200,
            event(json!({"output":{"choices":[{"message":{"content":[{"text":"不完整"}]}}]}})),
            true,
        ),
        (200, "data: {\"choices\":".into(), true),
        (
            200,
            json!({"code":"QuotaExceeded","message":"private-provider-detail"}).to_string(),
            false,
        ),
    ] {
        let (endpoint, server) = http_fixture(status, body, sse).await?;
        let result = provider(&endpoint)
            .polish("内容", &CancellationToken::new(), None)
            .await;
        let error = result.unwrap_err().to_string();
        assert!(!error.contains("private-provider-detail"));
        server.await??;
    }
    Ok(())
}

#[tokio::test]
async fn cancellation_interrupts_stalled_http_and_releases_the_request() -> Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let endpoint = format!("http://{}", listener.local_addr()?);
    let (started_tx, started_rx) = tokio::sync::oneshot::channel();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await?;
        let _ = read_request(&mut stream).await?;
        let _ = started_tx.send(());
        let mut byte = [0];
        let count = timeout(Duration::from_secs(2), stream.read(&mut byte)).await??;
        ensure!(count == 0, "cancelled HTTP connection remained open");
        Ok::<_, anyhow::Error>(())
    });
    let cancel = CancellationToken::new();
    let request_cancel = cancel.clone();
    let worker = tokio::spawn(async move {
        provider(&endpoint)
            .polish("内容", &request_cancel, None)
            .await
    });
    started_rx.await?;
    cancel.cancel();
    assert!(timeout(Duration::from_millis(500), worker).await??.is_err());
    server.await??;
    Ok(())
}

#[test]
fn silence_chunking_preserves_every_frame_and_bounds_allocations() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("long.wav");
    let samples = (0..7 * 16000)
        .map(|i| {
            if (22000..26000).contains(&i) {
                0_i16
            } else {
                (i % 1000 + 1000) as i16
            }
        })
        .collect::<Vec<_>>();
    let pcm = samples
        .iter()
        .flat_map(|s| s.to_le_bytes())
        .collect::<Vec<_>>();
    std::fs::write(&path, wav_bytes(&pcm)?)?;
    let mut chunks = WaveChunks::with_duration(&path, 2)?;
    let mut joined = Vec::new();
    let mut count = 0;
    while let Some(chunk) = chunks.next_chunk()? {
        assert!(chunk.len() <= 64000);
        joined.extend(chunk);
        count += 1;
    }
    assert_eq!(joined, pcm);
    assert!(count >= 4);
    Ok(())
}

#[test]
fn sse_decoder_handles_every_byte_boundary_and_rejects_unbounded_events() -> Result<()> {
    let input = event(json!({"text":"中文🙂"})) + "data: [DONE]\n\n";
    for size in 1..input.len() {
        let mut parser = SseDecoder::default();
        let mut output = Vec::new();
        for bytes in input.as_bytes().chunks(size) {
            output.extend(parser.push(bytes)?);
        }
        parser.finish()?;
        assert_eq!(output, vec![Some(json!({"text":"中文🙂"})), None]);
    }
    assert!(
        SseDecoder::default()
            .push(&vec![b'x'; 1024 * 1024 + 1])
            .is_err()
    );
    Ok(())
}
