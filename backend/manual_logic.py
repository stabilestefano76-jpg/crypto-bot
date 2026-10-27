"""Setup manuale: lettura di un segnale incollato come testo + calcoli di rischio/guadagno.
Funzioni pure (nessuna rete, nessun database), provate a parte. Solo long, spot, coppie USDC."""
from __future__ import annotations

import re
from typing import Any, Optional

NUM = r"(\d+(?:\.\d+)?)"
GAP = r"(?:(?!stop|\bsl\b|\btp\b|target|take|mercato|market|importo|rischio|risk|ingresso|entry)[^\d\n]){0,25}?"

_KW_ENTRY = r"(?:ingresso|entrata|entry|buy|compra(?:re)?|acquisto|entra|limite|limit)"
_KW_STOP = r"(?:stop[ _-]?loss|stoploss|stop|sl)"
_KW_TARGET = r"(?:take[ _-]?profit|target|tp|obiettivo|uscita)"
_KW_AMOUNT = r"(?:importo|investi(?:mento)?|size|capitale|budget|quantit[aà])"
_KW_RISK = r"(?:rischio|risk|perdita[ _]max(?:ima)?|max[ _]perdita)"
_MARKET = re.compile(r"\b(mercato|market|adesso|subito|ora)\b")


def _norm(text: str) -> str:
    t = text.replace(" ", " ").lower()
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)  # virgola decimale all'italiana
    return t


def _find(pattern: str, t: str) -> Optional[float]:
    m = re.search(r"\b" + pattern + r"\b" + GAP + NUM, t)
    return float(m.group(1)) if m else None


def parse_signal(text: str) -> dict[str, Any]:
    """Ritorna {symbol, entry (None = a mercato), stop, target, amount, risk}. ValueError con messaggio chiaro se manca qualcosa."""
    if not text or not text.strip():
        raise ValueError("Incolla il segnale: serve almeno moneta, stop e target.")
    t = _norm(text)
    # --- moneta
    sym = None
    m = re.search(r"\b([a-z0-9]{2,10})\s*[/\-_ ]?\s*(usdc|usdt)\b", t)
    if m and m.group(1) not in ("importo", "ordine"):
        sym = m.group(1).upper() + "USDC"
    if sym is None:
        m = re.search(r"\b(?:moneta|coppia|symbol|pair|setup|segnale)\b\s*[:=]?\s*([a-z0-9]{2,10})\b", t)
        if m and m.group(1) not in ("manuale", "long", "buy"):
            sym = m.group(1).upper() + "USDC"
    if sym is None:
        raise ValueError("Non trovo la moneta. Scrivi per esempio: SETUP SUIUSDC")
    # --- livelli
    stop = _find(_KW_STOP, t)
    target = _find(_KW_TARGET, t)
    entry = _find(_KW_ENTRY, t)
    market = bool(_MARKET.search(t)) and entry is None
    amount = _find(_KW_AMOUNT, t)
    if amount is None:
        m = re.search(r"\$[ \t]*" + NUM, t) or re.search(NUM + r"[ \t]*(?:\$|dollari|dollaro|usd\b)", t)
        amount = float(m.group(1)) if m else None
    risk = _find(_KW_RISK, t)
    miss = []
    if stop is None:
        miss.append("stop")
    if target is None:
        miss.append("target")
    if entry is None and not market:
        miss.append("ingresso (oppure scrivi 'mercato')")
    if amount is None:
        miss.append("importo")
    if miss:
        raise ValueError("Mancano: " + ", ".join(miss) + ". Esempio: Ingresso 0.9050 / Stop 0.8800 / Target 0.9600 / Importo 10")
    return {"symbol": sym, "entry": None if market else entry, "stop": stop, "target": target,
            "amount": amount, "risk": risk}


