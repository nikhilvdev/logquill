//! The C ABI, called the way a C caller calls it: through raw pointers, with owned strings handed back and freed. These
//! tests cover what the shared corpus cannot see: the ownership rules, failure on bad input instead of a crash, the
//! `Logger` (redaction before anything is written, the level, children, the tamper chain) and logging from many threads.

use std::ffi::{c_char, CStr, CString};
use std::ptr;
use std::sync::Arc;

use logquill_ffi::*;
use serde_json::{json, Value};

fn c(text: &str) -> CString {
    CString::new(text).unwrap()
}

/// Takes ownership of a returned string and frees it.
unsafe fn take(p: *mut c_char) -> String {
    assert!(!p.is_null(), "expected a string, got NULL");
    let text = unsafe { CStr::from_ptr(p) }.to_str().unwrap().to_owned();
    unsafe { lq_string_free(p) };
    text
}

/// `lq_call`: Ok(result JSON) or Err(message).
fn call(function: &str, args: &str) -> Result<Value, String> {
    let (f, a) = (c(function), c(args));
    let mut error: *mut c_char = ptr::null_mut();
    unsafe {
        let out = lq_call(f.as_ptr(), a.as_ptr(), &mut error);
        if out.is_null() {
            Err(take(error))
        } else {
            assert!(error.is_null(), "an error was set on success");
            Ok(serde_json::from_str(&take(out)).unwrap())
        }
    }
}

