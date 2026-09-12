// SPDX-License-Identifier: GPL-3.0-only
//! Full application protocol; the Python process is only a platform/UI client.
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::{
    io::{BufRead, Write},
    path::PathBuf,
    time::Duration,
};
use tokio::sync::mpsc;
use vocal_more_backend::{
    application::{Application, Options},
    provider::Endpoints,
};
use vocal_more_core::audio::NativeAudio;

fn options() -> Result<Options> {
    let mut args = std::env::args().skip(1);
    let mut import = None;
    let mut data = None;
    let mut native = None;
    let mut http = None;
    let mut ws = None;
    let mut test = false;
    while let Some(flag) = args.next() {
        if flag == "--version" {
            println!(
                "Vocal More Rust backend {}",
                vocal_more_backend::PRODUCT_VERSION
            );
            std::process::exit(0)
        }
        if flag == "--allow-test-sources" {
            test = true;
            continue;
        }
        let value = args
            .next()
            .with_context(|| format!("missing value for {flag}"))?;
        match flag.as_str() {
            "--data-dir" => data = Some(PathBuf::from(value)),
            "--native-library" => native = Some(PathBuf::from(value)),
            "--import-python" => import = Some(PathBuf::from(value)),
            "--fixture-http" => http = Some(value),
            "--fixture-websocket" => ws = Some(value),
            _ => bail!("unknown option: {flag}"),
        }
    }
    let mut options = Options::new(
        data.context("--data-dir is required; use a separate Rust backend data directory")?,
    );
    options.native = native.map(NativeAudio::load).transpose()?;
    options.allow_test_sources = test;
    options.import_from = import;
    if http.is_some() || ws.is_some() {
        ensure!(test, "fixture endpoints require --allow-test-sources");
        options.endpoints = Endpoints::loopback(
            &http.context("--fixture-http is required")?,
            &ws.context("--fixture-websocket is required")?,
        )?;
    } else {
        options.environment_key = std::env::var("DASHSCOPE_API_KEY").ok();
    }
    Ok(options)
}
fn error(id: Value, code: i32, message: &str) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}})
}

fn main() -> Result<()> {
    let options = options()?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(4)
        .thread_name("vocal-more-backend")
        .enable_all()
        .build()?;
    let result = runtime.block_on(run(options));
    runtime.shutdown_timeout(Duration::from_secs(1));
    result
}
async fn run(options: Options) -> Result<()> {
    let app = Application::open(options).await?;
    let mut events = app.subscribe();
    let (lines, mut input) = mpsc::channel(64);
    std::thread::Builder::new()
        .name("vocal-more-rpc-input".into())
        .spawn(move || {
            let stdin = std::io::stdin();
            let mut reader = stdin.lock();
            loop {
                let mut line = Vec::new();
                let mut oversized = false;
                let mut eof = false;
                loop {
                    let available = match reader.fill_buf() {
                        Ok(b) => b,
                        Err(_) => return,
                    };
                    if available.is_empty() {
                        eof = true;
                        break;
                    }
                    let count = available
                        .iter()
                        .position(|b| *b == b'\n')
                        .map_or(available.len(), |i| i + 1);
                    let ended = available[count - 1] == b'\n';
                    if line.len() + count <= 1024 * 1024 && !oversized {
                        line.extend_from_slice(&available[..count]);
                    } else {
                        oversized = true;
                        line.clear();
                    }
                    reader.consume(count);
                    if ended {
                        break;
                    }
                }
                if eof && line.is_empty() && !oversized {
                    break;
                }
                if lines
                    .blocking_send(if oversized {
                        Err("request line exceeds 1 MiB")
                    } else {
                        Ok(line)
                    })
                    .is_err()
                {
                    break;
                }
                if eof {
                    break;
                }
            }
        })?;
    let (output, mut writes) = mpsc::channel::<Value>(256);
    let (flushed, finished) = tokio::sync::oneshot::channel();
    std::thread::Builder::new()
        .name("vocal-more-rpc-output".into())
        .spawn(move || {
            let stdout = std::io::stdout();
            let mut writer = stdout.lock();
            let mut encoded = Vec::with_capacity(4096);
            while let Some(value) = writes.blocking_recv() {
                encoded.clear();
                if serde_json::to_writer(&mut encoded, &value).is_err() {
                    break;
                }
                encoded.push(b'\n');
                if writer
                    .write_all(&encoded)
                    .and_then(|_| writer.flush())
                    .is_err()
                {
                    break;
                }
            }
            let _ = flushed.send(());
        })?;
    loop {
        tokio::select! {
            line=input.recv()=>{
                let Some(line)=line else{break};
                let request=match line {
                    Err(message)=>{let _=output.send(error(Value::Null,-32600,message)).await;continue},
                    Ok(line)=>{if line.iter().all(u8::is_ascii_whitespace){continue}serde_json::from_slice::<Value>(&line)},
                };
                let request=match request{Ok(r)=>r,Err(_)=>{let _=output.send(error(Value::Null,-32700,"invalid JSON")).await;continue}};
                let id=request.get("id").cloned().unwrap_or(Value::Null);
                if !request.is_object() || request["jsonrpc"]!="2.0" || !request["method"].is_string() || !(id.is_null() || id.is_number() || id.is_string()) {
                    let _=output.send(error(Value::Null,-32600,"invalid JSON-RPC request")).await;continue
                }
                let method=request["method"].as_str().unwrap();let params=request.get("params").cloned().unwrap_or(json!({}));
                let result=app.call(method,params).await;
                if request.get("id").is_some() {
                    let reply=match result{Ok(value)=>json!({"jsonrpc":"2.0","id":id,"result":value}),Err(e)=>error(id,-32000,&e.to_string())};
                    if output.send(reply).await.is_err(){break}
                }
                if method=="shutdown"{break}
            },
            event=events.recv()=>{
                match event{Ok(event)=>{if output.send(event).await.is_err(){break}},
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(_))=>{if let Ok(snapshot)=app.call("snapshot",json!({})).await{let _=output.send(json!({"jsonrpc":"2.0","method":"resync","params":snapshot})).await;}},
                    Err(_)=>break}
            },
            _=tokio::signal::ctrl_c()=>break,
        }
    }
    let _ = app.call("shutdown", json!({})).await;
    // All complete responses already queued are flushed before process exit.
    drop(output);
    let _ = tokio::time::timeout(Duration::from_secs(2), finished).await;
    Ok(())
}
