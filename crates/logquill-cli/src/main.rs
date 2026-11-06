//! The `logquill` command: `tail`, `trace`, `dev`, `serve` and `verify`
//! (`docs/spec/06-cli-and-viewer.md`). The program itself is `logquill_cli::cli::run`.

use std::process::ExitCode;

fn main() -> ExitCode {
    ExitCode::from(logquill_cli::cli::run(std::env::args_os()))
}
