async def mng_prov(docs: list[dict[str, Any]]) -> dict[str, float]:
    """Confronto PROVVISORIO per le operazioni ombra ancora aperte: cosa varrebbero se si chiudessero adesso al prezzo di ora."""
    out: dict[str, float] = {}
    pend = [d for d in docs if d.get("status") == "open"]
    if not pend:
        return out
    cfg = await get_config()
    for d in pend:
        try:
            cur = await _desk_price(d["symbol"])
            if cur is None:
                continue
            days = max(0.0, (time.time() - float(d["opened_ts"])) / 86400.0)
            carry = d["notional"] * (d["fd"] / 100.0) * days if (d["lev"] > 1.0 or d["side"] == "short") else 0.0
            rest = DK.pnl(d["side"], d["fill"], float(cur), d["qty"], _desk_fee(cfg), carry, d["margin"])
            out[d["pos_id"]] = round(float(d["actual"]) - (float(d["partial"]) + rest), 4)
        except Exception:  # noqa: BLE001
            continue
    return out


async def mng_summary() -> dict[str, Any]:
    docs = await db.mng_shadow.find({}, {"_id": 0}).sort("created_ts", -1).to_list(500)
    done = [d for d in docs if d.get("status") == "done"]
    helped = [d for d in done if d["delta"] > 0.005]
    hurt = [d for d in done if d["delta"] < -0.005]
    prov = await mng_prov(docs)
    def net(k: str) -> float:
        return round(sum(d["delta"] for d in done if d["kind"] == k), 4)
    return {"n_done": len(done), "n_pending": len([d for d in docs if d.get("status") == "open"]), "helped": len(helped), "hurt": len(hurt),
            "net": round(sum(d["delta"] for d in done), 4), "net_uscita": net("uscita"), "net_stop": net("stop_stretto"),
            "net_regime": net("regime"), "n_regime": len([d for d in done if d["kind"] == "regime"]),
            "n_uscita": len([d for d in done if d["kind"] == "uscita"]), "n_stop": len([d for d in done if d["kind"] == "stop_stretto"]),
            "prov_net": round(sum(prov.values()), 4), "n_prov": len(prov),
            "prov_helped": len([v for v in prov.values() if v > 0.005]), "prov_hurt": len([v for v in prov.values() if v < -0.005]),
            "items": [{"symbol": d["symbol"], "side": d["side"], "kind": d["kind"], "status": d["status"], "actual": round(d["actual"], 4),
                       "shadow": d.get("shadow_pnl"), "delta": d.get("delta"), "outcome": d.get("outcome"), "at": d.get("created_at"),
                       "prov_delta": prov.get(d["pos_id"])}
                      for d in docs[:10]]}
