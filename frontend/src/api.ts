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
  score?: number;
  max_score?: number;
  rsi_value: number;
  volume_ratio: number;
  created_at: string;
  fvg_top: number;
  fvg_bottom: number;
  atr?: number;
  atr_multiplier?: number;
  reversal_signals?: string[];
  strategy?: string;
  tp1?: number;
  tp2?: number;
  consolidation_high?: number;
  consolidation_low?: number;
  status: string;
  outcome?: string | null;
};

export type Config = {
  scan_interval_minutes: number;
  timeframes: string[];
  rsi_reversion_timeframes: string[];
  quote_filter: string;
  min_24h_volume_usdt: number;
  rsi_period: number;
  rsi_overbought: number;
  rsi_oversold: number;
  pivot_window: number;
  volume_ma_period: number;
  volume_spike_multiplier: number;
  rr_ratio: number;
  sl_padding_pct: number;
  atr_period: number;
  atr_sl_multiplier: number;
  min_rr_ratio: number;
  premature_lookahead: number;
  signal_validity_candles: number;
  fvg_lookback: number;
  reversal_rejection_wick_ratio: number;
  consolidation_min_candles: number;
  consolidation_max_atr: number;
  tp1_pct: number;
  post_tp1_advance_pct: number;
  exhaustion_min_score: number;
  rsi_divergence_check_enabled: boolean;
  trend_htf_check_enabled: boolean;
  exhaustion_check_enabled: boolean;
  exhaustion_lookback: number;
  trend_structure_strict: boolean;
  trailing_enabled: boolean;
  trailing_atr_mult: number;
  enabled_strategies?: string[];
  fvgr_tp1_pct: number;
  fvgr_tp2_pct: number;
  fvgr_post_tp1_advance_pct: number;
  fvgr_trailing_pct: number;
  fvgr_atr_sl_multiplier: number;
  fvgr_min_rr_ratio: number;
  rsi_rev_overbought: number;
  rsi_rev_oversold: number;
  rsi_rev_min_extreme_candles: number;
  rsi_rev_catastrophic_atr_mult: number;
  rsi_rev_min_rr_ratio: number;
  rsi_rev_structural_lookback: number;
  rsi_rev_trailing_atr_mult: number;
  rsi_rev_trailing_activation_margin_pct: number;
  max_pairs_per_scan: number;
  enabled_pairs: string[];
  excluded_pairs: string[];
  s3360_enabled: boolean;
  s3360_timeframes: string[];
  s3360_rsi_period: number;
  s3360_low_threshold: number;
  s3360_high_threshold: number;
  s3360_max_open_positions: number;
  s3360_max_daily_rise_pct: number;
  s3360_min_atr_pct: number;
  s3360_hold_below_entry: boolean;
  s3360_min_exit_gain_pct: number;
  xrp_acc_enabled: boolean;
  xrp_acc_timeframes: string[];
  xrp_acc_rsi_period: number;
  xrp_acc_low_threshold: number;
  xrp_acc_high_threshold: number;
  xrp_acc_hold_below_entry: boolean;
  xrp_acc_min_exit_gain_pct: number;
  sim_realistic_fills: boolean;
  sim_fallback_slippage_pct: number;
  regime_risk_reduction_pct: number;
  live_strategies: string[];
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
  breakeven_active?: boolean;
  trailing_active?: boolean;
  partial_closed?: boolean;
  current_stop?: number;
  strategy?: string;
  tp1?: number;
  tp2?: number;
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
  strategy?: string;
  timeframe?: string;
  stop_loss?: number;
  take_profit?: number;
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
  clearSignals: () =>
    req<{ ok: boolean; deleted: number }>("/signals", { method: "DELETE" }),
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
  addFunds: (amount: number) =>
    req<{ ok: boolean; cash: number; initial_capital: number }>("/paper/add-funds", {
      method: "POST",
      body: JSON.stringify({ amount }),
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
      usdc_balance?: number;
      connected_at?: string;
      error?: string;
    }>("/exchange/status"),
  exchangeConnect: (creds: { api_key: string; api_secret: string }) =>
    req<{ connected: boolean; usdt_balance?: number; usdc_balance?: number; api_key_masked?: string }>(
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


// ---------------------------------------------------------------------------
// Per-strategy fund allocation ("cross"/shared by default, isolated on
// request) for the traditional strategies. Only rsi_reversion is active now;
// the other two names stay in the type so records from them still type-check.
// ---------------------------------------------------------------------------
export type StrategyName = "counter_trend" | "fvg_reversal" | "rsi_reversion";

export type StrategyWalletInfo = {
  strategy: StrategyName;
  allocated: boolean;
  cash: number | null;
  open_positions: number;
};

export type StrategyWalletsStatus = {
  shared_cash: number;
  strategies: StrategyWalletInfo[];
};

export type StrategyPortfolio = {
  strategy: StrategyName;
  wallet_type: "isolated" | "shared";
  cash: number;
  equity: number;
  unrealized_pnl: number;
  realized_pnl: number;
  open_positions: PaperPosition[];
  closed_trades: PaperTrade[];
  open_count: number;
  closed_count: number;
  win_rate: number;
};

export const strategyWalletApi = {
  status: () => req<StrategyWalletsStatus>("/strategy-wallets"),
  allocate: (strategy: StrategyName, amount: number) =>
    req<{ ok: boolean; strategy: string; cash: number; main_cash: number }>(
      `/strategy-wallets/${strategy}/allocate`,
      { method: "POST", body: JSON.stringify({ amount }) }
    ),
  withdraw: (strategy: StrategyName, amount: number) =>
    req<{ ok: boolean; strategy: string; cash: number; main_cash: number }>(
      `/strategy-wallets/${strategy}/withdraw`,
      { method: "POST", body: JSON.stringify({ amount }) }
    ),
  deallocate: (strategy: StrategyName) =>
    req<{ ok: boolean; strategy: string; main_cash: number }>(
      `/strategy-wallets/${strategy}/deallocate`,
      { method: "POST" }
    ),
};

export const strategyApi = {
  portfolio: (strategy: StrategyName) =>
    req<StrategyPortfolio>(`/strategy/${strategy}/portfolio`),
  reset: (strategy: StrategyName) =>
    req<{ ok: boolean; strategy: string }>(`/strategy/${strategy}/reset`, {
      method: "POST",
    }),
};

// ---------------------------------------------------------------------------
// Unified events feed (all 5 sections, chronological) — powers the Eventi tab
// ---------------------------------------------------------------------------
export type BotEvent = {
  id: string;
  type: "open" | "close";
  section: "counter_trend" | "fvg_reversal" | "rsi_reversion";
  symbol: string;
  side?: "long" | "short";
  pnl_usdt?: number | null;
  at: string;
};

export const eventsApi = {
  list: (limit: number = 100) =>
    req<{ events: BotEvent[]; count: number }>(`/events?limit=${limit}`),
};

// ---------------------------------------------------------------------------
// 33/60 (RSI crosses down through 35 -> exit once RSI reaches 60, or on
// stop-loss / timeout if it never gets there)
// ---------------------------------------------------------------------------
export type S3360Position = {
  id: string;
  symbol: string;
  timeframe?: string;
  side: "long";
  entry: number;
  quantity: number;
  notional: number;
  rsi_at_entry?: number;
  status: string;
  opened_at: string;
  current_price?: number;
  unrealized_pnl?: number;
  close_price?: number;
  close_reason?: string;
  pnl_usdt?: number;
  closed_at?: string;
  fill_price?: number;
  fill_model?: string;
  entry_cost_pct?: number | null;
  spread_pct?: number | null;
  exit_level?: number;
  pct_to_exit_level?: number | null;
  close_last_price?: number;
  exit_cost_pct?: number | null;
};

export type S3360Portfolio = {
  cash: number;
  equity: number;
  total_transferred_in: number;
  unrealized_pnl: number;
  realized_pnl: number;
  open_positions: S3360Position[];
  closed_positions: S3360Position[];
  open_count: number;
  closed_count: number;
  win_rate: number;
  rsi_low_threshold?: number;
  rsi_target?: number;
  hold_below_entry?: boolean;
  min_exit_gain_pct?: number;
};

export const s3360Api = {
  portfolio: () => req<S3360Portfolio>("/s3360/portfolio"),
  deposit: (amount: number) =>
    req<{ ok: boolean; s3360_cash: number; main_cash: number }>(
      "/s3360/deposit",
      { method: "POST", body: JSON.stringify({ amount }) }
    ),
  withdraw: (amount: number) =>
    req<{ ok: boolean; s3360_cash: number; main_cash: number }>(
      "/s3360/withdraw",
      { method: "POST", body: JSON.stringify({ amount }) }
    ),
  reset: () => req<{ ok: boolean }>("/s3360/reset", { method: "POST" }),
};

// ---------------------------------------------------------------------------
// XRP Accumulation (buy ALL capital on RSI<=25, sell all on RSI>=60 — only
// the profit converts to permanent XRP, the original capital cycles again)
// ---------------------------------------------------------------------------
export type XrpAccPosition = {
  id: string;
  symbol: string;
  timeframe?: string;
  side: "long";
  entry: number;
  quantity: number;
  notional: number;
  rsi_at_entry?: number;
  status: string;
  opened_at: string;
  current_price?: number;
  unrealized_pnl?: number;
  close_price?: number;
  close_reason?: string;
  profit_usdt?: number;
  profit_xrp?: number;
  closed_at?: string;
  fill_price?: number;
  fill_model?: string;
  entry_cost_pct?: number | null;
  spread_pct?: number | null;
  exit_level?: number;
  pct_to_exit_level?: number | null;
  close_last_price?: number;
  exit_cost_pct?: number | null;
};

export type XrpAccPortfolio = {
  cash: number;
  permanent_xrp: number;
  total_xrp: number;
  xrp_price: number;
  equity_usdt: number;
  total_transferred_in: number;
  total_profit_xrp: number;
  open_positions: XrpAccPosition[];
  closed_positions: XrpAccPosition[];
  open_count: number;
  closed_count: number;
  rsi_low_threshold?: number;
  rsi_target?: number;
  hold_below_entry?: boolean;
  min_exit_gain_pct?: number;
};

export const xrpAccApi = {
  portfolio: () => req<XrpAccPortfolio>("/xrp-accumulation/portfolio"),
  deposit: (amount: number) =>
    req<{ ok: boolean; xrp_acc_cash: number; main_cash: number }>(
      "/xrp-accumulation/deposit",
      { method: "POST", body: JSON.stringify({ amount }) }
    ),
  withdraw: (amount: number) =>
    req<{ ok: boolean; xrp_acc_cash: number; main_cash: number }>(
      "/xrp-accumulation/withdraw",
      { method: "POST", body: JSON.stringify({ amount }) }
    ),
  reset: () => req<{ ok: boolean }>("/xrp-accumulation/reset", { method: "POST" }),
};

// ---------------------------------------------------------------------------
// RSI Reversion — one of the 3 traditional strategies. Its isolated wallet
// was already fully supported by the generic strategyWalletApi above (built
// in an earlier session) — this is just a thin, fixed-strategy convenience
// wrapper for the dedicated RSI Reversion screen, not new infrastructure.
// ---------------------------------------------------------------------------
const RSI_REVERSION_STRATEGY: StrategyName = "rsi_reversion";

export const rsiReversionApi = {
  portfolio: () => strategyApi.portfolio(RSI_REVERSION_STRATEGY),
  deposit: (amount: number) => strategyWalletApi.allocate(RSI_REVERSION_STRATEGY, amount),
  withdraw: (amount: number) => strategyWalletApi.withdraw(RSI_REVERSION_STRATEGY, amount),
  reset: () => strategyApi.reset(RSI_REVERSION_STRATEGY),
};
