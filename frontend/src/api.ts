const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
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
};