fn scratch(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("lq-ffi-{}-{name}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn logger(config: Value) -> *mut lq_logger {
    let text = c(&config.to_string());
    let mut error: *mut c_char = ptr::null_mut();
    let handle = unsafe { lq_logger_new(text.as_ptr(), &mut error) };
    assert!(!handle.is_null(), "config rejected: {}", unsafe {
        take(error)
    });
    handle
}

/// One log call; Some(record) when kept.
unsafe fn log(
    handle: *const lq_logger,
    level: &str,
    message: &str,
    meta: Option<&str>,
) -> Option<Value> {
    let (level, message) = (c(level), c(message));
    let meta = meta.map(c);
    let mut record: *mut c_char = ptr::null_mut();
    let mut error: *mut c_char = ptr::null_mut();
    let code = unsafe {
        lq_log(
            handle,
            level.as_ptr(),
            ptr::null(),
            message.as_ptr(),
            meta.as_ref().map_or(ptr::null(), |m| m.as_ptr()),
            ptr::null(),
            &mut record,
            &mut error,
        )
    };
    match code {
        1 => Some(serde_json::from_str(&unsafe { take(record) }).unwrap()),
        0 => {
            assert!(record.is_null());
            None
        }
        _ => panic!("lq_log failed: {}", unsafe { take(error) }),
    }
}

// ---- information and the stateless helpers ---------------------------------------------------------------------

#[test]
fn reports_its_version_and_abi_version() {
    let version = unsafe { CStr::from_ptr(lq_version()) }.to_str().unwrap();
    assert!(version.starts_with("3."), "{version}");
    assert_eq!(lq_abi_version(), 1);
}

#[test]
fn a_helper_returns_json_and_text_results_come_back_as_json_strings() {
    assert_eq!(
        call("canonicalJson", r#"["{\"b\":1,\"a\":2.0}"]"#).unwrap(),
        json!("{\"a\": 2.0, \"b\": 1}")
    );
    assert_eq!(
        call("keysInClass", r#"["secret"]"#)
            .unwrap()
            .as_array()
            .unwrap()
            .len(),
        5
    );
}

#[test]
fn a_record_may_be_given_as_a_value_or_as_its_json_text() {
    let record = json!({"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": "INFO", "logger": "a", "message": "m", "meta": {}});
    let as_value = call("formatJson", &json!([record]).to_string()).unwrap();
    let as_text = call("formatJson", &json!([record.to_string()]).to_string()).unwrap();
    assert_eq!(as_value, as_text);
}

#[test]
fn a_helper_keeps_a_numbers_own_spelling_when_given_json_text() {
    // 1.0 and 1 are different to the tamper chain; passing the text keeps them apart
    let a = call("canonicalJson", r#"["{\"x\": 1.0}"]"#).unwrap();
    let b = call("canonicalJson", r#"["{\"x\": 1}"]"#).unwrap();
    assert_ne!(a, b);
}

#[test]
fn bad_calls_are_errors_with_a_message_not_crashes() {
    assert!(call("noSuchHelper", "[]")
        .unwrap_err()
        .contains("unknown helper"));
    assert!(call("canonicalJson", "not json")
        .unwrap_err()
        .contains("not valid JSON"));
    assert!(call("canonicalJson", "{}")
        .unwrap_err()
        .contains("JSON array"));
    assert!(call("canonicalJson", "[]").unwrap_err().contains("missing"));
    assert!(call("canonicalJson", "[5]")
        .unwrap_err()
        .contains("must be a string"));
    assert!(call("verifyChain", r#"["{}"]"#)
        .unwrap_err()
        .contains("JSON array"));
    assert!(call("verifyChain", r#"["not json at all"]"#)
        .unwrap_err()
        .contains("not valid JSON"));
    assert!(call("signHead", r#"["h", "!!!not base64"]"#)
        .unwrap_err()
        .contains("base64"));
}

#[test]
fn null_and_non_utf8_arguments_are_errors() {
    unsafe {
        let mut error: *mut c_char = ptr::null_mut();
        assert!(lq_call(ptr::null(), ptr::null(), &mut error).is_null());
        assert!(take(error).contains("NULL"));

        let bad = [0xff_u8, 0xfe, 0];
        let mut error: *mut c_char = ptr::null_mut();
        assert!(lq_call(bad.as_ptr().cast(), ptr::null(), &mut error).is_null());
        assert!(take(error).contains("UTF-8"));

        // a NULL error pointer is allowed: the failure is just NULL
        assert!(lq_call(ptr::null(), ptr::null(), ptr::null_mut()).is_null());
        lq_string_free(ptr::null_mut());
    }
}

#[test]
fn a_host_can_allocate_and_free_a_buffer_it_writes_into() {
    unsafe {
        let buffer = lq_buffer_alloc(5);
        assert!(!buffer.is_null());
        ptr::copy_nonoverlapping(b"hello".as_ptr(), buffer, 5);
        assert_eq!(std::slice::from_raw_parts(buffer, 5), b"hello");
        lq_buffer_free(buffer, 5);
        // zero length still gives a valid pointer, and NULL may be freed
        let empty = lq_buffer_alloc(0);
        assert!(!empty.is_null());
        lq_buffer_free(empty, 0);
        lq_buffer_free(ptr::null_mut(), 0);
    }
}

// ---- the chain handle -------------------------------------------------------------------------------------------

#[test]
fn a_chain_stamps_records_that_verify_and_signs_its_head() {
    unsafe {
        let mut error: *mut c_char = ptr::null_mut();
        let chain = lq_chain_new(ptr::null(), &mut error);
        assert!(!chain.is_null());
        let mut stamped = Vec::new();
        for i in 0..3 {
            let record = c(&json!({"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": "INFO", "logger": "a", "message": format!("m{i}"), "meta": {}}).to_string());
            let out = lq_chain_stamp(chain, record.as_ptr(), &mut error);
            stamped.push(serde_json::from_str::<Value>(&take(out)).unwrap());
        }
        let verdict = call("verifyChain", &json!([stamped]).to_string()).unwrap();
        assert_eq!(verdict["ok"], true);
        assert_eq!(verdict["recordsChecked"], 3);
        let head = take(lq_chain_head(chain, &mut error));
        assert_eq!(verdict["headHash"], head.as_str());
        let signature = take(lq_chain_sign_head(chain, b"key".as_ptr(), 3, &mut error));
        assert_eq!(signature.len(), 64);
        lq_chain_free(chain);
        lq_chain_free(ptr::null_mut());
    }
}

// ---- the logger -------------------------------------------------------------------------------------------------

#[test]
fn redaction_happens_before_the_record_is_written_or_returned() {
    let dir = scratch("redact");
    let path = dir.join("app.log");
    unsafe {
        let handle = logger(json!({
            "name": "app",
            "plugins": [{"type": "redact"}, {"type": "pii"}],
            "transports": [{"type": "file", "path": path}],
        }));
        let record = log(
            handle,
            "info",
            "signed in",
            Some(r#"{"password": "hunter2", "note": "mail alice@example.com"}"#),
        )
        .unwrap();
        assert_eq!(record["meta"]["password"], "***");
        assert_eq!(record["meta"]["note"], "mail ***");
        assert_eq!(record["level"], "INFO");
        assert_eq!(record["logger"], "app");
        assert_eq!(lq_logger_close(handle, 5000), 1);
        lq_logger_free(handle);
    }
    let written = std::fs::read_to_string(&path).unwrap();
    assert_eq!(written.lines().count(), 1);
    assert!(
        !written.contains("hunter2") && !written.contains("alice@example.com"),
        "{written}"
    );
}

#[test]
fn the_level_filters_and_can_be_changed() {
    unsafe {
        let handle = logger(json!({"level": "WARN"}));
        assert!(log(handle, "info", "below", None).is_none());
        assert!(log(handle, "ERROR", "above", None).is_some());
        let (level, mut error) = (c("debug"), ptr::null_mut());
        assert_eq!(lq_logger_set_level(handle, level.as_ptr(), &mut error), 0);
        assert!(log(handle, "debug", "now kept", None).is_some());
        let (bad, mut error) = (c("loud"), ptr::null_mut());
        assert_eq!(lq_logger_set_level(handle, bad.as_ptr(), &mut error), -1);
        assert!(take(error).contains("Unknown log level"));
        lq_logger_free(handle);
    }
}

#[test]
fn a_child_has_a_dotted_name_fixed_meta_and_shares_the_level() {
    unsafe {
        let parent = logger(json!({"name": "web"}));
        let (suffix, fixed, mut error) = (c("ui"), c(r#"{"page": "home"}"#), ptr::null_mut());
        let child = lq_logger_child(parent, suffix.as_ptr(), fixed.as_ptr(), &mut error);
        assert!(!child.is_null());
        assert_eq!(take(lq_logger_name(child, &mut error)), "web.ui");
        let record = log(child, "info", "m", Some(r#"{"x": 1}"#)).unwrap();
        assert_eq!(record["logger"], "web.ui");
        assert_eq!(record["meta"], json!({"page": "home", "x": 1}));
        // the call's own meta wins over the fixed meta
        let record = log(child, "info", "m", Some(r#"{"page": "cart"}"#)).unwrap();
        assert_eq!(record["meta"]["page"], "cart");
        // the level is shared
        let level = c("error");
        lq_logger_set_level(parent, level.as_ptr(), &mut error);
        assert!(log(child, "info", "filtered", None).is_none());
        lq_logger_free(child);
        lq_logger_free(parent);
    }
}

#[test]
fn an_llm_block_and_the_content_policy_are_applied() {
    unsafe {
        let handle = logger(json!({"content_policy": "off"}));
        let (level, message, meta, llm) = (
            c("info"),
            c("call"),
            c(r#"{"input_messages": [{"role": "user", "content": "secret prompt"}]}"#),
            c(r#"{"model": "m", "tokens_in": 3}"#),
        );
        let mut record: *mut c_char = ptr::null_mut();
        let mut error: *mut c_char = ptr::null_mut();
        assert_eq!(
            lq_log(
                handle,
                level.as_ptr(),
                ptr::null(),
                message.as_ptr(),
                meta.as_ptr(),
                llm.as_ptr(),
                &mut record,
                &mut error
            ),
            1
        );
        let record: Value = serde_json::from_str(&take(record)).unwrap();
        assert_eq!(record["llm"]["model"], "m");
        assert!(!record.to_string().contains("secret prompt"), "{record}");
        lq_logger_free(handle);
    }
}

#[test]
fn a_wrong_config_is_an_error_that_names_the_problem() {
    let reject = |config: &str| {
        let text = c(config);
        let mut error: *mut c_char = ptr::null_mut();
        let handle = unsafe { lq_logger_new(text.as_ptr(), &mut error) };
        assert!(handle.is_null(), "{config} was accepted");
        unsafe { take(error) }
    };
    assert!(reject("not json").contains("not valid JSON"));
    assert!(reject(r#"{"levle": "INFO"}"#).contains("unknown option 'levle'"));
    assert!(reject(r#"{"level": "LOUD"}"#).contains("Unknown log level"));
    assert!(reject(r#"{"plugins": [{"type": "redactt"}]}"#).contains("unknown plugin type"));
    assert!(reject(r#"{"plugins": [{"type": "redact", "kyes": []}]}"#)
        .contains("unknown option 'kyes'"));
    assert!(reject(r#"{"transports": [{"type": "file"}]}"#).contains("needs a \"path\""));
    assert!(reject(r#"{"transports": [{"type": "smtp"}]}"#).contains("unknown transport type"));
    assert!(reject(r#"{"plugins": [{"type": "sampling"}]}"#).contains("needs a \"rate\""));
    assert!(reject(r#"{"plugins": [{"type": "sampling", "rate": 2}]}"#).contains("between 0 and 1"));
    assert!(
        reject(r#"{"plugins": [{"type": "rate_limit", "per_seconds": 1}]}"#)
            .contains("needs \"max_records\"")
    );
    assert!(reject(
        r#"{"plugins": [{"type": "rate_limit", "max_records": 1, "per_seconds": 1, "key": "x"}]}"#
    )
    .contains("key is"));
    assert!(
        reject(r#"{"plugins": [{"type": "adaptive_sampling", "base_rate": 5}]}"#)
            .contains("between 0 and 1")
    );
    assert!(reject(r#"{"tamper_evident": true}"#).contains("at least one transport"));
    assert!(
        reject(r#"{"queue": {"backpressure": "wait"}, "transports": [{"type": "console"}]}"#)
            .contains("backpressure")
    );
}

#[test]
fn a_null_handle_is_an_error_not_a_crash() {
    unsafe {
        let (level, message) = (c("info"), c("m"));
        let mut error: *mut c_char = ptr::null_mut();
        assert_eq!(
            lq_log(
                ptr::null(),
                level.as_ptr(),
                ptr::null(),
                message.as_ptr(),
                ptr::null(),
                ptr::null(),
                ptr::null_mut(),
                &mut error
            ),
            -1
        );
        assert!(take(error).contains("NULL"));
        assert_eq!(lq_logger_flush(ptr::null(), 10), -1);
        assert_eq!(lq_logger_close(ptr::null(), 10), -1);
        lq_logger_free(ptr::null_mut());
    }
}

#[test]
fn the_tamper_chain_is_applied_as_records_are_written_and_catches_an_edit() {
    let dir = scratch("chain");
    let path = dir.join("audit.log");
    unsafe {
        let handle =
            logger(json!({"tamper_evident": true, "transports": [{"type": "file", "path": path}]}));
        for i in 0..20 {
            log(
                handle,
                "info",
                &format!("event {i}"),
                Some(&format!(r#"{{"i": {i}}}"#)),
            )
            .unwrap();
        }
        assert_eq!(lq_logger_close(handle, 5000), 1);
        lq_logger_free(handle);
    }
    let lines: Vec<Value> = std::fs::read_to_string(&path)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(lines.len(), 20);
    let verdict = call("verifyChain", &json!([lines]).to_string()).unwrap();
    assert_eq!(
        (verdict["ok"].clone(), verdict["recordsChecked"].clone()),
        (json!(true), json!(20))
    );

    let mut edited = lines.clone();
    edited[7]["message"] = json!("tampered");
    let verdict = call("verifyChain", &json!([edited]).to_string()).unwrap();
    assert_eq!(
        (verdict["ok"].clone(), verdict["brokenAt"].clone()),
        (json!(false), json!(8))
    );
}

#[test]
fn many_threads_log_through_one_handle_and_every_record_is_written_in_a_valid_chain() {
    const THREADS: usize = 8;
    const EACH: usize = 250;
    let dir = scratch("threads");
    let path = dir.join("busy.log");
    let handle = Arc::new(Handle(logger(
        json!({"tamper_evident": true, "transports": [{"type": "file", "path": path}]}),
    )));
    let workers: Vec<_> = (0..THREADS)
        .map(|t| {
            let handle = Arc::clone(&handle);
            std::thread::spawn(move || {
                for i in 0..EACH {
                    unsafe { log(handle.0, "info", &format!("t{t} #{i}"), None) }.unwrap();
                }
            })
        })
        .collect();
    for worker in workers {
        worker.join().unwrap();
    }
    unsafe {
        assert_eq!(lq_logger_close(handle.0, 10_000), 1);
        lq_logger_free(handle.0);
    }
    let lines: Vec<Value> = std::fs::read_to_string(&path)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(lines.len(), THREADS * EACH);
    assert_eq!(
        call("verifyChain", &json!([lines]).to_string()).unwrap()["ok"],
        true
    );
}

/// A handle that may cross threads: the ABI documents handles as thread-safe.
struct Handle(*mut lq_logger);
unsafe impl Send for Handle {}
unsafe impl Sync for Handle {}

#[test]
fn sampling_keeps_about_its_rate_and_the_extremes_are_exact() {
    unsafe {
        let none = logger(json!({"plugins": [{"type": "sampling", "rate": 0}]}));
        let all = logger(json!({"plugins": [{"type": "sampling", "rate": 1}]}));
        let half = logger(json!({"plugins": [{"type": "sampling", "rate": 0.5}]}));
        let kept = |handle| {
            (0..2000)
                .filter(|_| log(handle, "info", "m", None).is_some())
                .count()
        };
        assert_eq!(kept(none), 0);
        assert_eq!(kept(all), 2000);
        let n = kept(half);
        assert!((800..1200).contains(&n), "rate 0.5 kept {n} of 2000");
        for handle in [none, all, half] {
            lq_logger_free(handle);
        }
    }
}

#[test]
fn a_rate_limit_counts_per_key_and_drops_the_excess() {
    unsafe {
        let handle = logger(json!({"plugins": [
            {"type": "rate_limit", "max_records": 3, "per_seconds": 60, "key": "message"}]}));
        let kept = |message: &str| {
            (0..10)
                .filter(|_| log(handle, "info", message, None).is_some())
                .count()
        };
        assert_eq!(kept("a"), 3);
        assert_eq!(kept("b"), 3, "another message has its own window");
        lq_logger_free(handle);
    }
}

#[test]
fn adaptive_sampling_always_keeps_errors_and_samples_the_rest() {
    unsafe {
        let handle = logger(json!({"plugins": [
            {"type": "adaptive_sampling", "base_rate": 0, "min_rate": 0, "max_rate": 0}]}));
        assert!((0..200).all(|_| log(handle, "info", "m", None).is_none()));
        assert!((0..200).all(|_| log(handle, "error", "m", None).is_some()));
        lq_logger_free(handle);
    }
}
