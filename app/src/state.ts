import { create } from "zustand";
import type { Session } from "./api";

export type View = "scan" | "review" | "collection";

/** One identified capture of the running scan, as scan.py reports it. Repeats update the card they repeat. */
export type LiveCard = {
  n: number;
  t: number;
  scan_id: number | null;
  status: string;
  name: string | null;
  set_code: string | null;
  set_name: string | null;
  number: string | null;
  confidence: string | null;
  repeats: number;
};

type Prefs = { source: string; mode: string; record: boolean };

const PREFS_KEY = "cardscan.prefs";

function loadPrefs(): Prefs {
  const fallback = { source: "", mode: "normal", record: false };
  try {
    return { ...fallback, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") };
  } catch {
    return fallback;
  }
}

type Store = {
  base: string;
  view: View;
  message: string;
  sessions: Session[];
  reviewSession: number;
  prefs: Prefs;
  scanning: boolean;
  stopping: boolean;
  liveSession: number | null;
  live: LiveCard[];
  pending: number;
  setPrefs: (p: Partial<Prefs>) => void;
  say: (message: string) => void;
};

export const useStore = create<Store>((set, get) => ({
  base: "",
  view: "scan",
  message: "",
  sessions: [],
  reviewSession: 0,
  prefs: loadPrefs(),
  scanning: false,
  stopping: false,
  liveSession: null,
  live: [],
  pending: 0,
  setPrefs: (p) => {
    const prefs = { ...get().prefs, ...p };
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    set({ prefs });
  },
  say: (message) => set({ message }),
}));
