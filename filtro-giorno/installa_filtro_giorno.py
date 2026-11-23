#!/usr/bin/env python3
"""Filtro del giorno: il bot guarda se oggi il mercato (Bitcoin) sale o scende.
 - giorno di rialzo: niente nuovi short; giorno di ribasso: niente nuovi long (solo aperture automatiche)
 - posizioni gia' aperte contro un giorno CHIARAMENTE opposto: chiude con perdita piccola, senza aspettare lo stop
Parte SPENTO: lo accendi tu dal Cruscotto (scheda Gestione). Lancialo dalla cartella crypto-bot.
Copie di sicurezza in _backups/prima_filtro_giorno."""
import os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
S = os.path.join("backend", "server.py")
A = os.path.join("frontend", "src", "api.ts")
C = os.path.join("frontend", "app", "cruscotto.tsx")


def once(t, old, new, label):
    if t.count(old) != 1:
        raise SystemExit("STOP: punto da modificare non trovato o duplicato (%s). Non tocco nulla." % label)
    return t.replace(old, new)


for p in (S, A, C):
    if not os.path.exists(p):
        raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot (manca %s). Non tocco nulla." % p)
s = open(S, encoding="utf-8").read()
a = open(A, encoding="utf-8").read()
c = open(C, encoding="utf-8").read()
if "FILTRO DEL GIORNO (mercato generale" in s:
    raise SystemExit("Gia' installato. Non tocco nulla.")
if "# GESTIONE TRADE" not in s or "// GESTIONE-INIZIO" not in a:
    raise SystemExit("STOP: installa prima gestione-veloce. Non tocco nulla.")
block = open(os.path.join(HERE, "files", "regime_block.py"), encoding="utf-8").read()
api_new = open(os.path.join(HERE, "files", "api_gestione.ts"), encoding="utf-8").read()

# ---- backend
s = once(s, 'MNG_DEFAULT: dict[str, Any] = {"enabled": True, "mode": "chiudi", "every_min": 15, "fast": True, "stile": "prudente"}',
         'MNG_DEFAULT: dict[str, Any] = {"enabled": True, "mode": "chiudi", "every_min": 15, "fast": True, "stile": "prudente", "regime": False, "regime_exit": True}', "default")
s = once(s, "async def mng_manage(cfg: Config) -> None:\n", block + "async def mng_manage(cfg: Config) -> None:\n", "blocco")
s = once(s, '        now = time.time()\n        if mc.get("fast") and now - float(p["opened_ts"]) >= MNG_FAST_GRACE_MIN * 60 and peak >= MNG_FAST_MIN_PEAK \\\n',
         '        if mc.get("regime") and mc.get("regime_exit"):\n'
         '            try:\n'
         '                if await regime_exit_check(p, cfg, cur, r_now):\n'
         '                    continue\n'
         '            except Exception as e:  # noqa: BLE001\n'
         '                logger.exception("Filtro del giorno: errore su %s: %s", p.get("symbol"), e)\n'
         '        now = time.time()\n        if mc.get("fast") and now - float(p["opened_ts"]) >= MNG_FAST_GRACE_MIN * 60 and peak >= MNG_FAST_MIN_PEAK \\\n', "uscita")
s = once(s, '"kind": "uscita" if p.get("close_reason") == "uscita_anticipata" else "stop_stretto", "status": "open",',
         '"kind": ("regime" if p.get("mng_rg_exit") else "uscita") if p.get("close_reason") == "uscita_anticipata" else "stop_stretto", "status": "open",', "ombra")
s = once(s, '"net": round(sum(d["delta"] for d in done), 4), "net_uscita": net("uscita"), "net_stop": net("stop_stretto"),',
         '"net": round(sum(d["delta"] for d in done), 4), "net_uscita": net("uscita"), "net_stop": net("stop_stretto"),\n'
         '            "net_regime": net("regime"), "n_regime": len([d for d in done if d["kind"] == "regime"]),', "riepilogo")
s = once(s, 'return {"config": await mng_cfg(True), "summary": await mng_summary()}',
         'return {"config": await mng_cfg(True), "summary": await mng_summary(), "regime": await regime_read()}', "get")
s = once(s, '    if "fast" in body:\n        new["fast"] = bool(body["fast"])\n',
         '    if "fast" in body:\n        new["fast"] = bool(body["fast"])\n'
         '    if "regime" in body:\n        new["regime"] = bool(body["regime"])\n'
         '    if "regime_exit" in body:\n        new["regime_exit"] = bool(body["regime_exit"])\n', "set")
