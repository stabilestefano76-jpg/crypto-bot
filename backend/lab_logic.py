"""Laboratorio strategie: archivio di strategie note, prova su prezzi passati CON i costi, scelta statistica.
Logica pura (nessuna rete, nessun database): si puo' provare da sola.
Candele del bot: [tempo, apertura, chiusura, massimo, minimo, volume]. Qui si usano righe (t, o, h, l, c, v)."""
from __future__ import annotations

import math
from typing import Any, Callable, Optional

SECS = {"4h": 14400, "1d": 86400}
TFS = ("4h", "1d")
LOOKBACK = {"4h": 250, "1d": 150}
MAX_HOLD = {"4h": 60, "1d": 30}
PIV_N = 10
TOL_ATR, CLUSTER_ATR, MIN_TOUCH, WICK, VOL_X = 0.3, 0.5, 2, 0.5, 1.5
STOP_BUF, MAX_RISK_ATR, COOLDOWN = 0.1, 4.0, 20
BASE_STOP_ATR = 2.0
RRS = (2.0, 3.0)
SLIP_STOP = 0.05
RIGOR = {   # soglie per essere scelta: n operazioni, t minimo
    "severo": {"min_n": 100, "min_t": 3.0},
    "normale": {"min_n": 60, "min_t": 2.0},
}
DEFAULTS = {"enabled": True, "rigor": "severo", "auto_enabled": False, "auto_risk_pct": 1.0,
            "symbols": ["BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC", "DOGEUSDC", "ADAUSDC", "LINKUSDC", "LTCUSDC"],
            "lab_years": 3.0, "auto_every_min": 15, "max_universe": 12}
CFG_LIMITS = {"auto_risk_pct": (0.1, 3.0), "auto_every_min": (5, 240), "lab_years": (1.0, 4.0), "max_universe": (4, 20)}


def to_rows(candles: list[list[float]]) -> list[tuple]:
    return [(c[0], c[1], c[3], c[4], c[2], c[5] if len(c) > 5 else 0.0) for c in candles]


# ------------------------------------------------------------------ indicatori
def atr_series(rows: list[tuple], n: int = 14) -> list[Optional[float]]:
    out, prev, a, trs = [None] * len(rows), None, None, []
    for i, (_, _, h, l, cl, _) in enumerate(rows):
        tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev))
        prev = cl
        if a is None:
            trs.append(tr)
            if len(trs) == n:
                a = sum(trs) / n
                out[i] = a
        else:
            a = (a * (n - 1) + tr) / n
            out[i] = a
    return out


