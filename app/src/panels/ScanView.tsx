import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { MODES, url } from "../api";
import { useStore } from "../state";

type Camera = { path: string; name: string };

export default function ScanView() {
  const prefs = useStore((s) => s.prefs);
  const setPrefs = useStore((s) => s.setPrefs);
  const scanning = useStore((s) => s.scanning);
  const stopping = useStore((s) => s.stopping);
  const live = useStore((s) => s.live);
  const pending = useStore((s) => s.pending);
  const liveSession = useStore((s) => s.liveSession);
  const say = useStore((s) => s.say);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  // a new <img> per run, so the stream reconnects instead of showing the last run's final frame
  const [feed, setFeed] = useState(0);

  const refreshCameras = () =>
    invoke<Camera[]>("cameras").then((list) => {
      setCameras(list);
      const known = list.some((c) => c.path === prefs.source) || files.includes(prefs.source);
      if (!known && list.length) {
        // the phone's webcam mode, when it's plugged in, is what you want nearly every time
        const phone = list.find((c) => /pixel|android/i.test(c.name)) ?? list[0];
        setPrefs({ source: phone.path });
      }
    });

  useEffect(() => {
    refreshCameras();
  }, []);

  const start = async () => {
    if (!prefs.source) return say("Pick a camera first");
    try {
      const rec = await invoke<string | null>("scan_start", {
        source: prefs.source,
        mode: prefs.mode,
        record: prefs.record,
      });
      useStore.setState({ scanning: true, stopping: false, live: [], pending: 0 });
      setFeed((f) => f + 1);
      say(rec ? `Starting the scanner, recording to ${rec}` : "Starting the scanner");
    } catch (e) {
      say(String(e));
    }
  };

  const stop = () => {
    invoke("scan_stop");
    useStore.setState({ stopping: true });
    say("Finishing: identifying the cards still queued");
  };

  const setMode = (mode: string) => {
    setPrefs({ mode });
    if (scanning) invoke("scan_mode", { mode });
  };

  const openFile = async () => {
    const path = await invoke<string | null>("pick_recording");
    if (!path) return;
    setFiles((f) => (f.includes(path) ? f : [...f, path]));
    setPrefs({ source: path });
  };

  const added = live.filter((c) => c.status === "added").length;
  const review = live.filter((c) => c.status === "review").length;
  const repeats = live.reduce((n, c) => n + c.repeats, 0);

  return (
    <div className="view">
      <div className="toolbar">
        <select
          value={prefs.source}
          disabled={scanning}
          onChange={(e) => setPrefs({ source: e.target.value })}
          onFocus={refreshCameras}
        >
          {!cameras.length && !files.length && <option value="">No camera found</option>}
          {cameras.map((c) => (
            <option key={c.path} value={c.path}>
              {c.name} ({c.path})
            </option>
          ))}
          {files.map((f) => (
            <option key={f} value={f}>
              Recording: {f.split(/[\\/]/).pop()}
            </option>
          ))}
        </select>
        <button disabled={scanning} onClick={openFile}>
          Open recording
        </button>
        {scanning ? (
          <button className="danger" disabled={stopping} onClick={stop}>
            {stopping ? "Finishing" : "Stop"}
          </button>
        ) : (
          <button className="primary" onClick={start}>
            Start scanning
          </button>
        )}
        <span className="spacer" />
        <span className="dim">Finish</span>
        <div className="seg">
          {MODES.map((m) => (
            <button key={m.key} className={prefs.mode === m.key ? `on mode-${m.key}` : ""} onClick={() => setMode(m.key)}>
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <div className="scan-body">
        <div className="feed">
          {scanning ? (
            <img key={feed} src={url(`/live?run=${feed}`)} alt="" />
          ) : (
            <div className="empty">
              <p>Not scanning.</p>
              <p className="dim">
                Plug the phone in, choose "Webcam" in its USB options and switch to the back camera.
                <br />
                Then press Start scanning and drop cards onto the pile one at a time.
              </p>
              {liveSession && live.length > 0 && (
                <button onClick={() => useStore.setState({ view: "review", reviewSession: liveSession })}>
                  Review session {liveSession}
                </button>
              )}
            </div>
          )}
        </div>
        <div className="livelist">
          <div className="sidebar-h">
            This run: {added} added · {review} review · {repeats} repeats{pending > 0 ? ` · ${pending} queued` : ""}
          </div>
          <div className="rows">
            {[...live].reverse().map((c) => (
              <div key={c.n} className={`liverow st-${c.status}`}>
                <span className={`chip st-${c.status}`}>{c.status}</span>
                <span className="grow ellipsis">
                  {c.name ?? "?"}
                  <span className="dim"> {c.set_code ? `${c.set_code} #${c.number}` : ""}</span>
                </span>
                <span className="dim conf">{c.confidence ?? ""}</span>
              </div>
            ))}
            {!live.length && <div className="pad dim">Cards appear here as they're identified.</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
