# ============================================================================
# GESTIONE TRADE
# Il bot rilegge il grafico delle posizioni APERTE (solo quelle di Analisi e rischio, non quelle del Terminale).
# Due controlli:
#  - LENTO (ogni N minuti, grafico 1h): se la situazione peggiora stringe lo stop o chiude;
#  - VELOCE (ogni minuto, grafici 5 e 15 minuti): quando una posizione che era in guadagno comincia a tornare indietro, decide se e'
#    un'INVERSIONE (chiude coprendo i costi) o solo un RITRACCIAMENTO (tiene), come farebbe un trader.
# Esce SOLO in guadagno netto, cioe' oltre tutti i costi (commissioni di entrata e uscita, funding maturato, piccolo margine).
# In perdita non interviene: decide lo stop di sempre. Ogni intervento e' confrontato con l'operazione "ombra" (cosa sarebbe successo senza).
# ============================================================================
MNG_DEFAULT: dict[str, Any] = {"enabled": True, "mode": "chiudi", "every_min": 15, "fast": True, "stile": "prudente"}
MNG_MODES = ("chiudi", "stringi", "avvisa")
MNG_STILI = ("prudente", "paziente")
MNG_GRACE_MIN = 30           # controllo lento: nei primi 30 minuti non tocca nulla
MNG_FAST_GRACE_MIN = 10      # controllo veloce: nei primi 10 minuti non tocca nulla
MNG_FAST_EVERY = 60          # controllo veloce: ogni 60 secondi
MNG_FAST_MIN_PEAK = 0.5      # il controllo veloce parte solo se la posizione e' arrivata ad almeno +0.5R...
MNG_FAST_MIN_GIVE = 0.25     # ...e ha gia' restituito almeno 0.25R
MNG_SLIP_PCT = 0.05          # margine extra sui costi (scivolamento in uscita), in % del prezzo
MNG_SHADOW_H = 72            # l'operazione ombra si chiude da sola dopo 72 ore
_mng_cache: dict[str, Any] = {"at": 0.0, "v": dict(MNG_DEFAULT)}
_mng_fast_cache: dict[str, tuple[float, list[list[float]]]] = {}


async def mng_cfg(force: bool = False) -> dict[str, Any]:
    if not force and time.time() - float(_mng_cache["at"]) < 10:
        return dict(_mng_cache["v"])
    doc = await db.crus_config.find_one({"_id": "mng"}, {"_id": 0}) or {}
    v = dict(MNG_DEFAULT)
    v.update({k: doc[k] for k in MNG_DEFAULT if k in doc})
    _mng_cache["at"], _mng_cache["v"] = time.time(), v
    return dict(v)


async def mng_fast_candles(sym: str, tf: str) -> list[list[float]]:
    """Candele chiuse a 5 o 15 minuti, rinfrescate ogni 20 secondi."""
    key = "%s|%s" % (sym, tf)
    now = time.time()
    hit = _mng_fast_cache.get(key)
    if hit and now - hit[0] < 20:
        return hit[1]
    candles: list[list[float]] = []
    if _crus_cost["mode"] == "kraken" and tf == "15m":
        candles = await kr_candles(sym, tf)
    if not candles:
        candles = await exchange.get_klines(sym, tf, limit=60)
    if candles:
        _mng_fast_cache[key] = (now, candles)
        return candles
    return hit[1] if hit and now - hit[0] < 300 else []


def mng_cost_px(side: str, fill: float, qty: float, fee_pct: float, carry_usdc: float, slip_pct: float = MNG_SLIP_PCT) -> float:
    """Prezzo oltre il quale chiudere ora dà un guadagno NETTO: commissioni (entrata + uscita), funding maturato, piccolo margine."""
    unit = fill * (2.0 * fee_pct + slip_pct) / 100.0 + (carry_usdc / qty if qty > 0 else 0.0)
    return fill + unit if side == "long" else fill - unit


def mng_be_px(p: dict[str, Any], cfg: Config, now: float) -> float:
    days = max(0.0, (now - float(p["opened_ts"])) / 86400.0)
    carry = p["notional"] * (p["funding_day_pct"] / 100.0) * days if (p["leverage"] > 1.0 or p["side"] == "short") else 0.0
    return mng_cost_px(p["side"], float(p["fill_price"]), float(p["quantity"]), _desk_fee(cfg), carry)


