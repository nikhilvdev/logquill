//! The hash chain stays verifiable when many threads write through one shared chain.

use std::sync::{Arc, Mutex};
use std::thread;

use logquill_core::{verify_chain, Chain, SharedChain, GENESIS_HASH};
use serde_json::{json, Value};

#[test]
fn a_chain_shared_by_eight_writers_verifies_in_write_order() {
    let chain = Arc::new(SharedChain::new(Chain::default()));
    let written: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
    let handles: Vec<_> = (0..8)
        .map(|writer| {
            let (chain, written) = (chain.clone(), written.clone());
            thread::spawn(move || {
                for n in 0..250 {
                    let record = json!({
                        "schema_version": "2.0", "timestamp": "t", "level": "INFO", "logger": "l",
                        "message": format!("w{writer}-{n}"), "meta": {"writer": writer, "n": n},
                    });
                    // The sink runs inside the chain's critical section, so order is write order.
                    chain.write(record, |stamped| {
                        written.lock().unwrap().push(stamped.clone())
                    });
                }
            })
        })
        .collect();
    for handle in handles {
        handle.join().expect("a writer panicked");
    }
    let records = written.lock().unwrap();
    assert_eq!(records.len(), 2000);
    let result = verify_chain(records.iter(), GENESIS_HASH);
    assert!(result.ok, "{:?} at {:?}", result.reason, result.broken_at);
    assert_eq!(result.head_hash.as_deref(), Some(chain.head().as_str()));
}
