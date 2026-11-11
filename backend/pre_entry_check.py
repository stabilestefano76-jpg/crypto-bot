"""Controllo prima di entrare: funzioni pure (nessun accesso a rete o database).

Quattro controlli, tutti modificabili dall'app:
  1. costi coperti    - il guadagno atteso deve valere almeno N volte i costi (commissioni + libro ordini)
  2. rischio/rendimento - guadagno atteso / perdita possibile >= minimo
  3. libro ordini sottile - costo di ingresso e spread non oltre i limiti
  4. perdita massima  - se la perdita possibile supera il tetto, riduce l'importo; sotto il minimo blocca
"""
from typing import Any, Optional

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "also_paper": False,        # False = solo con soldi veri; True = anche in simulazione
    "check_costs": True,
    "cost_multiple": 2.0,       # guadagno atteso >= 2 x costi andata+ritorno
    "check_rr": True,
    "min_rr": 2.0,              # guadagno atteso >= 2 x perdita possibile
    "check_book": True,
    "max_book_cost_pct": 0.15,  # costo di ingresso nel libro ordini
    "max_spread_pct": 0.20,
    "check_max_loss": True,
    "max_loss_usdc": 1.5,       # perdita possibile massima per operazione (USDC)
    "target_atr_mult": 3.0,     # obiettivo di riferimento = prezzo + 3 x ATR (strategie automatiche)
    "stop_lookback": 20,        # stop di riferimento = minimo delle ultime 20 candele - 0.2 x ATR
    "min_order_usdc": 5.5,
}
NUM_KEYS = ("cost_multiple", "min_rr", "max_book_cost_pct", "max_spread_pct", "max_loss_usdc",
            "target_atr_mult", "stop_lookback", "min_order_usdc")
BOOL_KEYS = ("enabled", "also_paper", "check_costs", "check_rr", "check_book", "check_max_loss")


def clean_settings(raw: Optional[dict]) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for k, v in (raw or {}).items():
        if k in BOOL_KEYS:
            out[k] = bool(v)
        elif k in NUM_KEYS:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f == f and f > 0:
                out[k] = f
    return out


def reference_levels(price: float, highs: list, lows: list, atr: Optional[float], s: dict) -> tuple[Optional[float], Optional[float]]:
    """(stop, target) di riferimento per le strategie senza stop vero."""
    if not atr or atr <= 0 or not lows:
        return None, None
    n = max(2, int(s["stop_lookback"]))
    stop = min(lows[-n:]) - 0.2 * atr
    if stop >= price:
        stop = price - 1.5 * atr
    target = price + s["target_atr_mult"] * atr
    return stop, target


def evaluate(*, price: float, notional: float, fee_pct: float, stop: Optional[float], target: Optional[float],
             book_cost_pct: float, spread_pct: Optional[float], book_model: str, s: dict,
             use_max_loss: bool = True) -> dict[str, Any]:
    reasons: list[str] = []
    rt = 2 * fee_pct + max(0.0, book_cost_pct) + max(0.0, spread_pct or 0.0)  # costo andata+ritorno stimato (%)
    gain = (target / price - 1) * 100 if target and price > 0 else None
    risk = (1 - stop / price) * 100 if stop and price > 0 else None
    if s["check_book"]:
        if book_model == "fallback":
            reasons.append("libro ordini troppo sottile o illeggibile")
        else:
            if book_cost_pct > s["max_book_cost_pct"]:
                reasons.append(f"libro ordini sottile: entrare costa {book_cost_pct:.2f}% (max {s['max_book_cost_pct']:g}%)")
            if spread_pct is not None and spread_pct > s["max_spread_pct"]:
                reasons.append(f"spread troppo largo {spread_pct:.2f}% (max {s['max_spread_pct']:g}%)")
    if s["check_costs"] and gain is not None and gain < s["cost_multiple"] * rt:
        reasons.append(f"costi non coperti: guadagno atteso {gain:.2f}% < {s['cost_multiple']:g} x costi {rt:.2f}%")
    if s["check_rr"] and gain is not None and risk is not None and risk > 0:
        rr = gain / risk
        if rr < s["min_rr"]:
            reasons.append(f"rischio/rendimento {rr:.1f} sotto il minimo {s['min_rr']:g} (guadagno {gain:.2f}% / rischio {risk:.2f}%)")
    new_notional = notional
    if use_max_loss and s["check_max_loss"] and risk is not None:
        per_usdc = (risk + rt) / 100.0
        if per_usdc > 0 and notional * per_usdc > s["max_loss_usdc"]:
            new_notional = s["max_loss_usdc"] / per_usdc
            if new_notional < s["min_order_usdc"]:
                reasons.append(f"perdita massima {s['max_loss_usdc']:g} USDC: con rischio {risk:.2f}% si potrebbe comprare solo {new_notional:.2f} USDC (minimo {s['min_order_usdc']:g})")
                new_notional = notional
    return {"ok": not reasons, "reasons": reasons, "notional": min(notional, new_notional),
            "gain_pct": gain, "risk_pct": risk, "cost_pct": rt}
