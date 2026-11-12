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
  costs: { mode: "bybit" | "kraken"; fee_side_pct: number; round_trip_pct: number };
  kraken: { connected: boolean; error: string | null; total_eur: number | null; coins: { coin: string; qty: number }[]; masked: string | null };
  real: { connected: boolean; error: string | null; total_usd: number | null; coins: { coin: string; qty: number; usd: number }[]; note: string };
};
export type CrusConfronto = {
  kraken_ok: boolean; note: string;
  rows: { symbol: string; bybit: number | null; kraken_symbol: string | null; kraken: number | null; diff_pct: number | null; kraken_spread_pct: number | null; funding_day_pct: number | null }[];
};
export const crusApi = {
  confronto: () => req<CrusConfronto>("/cruscotto/confronto"),
  get: () => req<Cruscotto>("/cruscotto"),
  krakenConnect: (api_key: string, api_secret: string) =>
    req<{ connected: boolean }>("/kraken/connect", { method: "POST", body: JSON.stringify({ api_key, api_secret }) }),
  setCosts: (mode: "bybit" | "kraken") => req<{ ok: boolean }>("/cruscotto/costs", { method: "POST", body: JSON.stringify({ mode }) }),
  krakenDisconnect: () => req<{ ok: boolean }>("/kraken/disconnect", { method: "POST" }),
};
// CRUSCOTTO-FINE
