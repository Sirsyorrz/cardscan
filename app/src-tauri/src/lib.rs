//! Runs the Python side of cardscan: the review server in the background, the scanner on demand,
//! and one-off jobs such as rebuilding the card data. From a checkout it runs the repo this app
//! lives in; the Windows installer instead bundles the scripts, uv and node, copies the scripts to
//! the app's data folder and installs Python there on first run.

use std::ffi::OsString;
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

const PYTHON_VERSION: &str = "3.14";

/// Where the Python side lives and how to start it.
pub struct Runtime {
    /// The scripts with their data/, captures/ and .venv.
    pub root: PathBuf,
    /// The installer's bundle: `app/` (the scripts), `uv.exe`, `node/` and `seed/cards.db`.
    /// None when running from a checkout.
    pub bundle: Option<PathBuf>,
}

impl Runtime {
    pub fn checkout() -> Runtime {
        Runtime { root: root(), bundle: None }
    }

    /// Brings `root` up to the bundled scripts. data/ is left alone apart from seeding the card
    /// database the first time.
    pub fn installed(bundle: PathBuf, root: PathBuf) -> std::io::Result<Runtime> {
        copy_dir(&bundle.join("app"), &root)?;
        let cards = root.join("data").join("cards.db");
        if !cards.exists() {
            std::fs::create_dir_all(root.join("data"))?;
            std::fs::copy(bundle.join("seed").join("cards.db"), &cards)?;
        }
        Ok(Runtime { root, bundle: Some(bundle) })
    }

    pub fn python(&self) -> PathBuf {
        if cfg!(windows) {
            self.root.join(".venv/Scripts/python.exe")
        } else {
            self.root.join(".venv/bin/python")
        }
    }

    fn py(&self, args: &[&str]) -> Command {
        let mut cmd = Command::new(self.python());
        // -u: line-buffered output, so events arrive as they happen
        cmd.arg("-u").args(args).current_dir(&self.root);
        // Windows pipes and files otherwise use the ANSI code page, which can't hold every card name
        cmd.env("PYTHONUTF8", "1");
        if let Some(bundle) = &self.bundle {
            // identify.py starts `node` for the matcher
            let mut path = OsString::from(bundle.join("node"));
            if let Some(rest) = std::env::var_os("PATH") {
                path.push(if cfg!(windows) { ";" } else { ":" });
                path.push(rest);
            }
            cmd.env("PATH", path);
        }
        no_window(&mut cmd);
        cmd
    }

    fn requirements_marker(&self) -> PathBuf {
        self.root.join(".venv").join("requirements.txt")
    }

    /// An installed copy whose Python is missing or was set up from other requirements.
    pub fn needs_setup(&self) -> bool {
        if self.bundle.is_none() {
            return false;
        }
        let wanted = std::fs::read(self.root.join("requirements.txt")).ok();
        !self.python().exists() || std::fs::read(self.requirements_marker()).ok() != wanted
    }

    /// Installs Python and the requirements into `root/.venv` with the bundled uv. Everything
    /// (interpreter, package cache) stays under `root`. Returns the exit code of the failing step.
    pub fn setup(&self, on_line: impl Fn(String) + Send + Sync + 'static) -> std::io::Result<i32> {
        let Some(bundle) = &self.bundle else { return Ok(0) };
        let on_line = std::sync::Arc::new(on_line);
        let uv = |args: &[&str]| {
            let mut cmd = Command::new(bundle.join(if cfg!(windows) { "uv.exe" } else { "uv" }));
            cmd.args(args)
                .current_dir(&self.root)
                .env("UV_PYTHON_INSTALL_DIR", self.root.join("python"))
                .env("UV_CACHE_DIR", self.root.join("uv-cache"))
                .env("UV_PYTHON_PREFERENCE", "only-managed")
                .env("NO_COLOR", "1");
            no_window(&mut cmd);
            cmd
        };
        if !self.python().exists() {
            on_line(format!("Installing Python {PYTHON_VERSION}"));
            let code =
                stream(uv(&["venv", "--clear", "--python", PYTHON_VERSION, ".venv"]), &on_line)?;
            if code != 0 {
                return Ok(code);
            }
        }
        on_line("Installing packages (about 2 GB to download)".into());
        let python = self.python();
        let args =
            ["pip", "install", "--python", &python.to_string_lossy(), "-r", "requirements.txt"];
        let code = stream(uv(&args), &on_line)?;
        if code == 0 {
            std::fs::copy(self.root.join("requirements.txt"), self.requirements_marker())?;
        }
        Ok(code)
    }
}

