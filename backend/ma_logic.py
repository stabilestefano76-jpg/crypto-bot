"""Medie mobili: calcolo e regole (funzioni pure, senza rete e senza database).
Segnale sempre sull'ULTIMA candela CHIUSA: incrocio al rialzo = compra, la media veloce sotto la lenta = esci."""
from typing import Any, Optional

KINDS = ("SMA", "EMA")
TFS = ("1h", "4h", "1d")


def sma(values: list[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(values: list[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1.0)
    e = sum(values[:n]) / n  # partenza = media semplice delle prime n
    out[n - 1] = e
    for i in range(n, len(values)):
        e = values[i] * k + e * (1.0 - k)
        out[i] = e
    return out


def min_candles(kind: str, slow: int) -> int:
    """Candele chiuse necessarie: la lenta + margine (l'EMA ha bisogno di assestarsi)."""
    return slow + 3 if kind == "SMA" else slow * 2 + 3


def analyze(closes: list[float], kind: str, fast: int, slow: int) -> dict[str, Any]:
    """Stato sull'ultima candela chiusa. cross: 'up' (appena incrociata verso l'alto), 'down', oppure None."""
    f = (sma if kind == "SMA" else ema)(closes, fast)
    s = (sma if kind == "SMA" else ema)(closes, slow)
    if len(closes) < min_candles(kind, slow) or f[-1] is None or s[-1] is None or f[-2] is None or s[-2] is None:
        return {"ok": False, "reason": "dati insufficienti"}
    above = f[-1] > s[-1]
    was_above = f[-2] > s[-2]
    cross = "up" if (above and not was_above) else "down" if (was_above and not above) else None
    return {"ok": True, "fast": f[-1], "slow": s[-1], "above": above, "cross": cross,
            "dist_pct": (f[-1] / s[-1] - 1.0) * 100.0 if s[-1] else 0.0}


def clean_config(raw: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Controlla e normalizza le impostazioni. Alza ValueError con un messaggio semplice se qualcosa non va."""
    c = dict(current)
    if "enabled" in raw:
        c["enabled"] = bool(raw["enabled"])
    if "kind" in raw:
        k = str(raw["kind"]).upper()
        if k not in KINDS:
            raise ValueError("Tipo di media non valido (SMA o EMA).")
        c["kind"] = k
    if "tf" in raw:
        if raw["tf"] not in TFS:
            raise ValueError("Timeframe non valido (1h, 4h o 1d).")
        c["tf"] = raw["tf"]
    for key, lo, hi in (("fast", 2, 100), ("slow", 3, 250)):
        if key in raw:
            try:
                v = int(raw[key])
            except (TypeError, ValueError):
                raise ValueError("Il periodo delle medie deve essere un numero intero.")
            if not lo <= v <= hi:
                raise ValueError("Periodo %s: scegli un numero tra %d e %d." % ("veloce" if key == "fast" else "lenta", lo, hi))
            c[key] = v
    if c["fast"] >= c["slow"]:
        raise ValueError("La media veloce deve avere un periodo piu' piccolo di quella lenta.")
    if "amount" in raw:
        try:
            v = float(raw["amount"])
        except (TypeError, ValueError):
            raise ValueError("Importo non valido.")
        if not 5.5 <= v <= 100000:
            raise ValueError("L'importo per operazione deve essere almeno 5,50.")
        c["amount"] = v
    if "max_positions" in raw:
        try:
            v = int(raw["max_positions"])
        except (TypeError, ValueError):
            raise ValueError("Numero massimo di posizioni non valido.")
        if not 1 <= v <= 12:
            raise ValueError("Posizioni contemporanee: da 1 a 12.")
        c["max_positions"] = v
    if "stop_pct" in raw:
        try:
            v = float(raw["stop_pct"])
        except (TypeError, ValueError):
            raise ValueError("Stop di sicurezza non valido.")
        if not 0 <= v <= 50:
            raise ValueError("Stop di sicurezza: da 0 (spento) a 50 per cento.")
        c["stop_pct"] = v
    if "symbols" in raw:
        syms = []
        for x in raw["symbols"] or []:
            s = str(x).strip().upper()
            if s and s.endswith("USDC") and len(s) > 4 and s not in syms:
                syms.append(s)
        if not syms or len(syms) > 12:
            raise ValueError("Scegli da 1 a 12 monete (coppie USDC, per esempio BTCUSDC).")
        c["symbols"] = syms
    return c


DEFAULTS: dict[str, Any] = {
    "enabled": False, "kind": "SMA", "tf": "1d", "fast": 10, "slow": 50, "amount": 50.0,
    "max_positions": 4, "stop_pct": 0.0, "symbols": ["BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC"],
}


def entry_decision(an: dict[str, Any], cand_ts: float, last_done_ts: float, open_n: int, max_n: int, has_pos: bool) -> tuple[str, str]:
    """('buy'|'skip', motivo). Si entra solo su un incrocio NUOVO (mai visto prima su questa candela)."""
    if not an.get("ok"):
        return "skip", an.get("reason", "dati non disponibili")
    if has_pos:
        return "skip", "posizione gia' aperta"
    if an["cross"] != "up":
        return "skip", "nessun incrocio al rialzo"
    if cand_ts <= last_done_ts:
        return "skip", "incrocio gia' valutato"
    if open_n >= max_n:
        return "skip", "numero massimo di posizioni raggiunto"
    return "buy", "incrocio al rialzo"


def exit_decision(an: dict[str, Any], cur: float, entry: float, stop_pct: float) -> Optional[str]:
    """Motivo d'uscita oppure None. Stop di sicurezza (se acceso) controllato ad ogni passaggio; media veloce sotto la lenta a candela chiusa."""
    if stop_pct > 0 and entry > 0 and cur <= entry * (1.0 - stop_pct / 100.0):
        return "stop_sicurezza"
    if an.get("ok") and not an["above"]:
        return "incrocio_ribasso"
    return None