def rsi_series(rows: list[tuple], n: int = 14) -> list[Optional[float]]:
    out = [None] * len(rows)
    if len(rows) <= n:
        return out
    cl = [r[4] for r in rows]
    g = [max(cl[i] - cl[i - 1], 0) for i in range(1, len(cl))]
    l = [max(cl[i - 1] - cl[i], 0) for i in range(1, len(cl))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    for i in range(n, len(cl)):
        if i > n:
            ag = (ag * (n - 1) + g[i - 1]) / n
            al = (al * (n - 1) + l[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def sma_series(x: list[float], n: int) -> list[Optional[float]]:
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def vol_ratio(rows: list[tuple], n: int = 20) -> list[Optional[float]]:
    out, s = [None] * len(rows), 0.0
    for i, r in enumerate(rows):
        if i >= n:
            out[i] = r[5] / (s / n) if s > 0 else None       # media delle n candele PRECEDENTI
            s -= rows[i - n][5]
        s += r[5]
    return out


def indicators(rows: list[tuple]) -> dict[str, Any]:
    c = [r[4] for r in rows]
    return {"atr": atr_series(rows), "rsi": rsi_series(rows), "rsi2": rsi_series(rows, 2), "vr": vol_ratio(rows),
            "sma20": sma_series(c, 20), "sma50": sma_series(c, 50), "sma100": sma_series(c, 100), "sma200": sma_series(c, 200)}


# ---------------------------------------------------------------- generatori
# Ogni generatore ritorna eventi {t, side(+1/-1), stop, atr}: il segnale e' deciso alla CHIUSURA della candela t.
def _ev(t, side, stop, atr):
    return {"t": t, "side": side, "stop": stop, "atr": atr}


def gen_donchian(n: int) -> Callable:
    def g(rows, ind, tf):
        H, L, C, A = [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows], ind["atr"]
        out = []
        for t in range(n + 1, len(rows)):
            if A[t] is None:
                continue
            hi, lo = max(H[t - n:t]), min(L[t - n:t])
            hi1, lo1 = max(H[t - 1 - n:t - 1]), min(L[t - 1 - n:t - 1])
            if C[t] > hi and C[t - 1] <= hi1:
                out.append(_ev(t, 1, C[t] - BASE_STOP_ATR * A[t], A[t]))
            if C[t] < lo and C[t - 1] >= lo1:
                out.append(_ev(t, -1, C[t] + BASE_STOP_ATR * A[t], A[t]))
        return out
    return g


def gen_macross(key: str) -> Callable:
    def g(rows, ind, tf):
        C, A, M = [r[4] for r in rows], ind["atr"], ind[key]
        out = []
        for t in range(1, len(rows)):
            if A[t] is None or M[t] is None or M[t - 1] is None:
                continue
            if C[t] > M[t] and C[t - 1] <= M[t - 1]:
                out.append(_ev(t, 1, C[t] - BASE_STOP_ATR * A[t], A[t]))
            if C[t] < M[t] and C[t - 1] >= M[t - 1]:
                out.append(_ev(t, -1, C[t] + BASE_STOP_ATR * A[t], A[t]))
        return out
    return g


def gen_momentum(n: int) -> Callable:
    def g(rows, ind, tf):
        C, A = [r[4] for r in rows], ind["atr"]
        out = []
        for t in range(n + 1, len(rows)):
            if A[t] is None:
                continue
            up, up1 = C[t] > C[t - n], C[t - 1] > C[t - 1 - n]
            if up and not up1:
                out.append(_ev(t, 1, C[t] - BASE_STOP_ATR * A[t], A[t]))
            if (not up) and up1:
                out.append(_ev(t, -1, C[t] + BASE_STOP_ATR * A[t], A[t]))
        return out
    return g


def gen_rsi2(rows, ind, tf):
    C, A, R, M = [r[4] for r in rows], ind["atr"], ind["rsi2"], ind["sma200"]
    out = []
    for t in range(1, len(rows)):
        if A[t] is None or R[t] is None or M[t] is None:
            continue
        if R[t] < 10 and C[t] > M[t]:
            out.append(_ev(t, 1, C[t] - BASE_STOP_ATR * A[t], A[t]))
        if R[t] > 90 and C[t] < M[t]:
            out.append(_ev(t, -1, C[t] + BASE_STOP_ATR * A[t], A[t]))
    return out


def reaction_events(rows, ind, tf):
    """Reazioni a supporti/resistenze importanti (pivot larghi toccati almeno 2 volte). Vedi studio reazione."""
    n = len(rows)
    A, R, VR = ind["atr"], ind["rsi"], ind["vr"]
    H, L, O, C = [r[2] for r in rows], [r[3] for r in rows], [r[1] for r in rows], [r[4] for r in rows]
    piv, events, last_used = [], [], {}
    lb = LOOKBACK[tf]
    for t in range(n):
        i = t - 1 - PIV_N
        if i >= PIV_N:
            if all(H[i] > H[j] for j in range(i - PIV_N, i)) and all(H[i] >= H[j] for j in range(i + 1, i + PIV_N + 1)):
                piv.append((i, H[i]))
            if all(L[i] < L[j] for j in range(i - PIV_N, i)) and all(L[i] <= L[j] for j in range(i + 1, i + PIV_N + 1)):
                piv.append((i, L[i]))
        if A[t] is None or t < 1:
            continue
        window = [p for p in piv if p[0] >= t - lb]
        if len(window) < 2:
            continue
        a, tol = A[t], TOL_ATR * A[t]
        rng = H[t] - L[t]
        best: dict[str, tuple] = {}
        for (pi, lv) in window:
            et = None
            if C[t] > lv + tol and L[t] <= lv + tol and C[t - 1] > lv + tol and rng > 0 and (min(O[t], C[t]) - L[t]) >= WICK * rng:
                et = "bounce_sup"
            elif C[t] < lv - tol and H[t] >= lv - tol and C[t - 1] < lv - tol and rng > 0 and (H[t] - max(O[t], C[t])) >= WICK * rng:
                et = "reject_res"
            elif C[t] < lv - tol and C[t - 1] >= lv - tol:
                et = "break_sup"
            elif C[t] > lv + tol and C[t - 1] <= lv + tol:
                et = "break_res"
            if et is None or t - last_used.get((pi, et), -10 ** 9) < COOLDOWN:
                continue
            touches = sum(1 for (_, q) in window if abs(q - lv) <= CLUSTER_ATR * a)
            if touches < MIN_TOUCH:
                continue
            if et not in best or touches > best[et][0]:
                best[et] = (touches, pi, lv)
        for et, (touches, pi, lv) in best.items():
            last_used[(pi, et)] = t
            if et == "bounce_sup":
                side, stop = 1, min(L[t], lv - tol) - STOP_BUF * a
            elif et == "reject_res":
                side, stop = -1, max(H[t], lv + tol) + STOP_BUF * a
            elif et == "break_sup":
                side, stop = -1, lv + tol + STOP_BUF * a
            else:
                side, stop = 1, lv - tol - STOP_BUF * a
            e = _ev(t, side, stop, a)
            e.update({"type": et, "vr": VR[t], "rsi": R[t], "level": lv})
            events.append(e)
    return events


def _react_pass(e: dict, filt: bool) -> bool:
    if not filt:
        return True
    if e["vr"] is None or e["vr"] < VOL_X or e["rsi"] is None:
        return False
    r, ty = e["rsi"], e["type"]
    return (ty == "bounce_sup" and r <= 40) or (ty == "reject_res" and r >= 60) or (ty == "break_res" and r >= 55) or (ty == "break_sup" and r <= 45)


def gen_reaction(et: str, filt: bool) -> Callable:
    def g(rows, ind, tf):
        return [e for e in reaction_events(rows, ind, tf) if e["type"] == et and _react_pass(e, filt)]
    return g



# ---------------------------------------------------- figure di candele giapponesi
def _body(r):
    return abs(r[4] - r[1])


def gen_candle(kind: str, ctx: bool) -> Callable:
    """kind: morning, evening, bull_engulf, bear_engulf, hammer, star. ctx=True: solo dopo un calo (rialziste: sotto la media a 20)
    o dopo un rialzo (ribassiste: sopra la media a 20). Il segnale e' deciso alla chiusura dell'ultima candela del disegno."""
    def g(rows, ind, tf):
        A, M = ind["atr"], ind["sma20"]
        out = []
        for t in range(25, len(rows)):
            a = A[t]
            if a is None or M[t - 2] is None:
                continue
            r0, r1, r2 = rows[t - 2], rows[t - 1], rows[t]
            side, lo_hi = 0, None
            if kind == "morning":
                if (r0[4] < r0[1] and _body(r0) >= 0.6 * a and _body(r1) <= 0.35 * _body(r0)
                        and r2[4] > r2[1] and _body(r2) >= 0.5 * a and r2[4] > (r0[1] + r0[4]) / 2):
                    side, lo_hi = 1, min(r0[3], r1[3], r2[3])
            elif kind == "evening":
                if (r0[4] > r0[1] and _body(r0) >= 0.6 * a and _body(r1) <= 0.35 * _body(r0)
                        and r2[4] < r2[1] and _body(r2) >= 0.5 * a and r2[4] < (r0[1] + r0[4]) / 2):
                    side, lo_hi = -1, max(r0[2], r1[2], r2[2])
            elif kind == "bull_engulf":
                if r1[4] < r1[1] and _body(r1) >= 0.3 * a and r2[4] > r2[1] and r2[1] <= r1[4] and r2[4] >= r1[1] and _body(r2) > _body(r1):
                    side, lo_hi = 1, min(r1[3], r2[3])
            elif kind == "bear_engulf":
                if r1[4] > r1[1] and _body(r1) >= 0.3 * a and r2[4] < r2[1] and r2[1] >= r1[4] and r2[4] <= r1[1] and _body(r2) > _body(r1):
                    side, lo_hi = -1, max(r1[2], r2[2])
            elif kind == "hammer":
                rng, body = r2[2] - r2[3], _body(r2)
                lw, uw = min(r2[1], r2[4]) - r2[3], r2[2] - max(r2[1], r2[4])
                if rng >= 0.8 * a and lw >= 2 * max(body, 1e-12) and uw <= 0.3 * rng:
                    side, lo_hi = 1, r2[3]
            elif kind == "star":
                rng, body = r2[2] - r2[3], _body(r2)
                lw, uw = min(r2[1], r2[4]) - r2[3], r2[2] - max(r2[1], r2[4])
                if rng >= 0.8 * a and uw >= 2 * max(body, 1e-12) and lw <= 0.3 * rng:
                    side, lo_hi = -1, r2[2]
            if not side:
                continue
            c0 = rows[t - 2][4]
            if ctx and not ((side == 1 and c0 < M[t - 2]) or (side == -1 and c0 > M[t - 2])):
                continue
            stop = lo_hi - STOP_BUF * a if side == 1 else lo_hi + STOP_BUF * a
            out.append(_ev(t, side, stop, a))
        return out
    return g

# Archivio: id, nome semplice, famiglia, spiegazione, generatore. Per aggiungere una strategia basta una riga.
CATALOG: list[dict[str, Any]] = []


def _add(sid, name, family, text, gen, sides=(1, -1)):
    CATALOG.append({"id": sid, "name": name, "family": family, "text": text, "gen": gen, "sides": sides})


for _n in (20, 50, 100):
    _add("donchian%d" % _n, "Rottura dei massimi/minimi di %d candele" % _n, "Rottura di canale",
         "Il prezzo chiude oltre il massimo (o minimo) delle ultime %d candele: il movimento tende a continuare." % _n, gen_donchian(_n))
for _k, _nm in (("sma50", "50"), ("sma100", "100")):
    _add("ma" + _nm, "Prezzo supera la media a %s candele" % _nm, "Media mobile",
         "Il prezzo attraversa la media a %s candele: cambio di tendenza lenta." % _nm, gen_macross(_k))
for _n in (30, 90):
    _add("mom%d" % _n, "Momentum a %d candele" % _n, "Momentum",
         "Il prezzo passa da sotto a sopra il livello di %d candele fa: la forza tende a continuare." % _n, gen_momentum(_n))
_add("rsi2", "RSI(2) estremo con filtro di trend", "Ritorno alla media",
     "RSI a 2 candele sotto 10 sopra la media a 200 (o sopra 90 sotto): ritorno verso la media.", gen_rsi2)
for _et, _nm, _sd in (("bounce_sup", "Rimbalzo sul supporto", (1,)), ("break_sup", "Rottura del supporto", (-1,)),
                      ("reject_res", "Rifiuto alla resistenza", (-1,)), ("break_res", "Rottura della resistenza", (1,))):
    _add("re_" + _et, _nm, "Reazione ai livelli", "Reazione del prezzo a un supporto/resistenza importante (toccato almeno 2 volte).", gen_reaction(_et, False), _sd)
    _add("re_" + _et + "_vr", _nm + " con volume e RSI", "Reazione ai livelli",
         "Come sopra, ma solo con volume almeno 1,5 volte la media e RSI coerente.", gen_reaction(_et, True), _sd)


_CANDLES = (("morning", "Morning star (stella del mattino)", (1,), "Rossa grande, piccola, verde grande che chiude oltre meta' della prima: possibile inversione al rialzo."),
            ("evening", "Evening star (stella della sera)", (-1,), "Verde grande, piccola, rossa grande che chiude sotto meta' della prima: possibile inversione al ribasso."),
            ("bull_engulf", "Ingoiante rialzista", (1,), "Una candela verde che ingoia per intero il corpo della rossa precedente."),
            ("bear_engulf", "Ingoiante ribassista", (-1,), "Una candela rossa che ingoia per intero il corpo della verde precedente."),
            ("hammer", "Martello", (1,), "Candela con lunga ombra sotto e corpo piccolo in alto: rifiuto dei prezzi bassi."),
            ("star", "Stella cadente", (-1,), "Candela con lunga ombra sopra e corpo piccolo in basso: rifiuto dei prezzi alti."))
for _k, _nm, _sd, _tx in _CANDLES:
    _add("cd_" + _k, _nm, "Candele giapponesi", _tx, gen_candle(_k, False), _sd)
    _add("cd_" + _k + "_ctx", _nm + " dopo calo/rialzo", "Candele giapponesi", _tx + " Solo dopo un movimento contrario (prezzo sotto/sopra la media a 20 candele).", gen_candle(_k, True), _sd)


# ------------------------------------------------------------- prova sui prezzi
def simulate(rows: list[tuple], tf: str, events: list[dict], rr: float, fee_side: float) -> list[tuple]:
    """Una operazione alla volta. Ritorna (tempo, R lordo, R netto, rischio%). Ingresso all'apertura della candela dopo."""
    secs, out, blocked = SECS[tf], [], -1
    for ev in events:
        e = ev["t"] + 1
        if e >= len(rows) or rows[e][0] < blocked:
            continue
        side, stop = ev["side"], ev["stop"]
        entry = rows[e][1]
        risk = (entry - stop) * side
        if risk <= 0 or risk > MAX_RISK_ATR * ev["atr"]:
            continue
        risk_pct = risk / entry * 100
        if rr * risk_pct < 4 * fee_side:
            continue
        target = entry + side * rr * risk
        res = None
        for m in range(e, min(e + MAX_HOLD[tf], len(rows))):
            o, h, l = rows[m][1], rows[m][2], rows[m][3]
            if side == 1:
                if l <= stop:
                    res = (min(stop, o) if m > e else stop, True, m)
                    break
                if h >= target:
                    res = (target, False, m)
                    break
            else:
                if h >= stop:
                    res = (max(stop, o) if m > e else stop, True, m)
                    break
                if l <= target:
                    res = (target, False, m)
                    break
        if res is None:
            if e + MAX_HOLD[tf] > len(rows):
                continue
            m = e + MAX_HOLD[tf] - 1
            res = (rows[m][4], False, m)
        gross = side * (res[0] - entry) / entry * 100
        cost = 2 * fee_side + (SLIP_STOP if res[1] else 0.0)
        out.append((rows[e][0], gross / risk_pct, (gross - cost) / risk_pct, risk_pct))
        blocked = rows[res[2]][0] + secs
    return out


def mean_sd(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    m = sum(xs) / n
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0)


def candidates() -> list[tuple[str, dict, int, float]]:
    """Tutte le varianti: (id completo, strategia, lato, rr)."""
    out = []
    for s in CATALOG:
        for side in s["sides"]:
            for rr in RRS:
                out.append(("%s:%s:rr%g" % (s["id"], "L" if side == 1 else "S", rr), s, side, rr))
    return out


def run_lab(data: dict[tuple[str, str], list[tuple]], fee_side: float) -> dict[str, Any]:
    """data[(simbolo, tf)] = righe. Ritorna statistiche per ogni (strategia, lato, rr, tf), raggruppando tutte le monete."""
    ev_cache: dict[tuple, dict[str, list]] = {}
    for (sym, tf), rows in data.items():
        if len(rows) < 300:
            continue
        ind = indicators(rows)
        ev_cache[(sym, tf)] = {s["id"]: s["gen"](rows, ind, tf) for s in CATALOG}
    results = []
    for cid, s, side, rr in candidates():
        for tf in TFS:
            pooled: list[tuple] = []
            nsym = 0
            for (sym, tf2), rows in data.items():
                if tf2 != tf or (sym, tf) not in ev_cache:
                    continue
                evs = [e for e in ev_cache[(sym, tf)][s["id"]] if e["side"] == side]
                tr = simulate(rows, tf, evs, rr, fee_side)
                if tr:
                    nsym += 1
                    pooled.extend(tr)
            pooled.sort()
            n = len(pooled)
            row = {"id": cid, "sid": s["id"], "name": s["name"], "family": s["family"], "text": s["text"], "tf": tf,
                   "side": "long" if side == 1 else "short", "rr": rr, "n": n, "coins": nsym}
            if n >= 10:
                net = [x[2] for x in pooled]
                m, sd = mean_sd(net)
                h = n // 2
                o = (2 * n) // 3
                row.update({"net": m, "gross": sum(x[1] for x in pooled) / n,
                            "t": (m / (sd / math.sqrt(n))) if sd > 1e-9 else 0.0,
                            "win": 100.0 * sum(1 for x in net if x > 0) / n,
                            "h1": sum(net[:h]) / h, "h2": sum(net[h:]) / (n - h),
                            "oos": (sum(net[o:]) / (n - o)) if n - o >= 5 else None,
                            "risk": sum(x[3] for x in pooled) / n})
            results.append(row)
    return {"rows": results, "symbols": sorted({k[0] for k in ev_cache}), "candles": {"%s|%s" % k: len(v) for k, v in data.items()}}


def qualifies(row: dict, rigor: str) -> bool:
    r = RIGOR.get(rigor, RIGOR["normale"])
    return bool(row.get("n", 0) >= r["min_n"] and row.get("net", -1) > 0 and row.get("t", 0) >= r["min_t"]
                and row.get("h1", -1) > 0 and row.get("h2", -1) > 0 and (row.get("oos") or -1) > 0)


# ------------------------------------------------------------------- segnale ora
def live_signals(frames: dict[str, list[list[float]]], qualified: list[dict], price: float) -> list[dict]:
    """Per le strategie scelte, controlla se sull'ULTIMA candela chiusa c'e' un segnale ancora valido al prezzo di ora."""
    out = []
    cache: dict[str, tuple] = {}
    for q in qualified:
        tf = q["tf"]
        if tf not in frames or len(frames[tf]) < 120:
            continue
        if tf not in cache:
            rows = to_rows(frames[tf])
            cache[tf] = (rows, indicators(rows))
        rows, ind = cache[tf]
        s = next((x for x in CATALOG if x["id"] == q["sid"]), None)
        if not s:
            continue
        t = len(rows) - 1
        side = 1 if q["side"] == "long" else -1
        evs = [e for e in s["gen"](rows, ind, tf) if e["t"] == t and e["side"] == side]
        if not evs:
            continue
        ev = evs[-1]
        a = ev["atr"]
        if abs(price - rows[t][4]) > 0.5 * a:          # il prezzo si e' gia' mosso troppo dal segnale
            continue
        risk = (price - ev["stop"]) * side
        if risk <= 0 or risk > MAX_RISK_ATR * a:
            continue
        out.append({"q": q, "ev": ev, "atr": a, "side": side, "entry": price, "stop": ev["stop"],
                    "target": price + side * q["rr"] * risk, "risk_pct": risk / price * 100, "signal_close": rows[t][4]})
    return out


def choose(signals: list[dict], fee_side: float) -> tuple[Optional[dict], list[str]]:
    """La migliore per vantaggio statistico (t piu' alto) tra quelle con segnale e che passano i costi."""
    why: list[str] = []
    ok = []
    for s in sorted(signals, key=lambda x: -x["q"]["t"]):
        if s["q"]["rr"] * s["risk_pct"] < 4 * fee_side:
            why.append("%s: obiettivo troppo vicino rispetto ai costi" % s["q"]["name"])
            continue
        ok.append(s)
    return (ok[0] if ok else None), why


def plan_custom(side: str, entry: float, stop: float, target: float, *, capital: float, risk_pct: float, fee_pct: float,
                funding_day_pct: float, max_loss_usdc: Optional[float], hold_days: float = 2.0) -> dict[str, Any]:
    """Importo e rischio per uno stop/obiettivo scelti da noi (leva 1x). Stesse chiavi di desk_logic.risk_plan."""
    risk_dist = abs(entry - stop) / entry
    gain_dist = abs(target - entry) / entry
    carry = funding_day_pct * hold_days if side == "short" else 0.0
    cost_pct = 2 * fee_pct + carry
    risk_usdc = capital * risk_pct / 100.0
    if max_loss_usdc:
        risk_usdc = min(risk_usdc, max_loss_usdc)
    notional = risk_usdc / (risk_dist + cost_pct / 100.0)
    margin, capped = notional, False
    if margin > capital * 0.5:
        margin, notional, capped = capital * 0.5, capital * 0.5, True
    real_loss = notional * (risk_dist + cost_pct / 100.0)
    real_gain = notional * (gain_dist - cost_pct / 100.0)
    warns = []
    if capped:
        warns.append("Importo ridotto: non puo' superare meta' del capitale")
    if side == "short":
        warns.append("Short: solo simulazione (in spot su Bybit UE non esiste)")
    return {"side": side, "entry": entry, "stop": stop, "target": target, "risk_pct_price": risk_dist * 100,
            "gain_pct_price": gain_dist * 100, "rr": gain_dist / risk_dist if risk_dist else None, "leverage": 1.0,
            "safe_leverage": 1.0, "notional": notional, "margin": margin, "liquidation": None, "liq_dist_pct": None,
            "max_loss": real_loss, "expected_gain": real_gain, "cost_pct": cost_pct, "warnings": warns}
