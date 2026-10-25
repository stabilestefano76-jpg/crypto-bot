"""
ICT meccanico (solo LONG, spot). Funzioni pure, senza rete e senza database.

Formato candele del bot: [tempo_s, apertura, chiusura, massimo, minimo, volume], ordinate dal piu' vecchio,
SOLO candele chiuse.

Sequenza cercata (versione fissa e dichiarata, non la lettura "a occhio" di ICT):
  1. SWEEP   una candela fa un minimo piu' basso di tutti i minimi delle `window` candele precedenti
             (la "liquidita'" sotto i minimi) ma CHIUDE sopra quel livello.
  2. MSS     entro `mss_max` candele una candela CHIUDE sopra il massimo delle `struct` candele prima dello sweep.
  3. FVG     nel tratto sweep..MSS esiste un gap rialzista (minimo della candela k > massimo della candela k-2).
  4. INGRESSO quando il prezzo live ritorna al bordo alto del gap (e sta ancora sopra lo stop), entro `expire`
             candele dopo l'MSS.
  Stop = minimo dello sweep meno `stop_buf_pct`. Target = ingresso + rr * rischio.
Ogni setup ha un id (tempo dello sweep) per non rientrare due volte sullo stesso.
"""
from typing import Any, Optional

T, O, C, H, L, V = 0, 1, 2, 3, 4, 5

DEFAULTS = {
    "window": 48,          # candele di "liquidita'" sotto cui cercare lo sweep
    "struct": 6,           # candele prima dello sweep per il massimo da rompere
    "mss_max": 12,         # candele massime tra sweep e MSS
    "expire": 12,          # candele massime tra MSS e ingresso
    "stop_buf_pct": 0.10,  # margine sotto il minimo dello sweep (%)
    "min_risk_pct": 0.80,  # rischio minimo (%): sotto questo i costi (circa 0,6%) mangiano tutto
    "max_risk_pct": 3.00,
}


def find_setups(candles: list[list[float]], p: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Tutti i setup completi (sweep + MSS + FVG) ancora validi alla fine dei dati. Usa solo candele chiuse."""
    q = dict(DEFAULTS)
    if p:
        q.update({k: v for k, v in p.items() if k in DEFAULTS})
    n = len(candles)
    out: list[dict[str, Any]] = []
    w, st = int(q["window"]), int(q["struct"])
    if n < w + st + 5:
        return out
    last = n - 1
    first_s = max(w, last - int(q["mss_max"]) - int(q["expire"]) - 2)
    for s in range(first_s, last - 1):
        pool_low = min(c[L] for c in candles[s - w:s])
        if not (candles[s][L] < pool_low and candles[s][C] > pool_low):
            continue
        sweep_low = candles[s][L]
        struct_high = max(c[H] for c in candles[max(0, s - st):s])
        m = None
        for k in range(s + 1, min(last, s + int(q["mss_max"])) + 1):
            if candles[k][C] < sweep_low:
                break                       # lo sweep e' stato rinnegato
            if candles[k][C] > struct_high:
                m = k
                break
        if m is None or last - m > int(q["expire"]):
            continue
        # il minimo dello sweep non deve essere stato rotto dopo l'MSS
        if any(candles[k][C] < sweep_low for k in range(m, last + 1)):
            continue
        fvg = None
        for k in range(s + 2, m + 1):
            if candles[k][L] > candles[k - 2][H]:
                fvg = (candles[k - 2][H], candles[k][L])      # (basso, alto), si tiene l'ultimo
        if fvg is None:
            continue
        out.append({
            "id": int(candles[s][T]),
            "sweep_t": int(candles[s][T]),
            "mss_t": int(candles[m][T]),
            "sweep_low": sweep_low,
            "fvg_bottom": fvg[0],
            "fvg_top": fvg[1],
            "stop": sweep_low * (1.0 - q["stop_buf_pct"] / 100.0),
            "candles_since_mss": last - m,
        })
    return out


def entry_plan(setup: dict[str, Any], price: float, rr: float, p: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Il prezzo live e' tornato al bordo alto del gap? Allora calcola stop, target e rischio. Altrimenti None."""
    q = dict(DEFAULTS)
    if p:
        q.update({k: v for k, v in p.items() if k in DEFAULTS})
    if price <= 0 or price > setup["fvg_top"] or price <= setup["stop"]:
        return None
    risk_pct = (price - setup["stop"]) / price * 100.0
    if risk_pct < q["min_risk_pct"] or risk_pct > q["max_risk_pct"]:
        return None
    return {
        "entry": price,
        "stop": setup["stop"],
        "target": price + rr * (price - setup["stop"]),
        "risk_pct": risk_pct,
        "rr": rr,
    }


def in_kill_zone(hour_utc: int, zones: tuple = ((7, 10), (12, 15))) -> bool:
    """Londra 07-10 UTC e New York 12-15 UTC (ora di ingresso)."""
    return any(a <= hour_utc < b for a, b in zones)


def exit_check(pos: dict[str, Any], price: float, now_s: float, max_hold_h: float) -> Optional[str]:
    """'stop' | 'target' | 'tempo' | None, sul prezzo live."""
    if price <= pos["stop"]:
        return "stop"
    if price >= pos["target"]:
        return "target"
    if now_s - pos["opened_ts"] >= max_hold_h * 3600.0:
        return "tempo"
    return None
