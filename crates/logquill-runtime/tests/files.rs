//! Fernet interoperability with Python's `cryptography` library, and compressed / encrypted logs.

use std::fs;
use std::io::Read;
use std::path::PathBuf;

use logquill_runtime::{
    generate_fernet_key, Compression, Fernet, FileOptions, FileTransport, Formatter, Transport,
};
use serde_json::{json, Value};

fn fixture() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fernet_fixture.json");
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn tokens_made_by_python_decrypt_and_ours_are_byte_identical() {
    let fixture = fixture();
    let fernet = Fernet::new(fixture["key"].as_str().unwrap()).unwrap();
    let iv_hex = fixture["iv_hex"].as_str().unwrap();
    let iv: [u8; 16] = (0..16)
        .map(|i| u8::from_str_radix(&iv_hex[i * 2..i * 2 + 2], 16).unwrap())
        .collect::<Vec<_>>()
        .try_into()
        .unwrap();
    let timestamp = fixture["timestamp"].as_u64().unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let plaintext = case["plaintext"].as_str().unwrap();
        let token = case["token"].as_str().unwrap();
        assert_eq!(
            fernet.decrypt(token).unwrap(),
            plaintext.as_bytes(),
            "{plaintext:?}"
        );
        assert_eq!(
            fernet.encrypt_with(plaintext.as_bytes(), timestamp, iv),
            token,
            "{plaintext:?}"
        );
    }
}

#[test]
fn a_wrong_key_garbage_and_edits_are_refused_with_the_documented_texts() {
    let fixture = fixture();
    let fernet = Fernet::new(fixture["key"].as_str().unwrap()).unwrap();
    assert_eq!(
        fernet
            .decrypt(fixture["wrong_key_token"].as_str().unwrap())
            .unwrap_err(),
        "Fernet token failed verification \u{2014} wrong key, or the line was edited"
    );
    for junk in ["", "not a token", "AAAA"] {
        assert_eq!(
            fernet.decrypt(junk).unwrap_err(),
            "Not a Fernet token \u{2014} was this line written with encrypt_key?"
        );
    }
    let mut token = fixture["cases"][0]["token"].as_str().unwrap().to_string();
    let swap = if &token[40..41] == "A" { "B" } else { "A" };
    token.replace_range(40..41, swap);
    assert!(fernet.decrypt(&token).is_err());
    assert!(Fernet::new("too short")
        .err()
        .unwrap()
        .starts_with("Fernet key must be 32 url-safe base64-encoded bytes"));
}

#[test]
fn a_generated_key_round_trips() {
    let fernet = Fernet::new(&generate_fernet_key()).unwrap();
    let token = fernet.encrypt("caf\u{e9} \u{2603}".as_bytes());
    assert_eq!(
        fernet.decrypt(&token).unwrap(),
        "caf\u{e9} \u{2603}".as_bytes()
    );
}

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("logquill-files-{}-{name}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn write_all(transport: &mut FileTransport, count: usize) {
    for n in 0..count {
        let record =
            json!({"level": "INFO", "message": format!("secret message {n:03}"), "meta": {}});
        let line = transport.format(&record);
        transport.write(&line, &record).unwrap();
    }
    transport.close().unwrap();
}

#[test]
fn encrypted_lines_hide_the_text_and_decrypt_with_the_key() {
    let dir = scratch("encrypted");
    let path = dir.join("app.log");
    let key = generate_fernet_key();
    let options = FileOptions {
        encrypt_key: Some(key.clone()),
        max_bytes: 0,
        ..FileOptions::default()
    };
    let mut transport = FileTransport::new(&path, options).unwrap();
    write_all(&mut transport, 5);
    let text = fs::read_to_string(&path).unwrap();
    assert!(!text.contains("secret message"));
    let fernet = Fernet::new(&key).unwrap();
    let lines: Vec<String> = text
        .lines()
        .map(|line| String::from_utf8(fernet.decrypt(line).unwrap()).unwrap())
        .collect();
    assert_eq!(lines.len(), 5);
    assert!(lines[4].contains("secret message 004"));
}

#[test]
fn a_bad_encryption_key_fails_at_construction() {
    let options = FileOptions {
        encrypt_key: Some("nope".into()),
        ..FileOptions::default()
    };
    let error = FileTransport::new(scratch("badkey").join("a.log"), options)
        .err()
        .unwrap();
    assert!(error.contains("generate_fernet_key()"), "{error}");
}

#[test]
fn rotated_files_are_gzipped() {
    let dir = scratch("gz");
    let path = dir.join("app.log");
    let options = FileOptions {
        max_bytes: 300,
        backup_count: 2,
        compression: Some(Compression::Gz),
        ..FileOptions::default()
    };
    let mut transport = FileTransport::new(&path, options).unwrap();
    write_all(&mut transport, 30);
    assert!(dir.join("app.log.1.gz").exists() && dir.join("app.log.2.gz").exists());
    assert!(!dir.join("app.log.1").exists() && !dir.join("app.log.3.gz").exists());
    let mut text = String::new();
    flate2::read::GzDecoder::new(fs::File::open(dir.join("app.log.1.gz")).unwrap())
        .read_to_string(&mut text)
        .unwrap();
    assert!(text.contains("secret message") && text.lines().all(|l| l.starts_with('{')));
}

#[test]
fn rotated_files_can_be_zipped() {
    let dir = scratch("zip");
    let path = dir.join("app.log");
    let options = FileOptions {
        max_bytes: 300,
        backup_count: 1,
        compression: Some(Compression::Zip),
        formatter: Formatter::Text,
        ..FileOptions::default()
    };
    let mut transport = FileTransport::new(&path, options).unwrap();
    write_all(&mut transport, 20);
    let mut archive =
        zip::ZipArchive::new(fs::File::open(dir.join("app.log.1.zip")).unwrap()).unwrap();
    assert_eq!(archive.len(), 1);
    let mut entry = archive.by_index(0).unwrap();
    assert_eq!(entry.name(), "app.log");
    let mut text = String::new();
    entry.read_to_string(&mut text).unwrap();
    assert!(text.contains("secret message"));
}

#[test]
fn a_path_that_is_not_a_regular_file_is_written_but_never_rotated() {
    // /dev/null accepts any amount of data and cannot be renamed, so rotating it would fail.
    #[cfg(unix)]
    {
        let options = FileOptions {
            max_bytes: 100,
            ..FileOptions::default()
        };
        let mut transport = FileTransport::new("/dev/null", options).unwrap();
        let record = json!({"message": "x".repeat(60)});
        for _ in 0..20 {
            let line = transport.format(&record);
            transport
                .write(&line, &record)
                .expect("no rotation is attempted");
        }
    }
}
