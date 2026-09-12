// SPDX-License-Identifier: GPL-3.0-only
use serde_json::{Value, json};
use std::{
    io::Write,
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

fn wait(child: &mut Child) {
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        if child.try_wait().unwrap().is_some() {
            return;
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            panic!("host failed to exit before deadline");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[test]
fn stdin_eof_cancels_active_session_and_next_launch_reads_final_file() {
    let dir = tempfile::tempdir().unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_vocal-more-host"))
        .arg("--data-dir")
        .arg(dir.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    for request in [
        json!({"jsonrpc":"2.0","id":1,"method":"initialize"}),
        json!({"jsonrpc":"2.0","id":2,"method":"start"}),
        json!({"jsonrpc":"2.0","id":3,"method":"append","params":{"generation":1,"pcm_base64":"AQACAAMA"}}),
    ] {
        writeln!(input, "{request}").unwrap();
    }
    drop(input);
    wait(&mut child);
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let replies: Vec<Value> = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect();
    assert_eq!(replies.len(), 3);
    assert_eq!(replies[0]["result"]["python_host"], false);
    assert!(
        replies.iter().all(|v| v.get("error").is_none()),
        "{replies:?}"
    );
    let mut child = Command::new(env!("CARGO_BIN_EXE_vocal-more-host"))
        .arg("--data-dir")
        .arg(dir.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    writeln!(
        input,
        "{}",
        json!({"jsonrpc":"2.0","id":1,"method":"recordings"})
    )
    .unwrap();
    // Explicit shutdown must exit even while stdin remains open.
    writeln!(
        input,
        "{}",
        json!({"jsonrpc":"2.0","id":2,"method":"shutdown"})
    )
    .unwrap();
    input.flush().unwrap();
    wait(&mut child);
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success());
    let records: Value = serde_json::from_str(
        String::from_utf8(output.stdout)
            .unwrap()
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    assert_eq!(records["result"][0]["status"], "cancelled");
    assert_eq!(records["result"][0]["pcm_bytes"], 6);
}
