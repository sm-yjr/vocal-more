// SPDX-License-Identifier: GPL-3.0-only
use anyhow::{Context, Result, bail};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    io::{BufRead, Write},
    path::PathBuf,
};
use tokio::sync::mpsc;
use vocal_more_core::{
    audio::NativeAudio,
    recording::RecordingStore,
    runtime::{Host, StartRequest},
};

const MAX_REQUEST_BYTES: usize = 128 * 1024;

fn main() {
    if let Err(error) = run() {
        eprintln!("vocal-more-host: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let mut data_dir = None;
    let mut native_library = None;
    let mut api_key_env = "DASHSCOPE_API_KEY".to_string();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--version" => {
                println!("vocal-more-host {}", env!("CARGO_PKG_VERSION"));
                return Ok(());
            }
            "--help" | "-h" => {
                println!(
                    "vocal-more-host --data-dir PATH [--native-library PATH] [--api-key-env NAME]\n\nIndependent Rust JSON-RPC/NDJSON service on stdin/stdout.\nMethods: initialize, status, start, append, finish, cancel, recordings, shutdown.\nUse a separate, empty directory. No Python process or UI is launched."
                );
                return Ok(());
            }
            "--data-dir" => {
                data_dir = Some(PathBuf::from(
                    args.next().context("--data-dir requires a path")?,
                ))
            }
            "--native-library" => {
                native_library = Some(PathBuf::from(
                    args.next().context("--native-library requires a path")?,
                ))
            }
            "--api-key-env" => {
                api_key_env = args
                    .next()
                    .context("--api-key-env requires a variable name")?
            }
            _ => bail!("unknown argument: {arg}"),
        }
    }
    let data_dir =
        data_dir.context("--data-dir is required; use a separate directory from the Python app")?;
    let native = native_library.map(NativeAudio::load).transpose()?;
    let api_key = std::env::var(api_key_env).ok();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(2)
        .thread_name("vocal-more-runtime")
        .enable_all()
        .build()?;
    let result = runtime.block_on(async {
        let store = RecordingStore::open(data_dir).await?;
        let mut host = Host::new(store, native, api_key);
        let (tx, mut rx) = mpsc::channel(8);
        // A plain reader thread can be abandoned on shutdown without Tokio's
        // blocking-stdin shutdown hang. It owns no session, file or device.
        std::thread::Builder::new()
            .name("vocal-more-rpc-input".into())
            .spawn(move || {
                let stdin = std::io::stdin();
                let mut reader = stdin.lock();
                loop {
                    match read_bounded_line(&mut reader) {
                        Ok(Some(line)) => {
                            if tx.blocking_send(line).is_err() {
                                break;
                            }
                        }
                        Ok(None) => break,
                        Err(_) => {
                            let _ = tx.blocking_send(Err("stdin read failed".into()));
                            break;
                        }
                    }
                }
            })?;
        let result = loop {
            let next = tokio::select! {
                value = rx.recv() => value,
                _ = tokio::signal::ctrl_c() => None,
            };
            let Some(line) = next else { break Ok(()) };
            let mut quit = false;
            let response = match line {
                Err(message) => Some(error(Value::Null, -32600, &message)),
                Ok(line) => {
                    if line.iter().all(u8::is_ascii_whitespace) {
                        continue;
                    }
                    match serde_json::from_slice::<Value>(&line) {
                        Err(_) => Some(error(Value::Null, -32700, "invalid JSON")),
                        Ok(request) => {
                            let id = request.get("id").cloned().unwrap_or(Value::Null);
                            let valid_id = id.is_null() || id.is_string() || id.is_number();
                            if !request.is_object()
                                || request["jsonrpc"] != "2.0"
                                || !request["method"].is_string()
                                || !valid_id
                            {
                                Some(error(Value::Null, -32600, "invalid JSON-RPC request"))
                            } else {
                                let notification = request.get("id").is_none();
                                let method = request["method"].as_str().unwrap();
                                let params =
                                    request.get("params").cloned().unwrap_or_else(|| json!({}));
                                let result = dispatch(&mut host, method, params).await;
                                quit = method == "shutdown" && result.is_ok();
                                if notification {
                                    None
                                } else {
                                    Some(match result {
                                        Ok(value) => {
                                            json!({"jsonrpc":"2.0","id":id,"result":value})
                                        }
                                        Err(error_value) => {
                                            error(id, -32000, &error_value.to_string())
                                        }
                                    })
                                }
                            }
                        }
                    }
                }
            };
            if let Some(response) = response {
                let stdout = std::io::stdout();
                let mut output = stdout.lock();
                if writeln!(output, "{response}")
                    .and_then(|_| output.flush())
                    .is_err()
                {
                    break Ok(());
                }
            }
            if quit {
                break Ok(());
            }
        };
        host.shutdown().await?;
        result
    });
    runtime.shutdown_timeout(std::time::Duration::from_secs(1));
    result
}

