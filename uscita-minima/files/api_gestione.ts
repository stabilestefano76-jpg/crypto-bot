// GESTIONE-INIZIO
export type MngView = {
  managed: boolean; danger: number | null; signals: string[]; action: string | null; reason: string | null;
  at: number | null; tightened: boolean; peak_r: number | null; orig_stop: number | null;
  fast_verdict?: "inversione" | "ritracciamento" | "incerto" | null; fast_why?: string[]; fast_at?: number | null; fast_action?: string | null;
};
export type MngConfig = { enabled: boolean; mode: "chiudi" | "stringi" | "avvisa"; every_min: number; fast: boolean; stile: "prudente" | "paziente"; regime: boolean; regime_exit: boolean; exit_min_r: number };
export type MngRegime = { ok: boolean; label: "rialzo" | "ribasso" | "incerto"; score: number; reasons: string[]; symbol: string; price: number } | null;
export type MngSummary = {
  n_done: number; n_pending: number; helped: number; hurt: number; net: number; net_uscita: number; net_stop: number; n_uscita: number; n_stop: number;
  net_regime?: number; n_regime?: number;
  prov_net?: number; n_prov?: number; prov_helped?: number; prov_hurt?: number;
  items: { symbol: string; side: string; kind: "uscita" | "stop_stretto" | "regime"; status: string; actual: number; shadow: number | null; delta: number | null; outcome?: string | null; at?: string; prov_delta?: number | null }[];
};
export const mngApi = {
  get: () => req<{ config: MngConfig; summary: MngSummary; regime?: MngRegime }>("/gestione"),
  set: (b: Partial<MngConfig>) => req<{ ok: boolean; config: MngConfig }>("/gestione", { method: "POST", body: JSON.stringify(b) }),
};
// GESTIONE-FINE
