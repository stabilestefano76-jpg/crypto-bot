# ============================================================================
# GESTIONE TRADE
# Ogni N minuti il bot rilegge il grafico delle posizioni APERTE (solo quelle di Analisi e rischio, non quelle del Terminale):
# se la situazione e' peggiorata puo' stringere lo stop o chiudere in anticipo, ma SOLO in guadagno netto, cioe' oltre tutti i costi
# (commissioni di entrata e uscita, funding maturato, piccolo margine). In perdita non interviene: decide lo stop di sempre.
# Ogni uscita anticipata / stop stretto viene confrontata con "cosa sarebbe successo senza" (operazione ombra).
# ============================================================================
MNG_DEFAULT: dict[str, Any] = {"enabled": True, "mode": "chiudi", "every_min": 15}
MNG_MODES = ("chiudi", "stringi", "avvisa")
MNG_GRACE_MIN = 30           # nei primi 30 minuti non tocca nulla
MNG_SLIP_PCT = 0.05          # margine extra sui costi (scivolamento in uscita), in % del prezzo
MNG_SHADOW_H = 72            # l'operazione ombra si chiude da sola dopo 72 ore
_mng_cache: dict[str, Any] = {"at": 0.0, "v": dict(MNG_DEFAULT)}


async def mng_cfg(force: bool = False) -> dict[str, Any]:
    if not force and time.time() - float(_mng_cache["at"]) < 10:
        return dict(_mng_cache["v"])
    doc = await db.crus_config.find_one({"_id": "mng"}, {"_id": 0}) or {}
    v = dict(MNG_DEFAULT)
    v.update({k: doc[k] for k in MNG_DEFAULT if k in doc})
    _mng_cache["at"], _mng_cache["v"] = time.time(), v
    return dict(v)


def mng_cost_px(side: str, fill: float, qty: float, fee_pct: float, carry_usdc: float, slip_pct: float = MNG_SLIP_PCT) -> float:
    """Prezzo oltre il quale chiudere ora dà un guadagno NETTO: commissioni (entrata + uscita), funding maturato, piccolo margine."""
    unit = fill * (2.0 * fee_pct + slip_pct) / 100.0 + (carry_usdc / qty if qty > 0 else 0.0)
    return fill + unit if side == "long" else fill - unit


def mng_assess(side: str, cur: float, atr1: float, c1h: list[list[float]], an: dict[str, Any], entry_bias: str,
               peak_r: float, r_now: float) -> dict[str, Any]:
    """Quanti segnali di pericolo ci sono ora (0 = tutto come all'apertura)."""
    sg = 1.0 if side == "long" else -1.0
    opp = "short" if side == "long" else "long"
    danger = 0
    sig: list[str] = []
    bias = an.get("bias")
    if bias == opp:
        danger += 2
        sig.append("l'indicazione di mercato è passata a %s" % opp)
    elif bias == "attendi" and entry_bias == side:
        danger += 1
        sig.append("le tendenze non sono più d'accordo")
    closed = c1h[:-1] if len(c1h) > 14 else []      # l'ultima candela e' ancora in formazione
    if closed:
        last, prev = closed[-1], closed[-9:-1]
        if prev and side == "long" and last[2] < min(c[4] for c in prev):
            danger += 2
            sig.append("rotto l'ultimo minimo (grafico 1h)")
        if prev and side == "short" and last[2] > max(c[3] for c in prev):
            danger += 2
            sig.append("rotto l'ultimo massimo (grafico 1h)")
        closes = [c[2] for c in closed]
        e20, e20b = DK.ema(closes, 20), DK.ema(closes[:-3], 20)
        if e20 is not None and e20b is not None and (last[2] - e20) * sg < 0 and (e20 - e20b) * sg < 0:
            danger += 1
            sig.append("prezzo oltre la media a 20 (1h) contro la posizione, e la media gira")
        if atr1 > 0 and (last[2] - last[1]) * sg < -1.5 * atr1:
            danger += 1
            sig.append("candela 1h molto forte contro la posizione")
    if peak_r >= 1.0 and r_now <= peak_r * 0.5:
        danger += 2
        sig.append("ha restituito metà del guadagno massimo (%+.1fR → %+.1fR)" % (peak_r, r_now))
    return {"danger": danger, "signals": sig}


