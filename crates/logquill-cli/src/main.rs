//! The `logquill` command. Only `verify` exists so far; `tail`, `trace`, `dev` and `serve` are
//! specified in `docs/spec/06-cli-and-viewer.md` and arrive in the CLI stage.

use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use logquill_core::{verify_chain, verify_signed_chain, Verification, GENESIS_HASH};
use serde_json::Value;

#[derive(Parser)]
#[command(name = "logquill", version, about = "Tools for LogQuill logs")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Check that a hash-chained JSONL log has not been edited, reordered or truncated.
    Verify {
        /// The JSONL log file to check.
        file: PathBuf,
        /// Hex-encoded key the chain head was signed with.
        #[arg(long)]
        sign_key: Option<String>,
        /// Hex-encoded signature of the chain head, kept outside the log.
        #[arg(long)]
        signature: Option<String>,
    },
}

fn unhex(text: &str) -> Option<Vec<u8>> {
    if text.len() % 2 != 0 || !text.is_ascii() {
        return None;
    }
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).ok())
        .collect()
}

/// The JSON objects in a file, one per line; lines that are not JSON objects are skipped.
fn records(file: File) -> impl Iterator<Item = Value> {
    BufReader::new(file)
        .lines()
        .map_while(Result::ok)
        .filter_map(|line| serde_json::from_str::<Value>(&line).ok())
        .filter(Value::is_object)
}

fn run_verify(
    path: &PathBuf,
    sign_key: Option<&str>,
    signature: Option<&str>,
    out: &mut impl Write,
) -> u8 {
    let shown = path.display();
    let Ok(file) = File::open(path) else {
        let _ = writeln!(out, "logquill verify: no such file: {shown}");
        return 1;
    };
    let result: Verification = match (sign_key, signature) {
        (None, None) => verify_chain(records(file), GENESIS_HASH),
        (Some(key), Some(signature)) => {
            let Some(key) = unhex(key) else {
                let _ = writeln!(out, "logquill verify: --sign-key must be hex-encoded");
                return 1;
            };
            verify_signed_chain(records(file), &key, signature, GENESIS_HASH)
        }
        _ => {
            let _ = writeln!(
                out,
                "logquill verify: --sign-key and --signature must be given together"
            );
            return 1;
        }
    };
    if result.ok {
        let _ = writeln!(
            out,
            "logquill verify: OK — {} record(s) verified in {shown}",
            result.records_checked
        );
        if let Some(head) = &result.head_hash {
            let _ = writeln!(out, "logquill verify: chain head is {head}");
        }
        0
    } else {
        let _ = writeln!(
            out,
            "logquill verify: FAILED — {} (at record {} of {} checked) in {shown}",
            result.reason.as_deref().unwrap_or("the chain is broken"),
            result.broken_at.unwrap_or(result.records_checked),
            result.records_checked,
        );
        1
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    match cli.command {
        Command::Verify {
            file,
            sign_key,
            signature,
        } => ExitCode::from(run_verify(
            &file,
            sign_key.as_deref(),
            signature.as_deref(),
            &mut std::io::stdout(),
        )),
    }
}
