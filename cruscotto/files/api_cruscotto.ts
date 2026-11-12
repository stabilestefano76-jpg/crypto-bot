// CRUSCOTTO-INIZIO
export type CrusPos = {
  id: string; symbol: string; side: "long" | "short"; leverage: number; auto: boolean;
  fill_price: number; current_price: number; stop: number; target: number; liquidation: number | null;
  notional: number; margin: number; pnl: number; lots_left?: number; lots_total?: number; tp1_done: boolean; opened_ts?: number;
  to_stop_pct: number; to_target_pct: number; to_liq_pct: number | null; progress: number | null; r_now: number | null; move_pct: number;
};
export type CrusPart = {
  n: number; wins: number; pnl: number; win_rate: number | null; avg_win: number | null; avg_loss: number | null;
  profit_factor: number | null; expectancy: number | null;
};
export type Cruscotto = {
  portfolio: {
    capital0: number; equity: number; cash: number; margin: number; floating: number; realized: number;
    return_pct: number | null; today_pnl: number; max_drawdown_pct: number; curve: [number, number][];
  };
  exposure: { long: number; short: number; net: number; gross: number; gross_x_equity: number | null; n_long: number; n_short: number };
  risk: {
    open_risk: number; open_risk_limit: number; today_pnl: number; daily_limit: number; auto_today: number; pause: string | null;
    max_open: number; open_n: number; max_trades_day: number; daily_loss_pct: number; max_total_risk_pct: number;
  };
  stats: CrusPart & { long: CrusPart; short: CrusPart; best: number | null; worst: number | null };
  positions: CrusPos[];
  recent_closed: { symbol: string; side: string; pnl: number | null; reason?: string; closed_at?: string }[];
  bots: {
    desk_auto: boolean; scan_enabled: boolean; scan_at?: string | null; scan_top: string[];
    terminal: { installed: boolean; auto?: boolean; rigor?: string; qualified?: number | null; tested?: number | null; archive_at?: string | null; last_setups?: { symbol: string; side: string }[]; error?: string };
  };
  events: { at: string; symbol: string; text: string }[];
  settings: { capital: number; rr: number | null; can_change_capital: boolean };
  costs: { mode: "bybit" | "kraken"; fee_side_pct: number; round_trip_pct: number };
  kraken: { connected: boolean; error: string | null; total_eur: number | null; coins: { coin: string; qty: number }[]; masked: string | null };
  real: { connected: boolean; error: string | null; total_usd: number | null; coins: { coin: string; qty: number; usd: number }[]; note: string };
};
export type CrusConfronto = {
  kraken_ok: boolean; note: string;
  rows: { symbol: string; bybit: number | null; kraken_symbol: string | null; kraken: number | null; diff_pct: number | null; kraken_spread_pct: number | null; funding_day_pct: number | null }[];
};
export type CrusTrade = {
  id: string; symbol: string; side: "long" | "short"; leverage: number; auto: boolean; opened_at?: string; closed_at?: string; hold_h: number | null;
  entry: number | null; exit: number | null; stop: number | null; target: number | null; notional: number | null; margin: number | null;
  pnl: number; pnl_pct_margin: number | null; r: number | null; reason?: string | null; carry?: number | null;
};
export type CrusReport = {
  total: number; page: number; per: number; symbols: string[];
  summary: (CrusPart & { long: CrusPart; short: CrusPart; best: number | null; worst: number | null; max_loss_streak: number; avg_hold_h: number | null; carry_total: number; auto_n: number }) | null;
  by_symbol: { symbol: string; n: number; win_rate: number | null; pnl: number }[];
  by_reason: { reason: string; n: number; win_rate: number | null; pnl: number }[];
  by_day: { day: string; pnl: number }[];
  trades: CrusTrade[];
};
export const crusApi = {
  report: (q: { side?: string; esito?: string; symbol?: string; page?: number }) =>
    req<CrusReport>(`/cruscotto/report?side=${q.side ?? ""}&esito=${q.esito ?? ""}&symbol=${q.symbol ?? ""}&page=${q.page ?? 0}&per=30`),
  setSettings: (b: { capital?: number; rr?: number }) => req<{ ok: boolean }>("/cruscotto/impostazioni", { method: "POST", body: JSON.stringify(b) }),
  confronto: () => req<CrusConfronto>("/cruscotto/confronto"),
  get: () => req<Cruscotto>("/cruscotto"),
  krakenConnect: (api_key: string, api_secret: string) =>
    req<{ connected: boolean }>("/kraken/connect", { method: "POST", body: JSON.stringify({ api_key, api_secret }) }),
  setCosts: (mode: "bybit" | "kraken") => req<{ ok: boolean }>("/cruscotto/costs", { method: "POST", body: JSON.stringify({ mode }) }),
  krakenDisconnect: () => req<{ ok: boolean }>("/kraken/disconnect", { method: "POST" }),
};
// CRUSCOTTO-FINE