def mng_assess(side: str, cur: float, atr1: float, c1h: list[list[float]], an: dict[str, Any], entry_bias: str,
               peak_r: float, r_now: float) -> dict[str, Any]:
    """CONTROLLO LENTO: quanti segnali di pericolo ci sono ora sul grafico 1h (0 = tutto come all'apertura)."""
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
    closed = c1h if len(c1h) > 13 else []             # le candele che arrivano sono gia' tutte chiuse
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
    """CONTROLLO LENTO: tieni / stringi (nuovo stop) / chiudi / avvisa. In perdita (o sotto i costi) NON chiude e NON stringe."""
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


def mng_classify(side: str, fill: float, peak_px: float, cur: float, c5: list[list[float]], c15: list[list[float]],
                 bias: Optional[str]) -> dict[str, Any]:
    """CONTROLLO VELOCE: la posizione sta tornando indietro dal suo massimo. Inversione o solo ritracciamento?
    Punteggio da 0 a 9: >= 4 inversione, <= 1 ritracciamento, in mezzo incerto."""
    sg = 1.0 if side == "long" else -1.0
    opp = "short" if side == "long" else "long"
    leg = (peak_px - fill) * sg
    depth = ((peak_px - cur) * sg) / leg if leg > 0 else 0.0
    score = 0
    why: list[str] = []
    calm: list[str] = []
    if depth >= 0.618:
        score += 2
        why.append("ha restituito oltre il 62%% del movimento (%.0f%%)" % (depth * 100))
    elif depth >= 0.382:
        score += 1
        why.append("ha restituito il %.0f%% del movimento" % (depth * 100))
    else:
        calm.append("ritracciamento ancora corto (%.0f%% del movimento)" % (depth * 100))
    lvl = "minimo" if side == "long" else "massimo"
    if len(c15) >= 10:
        last, prev = c15[-1], c15[-7:-1]
        broke = last[2] < min(c[4] for c in prev) if side == "long" else last[2] > max(c[3] for c in prev)
        if broke:
            score += 2
            why.append("rotto l'ultimo %s sul grafico a 15 minuti" % lvl)
        else:
            calm.append("struttura a 15 minuti intatta")
    if len(c5) >= 20:
        last, prev = c5[-1], c5[-9:-1]
        broke5 = last[2] < min(c[4] for c in prev) if side == "long" else last[2] > max(c[3] for c in prev)
        if broke5:
            score += 1
            why.append("rotto l'ultimo %s a 5 minuti" % lvl)
        atr5 = DK.atr(c5) or 0.0
        against = [c for c in c5[-3:] if (c[2] - c[1]) * sg < 0]
        if len(against) >= 2 and atr5 > 0 and sum(abs(c[2] - c[1]) for c in against) >= 1.5 * atr5:
            score += 1
            why.append("candele a 5 minuti decise contro la posizione")
        base = c5[-15:-3]
        vb = sum(c[5] for c in base) / len(base) if base else 0.0
        va = sum(c[5] for c in against) / len(against) if against else 0.0
        if against and vb > 0 and va > 1.3 * vb:
            score += 1
            why.append("volume in aumento sulle candele contro")
        elif against and vb > 0 and va < vb:
            calm.append("volume in calo sul ritracciamento")
    if bias == opp:
        score += 2
        why.append("l'indicazione di mercato (1h/4h/1d) è passata a %s" % opp)
    verdict = "inversione" if score >= 4 else ("ritracciamento" if score <= 1 else "incerto")
    return {"verdict": verdict, "score": score, "reasons": (why + (calm if verdict != "inversione" else []))[:5], "depth": round(depth, 3)}


