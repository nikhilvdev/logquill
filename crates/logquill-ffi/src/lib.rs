//! LogQuill's C ABI. The only file in this crate with `unsafe`; the logic is in safe modules (`call` for the stateless
//! helpers, `logger` for the `Logger`), and this layer only moves strings and handles across the boundary.
//!
//! **Conventions** (stated again in the generated `include/logquill.h`):
//!
//! - Strings are UTF-8 and NUL-terminated. A string the library returns is owned by the caller and freed with
//!   [`lq_string_free`]; one the caller passes is only borrowed for the call.
//! - A function that can fail returns NULL (or -1) and, if `error` is not NULL, stores an owned message in `*error`.
//!   Nothing unwinds across the boundary: every entry point catches panics and reports them as errors. A NULL or non-UTF-8
//!   argument is an error, not a crash.
//! - A handle is thread-safe: any thread may use it, concurrently. Free it exactly once, after the last use.
//! - Data is JSON text, as in the Node and WASM bindings: a host number cannot tell `1` from `1.0`, and the tamper chain
//!   hashes the spelling.

#![allow(non_camel_case_types, clippy::missing_safety_doc)]

mod call;
mod logger;
mod stateful;

use std::ffi::{c_char, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::time::Duration;

use logquill_core as core;
use serde_json::Value;

use crate::logger::{Logged, Logger};

/// The version of this C ABI. It changes only when an existing function changes meaning or signature; adding a function
/// or a helper does not change it.
pub const LQ_ABI_VERSION: u32 = 1;

/// A logger (or a child of one). Opaque.
pub struct lq_logger {
    inner: Logger,
}

/// A hash chain several threads can stamp records into. Opaque.
pub struct lq_chain {
    inner: core::SharedChain,
}

// ---- moving strings across ---------------------------------------------------------------------------------------

unsafe fn read<'a>(p: *const c_char, what: &str) -> Result<&'a str, String> {
    if p.is_null() {
        return Err(format!("{what} is NULL"));
    }
    // SAFETY: the caller promises `p` is a NUL-terminated string that outlives this call.
    unsafe { CStr::from_ptr(p) }
        .to_str()
        .map_err(|_| format!("{what} is not valid UTF-8"))
}

unsafe fn read_opt<'a>(p: *const c_char, what: &str) -> Result<Option<&'a str>, String> {
    if p.is_null() {
        Ok(None)
    } else {
        // SAFETY: as in `read`.
        unsafe { read(p, what) }.map(Some)
    }
}

fn give(text: String) -> Result<*mut c_char, String> {
    CString::new(text).map(CString::into_raw).map_err(|_| {
        "the result contains a NUL character, which a C string cannot carry".to_string()
    })
}

unsafe fn report(error: *mut *mut c_char, message: String) {
    if error.is_null() {
        return;
    }
    let clean = CString::new(message.replace('\0', "\u{FFFD}")).unwrap_or_default();
    // SAFETY: the caller promises `error` is NULL or points at a writable `char *`.
    unsafe { *error = clean.into_raw() };
}

fn panic_text(payload: &(dyn std::any::Any + Send)) -> String {
    payload
        .downcast_ref::<&str>()
        .map(|s| (*s).to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "unknown".to_string())
}

/// Runs `f`, turning a panic into an error.
fn guarded<T>(f: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(result) => result,
        Err(payload) => Err(format!("internal error: {}", panic_text(&*payload))),
    }
}

/// A pointer result: NULL and `*error` on failure.
unsafe fn pointer<T>(error: *mut *mut c_char, result: Result<*mut T, String>) -> *mut T {
    match result {
        Ok(p) => p,
        Err(message) => {
            // SAFETY: as in `report`.
            unsafe { report(error, message) };
            ptr::null_mut()
        }
    }
}

/// A status result: `ok` on success, -1 and `*error` on failure.
unsafe fn status(error: *mut *mut c_char, result: Result<i32, String>) -> i32 {
    match result {
        Ok(code) => code,
        Err(message) => {
            // SAFETY: as in `report`.
            unsafe { report(error, message) };
            -1
        }
    }
}