fn copy_dir(from: &Path, to: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(to)?;
    for entry in std::fs::read_dir(from)? {
        let entry = entry?;
        let dest = to.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir(&entry.path(), &dest)?;
        } else {
            std::fs::copy(entry.path(), dest)?;
        }
    }
    Ok(())
}

#[cfg(windows)]
fn no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    // a GUI app's console children otherwise each open a console window
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn no_window(_cmd: &mut Command) {}

/// Runs `cmd` to completion, passing every stdout and stderr line to `on_line`.
fn stream(
    mut cmd: Command,
    on_line: &std::sync::Arc<impl Fn(String) + Send + Sync + 'static>,
) -> std::io::Result<i32> {
    let mut child = cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()?;
    let stderr = child.stderr.take().expect("piped");
    let err_out = on_line.clone();
    let err_thread = std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            err_out(line);
        }
    });
    for line in BufReader::new(child.stdout.take().expect("piped")).lines().map_while(Result::ok) {
        on_line(line);
    }
    let _ = err_thread.join();
    Ok(child.wait()?.code().unwrap_or(-1))
}

/// Where the scanner writes its preview frame and the review server reads it.
pub fn live_path() -> PathBuf {
    std::env::temp_dir().join("cardscan-live.jpg")
}

pub struct Server {
    child: Child,
    pub port: u16,
}

impl Server {
    pub fn start(rt: &Runtime) -> std::io::Result<Server> {
        let port = TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
        let live = live_path();
        let child = rt
            .py(&[
                "review.py",
                "--port",
                &port.to_string(),
                "--live",
                &live.to_string_lossy(),
                "--with-parent",
            ])
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

/// Capture devices, one per camera. NVIDIA Broadcast's loopback is left out on purpose.
pub fn cameras(rt: &Runtime) -> Vec<Camera> {
    let mut out = list_cameras(rt);
    out.retain(|c| !c.name.contains("NVIDIA Broadcast"));
    out
}

/// Linux lists a metadata node beside each capture node; only index 0 is the picture.
#[cfg(not(windows))]
fn list_cameras(_rt: &Runtime) -> Vec<Camera> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir("/sys/class/video4linux") else {
        return out;
    };
    for e in entries.flatten() {
        let dir = e.path();
        let read = |f: &str| std::fs::read_to_string(dir.join(f)).unwrap_or_default();
        if read("index").trim() != "0" {
            continue;
        }
        let name = read("name").trim().to_string();
        let node = e.file_name().to_string_lossy().to_string();
        out.push(Camera { path: format!("/dev/{node}"), name });
    }
    out.sort_by_key(|c| c.path.trim_start_matches("/dev/video").parse::<u32>().unwrap_or(u32::MAX));
    out
}

/// DirectShow's camera order is only known through OpenCV, so cameras.py asks it.
#[cfg(windows)]
fn list_cameras(rt: &Runtime) -> Vec<Camera> {
    #[derive(serde::Deserialize)]
    struct Line {
        path: String,
        name: String,
    }
    let Ok(output) = rt.py(&["cameras.py"]).stderr(Stdio::null()).output() else {
        return Vec::new();
    };
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|l| serde_json::from_str::<Line>(l).ok())
        .map(|l| Camera { path: l.path, name: l.name })
        .collect()
}

pub struct ScanOptions {
    pub source: String,
    pub mode: String,
}

/// A running scan.py. Commands go in on stdin; `on_line` gets each stdout line (JSON events) and
/// `on_err` each stderr line.
pub struct Scan {
    child: Child,
    stdin: Option<ChildStdin>,
}

impl Scan {
    pub fn start(
        rt: &Runtime,
        opts: &ScanOptions,
        on_line: impl Fn(String) + Send + 'static,
        on_err: impl Fn(String) + Send + 'static,
    ) -> std::io::Result<Scan> {
        let live = live_path();
        let _ = std::fs::remove_file(&live);
        let live = live.to_string_lossy().to_string();
        let args = ["scan.py", &opts.source, "--control", "--preview", &live, "--mode", &opts.mode];
        let mut child = rt
            .py(&args)
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
pub fn run_job(rt: &Runtime, args: &[&str], on_line: impl Fn(String)) -> std::io::Result<i32> {
    let mut child = rt.py(args).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()?;
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