def mng_fast_decide(*, side: str, style: str, mode: str, verdict: str, stop: float, cur: float, be_px: float, peak_px: float,
                    atr1: float) -> dict[str, Any]:
    """CONTROLLO VELOCE: tieni / chiudi (coprendo i costi) / stringi (stop sul pareggio dei costi) / avvisa. Solo in guadagno NETTO.
    prudente: inversione -> chiude; incerto -> chiude se resta meno del 50% del margine sopra i costi; ritracciamento -> chiude solo se resta meno del 20%.
    paziente: inversione -> chiude; incerto -> chiude se resta meno del 20%; ritracciamento -> tiene."""
    sg = 1.0 if side == "long" else -1.0
    hold = {"action": "tieni", "reason": None, "new_stop": None}
    cushion, total = (cur - be_px) * sg, (peak_px - be_px) * sg
    if total <= 0 or cushion <= 0.15 * max(atr1, 1e-12):          # non e' chiaramente in guadagno netto: non intervengo
        return hold
    frac = cushion / total
    why = None
    if verdict == "inversione":
        why = "inversione riconosciuta: chiudo coprendo i costi e tenendo il guadagno rimasto"
    elif verdict == "incerto" and frac < (0.5 if style == "prudente" else 0.2):
        why = "situazione incerta e il margine sopra i costi si sta esaurendo (resta il %.0f%%)" % (frac * 100)
    elif verdict == "ritracciamento" and style == "prudente" and frac < 0.2:
        why = "sembra un ritracciamento, ma il margine sopra i costi sta finendo (resta il %.0f%%)" % (frac * 100)
    if why is None:
        return hold
    if mode == "chiudi":
        return {"action": "chiudi", "reason": why, "new_stop": None}
    if mode == "stringi":
        new = max(stop, be_px) if side == "long" else min(stop, be_px)
        new = min(new, cur - 0.1 * atr1) if side == "long" else max(new, cur + 0.1 * atr1)
        if (new - stop) * sg <= 0.1 * atr1:
            return hold
        return {"action": "stringi", "reason": why + ": porto lo stop sul pareggio dei costi", "new_stop": new}
    return {"action": "avvisa", "reason": why + " (modalità solo avviso)", "new_stop": None}


def mng_view(o: dict[str, Any]) -> dict[str, Any]:
    managed = o.get("source") != "terminale"
    return {"managed": managed, "danger": o.get("mng_danger"), "signals": o.get("mng_signals") or [], "action": o.get("mng_action"),
            "reason": o.get("mng_reason"), "at": o.get("mng_last_ts"), "tightened": bool(o.get("mng_orig_stop")),
            "peak_r": o.get("mng_peak_r"), "orig_stop": o.get("mng_orig_stop"),
            "fast_verdict": o.get("mng_fast_verdict"), "fast_why": o.get("mng_fast_why") or [], "fast_at": o.get("mng_fast_ts"),
            "fast_action": o.get("mng_fast_action")}


async def mng_apply(p: dict[str, Any], cfg: Config, dec: dict[str, Any], cur: float, why: str, upd: dict[str, Any], tag: str) -> None:
    """Esegue la decisione (stringi / chiudi / avvisa) e salva le note sulla posizione."""
    sym, now = p["symbol"], time.time()
    if dec["action"] == "stringi":
        new = float(dec["new_stop"])
        upd["stop"] = new
        if not p.get("mng_orig_stop"):
            upd["mng_orig_stop"] = p["stop"]
        r = await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})
        if r.modified_count:
            await _desk_event(sym, "%s: stop stretto da %.6g a %.6g (guadagno protetto oltre i costi). %s" % (tag, p["stop"], new, why))
        return
    await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})
    if dec["action"] == "chiudi":
        await _desk_event(sym, "%s: chiudo in anticipo in guadagno netto. %s" % (tag, why))
        await _desk_close(p, cfg, "uscita_anticipata", cur)
    elif dec["action"] == "avvisa":
        sig = __import__("re").sub(r"\d+(\.\d+)?", "#", why)
        if p.get("mng_alert_sig") != sig or now - float(p.get("mng_alert_ts") or 0) > 3600:
            await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_alert_sig": sig, "mng_alert_ts": now}})
            await _desk_event(sym, "%s: attenzione. %s" % (tag, why))


async def mng_review(p: dict[str, Any], cfg: Config, mc: dict[str, Any], cur: float, r_now: float, peak: float) -> None:
    """CONTROLLO LENTO (grafico 1h)."""
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
    be_px = mng_be_px(p, cfg, now)
    a = mng_assess(side, cur, atr1, frames["1h"], an, str(p.get("bias_at_entry") or ""), peak, r_now)
    prev = int(p.get("mng_danger") or 0)
    dec = mng_decide(side=side, stop=float(p["stop"]), cur=cur, atr1=atr1, mode=str(mc["mode"]), danger=a["danger"], prev_danger=prev,
                     be_px=be_px, grace_ok=now - float(p["opened_ts"]) >= MNG_GRACE_MIN * 60)
    why = "; ".join(a["signals"]) if a["signals"] else "nessun segnale di pericolo"
    upd: dict[str, Any] = {"mng_last_ts": now, "mng_danger": a["danger"], "mng_signals": a["signals"], "mng_action": dec["action"],
                           "mng_reason": dec["reason"], "mng_peak_r": round(peak, 3), "mng_be_px": be_px}
    await mng_apply(p, cfg, dec, cur, "Motivi: %s" % why if dec["action"] != "avvisa" else "%s. Motivi: %s" % (dec["reason"], why), upd, "GESTIONE")


