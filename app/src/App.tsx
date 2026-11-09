import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { api, type Session } from "./api";
import { useStore, type LiveCard, type View } from "./state";
import ScanView from "./panels/ScanView";
import ReviewView from "./panels/ReviewView";
import CollectionView from "./panels/CollectionView";
import SearchView from "./panels/SearchView";
import BinderView from "./panels/BinderView";
import Settings from "./panels/Settings";
import { Updater } from "./panels/Updater";

export async function loadSessions() {
  const sessions = await api<Session[]>("/api/sessions");
  const s = useStore.getState();
  useStore.setState({
    sessions,
    reviewSession: sessions.some((x) => x.id === s.reviewSession) ? s.reviewSession : sessions[0]?.id ?? 0,
  });
}

type CardEvent = LiveCard & { event: "card"; repeat_of: number | null; entry_status: string; pending: number };
type ScanEvent =
  | { event: "started"; session: number | null; mode: string }
  | CardEvent
  | { event: "mode"; mode: string }
  | { event: "done"; added: number; review: number; repeat: number }
  | { event: "error"; message: string };

function onScanEvent(e: ScanEvent) {
  const s = useStore.getState();
  switch (e.event) {
    case "started":
      useStore.setState({ liveSession: e.session, live: [], pending: 0 });
      s.say(e.session ? `Scanning: session ${e.session}` : "Scanning");
      if (e.session) loadSessions();
      break;
    case "card": {
      const live = [...s.live];
      if (e.status === "repeat") {
        const i = live.findIndex((c) => c.n === e.repeat_of);
        if (i >= 0) live[i] = { ...live[i], ...pick(e), status: e.entry_status, repeats: live[i].repeats + 1 };
      } else {
        live.push({ ...pick(e), status: e.status, repeats: 0 });
      }
      useStore.setState({ live, pending: e.pending });
      break;
    }
    case "mode":
      s.setPrefs({ mode: e.mode });
      break;
    case "done":
      s.say(`Scan finished: ${e.added} added, ${e.review} to review, ${e.repeat} repeats`);
      loadSessions();
      break;
    case "error":
      s.say(e.message);
      break;
  }
}

function pick(e: CardEvent) {
  return {
    n: e.n,
    t: e.t,
    scan_id: e.scan_id,
    name: e.name,
    set_code: e.set_code,
    set_name: e.set_name,
    number: e.number,
    confidence: e.confidence,
  };
}

const VIEWS: { key: View; label: string }[] = [
  { key: "scan", label: "Scan" },
  { key: "review", label: "Review" },
  { key: "collection", label: "Collection" },
  { key: "binders", label: "Binders" },
  { key: "search", label: "Search" },
];

