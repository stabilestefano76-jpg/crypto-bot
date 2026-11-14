import { getAccessKey, handleUnauthorized } from "./auth";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const accessKey = await getAccessKey();
  const res = await fetch(`${BASE}/api${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(accessKey ? { "X-Access-Key": accessKey } : {}),
      ...((opts.headers as Record<string, string>) || {}),
    },
  });
  if (res.status === 401) {
    // Access code missing or no longer valid: forget it and open the login.
    await handleUnauthorized();
  }
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
  s3360_entry_mode: string;
  s3360_confirm_level: number;
  s3360_confirm_lookback: number;
  s3360_confirm_ma_period: number;
  s3360_confirm_closed_only: boolean;
  s3360_high_threshold: number;
  s3360_max_open_positions: number;
  s3360_max_daily_rise_pct: number;
  trend_filter_timeframe: string;
  trend_filter_ema_fast: number;
  trend_filter_ema_slow: number;
  s3360_require_coin_trend: boolean;
  trend_filter_long_timeframe: string;
  trend_filter_ema_long: number;
  s3360_require_above_long_ema: boolean;
  xrp_acc_require_above_long_ema: boolean;
  s3360_require_btc_trend: boolean;
  xrp_acc_require_coin_trend: boolean;
  xrp_acc_require_btc_trend: boolean;
  s3360_min_atr_pct: number;
  s3360_hold_below_entry: boolean;
  s3360_min_net_profit_pct: number;
  s3360_trailing_enabled: boolean;
  s3360_trailing_pct: number;
  s3360_rsi_giveback_enabled: boolean;
  s3360_rsi_arm: number;
  s3360_rsi_giveback: number;
  s3360_rsi_arm_closed_only: boolean;
  s3360_post_target_trailing_enabled: boolean;
  s3360_post_target_trailing_pct: number;
  s3360_post_target_trailing_timeframes: string[];
  s3360_rsi_giveback_timeframes: string[];
  xrp_acc_enabled: boolean;
  xrp_acc_timeframes: string[];
  xrp_acc_rsi_period: number;
  xrp_acc_low_threshold: number;
  xrp_acc_entry_mode: string;
  xrp_acc_confirm_level: number;
  xrp_acc_confirm_lookback: number;
  xrp_acc_confirm_ma_period: number;
  xrp_acc_confirm_closed_only: boolean;
  xrp_acc_high_threshold: number;
  xrp_acc_hold_below_entry: boolean;
  xrp_acc_min_net_profit_pct: number;
  xrp_acc_trailing_enabled: boolean;
  xrp_acc_trailing_pct: number;
  sim_realistic_fills: boolean;
  sim_fallback_slippage_pct: number;
  sim_fee_pct: number;
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
  fee_pct?: number;
  exit_cost_pct?: number | null;
  trailing_active?: boolean;
  peak_price?: number;
  trailing_stop?: number;
  break_even_price?: number;
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
  min_net_profit_pct?: number;
  fee_pct?: number;
  trailing_enabled?: boolean;
  trailing_pct?: number;
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
  fee_pct?: number;
  exit_cost_pct?: number | null;
  trailing_active?: boolean;
  peak_price?: number;
  trailing_stop?: number;
  break_even_price?: number;
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
  min_net_profit_pct?: number;
  fee_pct?: number;
  trailing_enabled?: boolean;
  trailing_pct?: number;
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


// ---------------------------------------------------------------------------
// Dettaglio di una singola operazione (33/60 o XRP): percorso del prezzo
// registrato dal bot, livelli di uscita, punto più basso e più alto.
// ---------------------------------------------------------------------------
export type PositionTrack = {
  kind: string;
  id: string;
  symbol: string;
  timeframe?: string | null;
  status?: string;
  opened_at?: string;
  closed_at?: string | null;
  entry: number;
  fill_price: number;
  quantity?: number;
  notional?: number;
  rsi_at_entry?: number;
  current_price?: number | null;
  pct_vs_entry?: number | null;
  close_price?: number | null;
  close_reason?: string | null;
  pnl_usdt?: number | null;
  profit_xrp?: number | null;
  fee_pct?: number;
  levels: {
    entry: number;
    break_even: number;
    exit_level: number;
    trailing_stop: number | null;
    rsi_target: number;
    peak_price?: number;
  };
  stats: {
    min_price: number | null;
    min_at?: string | null;
    max_price: number | null;
    max_at?: string | null;
    max_drawdown_pct: number | null;
    max_gain_pct: number | null;
    time_below_entry_s: number;
    age_s: number;
    samples: number;
  };
  samples: [number, number][];
};

export const trackApi = {
  get: (kind: string, id: string) =>
    req<PositionTrack>(`/positions/${kind}/${encodeURIComponent(id)}/track`),
};

// ---- Trading reale ----
export type RealLogRow = { at: string; level: string; text: string };
export type RealStatus = {
  enabled: boolean;
  live_strategies: string[];
  max_order: number;
  max_total: number;
  daily_loss_limit: number;
  daily_pnl: number;
  exposure: number;
  password_active: boolean;
  key_connected: boolean;
  open_positions: { symbol: string; notional: number; fill_price: number; quantity: number; opened_at: string }[];
  log: RealLogRow[];
};
export type RealTestResult = {
  symbol: string;
  comprato_a: number;
  venduto_a: number;
  speso: number;
  incassato: number;
  costo_totale: number;
  costo_pct: number;
};
export type RealDiagnosi = {
  motivi: Record<string, { motivo: string; volte: number }[]>;
  rsi_ore: { ora: string; tf: string; rsi_min: number | null; ultimo: number | null }[];
  xrp_acc: Record<string, any>;
  s3360: Record<string, any>;
};
// ---- ICT (paper, con interruttore reale) ----
export type IctPosition = {
  id: string; symbol: string; timeframe: string; fill_price: number; stop: number; target: number;
  risk_pct: number; rr: number; quantity: number; notional: number; opened_at: string;
  current_price?: number; floating_pnl?: number; is_real: boolean;
  status?: string; close_price?: number; close_reason?: string; pnl_usdt?: number; closed_at?: string;
};
export type IctPortfolio = {
  enabled: boolean; mode: "paper" | "real"; real_switch_on: boolean;
  settings: { ict_enabled: boolean; ict_symbols: string[]; ict_timeframe: string; ict_rr: number; ict_kill_zones: boolean; ict_alloc_pct: number; ict_max_open: number; ict_max_hold_h: number };
  cash: number; start_capital: number; equity: number; unrealized: number; realized: number;
  trades: number; win_rate: number | null;
  open_positions: IctPosition[]; closed_positions: IctPosition[];
  watching: Record<string, { setups: number; price: number; fvg_top: number | null; stop: number | null }>;
  last_scan: string | null; disclaimer: string;
};
export const ictApi = {
  portfolio: () => req<IctPortfolio>("/ict/portfolio"),
  save: (patch: Record<string, unknown>) =>
    req<Record<string, unknown>>("/ict/settings", { method: "PUT", body: JSON.stringify(patch) }),
  reset: () => req<{ ok: boolean }>("/ict/reset", { method: "POST" }),
};

export type S3360Signal = { at: string; symbol: string; tf: string; rsi: number; price: number; outcome: string };
export const signalsApi = {
  s3360: () => req<{ rows: S3360Signal[] }>("/s3360/segnali"),
};

export type ManualSetup = { symbol: string; entry: number | null; stop: number; target: number; amount: number; risk: number | null };
export type ManualPlan = {
  errors: string[]; warnings: string[]; market?: boolean; entry_ref?: number; qty?: number;
  risk_pct?: number; gain_pct?: number; loss_net?: number; gain_net?: number; rr?: number | null;
  fees_roundtrip_pct?: number; breakeven?: number;
};
export type ManualParse =
  | { ok: false; error: string }
  | { ok: true; setup: ManualSetup; price: number; plan: ManualPlan; real: { available: boolean; why: string | null } };
export type ManualRow = {
  id: string; symbol: string; mode: "paper" | "real"; status: string; entry?: number | null; fill_price?: number;
  stop: number; target: number; amount: number; current_price?: number | null; floating_pnl?: number;
  pnl_usdt?: number; close_price?: number; close_reason?: string; created_at?: string; closed_at?: string;
};
export type ManualList = { waiting: ManualRow[]; open: ManualRow[]; closed: ManualRow[]; trades: number; wins: number; realized: number };
export const manualApi = {
  parse: (text: string) => req<ManualParse>("/manual/parse", { method: "POST", body: JSON.stringify({ text }) }),
  create: (setup: ManualSetup, mode: "paper" | "real") =>
    req<{ ok: boolean; status: string; id: string }>("/manual/create", { method: "POST", body: JSON.stringify({ setup, mode, confirm: true }) }),
  list: () => req<ManualList>("/manual/list"),
  cancel: (id: string) => req<{ ok: boolean }>("/manual/cancel/" + id, { method: "POST" }),
  close: (id: string) => req<{ ok: boolean }>("/manual/close/" + id, { method: "POST" }),
};

export type MaConfig = {
  enabled: boolean; kind: "SMA" | "EMA"; tf: "1h" | "4h" | "1d"; fast: number; slow: number;
  amount: number; max_positions: number; stop_pct: number; symbols: string[];
};
export type MaCoin = {
  symbol: string; in_position: boolean; price: number | null; fast?: number; slow?: number;
  above?: boolean; dist_pct?: number; cross?: "up" | "down" | null; note?: string;
};
export type MaRow = {
  id: string; symbol: string; status: string; timeframe: string; kind: string; fast: number; slow: number;
  fill_price: number; notional: number; current_price?: number; floating_pnl?: number;
  pnl_usdt?: number; close_price?: number; close_reason?: string; opened_at?: string; closed_at?: string;
};
export type MaStatus = {
  config: MaConfig; coins: MaCoin[]; open: MaRow[]; closed: MaRow[];
  events: { at: string; symbol: string; text: string }[]; trades: number; wins: number; realized: number;
};
export const maApi = {
  status: () => req<MaStatus>("/ma/status"),
  setConfig: (c: Partial<MaConfig>) => req<{ ok: boolean; config: MaConfig }>("/ma/config", { method: "POST", body: JSON.stringify(c) }),
  close: (id: string) => req<{ ok: boolean }>("/ma/close/" + id, { method: "POST" }),
};
export type PrecheckSettings = {
  enabled: boolean; also_paper: boolean; cost_multiple: number; min_rr: number;
  max_book_cost_pct: number; max_spread_pct: number; max_loss_usdc: number;
};
export const precheckApi = {
  get: () => req<{ settings: PrecheckSettings }>("/precheck/config"),
  save: (patch: Record<string, unknown>) =>
    req<{ settings: PrecheckSettings }>("/precheck/config", { method: "PUT", body: JSON.stringify(patch) }),
};
export type TermPair = { symbol: string; last: number; change: number; turnover: number };
export type TermCandle = { t: number; o: number; h: number; l: number; c: number; v: number };
export type TermChart = {
  symbol: string; tf: string; candles: TermCandle[]; rsi: (number | null)[];
  ticker: { last?: number; change?: number; high?: number; low?: number; turnover?: number };
  book: { bids: number[][]; asks: number[][] };
  position: { side: "long" | "short"; fill_price: number; stop: number; target: number } | null;
};
export type TermStrategy = {
  id: string; name: string; family: string; tf: string; side: string; rr: number; n: number; coins: number;
  net: number; gross: number; t: number; win: number; h1: number; h2: number; oos: number | null;
};
export type TermAnalysis = {
  symbol: string; price: number; at: string; rigor: string; verdict: "LONG" | "SHORT" | "PASSA"; reasons: string[];
  lab?: { tested: number; qualified: number; at?: string };
  setup_id?: string; zone_from?: number; zone_to?: number; stop?: number; target?: number; simulation_only?: boolean;
  plan?: DeskPlan; strategy?: TermStrategy; explain?: string;
};
export type TermLabRow = TermStrategy & { qualified: boolean };
export type TermConfig = { enabled: boolean; rigor: string; auto_enabled: boolean; auto_risk_pct: number; symbols: string[]; lab_years: number; auto_every_min: number; max_universe: number };
export type TermLab = {
  running: boolean; error: string | null; rigor: string; config: TermConfig; at?: string; fee_side?: number; symbols?: string[];
  tested?: number; qualified?: number; top?: TermLabRow[]; all_candidates?: number;
};
export const termApi = {
  pairs: () => req<{ pairs: TermPair[] }>("/terminal/pairs"),
  chart: (symbol: string, tf: string) => req<TermChart>(`/terminal/chart?symbol=${encodeURIComponent(symbol)}&tf=${tf}`),
  analyze: (symbol: string) => req<TermAnalysis>("/terminal/analyze", { method: "POST", body: JSON.stringify({ symbol }) }),
  open: (setup_id: string) => req<{ ok: boolean }>("/terminal/open", { method: "POST", body: JSON.stringify({ setup_id }) }),
  lab: () => req<TermLab>("/terminal/lab"),
  refreshLab: () => req<{ ok: boolean }>("/terminal/lab/refresh", { method: "POST" }),
  setConfig: (c: Record<string, unknown>) => req<{ ok: boolean; config: TermConfig }>("/terminal/config", { method: "POST", body: JSON.stringify(c) }),
};

export type DeskPlan = {
  side: "long" | "short"; entry: number; stop: number; target: number; risk_pct_price: number; gain_pct_price: number;
  rr: number | null; leverage: number; safe_leverage: number; notional: number; margin: number;
  liquidation: number | null; liq_dist_pct: number | null; max_loss: number; expected_gain: number; cost_pct: number;
  warnings: string[]; checks: { ok: boolean; reasons: string[] };
};
export type DeskAnalysis = {
  symbol: string; price: number;
  analysis: { bias: "long" | "short" | "attendi"; score: number; notes: string[]; volatile: boolean };
  plans: { long: DeskPlan; short: DeskPlan };
};
export type DeskRow = {
  id: string; symbol: string; side: "long" | "short"; leverage: number; fill_price: number; notional: number; margin: number;
  stop: number; target: number; liquidation: number | null; current_price?: number; floating_pnl?: number;
  pnl_usdt?: number; close_price?: number; close_reason?: string; opened_at?: string; closed_at?: string; forced?: boolean;
  auto?: boolean; lots_total?: number; lots_left?: number; tp1?: number | null; tp1_done?: boolean; realized_partial?: number;
};
export type DeskConfig = {
  symbols: string[]; capital: number; risk_pct: number; max_leverage: number; funding_day_pct: number; max_open: number;
  auto_enabled: boolean; auto_leverage: number; auto_risk_pct: number; max_total_risk_pct: number; daily_loss_pct: number;
  max_trades_day: number; cooldown_min: number; lots: number; tp1_r: number; be_after_tp1: boolean; streak_pause: number; streak_pause_h: number;
  scan_enabled: boolean; scan_top: number; scan_pool: number; scan_min_volume: number; scan_every_min: number;
};
export type DeskScanRow = { symbol: string; bias: string; score: number; volume: number; price: number; change24h: number; checks_ok: boolean; why?: string | null; rr?: number | null; quality: number };
export type DeskScan = { running: boolean; at?: string | null; scanned?: number | null; analyzed?: number | null; total_pairs?: number | null; rows: DeskScanRow[] };
export type DeskRisk = { open_risk: number; open_risk_limit: number; today_pnl: number; daily_limit: number; auto_today: number; pause: string | null };
export type DeskStatus = {
  config: DeskConfig; risk: DeskRisk; scan: DeskScan; symbols_active: string[]; cash: number; equity: number; open: DeskRow[]; closed: DeskRow[];
  events: { at: string; symbol: string; text: string }[]; trades: number; wins: number; realized: number;
};
export const deskApi = {
  status: () => req<DeskStatus>("/desk/status"),
  analisi: (symbol: string, leverage: number, risk_pct: number) =>
    req<DeskAnalysis>(`/desk/analisi?symbol=${encodeURIComponent(symbol)}&leverage=${leverage}&risk_pct=${risk_pct}`),
  open: (b: { symbol: string; side: string; leverage: number; risk_pct: number; force?: boolean }) =>
    req<{ ok: boolean }>("/desk/open", { method: "POST", body: JSON.stringify(b) }),
  close: (id: string) => req<{ ok: boolean }>("/desk/close/" + id, { method: "POST" }),
  scan: () => req<{ ok: boolean; running: boolean }>("/desk/scan", { method: "POST" }),
  setConfig: (c: Record<string, unknown>) => req<{ ok: boolean; config: DeskConfig }>("/desk/config", { method: "POST", body: JSON.stringify(c) }),
};
export const realApi = {
  diagnosi: () => req<RealDiagnosi>("/real/diagnosi"),
  status: () => req<RealStatus>("/real/status"),
  save: (patch: Record<string, unknown>) =>
    req<Record<string, unknown>>("/real/settings", { method: "PUT", body: JSON.stringify(patch) }),
  stop: () => req<{ ok: boolean }>("/real/stop", { method: "POST" }),
  test: (amount: number) =>
    req<RealTestResult>("/real/test-roundtrip", { method: "POST", body: JSON.stringify({ symbol: "XRPUSDC", amount }) }),
  closeAll: () => req<{ chiuse: number; non_riuscite: number }>("/real/close-all", { method: "POST" }),
};

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