// ---- library information and memory --------------------------------------------------------------------------------

/// The library's version, e.g. `3.0.0-dev.0`. Static: do not free.
#[no_mangle]
pub extern "C" fn lq_version() -> *const c_char {
    static VERSION: &str = concat!(env!("CARGO_PKG_VERSION"), "\0");
    VERSION.as_ptr().cast()
}

/// The version of the C ABI (see `LQ_ABI_VERSION`). A wrapper checks this at load time.
#[no_mangle]
pub extern "C" fn lq_abi_version() -> u32 {
    LQ_ABI_VERSION
}

/// Frees a string this library returned (a result or an error message). NULL is allowed.
#[no_mangle]
pub unsafe extern "C" fn lq_string_free(string: *mut c_char) {
    if !string.is_null() {
        // SAFETY: the caller promises `string` came from this library and is freed once.
        drop(unsafe { CString::from_raw(string) });
    }
}

/// Allocates `len` bytes the host can write into, for hosts that cannot hand the library a pointer of their own (WebAssembly:
/// the host writes into the module's memory). Free with [`lq_buffer_free`], passing the same `len`.
#[no_mangle]
pub extern "C" fn lq_buffer_alloc(len: usize) -> *mut u8 {
    let mut buffer = Vec::<u8>::with_capacity(len.max(1));
    let pointer = buffer.as_mut_ptr();
    std::mem::forget(buffer);
    pointer
}

/// Frees a buffer from [`lq_buffer_alloc`]. NULL is allowed; `len` must be the length it was allocated with.
#[no_mangle]
pub unsafe extern "C" fn lq_buffer_free(buffer: *mut u8, len: usize) {
    if !buffer.is_null() {
        // SAFETY: the caller promises `buffer` came from `lq_buffer_alloc(len)` and is freed once.
        drop(unsafe { Vec::from_raw_parts(buffer, 0, len.max(1)) });
    }
}

// ---- stateless helpers ----------------------------------------------------------------------------------------------

/// Runs the helper `function` (named as in the Node addon: `canonicalJson`, `verifyChain`, `piiRedactText`, ...) with
/// `args_json`, a JSON array of its arguments (NULL means none). Returns the result as JSON text, or NULL with `*error`.
#[no_mangle]
pub unsafe extern "C" fn lq_call(
    function: *const c_char,
    args_json: *const c_char,
    error: *mut *mut c_char,
) -> *mut c_char {
    // SAFETY: the pointer arguments are as documented; `pointer` only writes through `error`.
    unsafe {
        pointer(
            error,
            guarded(|| {
                let name = read(function, "the function name")?;
                let args = match read_opt(args_json, "the arguments")? {
                    None => Vec::new(),
                    Some(text) => match core::parse_lenient(text) {
                        Ok(Value::Array(list)) => list,
                        Ok(_) => return Err("the arguments must be a JSON array".into()),
                        Err(e) => return Err(format!("the arguments are not valid JSON ({e})")),
                    },
                };
                give(call::call(name, &args)?.to_string())
            }),
        )
    }
}

// ---- hash chain -----------------------------------------------------------------------------------------------------

/// A new hash chain starting from `genesis` (NULL: the standard genesis hash).
#[no_mangle]
pub unsafe extern "C" fn lq_chain_new(
    genesis: *const c_char,
    error: *mut *mut c_char,
) -> *mut lq_chain {
    // SAFETY: as documented.
    unsafe {
        pointer(
            error,
            guarded(|| {
                let chain = match read_opt(genesis, "the genesis hash")? {
                    Some(genesis) => core::Chain::new(genesis),
                    None => core::Chain::default(),
                };
                Ok(Box::into_raw(Box::new(lq_chain {
                    inner: core::SharedChain::new(chain),
                })))
            }),
        )
    }
}

