"""Ingresso a conferma per 33/60 e XRP Accumulation. Funzioni pure (nessuna rete, nessun database).

Idea: invece di comprare mentre l'RSI sta ancora scendendo (tocco della soglia bassa), si aspetta che l'RSI,
dopo essere stato sotto la soglia bassa nelle ultime N candele, "giri" verso l'alto:
  - 'risale'   : l'RSI passa da <= livello a > livello (es. 30) in questa candela
  - 'incrocio' : l'RSI incrocia al rialzo la sua media mobile (SMA dell'RSI) in questa candela
  - 'entrambi' : una delle due cose avviene in questa candela E l'altra e' gia' vera in questo momento
                 (RSI sopra il livello E sopra la sua media)
closed_only=True : usa solo candele chiuse (il segnale non puo' sparire); False: usa anche il prezzo live (candela in formazione).
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

CONFIRM_MODES = ("risale", "incrocio", "entrambi")
ALL_MODES = ("tocco",) + CONFIRM_MODES


def sma(values: Sequence[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period < 1:
        return out
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def evaluate(closes: Sequence[float], live_price: Optional[float], rsi_fn: Callable[[list[float], int], list[Optional[float]]],
             *, mode: str, period: int, low: float, level: float, lookback: int, ma_period: int,
             closed_only: bool = True) -> dict[str, Any]:
    """Ritorna {ok, reason, rsi, ma}. reason e' sempre in italiano e si puo' mostrare nel registro."""
    res: dict[str, Any] = {"ok": False, "reason": "", "rsi": None, "ma": None}
    if mode not in CONFIRM_MODES:
        res["reason"] = "modalita' non di conferma"
        return res
    series = list(closes)
    if not closed_only and live_price:
        series.append(float(live_price))
    rs_all = rsi_fn(series, period)
    rs = [x for x in rs_all if x is not None]
    lookback = max(1, int(lookback))
    ma_period = max(2, int(ma_period))
    if len(rs) < ma_period + 2:
        res["reason"] = "dati insufficienti per la conferma"
        return res
    ma_all = sma(rs, ma_period)
    now, prev = float(rs[-1]), float(rs[-2])
    ma_now, ma_prev = ma_all[-1], ma_all[-2]
    res["rsi"], res["ma"] = now, ma_now
    window = rs[-(lookback + 1):]
    if not any(v < low for v in window):
        res["reason"] = "conferma: l'RSI non e' stato sotto %g nelle ultime %d candele" % (low, lookback)
        return res
    rise_cross = prev <= level < now
    ma_cross = ma_now is not None and ma_prev is not None and prev <= ma_prev and now > ma_now
    above_level = now > level
    above_ma = ma_now is not None and now > ma_now
    if mode == "risale":
        ok = rise_cross
        why = "conferma: l'RSI non e' appena risalito sopra %g" % level
    elif mode == "incrocio":
        ok = ma_cross
        why = "conferma: l'RSI non ha appena incrociato la sua media al rialzo"
    else:
        ok = (rise_cross and above_ma) or (ma_cross and above_level)
        why = "conferma: servono insieme RSI sopra %g e sopra la sua media (una delle due appena avvenuta)" % level
    res["ok"] = bool(ok)
    res["reason"] = "" if ok else why
    return res
