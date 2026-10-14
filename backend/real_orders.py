"""
Ordini reali Bybit (spot) — funzioni PURE: niente rete, niente database.
Qui ci sono solo calcoli e lettura delle risposte, cosi' si possono provare
senza toccare soldi veri. L'invio vero e' in server.py.

Regole di Bybit v5 usate (documentazione ufficiale):
 - firma: HMAC-SHA256 di  timestamp + apiKey + recvWindow + (query | body)
 - ordine di mercato spot: POST /v5/order/create con marketUnit esplicito
   ("quoteCoin" = importo in dollari, "baseCoin" = quantita' di monete)
 - l'ordine e' ASINCRONO: dopo l'invio si controlla lo stato
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Optional


def D(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def sign(secret: str, ts: str, api_key: str, recv: str, payload: str) -> str:
    return hmac.new(secret.encode(), (ts + api_key + recv + payload).encode(), hashlib.sha256).hexdigest()


def body_string(body: dict[str, Any]) -> str:
    """Il corpo che si firma e' ESATTAMENTE la stringa che si invia."""
    return json.dumps(body, separators=(",", ":"))


def step_floor(x: Any, step: Any) -> Decimal:
    """Arrotonda x PER DIFETTO al multiplo di step (mai di piu' di quello che hai)."""
    xs, st = D(x), D(step)
    if st <= 0:
        return xs
    return (xs / st).to_integral_value(rounding=ROUND_DOWN) * st


def fmt(x: Decimal) -> str:
    """Numero senza notazione scientifica (Bybit vuole una stringa normale)."""
    s = format(x, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def parse_instrument(resp: dict[str, Any], symbol: str) -> Optional[dict[str, Any]]:
    """Regole della coppia: passo della quantita', ordine minimo, monete."""
    try:
        items = resp["result"]["list"]
    except (KeyError, TypeError):
        return None
    for it in items:
        if it.get("symbol") != symbol:
            continue
        lot = it.get("lotSizeFilter", {}) or {}
        base_step = lot.get("basePrecision") or lot.get("qtyStep") or "0.000001"
        quote_step = lot.get("quotePrecision") or "0.00000001"
        return {
            "symbol": symbol,
            "base": it.get("baseCoin", ""),
            "quote": it.get("quoteCoin", ""),
            "base_step": D(base_step),
            "quote_step": D(quote_step),
            "min_qty": D(lot.get("minOrderQty") or 0),
            "min_amt": D(lot.get("minOrderAmt") or lot.get("minNotionalValue") or 0),
            "max_amt": D(lot.get("maxOrderAmt") or 0),
            "trading": (it.get("status") in (None, "", "Trading")),
        }
    return None


def buy_body(symbol: str, quote_amount: Any, link_id: str, inst: dict[str, Any]) -> dict[str, Any]:
    amt = step_floor(quote_amount, inst["quote_step"])
    return {
        "category": "spot", "symbol": symbol, "side": "Buy", "orderType": "Market",
        "qty": fmt(amt), "marketUnit": "quoteCoin", "orderLinkId": link_id,
    }


def sell_body(symbol: str, base_qty: Any, link_id: str, inst: dict[str, Any]) -> dict[str, Any]:
    q = step_floor(base_qty, inst["base_step"])
    return {
        "category": "spot", "symbol": symbol, "side": "Sell", "orderType": "Market",
        "qty": fmt(q), "marketUnit": "baseCoin", "orderLinkId": link_id,
    }


def order_status(history_resp: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """('Filled' | 'New' | ... | 'NotFound', ordine)."""
    try:
        items = history_resp["result"]["list"]
    except (KeyError, TypeError):
        return "Error", {}
    if not items:
        return "NotFound", {}
    o = items[0]
    return str(o.get("orderStatus", "")), o


FINAL_STATUSES = {"Filled", "PartiallyFilledCanceled", "Cancelled", "Rejected", "Deactivated"}


def parse_fills(executions: Optional[dict[str, Any]], order: dict[str, Any], base: str, quote: str) -> Optional[dict[str, Any]]:
    """Cosa e' stato eseguito DAVVERO: quantita', valore, prezzo medio, commissioni.
    Prima dalle esecuzioni (hanno la moneta della commissione), altrimenti dall'ordine."""
    qty = value = Decimal(0)
    fee_base = fee_quote = fee_other = Decimal(0)
    got = False
    try:
        rows = executions["result"]["list"] if executions else []
    except (KeyError, TypeError):
        rows = []
    for r in rows:
        if str(r.get("execType", "Trade")) not in ("Trade", ""):
            continue
        q = D(r.get("execQty"))
        if q <= 0:
            continue
        got = True
        qty += q
        value += D(r.get("execValue")) if r.get("execValue") not in (None, "") else q * D(r.get("execPrice"))
        fee = D(r.get("execFee"))
        cur = r.get("feeCurrency")
        if cur == base:
            fee_base += fee
        elif cur == quote:
            fee_quote += fee
        else:
            fee_other += fee
    if not got:
        q = D(order.get("cumExecQty"))
        if q <= 0:
            return None
        qty, value = q, D(order.get("cumExecValue"))
        if value <= 0:
            value = q * D(order.get("avgPrice"))
        detail = order.get("cumFeeDetail") or {}
        if isinstance(detail, dict) and detail:
            for k, v in detail.items():
                if k == base:
                    fee_base += D(v)
                elif k == quote:
                    fee_quote += D(v)
                else:
                    fee_other += D(v)
        else:
            fee = D(order.get("cumExecFee"))
            if order.get("side") == "Buy":
                fee_base += fee      # regola di Bybit spot: commissione sulla moneta ricevuta
            else:
                fee_quote += fee
    avg = value / qty if qty > 0 else Decimal(0)
    return {"qty": qty, "value": value, "avg_price": avg,
            "fee_base": fee_base, "fee_quote": fee_quote, "fee_other": fee_other}


def net_base_after_buy(f: dict[str, Any]) -> Decimal:
    """Monete che ti restano davvero dopo un acquisto (la commissione puo' essere in monete)."""
    return f["qty"] - f["fee_base"]


def net_quote_after_sell(f: dict[str, Any]) -> Decimal:
    """Dollari che entrano davvero dopo una vendita."""
    return f["value"] - f["fee_quote"]


def coin_available(balance_resp: dict[str, Any], coin: str) -> Optional[Decimal]:
    """Saldo libero di una moneta dalla risposta wallet-balance (None se non leggibile)."""
    try:
        for acc in balance_resp["result"]["list"]:
            for c in acc.get("coin", []):
                if c.get("coin") == coin:
                    wb = D(c.get("walletBalance"))
                    locked = D(c.get("locked") or 0)
                    return max(Decimal(0), wb - locked)
        return Decimal(0)
    except (KeyError, TypeError):
        return None