async fn dispatch(host: &mut Host, method: &str, params: Value) -> Result<Value> {
    anyhow::ensure!(params.is_object(), "params must be an object");
    let generation = || {
        params["generation"]
            .as_u64()
            .context("generation is required")
    };
    match method {
        "initialize" => Ok(
            json!({"version":env!("CARGO_PKG_VERSION"),"runtime":"rust","python_host":false,
            "protocol_version":1,"sample_rate":vocal_more_core::SAMPLE_RATE,"channels":1,"sample_format":"pcm16le",
            "native_loaded":host.native_loaded(),"capabilities":["stream_pcm","wav_replay","file_recording","qwen_omni_realtime","cancel","crash_recovery"],
            "status":host.status()}),
        ),
        "status" => Ok(serde_json::to_value(host.status())?),
        "start" => {
            let request: StartRequest =
                serde_json::from_value(params).context("invalid start parameters")?;
            Ok(serde_json::to_value(host.start(request).await?)?)
        }
        "append" => {
            let encoded = params["pcm_base64"]
                .as_str()
                .context("pcm_base64 is required")?;
            anyhow::ensure!(
                encoded.len() <= 1712,
                "encoded PCM block exceeds size limit"
            );
            let pcm = STANDARD.decode(encoded).context("invalid PCM base64")?;
            host.append(generation()?, pcm.into())?;
            Ok(json!({"accepted":true}))
        }
        "finish" => {
            host.finish(generation()?)?;
            Ok(json!({"accepted":true}))
        }
        "cancel" => {
            host.cancel(generation()?)?;
            Ok(json!({"accepted":true}))
        }
        "recordings" => Ok(serde_json::to_value(host.store().list().await?)?),
        "shutdown" => {
            host.shutdown().await?;
            Ok(json!({"closed":true}))
        }
        _ => bail!("unknown method: {method}"),
    }
}

fn error(id: Value, code: i32, message: &str) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}})
}

fn read_bounded_line(
    reader: &mut impl BufRead,
) -> std::io::Result<Option<Result<Vec<u8>, String>>> {
    let mut line = Vec::new();
    let mut oversized = false;
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return if line.is_empty() && !oversized {
                Ok(None)
            } else if oversized {
                Ok(Some(Err("request exceeds 128 KiB".into())))
            } else {
                Ok(Some(Ok(line)))
            };
        }
        let end = available.iter().position(|byte| *byte == b'\n');
        let count = end.map_or(available.len(), |at| at + 1);
        if !oversized {
            if line.len() + count > MAX_REQUEST_BYTES {
                oversized = true;
                line.clear();
            } else {
                line.extend_from_slice(&available[..count]);
            }
        }
        reader.consume(count);
        if end.is_some() {
            return Ok(Some(if oversized {
                Err("request exceeds 128 KiB".into())
            } else {
                Ok(line)
            }));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn oversized_request_is_drained_without_eating_the_next_line() -> Result<()> {
        let mut bytes = vec![b'x'; MAX_REQUEST_BYTES + 1];
        bytes.extend_from_slice(b"\n{}\n");
        let mut reader = std::io::BufReader::new(bytes.as_slice());
        assert!(read_bounded_line(&mut reader)?.unwrap().is_err());
        assert_eq!(read_bounded_line(&mut reader)?.unwrap().unwrap(), b"{}\n");
        assert!(read_bounded_line(&mut reader)?.is_none());
        Ok(())
    }
}
