//! The `logquill` command. Subcommands (`tail`, `trace`, `dev`, `serve`, `verify`) are
//! specified in `docs/spec/06-cli-and-viewer.md` and arrive in the CLI stage.

fn main() {
    println!("logquill {}", env!("CARGO_PKG_VERSION"));
}
