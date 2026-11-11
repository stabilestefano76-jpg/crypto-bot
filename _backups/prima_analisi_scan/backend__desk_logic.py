"""Sezione "Analisi e rischio": regole di calcolo (funzioni pure, nessun accesso a rete o database).
Candele nel formato del bot: [tempo, apertura, chiusura, massimo, minimo, volume], la piu' recente per ultima."""
from typing import Any, Optional

MAX_LEVERAGE = 10
MAINT_PCT = 0.5          # margine di mantenimento (%) usato per stimare la liquidazione
DEFAULTS: dict[str, Any] = {
    "symbols": ["BTCUSDC", "ETHUSDC", "XRPUSDC", "SOLUSDC"],
    "capital": 100.0,        # capitale della simulazione (USDC)
    "risk_pct": 1.0,         # quanto rischi per operazione, in % del capitale
    "max_leverage": 3.0,     # leva massima che il bot ti suggerisce
    "funding_day_pct": 0.03, # costo di tenere aperta una posizione con leva/short, % al giorno (stima)
    "max_open": 3,
    # --- apertura automatica e gestione del rischio totale ---
    "auto_enabled": False,       # il bot apre da solo quando tutto e' d'accordo
    "auto_leverage": 1.0,        # leva massima usata dal bot (abbassata da sola se la liquidazione e' troppo vicina)
    "auto_risk_pct": 1.0,        # rischio per operazione del bot (% del capitale)
    "max_total_risk_pct": 3.0,   # somma delle perdite possibili di tutte le posizioni aperte (% del capitale)
    "daily_loss_pct": 3.0,       # se oggi hai perso questa % del capitale, nessuna nuova apertura
    "max_trades_day": 4,         # aperture automatiche al giorno
    "cooldown_min": 120,         # attesa su una moneta dopo una chiusura
    "lots": 2,                   # la posizione e' divisa in questi lotti (1-3)
    "tp1_r": 1.0,                # il primo lotto si chiude quando il guadagno vale questo multiplo del rischio
    "be_after_tp1": True,        # dopo il primo lotto lo stop va a pareggio
    "streak_pause": 3,           # dopo N perdite di fila il bot si ferma per qualche ora
    "streak_pause_h": 6,
}
CFG_LIMITS = {"risk_pct": (0.1, 3.0), "max_leverage": (1.0, float(MAX_LEVERAGE)), "funding_day_pct": (0.0, 1.0), "max_open": (1, 6),
              "auto_leverage": (1.0, float(MAX_LEVERAGE)), "auto_risk_pct": (0.1, 3.0), "max_total_risk_pct": (0.5, 10.0),
              "daily_loss_pct": (0.5, 10.0), "max_trades_day": (1, 20), "cooldown_min": (0, 1440), "lots": (1, 3),
              "tp1_r": (0.3, 3.0), "streak_pause": (1, 10), "streak_pause_h": (1, 48)}
INT_KEYS = ("max_open", "max_trades_day", "cooldown_min", "lots", "streak_pause", "streak_pause_h")
TF_WEIGHT = {"1d": 2.0, "4h": 1.5, "1h": 1.0}


def ema(values: list[float], n: int) -> Optional[float]:
    if len(values) < n or n < 1:
        return None
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0); losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    return 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)


