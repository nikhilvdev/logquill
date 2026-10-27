//! Makes PyO3's build configuration visible to this crate, so `cfg(Py_GIL_DISABLED)` is true on
//! free-threaded Python builds (where the fast Python-to-Rust conversion is not used).

fn main() {
    pyo3_build_config::use_pyo3_cfgs();
}
