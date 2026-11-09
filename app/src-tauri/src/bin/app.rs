#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex};
use std::time::Duration;

use cardscan::{Camera, Runtime, Scan, ScanOptions, Server};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};

struct App {
    rt: Arc<Runtime>,
    server: Mutex<Option<Server>>,
    scan: Arc<Mutex<Option<Scan>>>,
    job: Mutex<Option<String>>,
}

#[tauri::command]
async fn server_url(state: State<'_, App>) -> Result<String, String> {
    let port = {
        let mut server = state.server.lock().unwrap();
        if server.is_none() {
            *server = Some(Server::start(&state.rt).map_err(|e| e.to_string())?);
        }
        server.as_ref().unwrap().port
    };
    let ready = tauri::async_runtime::spawn_blocking(move || {
        let start = std::time::Instant::now();
        while start.elapsed() < Duration::from_secs(15) {
            if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        false
    })
    .await
    .unwrap_or(false);
    if !ready {
        // let the next call start a fresh one
        *state.server.lock().unwrap() = None;
        return Err(format!("review server didn't start; check {}", state.rt.python().display()));
    }
    Ok(format!("http://127.0.0.1:{port}"))
}

/// Opens a web page in the default browser; only https links, since the URL comes from the page.
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !url.starts_with("https://") {
        return Err("not an https link".into());
    }
    let opener = if cfg!(windows) {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    let mut child =
        std::process::Command::new(opener).arg(&url).spawn().map_err(|e| e.to_string())?;
    // reap it so it doesn't linger as a zombie
    std::thread::spawn(move || child.wait());
    Ok(())
}

#[tauri::command]
fn server_restart(state: State<'_, App>) {
    state.server.lock().unwrap().take();
}

#[tauri::command]
fn root_dir(state: State<'_, App>) -> String {
    state.rt.root.to_string_lossy().to_string()
}

#[tauri::command]
async fn cameras(state: State<'_, App>) -> Result<Vec<Camera>, String> {
    let rt = state.rt.clone();
    tauri::async_runtime::spawn_blocking(move || cardscan::cameras(&rt))
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn setup_needed(state: State<'_, App>) -> bool {
    state.rt.needs_setup()
}

#[tauri::command]
fn scan_running(state: State<'_, App>) -> bool {
    state.scan.lock().unwrap().is_some()
}

#[derive(Serialize, Clone)]
struct ExitEvent {
    code: i32,
}

#[tauri::command]
fn scan_start(
    app: AppHandle,
    state: State<'_, App>,
    source: String,
    mode: String,
) -> Result<(), String> {
    let mut slot = state.scan.lock().unwrap();
    if slot.is_some() {
        return Err("a scan is already running".into());
    }
    let opts = ScanOptions { source, mode };
    let (out, err) = (app.clone(), app.clone());
    let scan = Scan::start(
        &state.rt,
        &opts,
        move |line| match serde_json::from_str::<serde_json::Value>(&line) {
            Ok(v) => {
                let _ = out.emit("scan:event", v);
            }
            Err(_) => {
                let _ = out.emit("scan:log", line);
            }
        },
        move |line| {
            let _ = err.emit("scan:log", line);
        },
    )
    .map_err(|e| e.to_string())?;
    *slot = Some(scan);

    let watch = state.scan.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(250));
        let mut slot = watch.lock().unwrap();
        let Some(code) = slot.as_mut().and_then(|s| s.try_wait()) else {
            if slot.is_none() {
                return;
            }
            continue;
        };
        *slot = None;
        let _ = app.emit("scan:exit", ExitEvent { code });
        return;
    });
    Ok(())
}

#[tauri::command]
fn scan_mode(state: State<'_, App>, mode: String) {
    if let Some(s) = state.scan.lock().unwrap().as_mut() {
        s.send(&format!("mode {mode}"));
    }
}

#[tauri::command]
fn scan_stop(state: State<'_, App>) {
    if let Some(s) = state.scan.lock().unwrap().as_mut() {
        s.stop();
    }
}

#[derive(Serialize, Clone)]
struct JobLine {
    job: String,
    line: String,
}

#[derive(Serialize, Clone)]
struct JobDone {
    job: String,
    code: i32,
}

/// One-off jobs: `prices` refreshes prices, `cards` rebuilds data/cards.db (several minutes),
/// `setup` installs Python for an installed copy (first run, a large download).
#[tauri::command]
fn job_run(app: AppHandle, state: State<'_, App>, job: String) -> Result<(), String> {
    let args: &[&str] = match job.as_str() {
        "prices" => &["prices.py"],
        "cards" => &["tools/build_db.py"],
        "setup" => &[],
        _ => return Err(format!("unknown job {job}")),
    };
    {
        let mut running = state.job.lock().unwrap();
        if let Some(other) = running.as_ref() {
            return Err(format!("{other} is still running"));
        }
        *running = Some(job.clone());
    }
    let rt = state.rt.clone();
    std::thread::spawn(move || {
        let lines = app.clone();
        let name = job.clone();
        let on_line = move |line| {
            let _ = lines.emit("job:line", JobLine { job: name.clone(), line });
        };
        let code =
            if job == "setup" { rt.setup(on_line) } else { cardscan::run_job(&rt, args, on_line) }
                .unwrap_or(-1);
        *app.state::<App>().job.lock().unwrap() = None;
        let _ = app.emit("job:done", JobDone { job, code });
    });
    Ok(())
}

fn main() {
    // WebKitGTK's DMA-BUF renderer hard-crashes with a Wayland protocol error on
    // NVIDIA. Must be set before GTK initialises.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    tauri::Builder::default()
        .setup(|app| {
            // only the Windows bundle (tauri.windows.conf.json) has resources
            let bundle = app.path().resource_dir()?.join("cardscan");
            let rt = if bundle.join("app").join("scan.py").exists() {
                Runtime::installed(bundle, app.path().app_local_data_dir()?)?
            } else {
                Runtime::checkout()
            };
            app.manage(App {
                rt: Arc::new(rt),
                server: Mutex::new(None),
                scan: Arc::new(Mutex::new(None)),
                job: Mutex::new(None),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            server_url,
            open_url,
            server_restart,
            root_dir,
            cameras,
            scan_running,
            scan_start,
            scan_mode,
            scan_stop,
            job_run,
            setup_needed
        ])
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                let state = app.state::<App>();
                if let Some(mut scan) = state.scan.lock().unwrap().take() {
                    // give the scanner a moment to save what it has identified
                    scan.stop();
                    for _ in 0..40 {
                        if scan.try_wait().is_some() {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(100));
                    }
                    scan.kill();
                }
                state.server.lock().unwrap().take();
            }
        });
}