export default function App() {
  const base = useStore((s) => s.base);
  const view = useStore((s) => s.view);
  const message = useStore((s) => s.message);
  const sessions = useStore((s) => s.sessions);
  const reviewSession = useStore((s) => s.reviewSession);
  const scanning = useStore((s) => s.scanning);
  const stopping = useStore((s) => s.stopping);
  const liveSession = useStore((s) => s.liveSession);
  const [settings, setSettings] = useState(false);
  const [error, setError] = useState("");
  const [setupLine, setSetupLine] = useState<string | null>(null);
  const updateNonce = useStore((s) => s.updateNonce);
  const [updateChecked, setUpdateChecked] = useState(0);

  useEffect(() => {
    const connect = () =>
      invoke<string>("server_url")
        .then((b) => {
          useStore.setState({ base: b });
          return loadSessions();
        })
        .catch((e) => setError(String(e)));
    invoke<boolean>("setup_needed").then((needed) => {
      if (!needed) return connect();
      setSetupLine("Starting");
      invoke("job_run", { job: "setup" }).catch((e) => setError(String(e)));
    });
    invoke<boolean>("scan_running").then((r) => useStore.setState({ scanning: r }));
    let lastLog = "";
    const off = [
      listen<ScanEvent>("scan:event", (e) => onScanEvent(e.payload)),
      listen<string>("scan:log", (e) => {
        if (e.payload.trim()) lastLog = e.payload.trim();
      }),
      listen<{ job: string; line: string }>("job:line", (e) => {
        const line = e.payload.line.trim();
        if (!line) return;
        if (e.payload.job === "setup") setSetupLine(line);
        if (e.payload.job === "cards") useStore.getState().say(line);
      }),
      listen<{ job: string; code: number }>("job:done", async (e) => {
        if (e.payload.job === "setup") {
          if (e.payload.code !== 0) return setError(`Setup failed (exit code ${e.payload.code})`);
          setSetupLine(null);
          return connect();
        }
        if (e.payload.job !== "cards") return;
        if (e.payload.code !== 0) return useStore.getState().say("Card data update failed");
        // the server holds the old cards.db open; a new one attaches the rebuilt file
        await invoke("server_restart");
        useStore.setState({ base: await invoke<string>("server_url") });
        useStore.getState().say("Card data updated");
      }),
      listen<{ code: number }>("scan:exit", (e) => {
        useStore.setState({ scanning: false, stopping: false, pending: 0 });
        if (e.payload.code !== 0) useStore.getState().say(`Scanner stopped: ${lastLog || `exit code ${e.payload.code}`}`);
        loadSessions();
      }),
    ];
    return () => off.forEach((p) => p.then((f) => f()));
  }, []);

  // sessions change while a scan runs or after edits; cheap to re-read
  useEffect(() => {
    if (!base) return;
    const t = setInterval(() => !document.hidden && loadSessions().catch(() => {}), 4000);
    return () => clearInterval(t);
  }, [base]);

  if (error) {
    return (
      <div className="fatal">
        <h2>Couldn't start</h2>
        <p className="mono">{error}</p>
        <p className="dim">The app runs the Python scripts in the cardscan folder with its .venv.</p>
      </div>
    );
  }

  if (setupLine !== null) {
    return (
      <div className="fatal">
        <h2>Setting up CardScan</h2>
        <p>First run only: installing Python and the scanner's packages, about 2 GB to download.</p>
        <p className="mono dim">{setupLine}</p>
      </div>
    );
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">CardScan</div>
        {VIEWS.map((v) => (
          <div
            key={v.key}
            className={`navrow ${view === v.key ? "on" : ""}`}
            onClick={() => useStore.setState({ view: v.key })}
          >
            {v.label}
            {v.key === "scan" && scanning && <span className="dot live" />}
          </div>
        ))}
        <div className="sidebar-h">Sessions</div>
        <div className="sessions">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`navrow session ${view === "review" && reviewSession === s.id ? "on" : ""}`}
              onClick={() => useStore.setState({ view: "review", reviewSession: s.id })}
            >
              <span>
                #{s.id} <span className="dim">{new Date(s.started_at).toLocaleDateString()}</span>
              </span>
              <span className="counts">
                <span className="ok">{s.added ?? 0}</span>
                {(s.review ?? 0) > 0 && <span className="warn"> · {s.review}</span>}
              </span>
            </div>
          ))}
          {!sessions.length && <div className="pad dim">No scans yet</div>}
        </div>
        <div className="sidebar-foot">
          <button onClick={() => setSettings(true)}>Settings</button>
        </div>
      </aside>
      <main className="main">
        {!base ? (
          <div className="empty">Starting</div>
        ) : view === "scan" ? (
          <ScanView />
        ) : view === "review" ? (
          <ReviewView />
        ) : view === "search" ? (
          <SearchView />
        ) : view === "binders" ? (
          <BinderView />
        ) : (
          <CollectionView />
        )}
      </main>
      <footer className="statusbar">
        <Updater manual={updateNonce > updateChecked} onDone={() => setUpdateChecked(updateNonce)} />
        <span className="msg">{message}</span>
        <span className="spacer" />
        <span>
          {stopping
            ? "finishing scan"
            : scanning
              ? `scanning${liveSession ? ` · session ${liveSession}` : ""}`
              : "idle"}
        </span>
      </footer>
      {settings && <Settings onClose={() => setSettings(false)} />}
    </div>
  );
}