def mng_decide(*, side: str, stop: float, cur: float, atr1: float, mode: str, danger: int, prev_danger: int,
               be_px: float, grace_ok: bool) -> dict[str, Any]:
    """Decide: tieni / stringi (nuovo stop) / chiudi / avvisa. In perdita (o sotto i costi) NON chiude e NON stringe."""
    sg = 1.0 if side == "long" else -1.0
    hold = {"action": "tieni", "reason": None, "new_stop": None}
    if not grace_ok or danger < 2 or atr1 <= 0:
        return hold
    if (cur - be_px) * sg <= 0.15 * atr1:                      # non e' chiaramente in guadagno netto
        return {"action": "avvisa" if danger >= 3 else "tieni", "new_stop": None,
                "reason": "pericolo %d, ma la posizione è sotto il pareggio dei costi: non esco, lascio decidere lo stop" % danger}
    if mode == "avvisa":
        return {"action": "avvisa" if danger >= 3 else "tieni", "new_stop": None, "reason": "pericolo %d (modalità solo avviso)" % danger}
    if mode == "chiudi" and danger >= 4 and prev_danger >= 3:    # confermato da due controlli di fila
        return {"action": "chiudi", "new_stop": None, "reason": "pericolo %d confermato da due controlli: chiudo in guadagno netto" % danger}
    gap = atr1 * (0.5 if danger >= 4 else 1.0)
    cand = cur - sg * gap
    new = max(stop, cand, be_px) if side == "long" else min(stop, cand, be_px)     # mai sotto il pareggio dei costi
    new = min(new, cur - 0.1 * atr1) if side == "long" else max(new, cur + 0.1 * atr1)
    if (new - stop) * sg <= 0.1 * atr1:
        return hold                                              # lo stop protegge gia' abbastanza
    return {"action": "stringi", "new_stop": new, "reason": "pericolo %d: stringo lo stop per proteggere il guadagno" % danger}


def mng_view(o: dict[str, Any]) -> dict[str, Any]:
    managed = o.get("source") != "terminale"
    return {"managed": managed, "danger": o.get("mng_danger"), "signals": o.get("mng_signals") or [], "action": o.get("mng_action"),
            "reason": o.get("mng_reason"), "at": o.get("mng_last_ts"), "tightened": bool(o.get("mng_orig_stop")),
            "peak_r": o.get("mng_peak_r"), "orig_stop": o.get("mng_orig_stop")}


async def mng_review(p: dict[str, Any], cfg: Config, mc: dict[str, Any], cur: float, r_now: float, peak: float) -> None:
    sym = p["symbol"]
    frames = {tf: await _desk_candles(sym, tf) for tf in ("1d", "4h", "1h")}
    if not frames["1h"]:
        return
    an = DK.analyze_market(frames)
    if not an.get("ok"):
        return
    atr1 = DK.atr(frames["1h"]) or 0.0
    now = time.time()
    side = p["side"]
    days = max(0.0, (now - float(p["opened_ts"])) / 86400.0)
    carry = p["notional"] * (p["funding_day_pct"] / 100.0) * days if (p["leverage"] > 1.0 or side == "short") else 0.0
    be_px = mng_cost_px(side, float(p["fill_price"]), float(p["quantity"]), _desk_fee(cfg), carry)
    a = mng_assess(side, cur, atr1, frames["1h"], an, str(p.get("bias_at_entry") or ""), peak, r_now)
    prev = int(p.get("mng_danger") or 0)
    dec = mng_decide(side=side, stop=float(p["stop"]), cur=cur, atr1=atr1, mode=str(mc["mode"]), danger=a["danger"], prev_danger=prev,
                     be_px=be_px, grace_ok=now - float(p["opened_ts"]) >= MNG_GRACE_MIN * 60)
    why = "; ".join(a["signals"]) if a["signals"] else "nessun segnale di pericolo"
    upd: dict[str, Any] = {"mng_last_ts": now, "mng_danger": a["danger"], "mng_signals": a["signals"], "mng_action": dec["action"],
                           "mng_reason": dec["reason"], "mng_peak_r": round(peak, 3), "mng_be_px": be_px}
    if dec["action"] == "stringi":
        new = float(dec["new_stop"])
        upd["stop"] = new
        if not p.get("mng_orig_stop"):
            upd["mng_orig_stop"] = p["stop"]
        r = await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})
        if r.modified_count:
            await _desk_event(sym, "GESTIONE: stop stretto da %.6g a %.6g (guadagno protetto oltre i costi). Motivi: %s" % (p["stop"], new, why))
        return
    await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})
    if dec["action"] == "chiudi":
        await _desk_event(sym, "GESTIONE: chiudo in anticipo in guadagno netto. Motivi: %s" % why)
        await _desk_close(p, cfg, "uscita_anticipata", cur)
    elif dec["action"] == "avvisa":
        sig = __import__("re").sub(r"\d+(\.\d+)?", "#", why + str(dec["reason"]))
        if p.get("mng_alert_sig") != sig or now - float(p.get("mng_alert_ts") or 0) > 3600:
            await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_alert_sig": sig, "mng_alert_ts": now}})
            await _desk_event(sym, "GESTIONE: attenzione. %s. Motivi: %s" % (dec["reason"], why))


