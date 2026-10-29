// SPDX-License-Identifier: GPL-3.0-only
//! Real OS capture; only the recognition endpoint is a local, credential-free fixture.
use super::screen::{Key, MAX_JPEG_BYTES, ScreenWorker};
use anyhow::{Context, Result, ensure};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::{net::TcpListener, time::timeout};
use tokio_tungstenite::{
    accept_hdr_async,
    tungstenite::{
        Message,
        handshake::server::{Callback, ErrorResponse, Request, Response},
    },
};
use vocal_more_backend::{
    application::{Application, Options},
    provider::Endpoints,
};

struct NoCredentials;
impl Callback for NoCredentials {
    fn on_request(
        self,
        request: &Request,
        response: Response,
    ) -> std::result::Result<Response, ErrorResponse> {
        assert!(
            !request.headers().contains_key("Authorization"),
            "fixture received a credential"
        );
        Ok(response)
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "run scripts/smoke_linux_screen.py; opens the real portal chooser"]
async fn linux_real_portal_captures_selected_window() -> Result<()> {
    ensure!(
        std::env::var("VOCAL_MORE_PRIVATE_SCREEN_TEST").as_deref() == Ok("1"),
        "use the private runner"
    );
    let output = std::path::PathBuf::from(
        std::env::var_os("VOCAL_MORE_SCREEN_TEST_OUTPUT").context("missing fixture output")?,
    );
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let endpoint = format!("ws://{}", listener.local_addr()?);
    let received = Arc::new(AtomicUsize::new(0));
    let count = received.clone();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await?;
        let mut socket = accept_hdr_async(stream, NoCredentials).await?;
        let update: Value = serde_json::from_str(
            socket
                .next()
                .await
                .context("missing session update")??
                .to_text()?,
        )?;
        ensure!(
            update["type"] == "session.update"
                && update["session"]["video"]["input"]["representation_compact"] == "normal",
            "missing visual session configuration"
        );
        socket
            .send(Message::Text(
                json!({"type":"session.updated"}).to_string().into(),
            ))
            .await?;
        let mut audio = false;
        let mut images = Vec::new();
        while let Some(message) = socket.next().await {
            let message = message?;
            if message.is_ping() {
                socket.flush().await?;
                continue;
            }
            let Ok(text) = message.to_text() else {
                continue;
            };
            let event: Value = serde_json::from_str(text)?;
            match event["type"].as_str().unwrap_or("") {
                "input_audio_buffer.append"=>audio=true,
                "input_image_buffer.append"=>{
                    ensure!(audio,"image was sent before audio");
                    let bytes=STANDARD.decode(event["image"].as_str().context("missing image")?)?;
                    ensure!(bytes.len()<=MAX_JPEG_BYTES && bytes.starts_with(&[255,216,255]),"invalid wire JPEG");
                    images.push(bytes);count.store(images.len(),Ordering::Release);
                }
                "input_audio_buffer.commit"=>socket.send(Message::Text(json!({"type":"conversation.item.input_audio_transcription.completed","item_id":"fixture","transcript":"本机测试音频"}).to_string().into())).await?,
                "response.create"=>{
                    socket.send(Message::Text(json!({"type":"response.done","response":{"status":"completed","output":[{"content":[{"type":"text","text":"屏幕上下文本机验收通过。"}]}],"usage":{"input_tokens":20,"output_tokens":8}}}).to_string().into())).await?;
                    break;
                }
                _=>{},
            }
        }
        Ok::<_, anyhow::Error>(images)
    });
    let profile = tempfile::tempdir()?;
    let mut options = Options::new(profile.path().into());
    options.allow_test_sources = true;
    options.endpoints = Endpoints::loopback("http://127.0.0.1:1", &endpoint)?;
    let app = Application::open(options).await?;
    for (key, value) in [
        ("enable_polish", json!(false)),
        ("dictionary_learning.enabled", json!(false)),
        ("auto_paste", json!(false)),
    ] {
        app.call("set_config", json!({"key":key,"value":value}))
            .await?;
    }
    let mut events = app.subscribe();
    let started = app
        .call(
            "start",
            json!({"source":{"kind":"stream"},"screen_context":true}),
        )
        .await?;
    let generation = started["generation"]
        .as_u64()
        .context("missing generation")?;
    let worker = ScreenWorker::new()?;
    let result=async {
        let key=Key {request:1,epoch:1};worker.start(key)?;
        let deadline=Instant::now()+Duration::from_secs(30);
        let mut frames=Vec::new();let mut dimensions=Vec::new();let mut arrivals=Vec::new();
        while Instant::now()<deadline && frames.len()<3 {
            app.call("append",json!({"generation":generation,"pcm_base64":STANDARD.encode([1u8;1280])})).await?;
            while events.try_recv().is_ok() {}
            if let Some(frame)=worker.poll() {
                ensure!(frame.key==key,"stale frame escaped capture session");
                let jpeg=frame.jpeg?;let image=image::load_from_memory(&jpeg)?;
                ensure!(image.width()<=1280 && image.height()<=720,"oversized frame");
                let rgb=image.to_rgb8();let color=rgb.get_pixel(rgb.width()/2,rgb.height()*85/100).0;
                if frames.is_empty() {ensure!(color[2]>80 && color[0]<80,"selected window was padded/cropped incorrectly: {color:?}");}
                if frames.len()==2 {ensure!(color[0]>100 && color[2]<80,"changed screen did not reach the capture lane: {color:?}");}
                dimensions.push((image.width(),image.height()));arrivals.push(Instant::now());
                app.call("append_screen_frame",json!({"generation":generation,"jpeg_base64":STANDARD.encode(&jpeg)})).await?;
                std::fs::write(output.join(format!("frame-{}.jpg",frames.len())),&jpeg)?;
                frames.push(jpeg);
                if frames.len()==1 {std::fs::write(output.join("flip"),"resize and change color")?;}
            }
            tokio::time::sleep(Duration::from_millis(40)).await;
        }
        ensure!(frames.len()==3,"no periodic frames from the selected window");
        ensure!(dimensions[0]!=dimensions[2],"window resize did not renegotiate screen dimensions");
        let delivery=Instant::now()+Duration::from_secs(2);
        while received.load(Ordering::Acquire)<3 && Instant::now()<delivery {tokio::time::sleep(Duration::from_millis(20)).await;}
        ensure!(received.load(Ordering::Acquire)==3,"recognition transport did not receive all screen frames");
        ensure!(app.call("append_screen_frame",json!({"generation":generation+1,"jpeg_base64":STANDARD.encode(&frames[0])})).await.is_err(),"stale generation accepted");
        app.call("finish",json!({})).await?;
        let final_result=timeout(Duration::from_secs(5),async {
            loop {let event=events.recv().await?;if event["method"]=="final_result" {return Ok::<_,anyhow::Error>(event["params"].clone());}}
        }).await??;
        ensure!(final_result["text"]=="屏幕上下文本机验收通过。","recognition result was lost");
        let observed=timeout(Duration::from_secs(3),server).await???;
        ensure!(observed==frames,"JPEG bytes changed between capture and recognition transport");
        let gaps:Vec<_>=arrivals.windows(2).map(|pair|pair[1].duration_since(pair[0]).as_millis() as u64).collect();
        Ok::<_,anyhow::Error>(json!({"ok":true,"real_portal":true,"real_pipewire":true,"frames":frames.len(),"recognition_received":observed.len(),
            "jpeg_bytes":frames.iter().map(Vec::len).collect::<Vec<_>>(),"dimensions":dimensions,"interval_ms":gaps,
            "window_resize":true,"stale_generation_rejected":true,"recognition":"loopback_fixture","cloud_tested":false}))
    }.await;
    worker.close();
    let until = Instant::now() + Duration::from_secs(7);
    while !worker.finished() && Instant::now() < until {
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    app.call("shutdown", json!({})).await?;
    ensure!(worker.finished(), "capture shutdown leaked");
    let mut report = result?;
    // A fresh worker carries no restore token: exercise the actual denial UI.
    std::fs::write(output.join("cancel-request"), "deny sharing")?;
    let denied = ScreenWorker::new()?;
    let key = Key {
        request: 2,
        epoch: 2,
    };
    denied.start(key)?;
    let rejection = timeout(Duration::from_secs(15), async {
        loop {
            if let Some(frame) = denied.poll() {
                return frame;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await;
    denied.close();
    let until = Instant::now() + Duration::from_secs(7);
    while !denied.finished() && Instant::now() < until {
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    ensure!(denied.finished(), "denied capture shutdown leaked");
    let rejection = rejection?;
    ensure!(
        rejection.key == key && rejection.jpeg.is_err(),
        "denied chooser produced pixels"
    );
    report["permission_denial"] = json!(true);
    std::fs::write(
        output.join("screen-capture.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    Ok(())
}
