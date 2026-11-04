import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { useStore } from "../state";

export default function Settings({ onClose }: { onClose: () => void }) {
  const prefs = useStore((s) => s.prefs);
  const setPrefs = useStore((s) => s.setPrefs);
  const say = useStore((s) => s.say);
  const [root, setRoot] = useState("");

  useEffect(() => {
    invoke<string>("root_dir").then(setRoot);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const run = async (job: string, start: string) => {
    try {
      await invoke("job_run", { job });
      say(start);
      onClose();
    } catch (e) {
      say(String(e));
    }
  };

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="sidebar-h">Scanning</div>
        <div className="setrow">
          <div>
            Record scans
            <div className="dim">
              Save the camera feed of each scan to recordings/ so it can be replayed later (about 2 GB an hour).
            </div>
          </div>
          <button className={prefs.record ? "on" : ""} onClick={() => setPrefs({ record: !prefs.record })}>
            {prefs.record ? "On" : "Off"}
          </button>
        </div>
        <div className="sidebar-h">Data</div>
        <div className="setrow">
          <div>
            Refresh prices
            <div className="dim">TCGplayer prices for everything in the collection, and the US$ to A$ rate.</div>
          </div>
          <button onClick={() => run("prices", "Refreshing prices")}>Refresh</button>
        </div>
        <div className="setrow">
          <div>
            Update card data
            <div className="dim">
              Rebuild the card database from Scryfall, TCGdex and TCGplayer, for new sets. Takes several minutes.
            </div>
          </div>
          <button onClick={() => run("cards", "Updating card data")}>Update</button>
        </div>
        <div className="sidebar-h">About</div>
        <div className="setrow">
          <div>
            Project folder
            <div className="dim mono">{root}</div>
          </div>
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
