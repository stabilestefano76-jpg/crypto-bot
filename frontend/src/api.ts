const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && j.detail) msg = String(j.detail);
    } catch {}
    throw new Error(msg);
  }
  return res.json();
}

export type Signal = {
  id: string;
  symbol: string;
  timeframe: string;
  side: "long" | "short";
  entry: number;
  stop_loss: number;
  take_profit: number;
  rr_ratio: number;
  confirmations: string[];
  strength: number;
  rsi_value: number;
  volume_ratio: number;
  created_at: string;
  fvg_top: number;
  fvg_bottom: number;
  atr?: number;
  atr_multiplier?: number;
  status: string;
  outcome?: string | null;
};

export type Config = {
  scan_interval_minutes: number;
  timeframes: string[];
  quote_filter: string;
  min_24h_volume_usdt: number;
  rsi_period: number;
  rsi_overbought: number;
  rsi_oversold: number;
  pivot_window: number;
  ema_fast: number;
  ema_slow: number;
  volume_ma_period: number;
  volume_spike_multiplier: number;
  require_volume_confirmation: boolean;
  require_ma_alignment: boolean;
  rr_ratio: number;
  sl_padding_pct: number;
  max_pairs_per_scan: number;
  enabled_pairs: string[];
  excluded_pairs: string[];
};

export type ScanState = {
  last_scan_at: string | null;
  last_scan_duration_s: number | null;
  last_scanned_pairs: number;
  last_signals_found: number;
  is_scanning: boolean;
};

export type Candle = { t: number; o: number; c: number; h: number; l: number; v: number };

export type PaperConfig = {
  initial_capital: number;
  risk_per_trade_pct: number;
  auto_execute: boolean;
  max_open_positions: number;
  trading_mode?: "spot" | "leverage";
  max_position_size_usdt: number;
  one_position_per_pair: boolean;
};

export type PaperPosition = {
  id: string;
  signal_id: string;
  symbol: string;
  timeframe: string;
  side: "long" | "short";
  entry: number;
  stop_loss: number;
  take_profit: number;
  quantity: number;
  risk_usdt: number;
  opened_at: string;
  current_price: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
};

export type PaperTrade = {
  id: string;
  signal_id: string;
  symbol: string;
  side: "long" | "short";
  entry: number;
  exit: number;
  quantity: number;
  pnl_usdt: number;
  pnl_pct: number;
  outcome: "win" | "loss";
  opened_at: string;
  closed_at: string;
};

export type Portfolio = {
  initial_capital: number;
  cash: number;
  equity: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_return_pct: number;
  open_positions_count: number;
  closed_trades_count: number;
  wins: number;
  losses: number;
  win_rate: number;
  auto_execute: boolean;
  trading_mode: "spot" | "leverage";
  positions: PaperPosition[];
};

export const api = {
  status: () => req<ScanState>("/status"),
  getConfig: () => req<Config>("/config"),
  saveConfig: (cfg: Config) =>
    req<Config>("/config", { method: "PUT", body: JSON.stringify(cfg) }),
  signals: (params: { side?: string; timeframe?: string; status?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.side) qs.set("side", params.side);
    if (params.timeframe) qs.set("timeframe", params.timeframe);
    if (params.status) qs.set("status", params.status);
    return req<{ signals: Signal[]; count: number }>(`/signals?${qs.toString()}`);
  },
  signal: (id: string) => req<Signal>(`/signals/${id}`),
  candles: (symbol: string, timeframe: string) =>
    req<{ symbol: string; timeframe: string; candles: Candle[]; rsi: number[] }>(
      `/candles/${encodeURIComponent(symbol)}?timeframe=${timeframe}`
    ),
  triggerScan: () => req<{ started: boolean }>("/scan", { method: "POST" }),
  historyStats: () =>
    req<{ total: number; active: number; wins: number; losses: number; win_rate: number }>(
      "/history/stats"
    ),
  paperConfig: () => req<PaperConfig>("/paper/config"),
  savePaperConfig: (cfg: PaperConfig) =>
    req<PaperConfig>("/paper/config", { method: "PUT", body: JSON.stringify(cfg) }),
  portfolio: () => req<Portfolio>("/paper/portfolio"),
  paperTrades: () => req<{ trades: PaperTrade[]; count: number }>("/paper/trades"),
  paperExecute: (signalId: string) =>
    req<{ position: PaperPosition }>(`/paper/execute/${signalId}`, { method: "POST" }),
  paperClose: (positionId: string) =>
    req<{ trade: PaperTrade }>(`/paper/positions/${positionId}/close`, { method: "POST" }),
  paperReset: () => req<{ ok: boolean; cash: number }>("/paper/reset", { method: "POST" }),
  setCapital: (amount: number) =>
    req<{ ok: boolean; initial_capital: number; cash: number }>("/paper/set-capital", {
      method: "POST",
      body: JSON.stringify({ initial_capital: amount }),
    }),
  setMode: (mode: "manual" | "auto") =>
    req<{ ok: boolean; mode: string; auto_execute: boolean }>("/paper/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  setTradingMode: (trading_mode: "spot" | "leverage") =>
    req<{ ok: boolean; trading_mode: string }>("/paper/trading-mode", {
      method: "POST",
      body: JSON.stringify({ trading_mode }),
    }),
  exchangeStatus: () =>
    req<{
      connected: boolean;
      exchange: string;
      api_key_masked?: string;
      usdt_balance?: number;
      connected_at?: string;
      error?: string;
    }>("/exchange/status"),
  exchangeConnect: (creds: { api_key: string; api_secret: string; api_passphrase: string }) =>
    req<{ connected: boolean; usdt_balance?: number; api_key_masked?: string }>(
      "/exchange/connect",
      { method: "POST", body: JSON.stringify(creds) }
    ),
  exchangeDisconnect: () =>
    req<{ ok: boolean }>("/exchange/disconnect", { method: "POST" }),
  slippageLog: () =>
    req<{
      logs: {
        id: string;
        symbol: string;
        side: string;
        signal_price: number;
        fill_price: number;
        slippage_usdt: number;
        slippage_pct: number;
        source: string;
        at: string;
      }[];
      count: number;
      total_abs_slippage_usdt: number;
      avg_slippage_pct: number;
    }>("/slippage/log"),
  feedStatus: () =>
    req<{ ws_connected: boolean; subscribed: string[]; cached_symbols: number }>(
      "/feed/status"
    ),
  stopDebugLog: () =>
    req<{
      logs: {
        id: string;
        symbol: string;
        side: string;
        entry: number;
        stop_loss: number;
        take_profit: number;
        atr_at_entry: number;
        stop_distance_in_atr: number | null;
        premature_status: string;
        would_hit_target: boolean | null;
        candles_to_target: number | null;
        closed_at: string;
      }[];
      count: number;
      premature: number;
      valid: number;
      pending: number;
      premature_rate: number;
      avg_stop_distance_atr: number;
    }>("/stop-debug/log"),
};
