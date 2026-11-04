//! Drives the real scanner on a recording. Needs the Python setup and a recording, so it only runs
//! when asked: `CARDSCAN_TEST_VIDEO=/tmp/tray3.mkv cargo test -- --ignored`.

use std::sync::mpsc;
use std::time::{Duration, Instant};

use cardscan::{Scan, ScanOptions};

#[test]
#[ignore]
fn replay_reports_cards_and_takes_commands() {
    let video = std::env::var("CARDSCAN_TEST_VIDEO").expect("set CARDSCAN_TEST_VIDEO");
    let db = std::env::temp_dir().join(format!("cardscan-test-{}.db", std::process::id()));
    // the scanner inherits this, so the real collection is never touched
    std::env::set_var("CARDSCAN_COLLECTION", &db);

    let (tx, rx) = mpsc::channel::<serde_json::Value>();
    let opts = ScanOptions { source: video, mode: "normal".into() };
    let mut scan = Scan::start(
        &cardscan::root(),
        &opts,
        move |line| {
            if let Ok(v) = serde_json::from_str(&line) {
                let _ = tx.send(v);
            }
        },
        |_| {},
    )
    .expect("scan.py starts");

    let mut events = Vec::new();
    let start = Instant::now();
    let mut switched = false;
    while start.elapsed() < Duration::from_secs(180) {
        let Ok(e) = rx.recv_timeout(Duration::from_millis(200)) else {
            if scan.try_wait().is_some() {
                break;
            }
            continue;
        };
        if e["event"] == "card" && !switched {
            scan.send("mode foil");
            switched = true;
        }
        let done = e["event"] == "done";
        events.push(e);
        if done {
            break;
        }
    }
    let _ = std::fs::remove_file(&db);
    let kinds: Vec<&str> = events.iter().filter_map(|e| e["event"].as_str()).collect();
    assert_eq!(kinds.first(), Some(&"started"), "{kinds:?}");
    assert!(kinds.contains(&"mode"), "the mode command was acknowledged: {kinds:?}");
    let added = events.iter().filter(|e| e["event"] == "card" && e["status"] == "added").count();
    assert!(added >= 10, "only {added} cards added");
    assert_eq!(kinds.last(), Some(&"done"));
}
