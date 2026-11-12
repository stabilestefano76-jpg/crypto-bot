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


_crus_kr_cache: dict[str, Any] = {"at": 0.0, "val": None}
_KR_NAMES = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "XXRP": "XRP", "XLTC": "LTC", "XXDG": "DOGE", "ZEUR": "EUR", "ZUSD": "USD", "ZGBP": "GBP"}


def kr_sign(secret_b64: str, path: str, nonce: int, postdata: str) -> str:
    """Firma Kraken (API spot): HMAC-SHA512 di percorso + SHA256(nonce + dati), con il segreto decodificato base64."""
    import base64, hashlib, hmac
    sha = hashlib.sha256((str(nonce) + postdata).encode()).digest()
    mac = hmac.new(base64.b64decode(secret_b64), path.encode() + sha, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


async def kr_private(path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Chiamata firmata a Kraken. SOLO percorsi di lettura (Balance, TradeBalance, OpenOrders...): gli ordini non sono previsti."""
    from urllib.parse import urlencode
    if path.split("/")[-1] not in ("Balance", "TradeBalance", "OpenOrders", "ClosedOrders", "TradesHistory", "Ledgers"):
        raise RuntimeError("Percorso Kraken non consentito: solo lettura")
    doc = await db.exchange_creds.find_one({"_id": "kraken"}, {"_id": 0})
    if not doc:
        raise RuntimeError("Nessuna chiave Kraken collegata")
    nonce = time.time_ns() // 1000
    post = urlencode({"nonce": nonce, **(params or {})})
    headers = {"API-Key": decrypt_str(doc["api_key"]), "API-Sign": kr_sign(decrypt_str(doc["api_secret"]), path, nonce, post),
               "Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post("https://api.kraken.com" + path, content=post, headers=headers)
    return r.json()


def kr_error_text(errs: list[str]) -> str:
    t = "; ".join(errs)
    if "Permission denied" in t:
        return "Manca un permesso sulla chiave (serve 'Query' fra i fondi). " + t
    if "Invalid key" in t or "Invalid signature" in t:
        return "Chiave o segreto non corretti. " + t
    return t


async def crus_kraken() -> dict[str, Any]:
    """Saldi Kraken (spot), SOLA LETTURA. Cache 30 secondi."""
    if _crus_kr_cache["val"] is not None and time.time() - _crus_kr_cache["at"] < 30:
        return _crus_kr_cache["val"]
    out: dict[str, Any] = {"connected": False, "error": None, "total_eur": None, "coins": [], "masked": None}
    try:
        doc = await db.exchange_creds.find_one({"_id": "kraken"}, {"_id": 0, "api_key_masked": 1})
        if not doc:
            out["error"] = "Nessuna chiave Kraken collegata"
        else:
            out["masked"] = doc.get("api_key_masked")
            b = await kr_private("/0/private/Balance")
            if b.get("error"):
                out["error"] = kr_error_text(b["error"])
            else:
                coins = []
                for k, v in (b.get("result") or {}).items():
                    try:
                        q = float(v)
                    except (TypeError, ValueError):
                        continue
                    if q > 0:
                        coins.append({"coin": _KR_NAMES.get(k, k), "qty": q})
                tb = await kr_private("/0/private/TradeBalance", {"asset": "ZEUR"})
                tot = None
                if not tb.get("error"):
                    try:
                        tot = round(float((tb.get("result") or {}).get("eb")), 2)
                    except (TypeError, ValueError):
                        tot = None
                out.update({"connected": True, "coins": coins[:15], "total_eur": tot})
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:160]
    _crus_kr_cache.update({"at": time.time(), "val": out})
    return out


@api.post("/kraken/connect")
async def kraken_connect(body: dict[str, Any]) -> dict[str, Any]:
    if not os.environ.get("ENCRYPTION_SECRET", "").strip():
        raise HTTPException(status_code=400, detail="Prima imposta la variabile ENCRYPTION_SECRET su Railway.")
    key, sec = str(body.get("api_key") or "").strip(), str(body.get("api_secret") or "").strip()
    if not key or not sec:
        raise HTTPException(status_code=400, detail="Servono chiave e segreto")
    try:
        import base64
        base64.b64decode(sec, validate=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Il segreto non sembra corretto: deve essere copiato per intero.")
    await db.exchange_creds.update_one({"_id": "kraken"}, {"$set": {
        "api_key": encrypt_str(key), "api_secret": encrypt_str(sec),
        "api_key_masked": (key[:4] + "…" + key[-4:]) if len(key) > 8 else "***",
        "connected_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
    _crus_kr_cache.update({"at": 0.0, "val": None})
    res = await crus_kraken()
    if not res["connected"]:
        await db.exchange_creds.delete_one({"_id": "kraken"})
        _crus_kr_cache.update({"at": 0.0, "val": None})
        raise HTTPException(status_code=400, detail="Collegamento non riuscito: " + str(res.get("error")))
    return res


@api.post("/kraken/disconnect")
async def kraken_disconnect() -> dict[str, Any]:
    await db.exchange_creds.delete_one({"_id": "kraken"})
    _crus_kr_cache.update({"at": 0.0, "val": None})
    return {"ok": True}


_kr_cache: dict[str, Any] = {"tickers": (0.0, {}), "candles": {}}
_KR_BASE = {"BTC": "XBT"}


async def kr_pub(path: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Dati PUBBLICI dei future Kraken (nessuna chiave). None se non risponde."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://futures.kraken.com" + path, params=params)
        j = r.json()
        return j if isinstance(j, dict) else None
    except Exception:  # noqa: BLE001
        return None


async def kr_tickers() -> dict[str, dict[str, Any]]:
    at, cur = _kr_cache["tickers"]
    if cur and time.time() - at < 4:
        return cur
    j = await kr_pub("/derivatives/api/v3/tickers")
    if j and j.get("result") == "success":
        t = {x["symbol"]: x for x in j.get("tickers", []) if x.get("symbol") and x.get("tag") == "perpetual" and not x.get("suspended")}
        if t:
            _kr_cache["tickers"] = (time.time(), t)
            return t
    return cur if cur and time.time() - at < 120 else {}


def kr_symbol(bybit_symbol: str, tickers: dict[str, dict[str, Any]]) -> Optional[str]:
    """BTCUSDC -> PF_XBTUSD (se esiste fra i perpetui di Kraken)."""
    if not bybit_symbol.endswith("USDC"):
        return None
    base = bybit_symbol[:-4]
    for b in (_KR_BASE.get(base, base), base):
        sym = "PF_%sUSD" % b
        if sym in tickers:
            return sym
    return None


async def kr_price(bybit_symbol: str) -> Optional[float]:
    t = await kr_tickers()
    sym = kr_symbol(bybit_symbol, t)
    if not sym:
        return None
    x = t[sym]
    for k in ("last", "markPrice"):
        try:
            v = float(x.get(k))
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return None


async def kr_book(bybit_symbol: str) -> Optional[tuple[list[list[float]], list[list[float]]]]:
    t = await kr_tickers()
    sym = kr_symbol(bybit_symbol, t)
    if not sym:
        return None
    j = await kr_pub("/derivatives/api/v3/orderbook", {"symbol": sym})
    try:
        ob = (j or {}).get("orderBook") or {}
        bids = [[float(r[0]), float(r[1])] for r in ob.get("bids", [])][:50]
        asks = [[float(r[0]), float(r[1])] for r in ob.get("asks", [])][:50]
        if not bids or not asks or bids[0][0] <= 0 or asks[0][0] < bids[0][0]:
            return None
        return bids, asks
    except (TypeError, ValueError, IndexError):
        return None


async def kr_candles(bybit_symbol: str, tf: str) -> list[list[float]]:
    """Candele dei future Kraken nel formato del bot [tempo_s, apertura, chiusura, massimo, minimo, volume], senza l'ultima ancora in formazione."""
    if tf not in ("15m", "1h", "4h", "1d"):
        return []
    t = await kr_tickers()
    sym = kr_symbol(bybit_symbol, t)
    if not sym:
        return []
    key = sym + "|" + tf
    hit = _kr_cache["candles"].get(key)
    if hit and time.time() - hit[0] < (60 if tf in ("15m", "1h") else 180):
        return hit[1]
    j = await kr_pub("/api/charts/v1/trade/%s/%s" % (sym, tf), {"count": 301})
    out: list[list[float]] = []
    try:
        for c in (j or {}).get("candles", []):
            out.append([float(c["time"]) / 1000.0, float(c["open"]), float(c["close"]), float(c["high"]), float(c["low"]), float(c.get("volume") or 0)])
    except (TypeError, ValueError, KeyError):
        out = []
    out.sort(key=lambda r: r[0])
    if len(out) > 1:
        out = out[:-1]
    out = out[-300:]
    if len(out) >= 50:
        _kr_cache["candles"][key] = (time.time(), out)
        return out
    return hit[1] if hit and time.time() - hit[0] < 1800 else []


async def _desk_fill(symbol: str, side: str, ref_price: float, cfg: Any, *, quote_budget: Optional[float] = None, base_qty: Optional[float] = None) -> tuple[float, dict[str, Any]]:
    """Prezzo a cui verrebbe eseguito un ordine a mercato in Analisi e rischio: libro di Kraken se il modo e' Kraken e la moneta c'e' li', altrimenti Bybit."""
    if _crus_cost["mode"] == "kraken" and cfg.sim_realistic_fills:
        book = await kr_book(symbol)
        if book:
            bids, asks = book
            avg = walk_book(asks, quote_budget=quote_budget) if side == "buy" else walk_book(bids, base_qty=base_qty)
            if avg:
                spread = (asks[0][0] / bids[0][0] - 1.0) * 100.0
                cost = (avg / ref_price - 1.0) * 100.0 if side == "buy" else (1.0 - avg / ref_price) * 100.0
                return avg, {"fill_model": "book", "source": "kraken", "spread_pct": round(spread, 4), "cost_pct": round(cost, 4)}
    return await simulate_fill(symbol, side, ref_price, cfg, quote_budget=quote_budget, base_qty=base_qty)


@api.get("/cruscotto/confronto")
async def cruscotto_confronto() -> dict[str, Any]:
    """Prezzi di Bybit e di Kraken a confronto, per controllare che i dati corrispondano a quello che vedi sui due siti."""
    dc = await _desk_cfg()
    t = await kr_tickers()
    rows = []
    for sym in list(dc["symbols"])[:8]:
        try:
            bp = price_feed.get(sym) or await price_feed.price_or_rest(sym)
        except Exception:  # noqa: BLE001
            bp = None
        ks = kr_symbol(sym, t)
        x = t.get(ks) if ks else None
        row: dict[str, Any] = {"symbol": sym, "bybit": float(bp) if bp else None, "kraken_symbol": ks, "kraken": None, "diff_pct": None,
                               "kraken_spread_pct": None, "funding_day_pct": None}
        if x:
            try:
                row["kraken"] = float(x.get("last") or x.get("markPrice"))
                if x.get("bid") and x.get("ask"):
                    row["kraken_spread_pct"] = round((float(x["ask"]) / float(x["bid"]) - 1.0) * 100.0, 4)
                if x.get("relativeFundingRate") is not None:
                    row["funding_day_pct"] = round(float(x["relativeFundingRate"]) * 24.0 * 100.0, 4)
                if row["bybit"] and row["kraken"]:
                    row["diff_pct"] = round((row["kraken"] / row["bybit"] - 1.0) * 100.0, 3)
            except (TypeError, ValueError):
                pass
        rows.append(row)
    return {"kraken_ok": bool(t), "rows": rows,
            "note": "Finanziamento: stima ricavata dal tasso relativo di Kraken per 24 ore. Positivo = i long pagano e gli short incassano (da verificare sul sito)."}


_crus_cost: dict[str, Any] = {"mode": "bybit", "fee": None}
KRAKEN_PERP_FEE_SIDE = 0.05      # % per lato: taker dei future Kraken (maker 0,02%): scelta prudente, tutti gli ordini a mercato


def _desk_fee(cfg: Any) -> float:
    """Commissione per lato usata da Analisi e rischio: quella di Bybit spot (impostazioni del bot) oppure quella dei future Kraken."""
    f = _crus_cost.get("fee")
    return float(f) if f is not None else float(cfg.sim_fee_pct)


async def crus_cost_load() -> None:
    doc = await db.crus_config.find_one({"_id": "cost"}, {"_id": 0}) or {}
    mode = doc.get("mode") if doc.get("mode") in ("bybit", "kraken") else "bybit"
    _crus_cost.update({"mode": mode, "fee": KRAKEN_PERP_FEE_SIDE if mode == "kraken" else None})
    rr = (await db.crus_config.find_one({"_id": "rr"}, {"_id": 0}) or {}).get("rr")
    _crus_rr["rr"] = float(rr) if rr else None


_crus_rr: dict[str, Any] = {"rr": None}      # None = automatico (come prima: obiettivo a 4 ATR, rendimento/rischio fra 1,6 e 2,7 circa)


def _desk_risk_plan(side: str, price: float, candles_1h: list[list[float]], atr_val: float, **kw: Any) -> dict[str, Any]:
    """Piano di Analisi e rischio; se hai scelto un rendimento/rischio (RR) fisso, l'obiettivo si mette a RR volte la distanza dello stop."""
    pl = DK.risk_plan(side, price, candles_1h, atr_val, **kw)
    rr = _crus_rr.get("rr")
    if rr and pl.get("risk_pct_price"):
        risk_frac = pl["risk_pct_price"] / 100.0
        gain_frac = float(rr) * risk_frac
        pl["target"] = price * (1.0 + gain_frac) if side == "long" else price * (1.0 - gain_frac)
        pl["gain_pct_price"] = gain_frac * 100.0
        pl["rr"] = float(rr)
        pl["expected_gain"] = pl["notional"] * (gain_frac - pl["cost_pct"] / 100.0)
    return pl


@api.post("/cruscotto/impostazioni")
async def cruscotto_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Capitale del portafoglio simulato e rendimento/rischio (RR) delle nuove analisi."""
    out: dict[str, Any] = {}
    if "rr" in body:
        try:
            v = float(body["rr"] or 0.0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="RR non valido")
        if v != 0.0 and not (0.8 <= v <= 6.0):
            raise HTTPException(status_code=400, detail="RR tra 0,8 e 6 (oppure automatico).")
        await db.crus_config.update_one({"_id": "rr"}, {"$set": {"rr": v if v else None}}, upsert=True)
        await crus_cost_load()
        out["rr"] = _crus_rr["rr"]
    if "capital" in body:
        try:
            c = float(body["capital"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Capitale non valido")
        await desk_set_config({"capital": c})                  # controlla i limiti, rifiuta se ci sono posizioni aperte e rimette il contante a c
        await db.desk_equity.delete_many({})                    # la curva riparte dal nuovo capitale
        await crus_snapshot(force=True)
        out["capital"] = c
    return {"ok": True, **out}


@api.post("/cruscotto/costs")
async def cruscotto_set_costs(body: dict[str, Any]) -> dict[str, Any]:
    mode = str(body.get("mode") or "")
    if mode not in ("bybit", "kraken"):
        raise HTTPException(status_code=400, detail="Scelta non valida")
    if await db.desk_positions.find_one({"status": "open"}, {"_id": 1}):
        raise HTTPException(status_code=400, detail="Chiudi prima le posizioni aperte: cambiare i costi a meta' falserebbe i risultati.")
    if mode == "kraken" and not await kr_tickers():
        raise HTTPException(status_code=400, detail="Kraken non risponde adesso: resto su Bybit. Riprova fra poco.")
    await db.crus_config.update_one({"_id": "cost"}, {"$set": {"mode": mode}}, upsert=True)
    await crus_cost_load()
    return {"ok": True, "mode": mode}


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
        "settings": {"capital": cap0, "rr": _crus_rr["rr"], "can_change_capital": len(st["open"]) == 0},
        "kraken": await crus_kraken(),
        "costs": {"mode": _crus_cost["mode"], "fee_side_pct": round(_desk_fee(await get_config()), 4),
                  "round_trip_pct": round(2 * _desk_fee(await get_config()), 4)},
    }


async def crus_loop() -> None:
    await asyncio.sleep(3)
    while True:
        try:
            await crus_cost_load()
            await crus_snapshot()
        except Exception as e:  # noqa: BLE001
            logger.exception("Cruscotto: errore nel salvataggio della curva: %s", e)
        await asyncio.sleep(120)