s = once(s, "        # rischio TOTALE (vale anche per le aperture manuali)\n",
         "        if auto:\n            why += await regime_block_why(side)       # filtro del giorno (se acceso)\n        # rischio TOTALE (vale anche per le aperture manuali)\n", "ingresso")

# ---- frontend api
i, j = a.index("// GESTIONE-INIZIO"), a.index("// GESTIONE-FINE") + len("// GESTIONE-FINE")
a = a[:i] + api_new.strip("\n") + a[j:]

# ---- frontend cruscotto
UI = '''                <Text style={[st.txt, { marginTop: 10, fontWeight: "700" }]}>Filtro del giorno</Text>
                <Text style={[st.dim, { marginBottom: 6 }]}>Guarda se oggi il mercato (Bitcoin) sale o scende. Giorno di rialzo: niente nuovi short. Giorno di ribasso: niente nuovi long. Se una posizione è contro un giorno chiaramente opposto e sta già perdendo, la chiude con perdita piccola senza aspettare lo stop.</Text>
                {mg.regime ? (
                  <Text style={[st.txt, { marginBottom: 6, color: mg.regime.label === "rialzo" ? UP : mg.regime.label === "ribasso" ? DOWN : TXT }]}>
                    Oggi: {mg.regime.label} ({mg.regime.score > 0 ? "+" : ""}{mg.regime.score} punti su 5) · {mg.regime.reasons.slice(0, 2).join(", ")}
                  </Text>
                ) : (<Text style={[st.dim, { marginBottom: 6 }]}>Oggi: lettura del mercato non disponibile.</Text>)}
                <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
                  {[{ k: true, t: "Filtro acceso" }, { k: false, t: "Filtro spento" }].map((o) => (
                    <Pressable key={String(o.k)} onPress={() => saveMng({ regime: o.k }, o.k ? "Filtro del giorno acceso." : "Filtro del giorno spento.")} style={[st.chip, !!mg.config.regime === o.k && { backgroundColor: WARN }]}>
                      <Text style={[st.txt, { fontWeight: "700", color: !!mg.config.regime === o.k ? "#000" : TXT }]}>{o.t}</Text>
                    </Pressable>
                  ))}
                </View>
                <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
                  {[{ k: true, t: "Chiude con perdita piccola" }, { k: false, t: "Solo blocca gli ingressi" }].map((o) => (
                    <Pressable key={String(o.k)} onPress={() => saveMng({ regime_exit: o.k }, "Impostazione salvata.")} style={[st.chip, (mg.config.regime_exit !== false) === o.k && { backgroundColor: WARN }]}>
                      <Text style={[st.txt, { fontWeight: "700", color: (mg.config.regime_exit !== false) === o.k ? "#000" : TXT }]}>{o.t}</Text>
                    </Pressable>
                  ))}
                </View>
'''
if "Filtro del giorno" in c:
    raise SystemExit("Gia' installato (schermata). Non tocco nulla.")
c = once(c, '                <Text style={[st.txt, { marginTop: 8, fontWeight: "700" }]}>Ha davvero aiutato?</Text>\n',
         UI + '                <Text style={[st.txt, { marginTop: 8, fontWeight: "700" }]}>Ha davvero aiutato?</Text>\n', "ui")
c = once(c, '{it.kind === "uscita" ? "uscita anticipata" : "stop stretto"}',
         '{it.kind === "uscita" ? "uscita anticipata" : it.kind === "regime" ? "uscita filtro del giorno" : "stop stretto"}', "etichetta")
c = once(c, '                  {mg.summary.items.slice(0, 5).map((it, i) => (\n',
         '                  {!!mg.summary.n_regime && <Text style={st.dim}>Filtro del giorno: {(mg.summary.net_regime ?? 0) >= 0 ? "+" : "-"}${Math.abs(mg.summary.net_regime ?? 0).toFixed(2)} ({mg.summary.n_regime} uscite)</Text>}\n'
         '                  {mg.summary.items.slice(0, 5).map((it, i) => (\n', "riga")

os.makedirs("_backups/prima_filtro_giorno", exist_ok=True)
for p in (S, A, C):
    shutil.copy(p, os.path.join("_backups/prima_filtro_giorno", os.path.basename(p)))
open(S, "w", encoding="utf-8").write(s); open(A, "w", encoding="utf-8").write(a); open(C, "w", encoding="utf-8").write(c)
print("Fatto. Ora git add/commit/push. Il filtro parte SPENTO: accendilo dal Cruscotto > Gestione.")