async def mng_manage(cfg: Config) -> None:
    mc = await mng_cfg()
    if not mc["enabled"]:
        return
    every = max(5, int(mc["every_min"])) * 60
    for p in await db.desk_positions.find({"status": "open"}, {"_id": 0}).to_list(50):
        if p.get("source") == "terminale":
            continue
        cur = await _desk_price(p["symbol"])
        if cur is None:
            continue
        fill, stop = float(p["fill_price"]), float(p["stop"])
        sg = 1.0 if p["side"] == "long" else -1.0
        risk0 = float(p.get("mng_risk0") or 0.0)
        if risk0 <= 0:                                           # distanza di rischio iniziale (non cambia quando lo stop si sposta)
            rr = float(p.get("rr") or 0.0)
            risk0 = abs(fill - stop) if (not p.get("tp1_done") and not p.get("mng_orig_stop")) else (abs(float(p["target"]) - fill) / rr if rr > 0 else abs(fill - stop))
            if risk0 > 0:
                await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_risk0": risk0}})
        if risk0 <= 0:
            continue
        r_now = (cur - fill) * sg / risk0
        peak = max(float(p.get("mng_peak_r") if p.get("mng_peak_r") is not None else r_now), r_now)
        if peak > float(p.get("mng_peak_r") if p.get("mng_peak_r") is not None else -999) + 0.02:
            await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_peak_r": round(peak, 3)}})
        if time.time() - float(p.get("mng_last_ts") or 0) >= every:
            try:
                await mng_review(p, cfg, mc, cur, r_now, peak)
            except Exception as e:  # noqa: BLE001
                logger.exception("Gestione trade: errore su %s: %s", p.get("symbol"), e)


async def mng_shadow_scan() -> None:
    """Per ogni uscita anticipata o stop stretto fa partire l'operazione ombra: cosa sarebbe successo lasciando stop e obiettivo originali."""
    closed = await db.desk_positions.find(
        {"status": "closed", "$or": [{"close_reason": "uscita_anticipata"}, {"close_reason": "stop", "mng_orig_stop": {"$exists": True}}]},
        {"_id": 0}).sort("closed_at", -1).to_list(150)
    if not closed:
        return
    have = {d["pos_id"] for d in await db.mng_shadow.find({}, {"_id": 0, "pos_id": 1}).to_list(2000)}
    for p in closed:
        if p["id"] in have:
            continue
        orig = float(p.get("mng_orig_stop") or p["stop"])
        now = time.time()
        await db.mng_shadow.insert_one({
            "pos_id": p["id"], "symbol": p["symbol"], "side": p["side"], "fill": float(p["fill_price"]), "qty": float(p["quantity"]),
            "margin": float(p["margin"]), "notional": float(p["notional"]), "lev": float(p["leverage"]), "stop": orig, "target": float(p["target"]),
            "liq": p.get("liquidation"), "fd": float(p["funding_day_pct"]), "opened_ts": float(p["opened_ts"]),
            "start_price": p.get("close_price"), "partial": float(p.get("realized_partial") or 0.0), "actual": float(p.get("pnl_usdt") or 0.0),
            "kind": "uscita" if p.get("close_reason") == "uscita_anticipata" else "stop_stretto", "status": "open",
            "created_ts": now, "created_at": datetime.now(timezone.utc).isoformat()})