async def mng_fast_review(p: dict[str, Any], cfg: Config, mc: dict[str, Any], cur: float, pk_px: float, r_now: float, peak: float) -> None:
    """CONTROLLO VELOCE (5 e 15 minuti): inversione o solo ritracciamento?"""
    sym, side = p["symbol"], p["side"]
    c5, c15 = await mng_fast_candles(sym, "5m"), await mng_fast_candles(sym, "15m")
    if len(c5) < 20 or len(c15) < 10:
        return
    frames = {tf: await _desk_candles(sym, tf) for tf in ("1d", "4h", "1h")}
    an = DK.analyze_market(frames) if frames["1h"] else {}
    atr1 = DK.atr(frames["1h"]) or 0.0 if frames["1h"] else 0.0
    now = time.time()
    be_px = mng_be_px(p, cfg, now)
    cl = mng_classify(side, float(p["fill_price"]), pk_px, cur, c5, c15, an.get("bias") if an.get("ok") else None)
    dec = mng_fast_decide(side=side, style=str(mc["stile"]), mode=str(mc["mode"]), verdict=cl["verdict"], stop=float(p["stop"]), cur=cur,
                          be_px=be_px, peak_px=pk_px, atr1=atr1)
    upd: dict[str, Any] = {"mng_fast_ts": now, "mng_fast_verdict": cl["verdict"], "mng_fast_score": cl["score"], "mng_fast_why": cl["reasons"],
                           "mng_fast_action": dec["action"], "mng_peak_r": round(peak, 3), "mng_peak_px": pk_px, "mng_be_px": be_px}
    txt = "%s (punti %d). %s" % (cl["verdict"], cl["score"], "; ".join(cl["reasons"]))
    if dec["action"] == "tieni":
        await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})
        if p.get("mng_fast_verdict") != cl["verdict"] and cl["verdict"] != "inversione":
            await _desk_event(sym, "GESTIONE VELOCE: la posizione sta tornando indietro: %s. Per ora tengo." % txt)
        return
    await mng_apply(p, cfg, dec, cur, "%s. Motivi: %s" % (dec["reason"], txt), upd, "GESTIONE VELOCE")


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
        old_peak = p.get("mng_peak_r")
        peak = max(float(old_peak) if old_peak is not None else r_now, r_now)
        pk_px = p.get("mng_peak_px")                              # miglior prezzo visto finora (massimo per i long, minimo per gli short)
        if pk_px is None:
            pk_px = (fill + sg * max(peak, 0.0) * risk0) if old_peak is not None else cur
        pk_px = max(float(pk_px), cur) if sg > 0 else min(float(pk_px), cur)
        if old_peak is None or peak > float(old_peak) + 0.02 or abs(pk_px - float(p.get("mng_peak_px") or 0.0)) > 1e-12:
            await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_peak_r": round(peak, 3), "mng_peak_px": pk_px}})
        now = time.time()
        if mc.get("fast") and now - float(p["opened_ts"]) >= MNG_FAST_GRACE_MIN * 60 and peak >= MNG_FAST_MIN_PEAK \
                and peak - r_now >= MNG_FAST_MIN_GIVE and now - float(p.get("mng_fast_ts") or 0) >= MNG_FAST_EVERY:
            try:
                await mng_fast_review(p, cfg, mc, cur, pk_px, r_now, peak)
            except Exception as e:  # noqa: BLE001
                logger.exception("Gestione veloce: errore su %s: %s", p.get("symbol"), e)
            continue
        if now - float(p.get("mng_last_ts") or 0) >= every:
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
    if "fast" in body:
        new["fast"] = bool(body["fast"])
    if "mode" in body:
        if body["mode"] not in MNG_MODES:
            raise HTTPException(status_code=400, detail="Modalità non valida.")
        new["mode"] = body["mode"]
    if "stile" in body:
        if body["stile"] not in MNG_STILI:
            raise HTTPException(status_code=400, detail="Stile non valido.")
        new["stile"] = body["stile"]
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
        await asyncio.sleep(20)


