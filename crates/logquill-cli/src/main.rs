//! The `logquill` command: `tail`, `trace`, `dev`, `serve` and `verify`
//! (`docs/spec/06-cli-and-viewer.md`).

use std::fs::File;
use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{ArgGroup, Parser, Subcommand};
use logquill_cli::lines::records;
use logquill_cli::render::colour_wanted;
use logquill_cli::source::{valid_table_name, Source};
use logquill_cli::{dev, serve, tail, trace};
use logquill_core::{verify_chain, verify_signed_chain, Level, Verification, GENESIS_HASH};

#[derive(Parser)]
#[command(name = "logquill", version, about = "Tools for LogQuill logs")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

fn level(text: &str) -> Result<Level, String> {
    Level::parse(text).map_err(|e| e.to_string())
}

#[derive(Subcommand)]
enum Command {
    /// Print a log file's records, filtered by level, and optionally keep following it.
    Tail {
        /// The JSONL log file.
        file: PathBuf,
        /// Only records at or above this level.
        #[arg(long, value_parser = level)]
        level: Option<Level>,
        /// Print each record as compact JSON instead of a text line.
        #[arg(long)]
        json: bool,
        /// Keep running and print records as they are appended (survives rotation).
        #[arg(short = 'f', long)]
        follow: bool,
        /// Show only the last N matching records already in the file.
        #[arg(short = 'n', long)]
        lines: Option<usize>,
        /// Never colour the output.
        #[arg(long)]
        no_color: bool,
    },
    /// Print one run's span tree, with duration, token and cost annotations.
    Trace {
        /// The `meta.run_id` to show.
        run_id: String,
        /// The JSONL log file.
        #[arg(long)]
        file: PathBuf,
        /// Print the tree as one JSON array instead of text.
        #[arg(long)]
        json: bool,
        /// Never colour the output.
        #[arg(long)]
        no_color: bool,
    },
    /// Watch one run's span tree redraw live as the file grows.
    Dev {
        /// The JSONL log file.
        file: PathBuf,
        /// Track this run instead of the most recent one.
        #[arg(long)]
        run_id: Option<String>,
        /// Never colour the output or clear the screen.
        #[arg(long)]
        no_color: bool,
        /// How many of the file's last lines to start from.
        #[arg(long, default_value_t = 5000)]
        backlog: usize,
    },
    /// Serve a local web viewer for a log file or a SQLite log database.
    #[command(group(ArgGroup::new("source").required(true).args(["file", "db"])))]
    Serve {
        /// A JSONL log file.
        #[arg(long)]
        file: Option<PathBuf>,
        /// A SQLite database written by the SQLite transport.
        #[arg(long)]
        db: Option<PathBuf>,
        /// The table to read with --db.
        #[arg(long, default_value = "logs")]
        table: String,
        /// The address to listen on. Anything but loopback exposes the logs to the network.
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        /// The port; 0 lets the operating system choose.
        #[arg(long, default_value_t = 0)]
        port: u16,
    },
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
    let mut out = std::io::stdout();
    let code = match cli.command {
        Command::Tail {
            file,
            level,
            json,
            follow,
            lines,
            no_color,
        } => tail::run(
            &file,
            &tail::Options {
                level,
                json,
                follow,
                lines,
                colour: !json && colour_wanted(no_color),
            },
            &mut out,
        ),
        Command::Trace {
            run_id,
            file,
            json,
            no_color,
        } => trace::run(&run_id, &file, json, colour_wanted(no_color), &mut out),
        Command::Dev {
            file,
            run_id,
            no_color,
            backlog,
        } => dev::run(
            &file,
            &dev::Options {
                run_id,
                colour: colour_wanted(no_color),
                backlog,
            },
            &mut out,
        ),
        Command::Serve {
            file,
            db,
            table,
            host,
            port,
        } => match (file, db) {
            (Some(path), _) => serve::run(Source::Jsonl(path), &host, port, &mut out),
            (None, Some(path)) if valid_table_name(&table) => {
                serve::run(Source::Sqlite { path, table }, &host, port, &mut out)
            }
            _ => {
                eprintln!(
                    "logquill serve: --table {table} is not a plain SQL identifier \u{2014} use letters, digits and underscores"
                );
                1
            }
        },
        Command::Verify {
            file,
            sign_key,
            signature,
        } => run_verify(&file, sign_key.as_deref(), signature.as_deref(), &mut out),
    };
    ExitCode::from(code)
}
