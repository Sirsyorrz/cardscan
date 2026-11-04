//! Runs the Python side of cardscan (the repo this app lives in): the review server in the
//! background, the scanner on demand, and one-off jobs such as rebuilding the card data.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};

use serde::Serialize;

/// The cardscan checkout: `CARDSCAN_DIR`, else the repo this binary was built from.
pub fn root() -> PathBuf {
    if let Some(dir) = std::env::var_os("CARDSCAN_DIR") {
        return PathBuf::from(dir);
    }
    let built = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    built.canonicalize().unwrap_or(built)
}

pub fn python(root: &Path) -> PathBuf {
    if cfg!(windows) {
        root.join(".venv/Scripts/python.exe")
    } else {
        root.join(".venv/bin/python")
    }
}

/// Where the scanner writes its preview frame and the review server reads it.
pub fn live_path() -> PathBuf {
    std::env::temp_dir().join("cardscan-live.jpg")
}

fn py(root: &Path, args: &[&str]) -> Command {
    let mut cmd = Command::new(python(root));
    // -u: line-buffered output, so events arrive as they happen
    cmd.arg("-u").args(args).current_dir(root);
    cmd
}

pub struct Server {
    child: Child,
    pub port: u16,
}

impl Server {
    pub fn start(root: &Path) -> std::io::Result<Server> {
        let port = TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
        let live = live_path();
        let child = py(
            root,
            &[
                "review.py",
                "--port",
                &port.to_string(),
                "--live",
                &live.to_string_lossy(),
                "--with-parent",
            ],
        )
        .stdout(Stdio::null())
        .spawn()?;
        Ok(Server { child, port })
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Serialize, Clone)]
pub struct Camera {
    pub path: String,
    pub name: String,
}

/// Capture devices, one per camera. Linux lists a metadata node beside each capture node; only
/// index 0 is the picture. NVIDIA Broadcast's loopback is left out on purpose.
pub fn cameras() -> Vec<Camera> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir("/sys/class/video4linux") else {
        return out;
    };
    for e in entries.flatten() {
        let dir = e.path();
        let read = |f: &str| std::fs::read_to_string(dir.join(f)).unwrap_or_default();
        let name = read("name").trim().to_string();
        if read("index").trim() != "0" || name.contains("NVIDIA Broadcast") {
            continue;
        }
        let node = e.file_name().to_string_lossy().to_string();
        out.push(Camera { path: format!("/dev/{node}"), name });
    }
    out.sort_by_key(|c| c.path.trim_start_matches("/dev/video").parse::<u32>().unwrap_or(u32::MAX));
    out
}

pub struct ScanOptions {
    pub source: String,
    pub mode: String,
    pub record: Option<String>,
}

/// A running scan.py. Commands go in on stdin; `on_line` gets each stdout line (JSON events) and
/// `on_err` each stderr line.
pub struct Scan {
    child: Child,
    stdin: Option<ChildStdin>,
}

impl Scan {
    pub fn start(
        root: &Path,
        opts: &ScanOptions,
        on_line: impl Fn(String) + Send + 'static,
        on_err: impl Fn(String) + Send + 'static,
    ) -> std::io::Result<Scan> {
        let live = live_path();
        let _ = std::fs::remove_file(&live);
        let live = live.to_string_lossy().to_string();
        let mut args =
            vec!["scan.py", &opts.source, "--control", "--preview", &live, "--mode", &opts.mode];
        if let Some(r) = &opts.record {
            args.extend(["--record", r]);
        }
        let mut child = py(root, &args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        let stdout = child.stdout.take().expect("piped");
        let stderr = child.stderr.take().expect("piped");
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                on_line(line);
            }
        });
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                on_err(line);
            }
        });
        let stdin = child.stdin.take();
        Ok(Scan { child, stdin })
    }

    pub fn send(&mut self, command: &str) {
        if let Some(stdin) = &mut self.stdin {
            let _ = writeln!(stdin, "{command}");
            let _ = stdin.flush();
        }
    }

    /// Asks the scanner to finish: it stops reading the camera, identifies what's queued and
    /// writes the session out, so this can take a few seconds.
    pub fn stop(&mut self) {
        self.send("quit");
        self.stdin = None;
    }

    pub fn try_wait(&mut self) -> Option<i32> {
        self.child.try_wait().ok().flatten().map(|s| s.code().unwrap_or(-1))
    }

    pub fn kill(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Runs a script to completion, passing every output line to `on_line`. Returns the exit code.
pub fn run_job(root: &Path, args: &[&str], on_line: impl Fn(String)) -> std::io::Result<i32> {
    let mut child = py(root, args).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()?;
    let stderr = child.stderr.take().expect("piped");
    let err_lines = std::thread::spawn(move || {
        BufReader::new(stderr).lines().map_while(Result::ok).collect::<Vec<_>>()
    });
    for line in BufReader::new(child.stdout.take().expect("piped")).lines().map_while(Result::ok) {
        on_line(line);
    }
    let code = child.wait()?.code().unwrap_or(-1);
    if code != 0 {
        if let Ok(lines) = err_lines.join() {
            if let Some(last) = lines.iter().rev().find(|l| !l.trim().is_empty()) {
                on_line(last.clone());
            }
        }
    }
    Ok(code)
}
