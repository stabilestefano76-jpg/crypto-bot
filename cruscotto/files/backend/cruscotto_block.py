# ============================================================================
# CRUSCOTTO
# Una sola schermata con tutto: portafoglio simulato dedicato (long e short), curva del capitale, esposizione,
# limiti di rischio, posizioni con distanza da stop/obiettivo/liquidazione, stato di bot e Terminale, eventi.
# Il portafoglio REALE su Bybit e' in SOLA LETTURA (saldi): nessun ordine parte da qui.
# ============================================================================
_crus_real_cache: dict[str, Any] = {"at": 0.0, "val": None}


def crus_stats(closed: list[dict[str, Any]]) -> dict[str, Any]:
    """Statistiche sulle posizioni chiuse (piu' recenti per prime nell'input)."""
    def part(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pn = [float(r.get("pnl_usdt") or 0.0) for r in rows]
        w = [x for x in pn if x > 0]
        l = [x for x in pn if x < 0]
        gl = -sum(l)
        return {"n": len(pn), "wins": len(w), "pnl": round(sum(pn), 4),
                "win_rate": round(100.0 * len(w) / len(pn), 1) if pn else None,
                "avg_win": round(sum(w) / len(w), 4) if w else None,
                "avg_loss": round(sum(l) / len(l), 4) if l else None,
                "profit_factor": round(sum(w) / gl, 2) if gl > 1e-12 else None,
                "expectancy": round(sum(pn) / len(pn), 4) if pn else None}
    out = part(closed)
    out["long"] = part([c for c in closed if c.get("side") == "long"])
    out["short"] = part([c for c in closed if c.get("side") == "short"])
    pn = [float(c.get("pnl_usdt") or 0.0) for c in closed]
    out["best"] = round(max(pn), 4) if pn else None
    out["worst"] = round(min(pn), 4) if pn else None
    return out


def crus_drawdown(values: list[float]) -> float:
    """Massimo calo percentuale dal picco, sulla curva del capitale."""
    peak, dd = None, 0.0
    for v in values:
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            dd = max(dd, (peak - v) / peak * 100.0)
    return round(dd, 2)


def crus_position_view(p: dict[str, Any], cur: float) -> dict[str, Any]:
    """Distanze in % dal prezzo attuale e avanzamento fra stop (0) e obiettivo (100)."""
    side = p["side"]
    stop, target, liq, fill = float(p["stop"]), float(p["target"]), p.get("liquidation"), float(p["fill_price"])
    sgn = 1.0 if side == "long" else -1.0
    span = (target - stop) * sgn
    prog = ((cur - stop) * sgn / span * 100.0) if span > 0 else None
    risk_px = abs(fill - stop)
    r_now = ((cur - fill) * sgn / risk_px) if risk_px > 0 else None
    return {"to_stop_pct": round(abs(cur - stop) / cur * 100.0, 2), "to_target_pct": round(abs(target - cur) / cur * 100.0, 2),
            "to_liq_pct": round(abs(float(liq) - cur) / cur * 100.0, 2) if liq else None,
            "progress": None if prog is None else round(max(-20.0, min(120.0, prog)), 1),
            "r_now": None if r_now is None else round(r_now, 2),
            "move_pct": round((cur - fill) * sgn / fill * 100.0, 2)}


async def crus_snapshot(force: bool = False) -> None:
    """Salva un punto della curva del capitale (al massimo uno ogni 4 minuti)."""
    last = await db.desk_equity.find_one({}, {"_id": 0, "t": 1}, sort=[("t", -1)])
    now = time.time()
    if last and not force and now - float(last["t"]) < 240:
        return
    s = await desk_status()
    await db.desk_equity.insert_one({"t": now, "equity": s["equity"], "cash": s["cash"], "realized": s["realized"]})
    n = await db.desk_equity.count_documents({})
    if n > 6000:
        old = await db.desk_equity.find({}, {"_id": 1}).sort("t", 1).to_list(n - 5000)
        await db.desk_equity.delete_many({"_id": {"$in": [o["_id"] for o in old]}})


async def crus_real() -> dict[str, Any]:
    """Portafoglio reale su Bybit, SOLO LETTURA. Cache 30 secondi."""
    if _crus_real_cache["val"] is not None and time.time() - _crus_real_cache["at"] < 30:
        return _crus_real_cache["val"]
    out: dict[str, Any] = {"connected": False, "error": None, "total_usd": None, "coins": [],
                           "note": "Solo lettura dei saldi. Bybit EU (spot) non permette lo short con questa chiave."}
    try:
        has = await db.exchange_creds.find_one({"_id": "bybit"}, {"_id": 1})
        if not has:
            out["error"] = "Nessuna chiave Bybit collegata"
        else:
            resp = await _bybit_signed("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            if resp.get("retCode") != 0:
                out["error"] = str(resp.get("retMsg"))
            else:
                acc = ((resp.get("result") or {}).get("list") or [{}])[0]
                coins = []
                for c in acc.get("coin") or []:
                    try:
                        qty, usd = float(c.get("walletBalance") or 0), float(c.get("usdValue") or 0)
                    except (TypeError, ValueError):
                        continue
                    if qty > 0 and usd >= 0.01:
                        coins.append({"coin": c.get("coin"), "qty": qty, "usd": round(usd, 2)})
                coins.sort(key=lambda x: -x["usd"])
                tot = acc.get("totalEquity")
                out.update({"connected": True, "coins": coins[:12],
                            "total_usd": round(float(tot), 2) if tot not in (None, "") else round(sum(c["usd"] for c in coins), 2)})
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:160]
    _crus_real_cache.update({"at": time.time(), "val": out})
    return out


@api.get("/cruscotto")
async def cruscotto() -> dict[str, Any]:
    st = await desk_status()
    dc = st["config"]
    await crus_snapshot()
    curve_docs = await db.desk_equity.find({}, {"_id": 0, "t": 1, "equity": 1}).sort("t", -1).to_list(600)
    curve_docs.reverse()
    if len(curve_docs) > 120:                                   # al massimo 120 punti da disegnare
        step = len(curve_docs) / 120.0
        curve_docs = [curve_docs[int(i * step)] for i in range(120)] + [curve_docs[-1]]
    curve = [[round(c["t"]), round(c["equity"], 4)] for c in curve_docs]
    cap0 = float(dc["capital"])
    allc = await db.desk_positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(2000)
    stats = crus_stats(allc)
    longs = [o for o in st["open"] if o["side"] == "long"]
    shorts = [o for o in st["open"] if o["side"] == "short"]
    eq = float(st["equity"])
    gross_l = sum(o["notional"] for o in longs)
    gross_s = sum(o["notional"] for o in shorts)
    margins = sum(o["margin"] for o in st["open"])
    floating = sum(float(o.get("floating_pnl") or 0.0) for o in st["open"])
    positions = []
    for o in st["open"]:
        v = crus_position_view(o, float(o["current_price"]))
        positions.append({"id": o["id"], "symbol": o["symbol"], "side": o["side"], "leverage": o["leverage"], "auto": bool(o.get("auto")),
                          "fill_price": o["fill_price"], "current_price": o["current_price"], "stop": o["stop"], "target": o["target"],
                          "liquidation": o.get("liquidation"), "notional": o["notional"], "margin": o["margin"], "pnl": o["floating_pnl"],
                          "lots_left": o.get("lots_left"), "lots_total": o.get("lots_total"), "tp1_done": bool(o.get("tp1_done")),
                          "opened_ts": o.get("opened_ts"), **v})
    risk = st["risk"]
    term: dict[str, Any] = {"installed": False}
    try:
        if "_term_cfg" in globals():
            tc = await _term_cfg()
            lab = await db.lab_results.find_one({"_id": "lab"}, {"_id": 0, "rows": 1, "at": 1})
            nq = None
            if lab:
                nq = len([r for r in lab.get("rows", []) if LAB.qualifies(r, tc["rigor"])])
            setups = await db.term_setups.find({}, {"_id": 0}).sort("created_ts", -1).to_list(3)
            term = {"installed": True, "auto": bool(tc["auto_enabled"]), "rigor": tc["rigor"], "qualified": nq,
                    "tested": len(lab.get("rows", [])) if lab else None, "archive_at": lab.get("at") if lab else None,
                    "last_setups": [{"symbol": s.get("symbol"), "side": s.get("side")} for s in setups]}
    except Exception as e:  # noqa: BLE001
        term = {"installed": True, "error": str(e)[:120]}
    scan = st["scan"]
    return {
        "portfolio": {"capital0": cap0, "equity": eq, "cash": st["cash"], "margin": round(margins, 4), "floating": round(floating, 4),
                      "realized": st["realized"], "return_pct": round((eq - cap0) / cap0 * 100.0, 2) if cap0 else None,
                      "today_pnl": risk["today_pnl"], "max_drawdown_pct": crus_drawdown([c[1] for c in curve]),
                      "curve": curve},
        "exposure": {"long": round(gross_l, 2), "short": round(gross_s, 2), "net": round(gross_l - gross_s, 2),
                     "gross": round(gross_l + gross_s, 2), "gross_x_equity": round((gross_l + gross_s) / eq, 2) if eq > 0 else None,
                     "n_long": len(longs), "n_short": len(shorts)},
        "risk": {**risk, "max_open": dc["max_open"], "open_n": len(st["open"]), "max_trades_day": dc["max_trades_day"],
                 "daily_loss_pct": dc["daily_loss_pct"], "max_total_risk_pct": dc["max_total_risk_pct"]},
        "stats": stats, "positions": positions, "recent_closed": [
            {"symbol": c["symbol"], "side": c["side"], "pnl": c.get("pnl_usdt"), "reason": c.get("close_reason"), "closed_at": c.get("closed_at")}
            for c in allc[:6]],
        "bots": {"desk_auto": bool(dc["auto_enabled"]), "scan_enabled": bool(dc["scan_enabled"]), "scan_at": scan.get("at"),
                 "scan_top": [r.get("symbol") for r in (scan.get("rows") or [])[:5]], "terminal": term},
        "events": st["events"][:8],
        "real": await crus_real(),
    }


async def crus_loop() -> None:
    await asyncio.sleep(25)
    while True:
        try:
            await crus_snapshot()
        except Exception as e:  # noqa: BLE001
            logger.exception("Cruscotto: errore nel salvataggio della curva: %s", e)
        await asyncio.sleep(120)


