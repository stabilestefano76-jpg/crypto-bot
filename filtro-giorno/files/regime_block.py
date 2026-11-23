# ====================================================================
# FILTRO DEL GIORNO (mercato generale: rialzo / ribasso / incerto)
# ====================================================================
RG_SYMBOL = "BTCUSDC"        # il "termometro" del mercato
RG_BLOCK_SCORE = 3           # da 3 punti in su (su 5) il giorno e' di rialzo/ribasso: niente ingressi contro
RG_EXIT_SCORE = 4            # da 4 punti in su e' "chiaro": si possono chiudere le posizioni contro
RG_EXIT_R = -0.25            # chiude con perdita piccola solo se la posizione e' gia' a -0.25R o peggio
RG_EXIT_HITS = 3             # deve essere vero per 3 controlli di fila (circa 1 minuto)
RG_GRACE_S = 600             # nei primi 10 minuti di vita di una posizione non tocca nulla
_rg_cache: dict[str, Any] = {"at": 0.0, "v": None}


def regime_calc(c1h: list[list[float]], c4h: list[list[float]], cur: float) -> dict[str, Any]:
    """Punteggio da -5 (ribasso forte) a +5 (rialzo forte). Candele nel formato [t, apertura, chiusura, massimo, minimo, volume]."""
    closes = [c[2] for c in c1h]
    atr1 = DK.atr(c1h)
    e20, e50 = DK.ema(closes, 20), DK.ema(closes, 50)
    if not atr1 or atr1 <= 0 or e20 is None or e50 is None or len(closes) < 10:
        return {"ok": False, "reason": "dati insufficienti"}
    score, why, mvpts = 0, [], 0
    mv = (cur - closes[-9]) / atr1                      # movimento delle ultime 8 ore in unita' di mosso orario (in 8 ore un mercato fermo fa gia' ~2.8 unita' per caso)
    pct = (cur / closes[-9] - 1.0) * 100.0
    if mv >= 6.2: score += 2; mvpts = 2; why.append("%+.1f%% in 8 ore: rialzo deciso" % pct)
    elif mv >= 4.2: score += 1; mvpts = 1; why.append("%+.1f%% in 8 ore: sale" % pct)
    elif mv <= -6.2: score -= 2; mvpts = -2; why.append("%+.1f%% in 8 ore: ribasso deciso" % pct)
    elif mv <= -4.2: score -= 1; mvpts = -1; why.append("%+.1f%% in 8 ore: scende" % pct)
    else: why.append("%+.1f%% in 8 ore: movimento piccolo" % pct)
    if cur > e20: score += 1; why.append("prezzo sopra la media a 1 ora")
    else: score -= 1; why.append("prezzo sotto la media a 1 ora")
    if e20 > e50: score += 1; why.append("media veloce sopra quella lenta (1h)")
    else: score -= 1; why.append("media veloce sotto quella lenta (1h)")
    c4 = [c[2] for c in c4h]
    e4 = DK.ema(c4, 20) if len(c4) >= 20 else None
    if e4 is not None:
        if c4[-1] > e4: score += 1; why.append("a 4 ore chiude sopra la media")
        else: score -= 1; why.append("a 4 ore chiude sotto la media")
    # serve anche un movimento vero nelle ultime 8 ore: senza, tre "sopra la media" messi insieme per caso non bastano
    label = "rialzo" if (score >= RG_BLOCK_SCORE and mvpts > 0) else "ribasso" if (score <= -RG_BLOCK_SCORE and mvpts < 0) else "incerto"
    return {"ok": True, "label": label, "score": score, "reasons": why, "symbol": RG_SYMBOL, "price": cur, "at": time.time()}


async def regime_read(force: bool = False) -> Optional[dict[str, Any]]:
    if not force and _rg_cache["v"] is not None and time.time() - float(_rg_cache["at"]) < 60:
        return _rg_cache["v"]
    try:
        c1h = await _desk_candles(RG_SYMBOL, "1h")
        c4h = await _desk_candles(RG_SYMBOL, "4h")
        cur = await _desk_price(RG_SYMBOL)
        if not c1h or cur is None:
            return None
        v = regime_calc(c1h, c4h or [], float(cur))
    except Exception as e:  # noqa: BLE001
        logger.warning("Filtro del giorno: lettura non riuscita: %s", e)
        return None
    if not v.get("ok"):
        return None
    _rg_cache["at"], _rg_cache["v"] = time.time(), v
    return v


async def regime_block_why(side: str) -> list[str]:
    """Motivi per NON aprire (solo aperture automatiche). Lista vuota = via libera."""
    mc = await mng_cfg()
    if not mc.get("regime"):
        return []
    rg = await regime_read()
    if not rg:
        return []
    if (rg["label"] == "rialzo" and side == "short") or (rg["label"] == "ribasso" and side == "long"):
        return ["filtro del giorno: oggi il mercato e' in %s (%s, %+d punti su 5): niente %s" % (rg["label"], RG_SYMBOL.replace("USDC", ""), rg["score"], side)]
    return []


async def regime_exit_check(p: dict[str, Any], cfg: Config, cur: float, r_now: float) -> bool:
    """Chiude con perdita piccola una posizione contro un giorno chiaramente opposto. True = ha chiuso."""
    rg = await regime_read()
    against = bool(rg) and abs(rg["score"]) >= RG_EXIT_SCORE and \
        ((rg["label"] == "rialzo" and p["side"] == "short") or (rg["label"] == "ribasso" and p["side"] == "long"))
    if not against or r_now > RG_EXIT_R or time.time() - float(p["opened_ts"]) < RG_GRACE_S:
        if p.get("mng_rg_hits"):
            await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_rg_hits": 0}})
        return False
    hits = int(p.get("mng_rg_hits") or 0) + 1
    if hits < RG_EXIT_HITS:
        await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_rg_hits": hits}})
        return False
    await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_rg_exit": True, "mng_rg_hits": hits}})
    await _desk_event(p["symbol"], "FILTRO DEL GIORNO: il mercato e' in %s (%+d punti su 5) e questa posizione %s e' a %.2fR. Chiudo ora con perdita piccola, senza aspettare lo stop. %s"
                      % (rg["label"], rg["score"], p["side"], r_now, "; ".join(rg["reasons"][:3])))
    await _desk_close(p, cfg, "uscita_anticipata", cur)
    return True


