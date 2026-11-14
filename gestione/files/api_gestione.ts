// GESTIONE-INIZIO
export type MngView = {
  managed: boolean; danger: number | null; signals: string[]; action: string | null; reason: string | null;
  at: number | null; tightened: boolean; peak_r: number | null; orig_stop: number | null;
};
export type MngConfig = { enabled: boolean; mode: "chiudi" | "stringi" | "avvisa"; every_min: number };
export type MngSummary = {
  n_done: number; n_pending: number; helped: number; hurt: number; net: number; net_uscita: number; net_stop: number; n_uscita: number; n_stop: number;
  items: { symbol: string; side: string; kind: "uscita" | "stop_stretto"; status: string; actual: number; shadow: number | null; delta: number | null; outcome?: string | null; at?: string }[];
};
export const mngApi = {
  get: () => req<{ config: MngConfig; summary: MngSummary }>("/gestione"),
  set: (b: Partial<MngConfig>) => req<{ ok: boolean; config: MngConfig }>("/gestione", { method: "POST", body: JSON.stringify(b) }),
};
// GESTIONE-FINE