async def mng_shadow_resolve(cfg: Config) -> None:
    for s in await db.mng_shadow.find({"status": "open"}, {"_id": 0}).to_list(100):
        cur = await _desk_price(s["symbol"])
        if cur is None:
            continue
        hit = DK.exit_hit(s["side"], cur, s["stop"], s["target"], s.get("liq"))
        ex, out = None, None
        if hit == "liquidazione":
            ex, out = float(s["liq"]), "liquidazione"
        elif hit == "stop":
            ex, out = s["stop"], "stop"
        elif hit == "obiettivo":
            ex, out = s["target"], "obiettivo"
        elif time.time() - float(s["created_ts"]) > MNG_SHADOW_H * 3600:
            ex, out = cur, "scaduta"
        if out is None:
            continue
        days = max(0.0, (time.time() - float(s["opened_ts"])) / 86400.0)
        carry = s["notional"] * (s["fd"] / 100.0) * days if (s["lev"] > 1.0 or s["side"] == "short") else 0.0
        rest = DK.pnl(s["side"], s["fill"], ex, s["qty"], _desk_fee(cfg), carry, s["margin"])
        total = s["partial"] + rest
        await db.mng_shadow.update_one({"pos_id": s["pos_id"], "status": "open"}, {"$set": {
            "status": "done", "outcome": out, "shadow_pnl": round(total, 4), "delta": round(s["actual"] - total, 4),
            "done_at": datetime.now(timezone.utc).isoformat()}})


async def mng_summary() -> dict[str, Any]:
    docs = await db.mng_shadow.find({}, {"_id": 0}).sort("created_ts", -1).to_list(500)
    done = [d for d in docs if d.get("status") == "done"]
    helped = [d for d in done if d["delta"] > 0.005]
    hurt = [d for d in done if d["delta"] < -0.005]
    def net(k: str) -> float:
        return round(sum(d["delta"] for d in done if d["kind"] == k), 4)
    return {"n_done": len(done), "n_pending": len([d for d in docs if d.get("status") == "open"]), "helped": len(helped), "hurt": len(hurt),
            "net": round(sum(d["delta"] for d in done), 4), "net_uscita": net("uscita"), "net_stop": net("stop_stretto"),
            "n_uscita": len([d for d in done if d["kind"] == "uscita"]), "n_stop": len([d for d in done if d["kind"] == "stop_stretto"]),
            "items": [{"symbol": d["symbol"], "side": d["side"], "kind": d["kind"], "status": d["status"], "actual": round(d["actual"], 4),
                       "shadow": d.get("shadow_pnl"), "delta": d.get("delta"), "outcome": d.get("outcome"), "at": d.get("created_at")}
                      for d in docs[:10]]}


@api.get("/gestione")
async def mng_get() -> dict[str, Any]:
    return {"config": await mng_cfg(True), "summary": await mng_summary()}


@api.post("/gestione")
async def mng_set(body: dict[str, Any]) -> dict[str, Any]:
    cur = await mng_cfg(True)
    new = dict(cur)
    if "enabled" in body:
        new["enabled"] = bool(body["enabled"])
    if "mode" in body:
        if body["mode"] not in MNG_MODES:
            raise HTTPException(status_code=400, detail="Modalità non valida.")
        new["mode"] = body["mode"]
    if "every_min" in body:
        try:
            v = int(float(body["every_min"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Minuti non validi.")
        if not (5 <= v <= 240):
            raise HTTPException(status_code=400, detail="Ogni quanti minuti: da 5 a 240.")
        new["every_min"] = v
    await db.crus_config.update_one({"_id": "mng"}, {"$set": new}, upsert=True)
    await mng_cfg(True)
    return {"ok": True, "config": new}


async def mng_loop() -> None:
    await asyncio.sleep(25)
    while True:
        try:
            cfg = await get_config()
            await mng_manage(cfg)
            await mng_shadow_scan()
            await mng_shadow_resolve(cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception("Gestione trade: errore nel ciclo: %s", e)
        await asyncio.sleep(30)