def plan(setup: dict[str, Any], price: float, fee_pct: float, real: bool = False) -> dict[str, Any]:
    """Numeri del setup e controlli. errors = blocca; warnings = avvisa. Tutto in dollari (USDC)."""
    errors: list[str] = []
    warnings: list[str] = []
    sym, stop, target, amount = setup["symbol"], float(setup["stop"]), float(setup["target"]), float(setup["amount"])
    entry = setup.get("entry")
    market = entry is None
    ref = float(price) if market else float(entry)
    f = fee_pct / 100.0
    if not sym.endswith("USDC") or len(sym) < 6 or len(sym) > 14 or not sym.isalnum():
        errors.append("Coppia non valida (serve una coppia USDC, es. SUIUSDC).")
    if min(stop, target, ref, amount) <= 0:
        errors.append("Prezzi e importo devono essere positivi.")
    if errors:
        return {"errors": errors, "warnings": warnings}
    if not (stop < ref < target):
        errors.append("Il setup e' solo al rialzo: serve stop < ingresso < target.")
    if not market and ref > price * 1.02:
        errors.append("L'ingresso e' piu' del 2% sopra il prezzo attuale: sarebbe un acquisto a mercato. Scrivi 'mercato' oppure abbassa l'ingresso.")
    if amount < 5.5 and real:
        errors.append("Per il reale l'importo minimo e' 5,50 (minimo di Bybit con margine).")
    if amount < 1.0:
        errors.append("Importo troppo basso.")
    qty = amount / ref
    loss_gross = (ref - stop) * qty
    gain_gross = (target - ref) * qty
    fees_stop = f * (amount + stop * qty)
    fees_target = f * (amount + target * qty)
    loss_net = loss_gross + fees_stop
    gain_net = gain_gross - fees_target
    rr = gain_net / loss_net if loss_net > 0 else None
    risk_pct = (ref - stop) / ref * 100.0
    gain_pct = (target - ref) / ref * 100.0
    breakeven = ref * (1 + f) / (1 - f)
    risk_limit = setup.get("risk")
    if risk_limit is not None and loss_net > float(risk_limit) + 1e-9:
        per_dollar = loss_net / amount
        errors.append("Con questi livelli, allo stop perdi %.2f: sopra il tuo limite di %.2f. Importo massimo: %.2f."
                      % (loss_net, float(risk_limit), float(risk_limit) / per_dollar))
    if not market and ref < price * 0.80:
        warnings.append("L'ingresso e' molto lontano dal prezzo attuale (oltre -20%): potrebbe non scattare mai.")
    if risk_pct < 0.5:
        warnings.append("Stop molto vicino (%.2f%%): il rumore normale del prezzo puo' farlo scattare." % risk_pct)
    if gain_pct < 1.0:
        warnings.append("Target vicino (%.2f%%): le commissioni (circa %.2f%% a giro) ne mangiano una parte grande." % (gain_pct, 2 * fee_pct))
    if gain_net <= 0:
        errors.append("Al target guadagni meno delle commissioni: setup in perdita anche se va bene.")
    elif rr is not None and rr < 1.0:
        warnings.append("Rapporto guadagno/rischio sotto 1: rischi piu' di quanto puoi guadagnare.")
    if loss_net > amount * 0.5:
        warnings.append("Lo stop e' lontano: perdi piu' del 50% dell'importo.")
    return {
        "errors": errors, "warnings": warnings, "market": market, "entry_ref": ref, "qty": qty,
        "risk_pct": round(risk_pct, 3), "gain_pct": round(gain_pct, 3),
        "loss_net": round(loss_net, 4), "gain_net": round(gain_net, 4),
        "rr": None if rr is None else round(rr, 2), "fees_roundtrip_pct": round(2 * fee_pct, 3),
        "breakeven": breakeven,
    }


def exit_check(pos: dict[str, Any], price: float) -> Optional[str]:
    if price <= pos["stop"]:
        return "stop"
    if price >= pos["target"]:
        return "target"
    return None


def waiting_check(s: dict[str, Any], price: float, now_s: float) -> str:
    """'trigger' = compra ora; 'invalid' = prezzo gia' fuori dal setup; 'expired'; 'wait'."""
    if now_s >= s["expires_ts"]:
        return "expired"
    if price <= s["stop"] or price >= s["target"]:
        return "invalid"
    if price <= s["entry"]:
        return "trigger"
    return "wait"
