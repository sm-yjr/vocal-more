// SPDX-License-Identifier: GPL-3.0-only
//! Explicit opt-in live service acceptance using synthetic OS fixture content.
use anyhow::{Context, Result, ensure};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{path::PathBuf, time::Duration};
use tokio::time::{sleep, timeout};
use vocal_more_backend::application::{Application, Options};

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "billable live DashScope requests; run smoke_linux_cloud.py explicitly"]
async fn linux_live_cloud_screen_and_learning() -> Result<()> {
    ensure!(
        std::env::var("VOCAL_MORE_LIVE_CLOUD_TEST").as_deref() == Ok("1"),
        "explicit live runner required"
    );
    let output =
        PathBuf::from(std::env::var_os("VOCAL_MORE_CLOUD_OUTPUT").context("output required")?);
    let file = std::fs::read_to_string(
        std::env::var_os("VOCAL_MORE_CLOUD_KEY_FILE").context("key file required")?,
    )?;
    // The runner accepts a raw key or conventional dotenv assignment. Never
    // print key material or inject it into argv. The temporary profile is scrubbed
    // before shutdown and deleted by its owner, including on early failure.
    let keys: Vec<_> = file
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            let value = if let Some((name, value)) = line.split_once('=') {
                if !matches!(
                    name.trim().trim_start_matches("export "),
                    "DASHSCOPE_API_KEY" | "API_KEY"
                ) {
                    return None;
                }
                value.trim().trim_matches(['\'', '"'])
            } else {
                line
            };
            (value.starts_with("sk-")
                && value.len() > 16
                && !value.chars().any(char::is_whitespace))
            .then_some(value.to_owned())
        })
        .collect();
    ensure!(keys.len() == 1, "expected exactly one DashScope credential");
    let key = &keys[0];
    let temporary = tempfile::tempdir()?;
    let mut options = Options::new(temporary.path().into());
    options.allow_test_sources = true;
    options.environment_key = Some(key.clone());
    let app = Application::open(options).await?;
    let outcome=async {
        for (name,value) in [("enable_polish",json!(false)),("dictionary_learning.enabled",json!(false)),("auto_paste",json!(false))] {
            app.call("set_config",json!({"key":name,"value":value})).await?;
        }
        let mut events=app.subscribe();
        let started=app.call("start",json!({"source":{"kind":"stream"},"screen_context":true})).await?;
        let generation=started["generation"].as_u64().context("missing generation")?;
        let frames=PathBuf::from(std::env::var_os("VOCAL_MORE_CLOUD_FRAMES").context("frames required")?);
        let pcm=std::fs::read(output.join("speech.pcm"))?;
        ensure!(pcm.len()>32000 && pcm.len()<32*32000,"invalid synthetic speech duration");
        for (index,chunk) in pcm.chunks(1280).enumerate() {
            app.call("append",json!({"generation":generation,"pcm_base64":STANDARD.encode(chunk)})).await?;
            if [0,50,100].contains(&index) {
                let number=index/50;
                let jpeg=std::fs::read(frames.join(format!("frame-{number}.jpg")))?;
                app.call("append_screen_frame",json!({"generation":generation,"jpeg_base64":STANDARD.encode(&jpeg)})).await?;
            }
            sleep(Duration::from_millis(40)).await;
        }
        app.call("finish",json!({})).await?;
        let result=timeout(Duration::from_secs(60),async {
            loop {
                let event=events.recv().await?;
                match event["method"].as_str() {
                    Some("final_result")=>return Ok::<_,anyhow::Error>(event["params"].clone()),
                    Some("error")=>anyhow::bail!("cloud recognition: {}",event["params"]["message"].as_str().unwrap_or("unknown error")),
                    _=>{},
                }
            }
        }).await??;
        let text=result["text"].as_str().unwrap_or("");
        ensure!(!text.trim().is_empty(),"cloud returned empty transcription");
        let visual_code = visual_probe(key, &frames, &output).await?;
        // Use correction evidence collected from the actual private Wayland
        // editor, then run the production classifier and learning actor.
        let evidence_path=PathBuf::from(std::env::var_os("VOCAL_MORE_CLOUD_EVIDENCE").context("evidence required")?);
        let evidence:Value=serde_json::from_slice(&std::fs::read(evidence_path)?)?;
        ensure!(evidence["ok"]==true && evidence["session"]=="Wayland","real Wayland evidence required");
        app.call("set_config",json!({"key":"dictionary_learning.enabled","value":true})).await?;
        app.call("submit_correction",json!({"evidence":evidence["evidence"]})).await?;
        let learned=timeout(Duration::from_secs(40),async {
            loop {
                let jobs=app.call("get_dictionary_learning",json!({})).await?;
                if let Some(job)=jobs.as_array().and_then(|items|items.first())
                    && let Some("applied"|"review"|"ignored"|"failed")=job["status"].as_str() {
                    return Ok::<_,anyhow::Error>(job.clone());
                }
                sleep(Duration::from_millis(100)).await;
            }
        }).await??;
        ensure!(learned["status"]=="applied", "live classifier did not apply synthetic correction: {}",serde_json::to_string(&learned)?);
        let dictionary=app.call("get_dictionary",json!({})).await?;
        ensure!(dictionary.as_array().is_some_and(|entries|entries.iter().any(|entry|entry["term"]=="GitHub")),"learned term not in dictionary");
        let undone=app.call("undo_dictionary_learning",json!({"id":learned["id"]})).await?;
        ensure!(undone["ok"]==true,"live learning undo failed");
        ensure!(app.call("get_dictionary",json!({})).await?.as_array().is_some_and(Vec::is_empty),"undo left synthetic term");
        Ok::<_,anyhow::Error>(json!({"ok":true,"cloud_tested":true,"recognition_model":"qwen3.8-omni-flash-realtime","frames":3,"text":text,"raw_text":result["raw_text"],"learning_model":"qwen3.7-plus","learning_status":learned["status"],"learning_term":learned["term"],"learning_reason":learned["reason_code"],"visual_probe_code":visual_code,"visual_probe_prompt":"test-only question answering; production remains dictation","proper_name_matched":text.contains("LumaHarbor"),"learning_undo":true,"synthetic_content_only":true}))
    }.await;
    // Environment credentials initialize the backend live config. Settings
    // saves can include that value; remove it from this disposable profile.
    let scrubbed = app
        .call("set_config", json!({"key":"api_key","value":""}))
        .await;
    let _ = app.call("shutdown", json!({})).await;
    scrubbed?;
    let report = match outcome {
        Ok(report) => report,
        Err(error) => {
            let safe = format!("{error:#}").replace(key, "[REDACTED]");
            std::fs::write(
                output.join("cloud.json"),
                serde_json::to_vec_pretty(&json!({"ok":false,"cloud_tested":true,"error":safe}))?,
            )?;
            anyhow::bail!("live cloud acceptance failed; inspect redacted cloud.json");
        }
    };
    // An environment key must never be written into the isolated configuration.
    ensure!(
        !std::fs::read_to_string(temporary.path().join("config.yaml"))?.contains(key),
        "credential persisted unexpectedly"
    );
    std::fs::write(
        output.join("cloud.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    Ok(())
}

/// This probe deliberately uses a test-only question-answering instruction.
/// The answer exists only in captured pixels, never in audio or instructions.
/// Normal production dictation must still only output the user's spoken text.
async fn visual_probe(
    key: &str,
    frames: &std::path::Path,
    output: &std::path::Path,
) -> Result<String> {
    use vocal_more_backend::{
        config::Config,
        provider::{Endpoints, Provider},
    };
    use vocal_more_core::protocol::{self, RealtimeConfig, Transcript};
    let mut config = Config::default();
    config.apply_update("asr.model", &json!("qwen3.8-omni-flash-realtime"))?;
    let mut provider = Provider::new(config, vec![], "", Endpoints::default(), Some(key));
    provider.enable_screen_context()?;
    let instructions = "Answer the spoken request using the received screen image. Return only the visible Verification code, exactly as printed. Never guess when it is unreadable.";
    let realtime = RealtimeConfig {
        endpoint: "wss://dashscope.aliyuncs.com/api-ws/v1/realtime".into(),
        model: provider.model().into(),
        instructions: instructions.into(),
    };
    timeout(Duration::from_secs(30), async {
        let mut socket = protocol::connect(&realtime, Some(key)).await?;
        let mut update = provider.session_update();
        update["session"]["instructions"] = json!(instructions);
        protocol::send(&mut socket, update).await?;
        let mut transcript = Transcript::default();
        loop {
            let event = protocol::receive(&mut socket).await?;
            transcript.consume(&event, false)?;
            if event["type"] == "session.updated" {
                break;
            }
        }
        let pcm = std::fs::read(output.join("visual-query.pcm"))?;
        for (index, chunk) in pcm.chunks(3200).enumerate() {
            protocol::send(&mut socket, protocol::append_event(chunk)).await?;
            if index == 0 {
                let mut image = protocol::command("input_image_buffer.append");
                image["image"] = json!(STANDARD.encode(std::fs::read(frames.join("frame-2.jpg"))?));
                protocol::send(&mut socket, image).await?;
            }
            sleep(Duration::from_millis(100)).await;
        }
        protocol::send(&mut socket, protocol::command("input_audio_buffer.commit")).await?;
        let mut response = protocol::command("response.create");
        response["response"] = json!({"modalities":["text"]});
        protocol::send(&mut socket, response).await?;
        while !transcript.consume(&protocol::receive(&mut socket).await?, true)? {}
        let text = transcript.text().trim().to_owned();
        ensure!(
            text.contains("VM-8617"),
            "cloud could not read captured verification code: {text}"
        );
        let _ = timeout(Duration::from_millis(250), socket.close(None)).await;
        Ok::<_, anyhow::Error>(text)
    })
    .await?
}