/// Chains `record_json` now and returns it with `meta.prev_hash` and `meta.hash` set (JSON text).
#[no_mangle]
pub unsafe extern "C" fn lq_chain_stamp(
    chain: *const lq_chain,
    record_json: *const c_char,
    error: *mut *mut c_char,
) -> *mut c_char {
    // SAFETY: `chain` is a live handle from `lq_chain_new`.
    unsafe {
        pointer(
            error,
            guarded(|| {
                let chain = chain.as_ref().ok_or("the chain handle is NULL")?;
                let record = core::parse_lenient(read(record_json, "the record")?)
                    .map_err(|e| format!("the record is not valid JSON ({e})"))?;
                give(chain.inner.write(record, Value::clone).to_string())
            }),
        )
    }
}

/// The hash of the last record chained (the genesis hash before any).
#[no_mangle]
pub unsafe extern "C" fn lq_chain_head(
    chain: *const lq_chain,
    error: *mut *mut c_char,
) -> *mut c_char {
    // SAFETY: as above.
    unsafe {
        pointer(
            error,
            guarded(|| {
                give(
                    chain
                        .as_ref()
                        .ok_or("the chain handle is NULL")?
                        .inner
                        .head(),
                )
            }),
        )
    }
}

/// HMAC-SHA256 of the chain's head under the `key_len` bytes at `key`, hex-encoded.
#[no_mangle]
pub unsafe extern "C" fn lq_chain_sign_head(
    chain: *const lq_chain,
    key: *const u8,
    key_len: usize,
    error: *mut *mut c_char,
) -> *mut c_char {
    // SAFETY: `key` points at `key_len` readable bytes (or is NULL with a zero length).
    unsafe {
        pointer(
            error,
            guarded(|| {
                let chain = chain.as_ref().ok_or("the chain handle is NULL")?;
                let key: &[u8] = if key.is_null() {
                    &[]
                } else {
                    std::slice::from_raw_parts(key, key_len)
                };
                give(core::sign_head(&chain.inner.head(), key))
            }),
        )
    }
}

/// Frees a chain. NULL is allowed.
#[no_mangle]
pub unsafe extern "C" fn lq_chain_free(chain: *mut lq_chain) {
    if !chain.is_null() {
        // SAFETY: the caller promises `chain` came from `lq_chain_new` and is freed once.
        let _ = catch_unwind(AssertUnwindSafe(|| drop(unsafe { Box::from_raw(chain) })));
    }
}

// ---- logger ---------------------------------------------------------------------------------------------------------

/// Builds a logger from a JSON config (see `docs/stages/stage-8.md`). NULL and `*error` if the config is wrong.
#[no_mangle]
pub unsafe extern "C" fn lq_logger_new(
    config_json: *const c_char,
    error: *mut *mut c_char,
) -> *mut lq_logger {
    // SAFETY: as documented.
    unsafe {
        pointer(
            error,
            guarded(|| {
                let text = read_opt(config_json, "the config")?.unwrap_or("{}");
                Ok(Box::into_raw(Box::new(lq_logger {
                    inner: Logger::from_config(text)?,
                })))
            }),
        )
    }
}

/// A logger named `<parent>.<suffix>` with `fixed_meta_json` (a JSON object, or NULL) added to every record; it shares the
/// parent's level and transports. Free it like any logger.
#[no_mangle]
pub unsafe extern "C" fn lq_logger_child(
    parent: *const lq_logger,
    suffix: *const c_char,
    fixed_meta_json: *const c_char,
    error: *mut *mut c_char,
) -> *mut lq_logger {
    // SAFETY: `parent` is a live handle.
    unsafe {
        pointer(
            error,
            guarded(|| {
                let parent = parent.as_ref().ok_or("the logger handle is NULL")?;
                let child = parent.inner.child(
                    read(suffix, "the child name")?,
                    read_opt(fixed_meta_json, "the fixed meta")?,
                )?;
                Ok(Box::into_raw(Box::new(lq_logger { inner: child })))
            }),
        )
    }
}

