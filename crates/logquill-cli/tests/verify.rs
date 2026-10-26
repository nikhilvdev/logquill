//! End-to-end tests of `logquill verify` against real files.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

use logquill_core::Chain;
use serde_json::{json, Value};

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("logquill-verify-{}", std::process::id()));
    fs::create_dir_all(&dir).expect("a scratch directory");
    dir.join(name)
}

fn chained(count: u32) -> (Vec<Value>, String) {
    let mut chain = Chain::default();
    let records: Vec<Value> = (0..count)
        .map(|n| {
            let mut record = json!({
                "schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": "INFO",
                "logger": "app", "message": format!("m{n}"), "meta": {"n": n}
            });
            chain.stamp(&mut record);
            record
        })
        .collect();
    (records, chain.head().to_string())
}

fn write_log(path: &PathBuf, records: &[Value]) {
    let text: String = records.iter().map(|r| format!("{r}\n")).collect();
    fs::write(path, text).expect("write the log");
}

fn verify(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_logquill"))
        .arg("verify")
        .args(args)
        .output()
        .expect("run logquill")
}

fn stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).to_string()
}

#[test]
fn an_intact_log_verifies_and_prints_the_head() {
    let path = scratch("ok.jsonl");
    let (records, head) = chained(3);
    write_log(&path, &records);
    let output = verify(&[path.to_str().unwrap()]);
    assert_eq!(output.status.code(), Some(0));
    let text = stdout(&output);
    assert!(text.contains("OK — 3 record(s) verified in"), "{text}");
    assert!(text.contains(&format!("chain head is {head}")), "{text}");
}

#[test]
fn an_edited_line_fails_and_says_where() {
    let path = scratch("edited.jsonl");
    let (records, _) = chained(3);
    write_log(&path, &records);
    let text = fs::read_to_string(&path)
        .unwrap()
        .replace("\"m1\"", "\"tampered\"");
    fs::write(&path, text).unwrap();
    let output = verify(&[path.to_str().unwrap()]);
    assert_eq!(output.status.code(), Some(1));
    let text = stdout(&output);
    assert!(
        text.contains("FAILED — this record's content doesn't match its own stored hash"),
        "{text}"
    );
    assert!(text.contains("(at record 2 of 2 checked)"), "{text}");
}

#[test]
fn unparseable_lines_are_skipped_silently() {
    let path = scratch("noisy.jsonl");
    let (records, _) = chained(2);
    write_log(&path, &records);
    let text = format!(
        "not json\n{}\n[1,2]\n",
        fs::read_to_string(&path).unwrap().trim_end()
    );
    fs::write(&path, text).unwrap();
    assert_eq!(verify(&[path.to_str().unwrap()]).status.code(), Some(0));
}

#[test]
fn a_signed_head_is_checked_and_truncation_is_caught() {
    let path = scratch("signed.jsonl");
    let (records, head) = chained(3);
    write_log(&path, &records);
    let key_hex = "6b6579"; // "key"
    let signature = logquill_core::sign_head(&head, b"key");
    let args = |p: &PathBuf| {
        vec![
            p.to_str().unwrap().to_string(),
            "--sign-key".to_string(),
            key_hex.to_string(),
            "--signature".to_string(),
            signature.clone(),
        ]
    };
    let run = |p: &PathBuf| {
        let owned = args(p);
        let borrowed: Vec<&str> = owned.iter().map(String::as_str).collect();
        verify(&borrowed)
    };
    let ok = run(&path);
    assert_eq!(ok.status.code(), Some(0), "{}", stdout(&ok));

    write_log(&path, &records[..2]);
    let cut = run(&path);
    assert_eq!(cut.status.code(), Some(1));
    assert!(
        stdout(&cut).contains("doesn't match the given signature"),
        "{}",
        stdout(&cut)
    );
}

#[test]
fn usage_errors_use_the_documented_texts() {
    let path = scratch("usage.jsonl");
    let (records, _) = chained(1);
    write_log(&path, &records);
    let missing = verify(&["/no/such/file.jsonl"]);
    assert_eq!(missing.status.code(), Some(1));
    assert!(stdout(&missing).starts_with("logquill verify: no such file: /no/such/file.jsonl"));

    let alone = verify(&[path.to_str().unwrap(), "--sign-key", "6b6579"]);
    assert_eq!(alone.status.code(), Some(1));
    assert!(stdout(&alone).contains("--sign-key and --signature must be given together"));

    let not_hex = verify(&[
        path.to_str().unwrap(),
        "--sign-key",
        "zz",
        "--signature",
        "00",
    ]);
    assert_eq!(not_hex.status.code(), Some(1));
    assert!(stdout(&not_hex).contains("--sign-key must be hex-encoded"));
}