def atr(candles: list[list[float]], n: int = 14) -> Optional[float]:
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i][3], candles[i][4], candles[i - 1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def frame(candles: list[list[float]]) -> dict[str, Any]:
    """Lettura di un timeframe: tendenza, RSI, volatilita'."""
    closes = [c[2] for c in candles]
    if len(closes) < 60:
        return {"ok": False}
    price = closes[-1]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    e200 = ema(closes, 200)
    a = atr(candles)
    up = price > e50 and e20 > e50 and (e200 is None or e50 > e200 * 0.995)
    down = price < e50 and e20 < e50 and (e200 is None or e50 < e200 * 1.005)
    return {"ok": True, "price": price, "trend": 1 if up else -1 if down else 0,
            "rsi": rsi(closes), "atr_pct": (a / price * 100) if a else None,
            "ema50_dist_pct": (price / e50 - 1) * 100, "has200": e200 is not None}


def analyze_market(frames: dict[str, list[list[float]]]) -> dict[str, Any]:
    """Prima cosa che fa il bot: legge 1d / 4h / 1h e dice se conviene long, short o aspettare."""
    per: dict[str, Any] = {}
    score = tot = 0.0
    for tf, w in TF_WEIGHT.items():
        f = frame(frames.get(tf) or [])
        per[tf] = f
        if f["ok"]:
            score += f["trend"] * w; tot += w
    if tot == 0:
        return {"ok": False, "reason": "dati insufficienti"}
    s = score / tot
    notes: list[str] = []
    bias = "long" if s >= 0.5 else "short" if s <= -0.5 else "attendi"
    names = {1: "al rialzo", -1: "al ribasso", 0: "incerta"}
    for tf in ("1d", "4h", "1h"):
        f = per[tf]
        if f["ok"]:
            notes.append("%s: tendenza %s, RSI %s" % (tf, names[f["trend"]], "-" if f["rsi"] is None else "%.0f" % f["rsi"]))
    h1 = per["1h"]
    volatile = bool(h1["ok"] and h1["atr_pct"] and h1["atr_pct"] > 2.5)
    if volatile:
        notes.append("Mercato molto mosso (1h): stop piu' larghi, meglio leva bassa")
    if bias == "long" and h1["ok"] and h1["rsi"] and h1["rsi"] > 75:
        notes.append("RSI 1h molto alto: il rialzo e' tirato, rischio di ritracciamento")
    if bias == "short" and h1["ok"] and h1["rsi"] and h1["rsi"] < 25:
        notes.append("RSI 1h molto basso: il ribasso e' tirato, rischio di rimbalzo")
    if bias == "attendi":
        notes.append("Le tendenze non sono d'accordo: di solito conviene aspettare")
    return {"ok": True, "bias": bias, "score": round(s, 2), "frames": per, "volatile": volatile, "notes": notes}


def liquidation_price(side: str, entry: float, lev: float) -> Optional[float]:
    if lev <= 1.0 and side == "long":
        return None  # senza leva un long non si liquida
    m = MAINT_PCT / 100.0
    if side == "long":
        return entry * (1 - 1 / lev + m)
    return entry * (1 + 1 / lev - m)


def risk_plan(side: str, price: float, candles_1h: list[list[float]], atr_val: float, *, capital: float, risk_pct: float,
              leverage: float, fee_pct: float, funding_day_pct: float, max_leverage: float,
              max_loss_usdc: Optional[float] = None, hold_days: float = 2.0, target_atr: float = 4.0) -> dict[str, Any]:
    """Stop, obiettivo, importo, margine, liquidazione e leva consigliata."""
    lows = [c[4] for c in candles_1h[-20:]]
    highs = [c[3] for c in candles_1h[-20:]]
    # stop = ultimo minimo/massimo di 20 candele, ma sempre tra 1,5 e 2,5 volte l'ATR dal prezzo
    # (ne' troppo stretto, ne' cosi' lontano da rendere il rischio/rendimento inutile)
    if side == "long":
        d = price - (min(lows) - 0.2 * atr_val)
        d = max(1.5 * atr_val, min(d, 2.5 * atr_val))
        stop, target = price - d, price + target_atr * atr_val
    else:
        d = (max(highs) + 0.2 * atr_val) - price
        d = max(1.5 * atr_val, min(d, 2.5 * atr_val))
        stop, target = price + d, price - target_atr * atr_val
    risk_dist = abs(price - stop) / price          # frazione
    gain_dist = abs(target - price) / price
    lev = max(1.0, min(float(leverage), float(MAX_LEVERAGE)))
    # costi di andata+ritorno e di tenere la posizione
    carry = (funding_day_pct * hold_days) if (lev > 1.0 or side == "short") else 0.0
    cost_pct = 2 * fee_pct + carry
    risk_usdc = capital * risk_pct / 100.0
    if max_loss_usdc:
        risk_usdc = min(risk_usdc, max_loss_usdc)
    notional = risk_usdc / (risk_dist + cost_pct / 100.0)
    margin = notional / lev
    capped = False
    if margin > capital * 0.5:                      # mai oltre meta' del capitale come margine su una sola operazione
        margin = capital * 0.5; notional = margin * lev; capped = True
    liq = liquidation_price(side, price, lev)
    liq_dist = None if liq is None else abs(price - liq) / price
    # leva consigliata: la liquidazione deve essere almeno 1.5 volte piu' lontana dello stop
    safe = 1.0
    for L in range(1, int(min(max_leverage, MAX_LEVERAGE)) + 1):
        ld = None if liquidation_price(side, price, float(L)) is None else abs(price - liquidation_price(side, price, float(L))) / price
        if ld is None or ld >= 1.5 * risk_dist:
            safe = float(L)
    real_loss = notional * (risk_dist + cost_pct / 100.0)
    real_gain = notional * (gain_dist - cost_pct / 100.0)
    warns: list[str] = []
    if liq_dist is not None and liq_dist < 1.5 * risk_dist:
        warns.append("Con leva %gx la liquidazione e' troppo vicina allo stop: puoi perdere tutto il margine. Leva consigliata: %gx" % (lev, safe))
    if capped:
        warns.append("Importo ridotto: il margine non puo' superare meta' del capitale")
    if side == "short":
        warns.append("Short: se il prezzo sale la perdita puo' essere grande, lo stop e' obbligatorio")
    return {"side": side, "entry": price, "stop": stop, "target": target, "risk_pct_price": risk_dist * 100,
            "gain_pct_price": gain_dist * 100, "rr": gain_dist / risk_dist if risk_dist else None,
            "leverage": lev, "safe_leverage": safe, "notional": notional, "margin": margin,
            "liquidation": liq, "liq_dist_pct": None if liq_dist is None else liq_dist * 100,
            "max_loss": real_loss, "expected_gain": real_gain, "cost_pct": cost_pct, "warnings": warns}


def mirror_for_check(plan: dict[str, Any]) -> tuple[float, float]:
    """(stop, obiettivo) in forma 'long' per riusare i controlli prima di entrare anche per gli short."""
    p = plan["entry"]
    return p * (1 - plan["risk_pct_price"] / 100.0), p * (1 + plan["gain_pct_price"] / 100.0)


def pnl(side: str, entry: float, exit_: float, qty: float, fee_pct: float, funding_usdc: float, margin: float) -> float:
    gross = (exit_ - entry) * qty if side == "long" else (entry - exit_) * qty
    fees = (entry * qty + exit_ * qty) * fee_pct / 100.0
    return max(-margin, gross - fees - funding_usdc)


def exit_hit(side: str, cur: float, stop: float, target: float, liq: Optional[float]) -> Optional[str]:
    if side == "long":
        if liq is not None and cur <= liq: return "liquidazione"
        if cur <= stop: return "stop"
        if cur >= target: return "obiettivo"
    else:
        if liq is not None and cur >= liq: return "liquidazione"
        if cur >= stop: return "stop"
        if cur <= target: return "obiettivo"
    return None


def lots_for(notional: float, wanted: int, min_lot_usdc: float = 5.5) -> int:
    """Quanti lotti si possono davvero fare: ogni lotto deve valere almeno il minimo d'ordine."""
    return max(1, min(int(wanted), int(notional // min_lot_usdc)))


def tp1_price(side: str, fill: float, stop: float, r: float) -> float:
    d = abs(fill - stop) * r
    return fill + d if side == "long" else fill - d


def tp1_hit(side: str, cur: float, tp1: float) -> bool:
    return cur >= tp1 if side == "long" else cur <= tp1


def risk_gate(*, capital: float, open_risks: list[float], new_risk: float, max_total_risk_pct: float,
              today_pnl: float, daily_loss_pct: float) -> list[str]:
    """Limiti sul rischio TOTALE (valgono per aperture automatiche e manuali)."""
    out = []
    tot = sum(open_risks) + new_risk
    if tot > capital * max_total_risk_pct / 100.0 + 1e-9:
        out.append("rischio totale %.2f USDC sopra il limite %.2f (%g%% del capitale)" % (tot, capital * max_total_risk_pct / 100.0, max_total_risk_pct))
    if today_pnl <= -capital * daily_loss_pct / 100.0:
        out.append("perdita di oggi %.2f USDC: limite giornaliero raggiunto" % today_pnl)
    return out


def streak_block(recent_pnls_newest_first: list[float], since_last_close_h: Optional[float], n: int, hours: float) -> Optional[str]:
    if n <= 0 or len(recent_pnls_newest_first) < n or since_last_close_h is None:
        return None
    if all(x < 0 for x in recent_pnls_newest_first[:n]) and since_last_close_h < hours:
        return "%d perdite di fila: pausa di %g ore (ne restano %.1f)" % (n, hours, hours - since_last_close_h)
    return None