/// One log call. `level` is a name (`trace` to `fatal`, any case); `logger_name` overrides the logger's name for this
/// record (NULL keeps it); `meta_json` and `llm_json` are JSON objects or NULL.
///
/// Returns 1 and stores the finished record (JSON text, to be freed) in `*record_out` when it was kept, 0 when it was
/// filtered out (`*record_out` is set to NULL), and -1 with `*error` on a bad argument. A kept record has also been queued
/// to the logger's own transports. `record_out` may be NULL if the host does not want the record.
#[no_mangle]
pub unsafe extern "C" fn lq_log(
    logger: *const lq_logger,
    level: *const c_char,
    logger_name: *const c_char,
    message: *const c_char,
    meta_json: *const c_char,
    llm_json: *const c_char,
    record_out: *mut *mut c_char,
    error: *mut *mut c_char,
) -> i32 {
    // SAFETY: `logger` is a live handle; the string pointers are as documented.
    unsafe {
        if !record_out.is_null() {
            *record_out = ptr::null_mut();
        }
        status(
            error,
            guarded(|| {
                let logger = logger.as_ref().ok_or("the logger handle is NULL")?;
                let logged = logger.inner.log(
                    read(level, "the level")?,
                    read_opt(logger_name, "the logger name")?,
                    read(message, "the message")?,
                    read_opt(meta_json, "the meta")?,
                    read_opt(llm_json, "the llm block")?,
                )?;
                match logged {
                    Logged::Dropped => Ok(0),
                    Logged::Kept(record) => {
                        if !record_out.is_null() {
                            *record_out = give(record)?;
                        }
                        Ok(1)
                    }
                }
            }),
        )
    }
}

/// The logger's name (`app`, `app.web`, ...), as a string to free with `lq_string_free`.
#[no_mangle]
pub unsafe extern "C" fn lq_logger_name(
    logger: *const lq_logger,
    error: *mut *mut c_char,
) -> *mut c_char {
    // SAFETY: `logger` is a live handle.
    unsafe {
        pointer(
            error,
            guarded(|| {
                give(
                    logger
                        .as_ref()
                        .ok_or("the logger handle is NULL")?
                        .inner
                        .name()
                        .to_string(),
                )
            }),
        )
    }
}

/// Sets the level of the logger and everything sharing its pipeline. Returns 0, or -1 with `*error`.
#[no_mangle]
pub unsafe extern "C" fn lq_logger_set_level(
    logger: *const lq_logger,
    level: *const c_char,
    error: *mut *mut c_char,
) -> i32 {
    // SAFETY: as above.
    unsafe {
        status(
            error,
            guarded(|| {
                let logger = logger.as_ref().ok_or("the logger handle is NULL")?;
                logger.inner.set_level(read(level, "the level")?)?;
                Ok(0)
            }),
        )
    }
}

/// Waits up to `timeout_ms` for queued records to be written. 1 when done, 0 when the time ran out, -1 on a bad handle.
#[no_mangle]
pub unsafe extern "C" fn lq_logger_flush(logger: *const lq_logger, timeout_ms: u32) -> i32 {
    // SAFETY: `logger` is a live handle.
    let logger = unsafe { logger.as_ref() };
    match logger {
        Some(logger) => catch_unwind(AssertUnwindSafe(|| {
            i32::from(logger.inner.flush(Duration::from_millis(timeout_ms.into())))
        }))
        .unwrap_or(-1),
        None => -1,
    }
}

/// Flushes (up to `timeout_ms`) and stops the logger's worker. Records logged afterwards are returned but not written.
/// 1 when everything was written, 0 when the time ran out, -1 on a bad handle.
#[no_mangle]
pub unsafe extern "C" fn lq_logger_close(logger: *const lq_logger, timeout_ms: u32) -> i32 {
    // SAFETY: `logger` is a live handle.
    let logger = unsafe { logger.as_ref() };
    match logger {
        Some(logger) => catch_unwind(AssertUnwindSafe(|| {
            i32::from(logger.inner.close(Duration::from_millis(timeout_ms.into())))
        }))
        .unwrap_or(-1),
        None => -1,
    }
}

/// Frees a logger. Freeing the last handle of a logger that was never closed flushes it (up to five seconds). NULL is allowed.
#[no_mangle]
pub unsafe extern "C" fn lq_logger_free(logger: *mut lq_logger) {
    if !logger.is_null() {
        // SAFETY: the caller promises `logger` came from this library and is freed once.
        let _ = catch_unwind(AssertUnwindSafe(|| drop(unsafe { Box::from_raw(logger) })));
    }
}
