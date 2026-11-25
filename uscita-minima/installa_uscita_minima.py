#!/usr/bin/env python3
"""1) 'Guadagno minimo per uscire in anticipo' (scheda Gestione): sotto quel guadagno (in R) il bot NON chiude in anticipo e lascia
   decidere stop e obiettivo. Parte SPENTO (0 = come adesso). Non riguarda l'uscita del filtro del giorno (quella e' per limitare le perdite).
2) 'Provvisorio' in 'Ha davvero aiutato?': per le uscite ancora in attesa mostra cosa varrebbero se si chiudessero adesso.
Lancialo dalla cartella crypto-bot. Copie in _backups/prima_uscita_minima."""
import os, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
S = os.path.join("backend", "server.py")
A = os.path.join("frontend", "src", "api.ts")
C = os.path.join("frontend", "app", "cruscotto.tsx")
for p in (S, A, C):
    if not os.path.exists(p):
        raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot (manca %s). Non tocco nulla." % p)
s = open(S, encoding="utf-8").read(); a = open(A, encoding="utf-8").read(); c = open(C, encoding="utf-8").read()
if "exit_min_r" in s:
    raise SystemExit("Gia' installato. Non tocco nulla.")
if "FILTRO DEL GIORNO (mercato generale" not in s:
    raise SystemExit("STOP: installa prima filtro-giorno. Non tocco nulla.")


def once(t, old, new, label):
    if t.count(old) != 1:
        raise SystemExit("STOP: punto da modificare non trovato o duplicato (%s). Non tocco nulla." % label)
    return t.replace(old, new)


summ = open(os.path.join(HERE, "files", "summary_new.py"), encoding="utf-8").read()
api_new = open(os.path.join(HERE, "files", "api_gestione.ts"), encoding="utf-8").read()
# ---- backend
s = once(s, '"regime": False, "regime_exit": True}', '"regime": False, "regime_exit": True, "exit_min_r": 0.0}', "default")
s = once(s, '    if "regime" in body:\n        new["regime"] = bool(body["regime"])\n',
         '    if "exit_min_r" in body:\n'
         '        try:\n            v = float(body["exit_min_r"])\n        except (TypeError, ValueError):\n            raise HTTPException(status_code=400, detail="Guadagno minimo non valido.")\n'
         '        if not (0.0 <= v <= 3.0):\n            raise HTTPException(status_code=400, detail="Guadagno minimo: da 0 a 3 (0 = spento).")\n'
         '        new["exit_min_r"] = round(v, 2)\n'
         '    if "regime" in body:\n        new["regime"] = bool(body["regime"])\n', "set")
GATE = '''    if dec["action"] == "chiudi":                                 # guadagno minimo per uscire in anticipo (0 = spento)
        try:
            mr = float((await mng_cfg()).get("exit_min_r") or 0.0)
            risk0 = float(p.get("mng_risk0") or 0.0)
            sg0 = 1.0 if p["side"] == "long" else -1.0
            r_cur = (cur - float(p["fill_price"])) * sg0 / risk0 if risk0 > 0 else None
        except Exception:  # noqa: BLE001
            mr, r_cur = 0.0, None
        if mr > 0 and r_cur is not None and r_cur < mr:
            await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})
            sig = "min%.2f" % mr
            if p.get("mng_minr_sig") != sig:
                await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": {"mng_minr_sig": sig}})
                await _desk_event(sym, "%s: avrei chiuso in anticipo, ma il guadagno e' %.2fR, sotto il minimo di %.2fR: lascio decidere stop e obiettivo. %s" % (tag, r_cur, mr, why))
            return
'''
s = once(s, '    await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})\n    if dec["action"] == "chiudi":\n        await _desk_event(sym, "%s: chiudo in anticipo in guadagno netto. %s" % (tag, why))',
         GATE + '    await db.desk_positions.update_one({"id": p["id"], "status": "open"}, {"$set": upd})\n    if dec["action"] == "chiudi":\n        await _desk_event(sym, "%s: chiudo in anticipo in guadagno netto. %s" % (tag, why))', "gate")
i = s.index("async def mng_summary() -> dict[str, Any]:\n")
j = s.index('\n\n@api.get("/gestione")', i)
s = s[:i] + summ.rstrip("\n") + "\n" + s[j:]
# ---- frontend api
i, j = a.index("// GESTIONE-INIZIO"), a.index("// GESTIONE-FINE") + len("// GESTIONE-FINE")
a = a[:i] + api_new.strip("\n") + a[j:]
# ---- frontend cruscotto
UI = '''                <Text style={[st.txt, { marginTop: 10, fontWeight: "700" }]}>Guadagno minimo per uscire in anticipo</Text>
                <Text style={[st.dim, { marginBottom: 6 }]}>Sotto questo guadagno (in R, cioè multipli del rischio) il bot NON chiude in anticipo e lascia decidere stop e obiettivo. 0 = spento, come prima. Con 1R esce solo se ha già guadagnato almeno quanto rischiava.</Text>
                <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
                  {[0, 0.7, 1, 1.5].map((v) => (
                    <Pressable key={v} onPress={() => saveMng({ exit_min_r: v }, v === 0 ? "Minimo spento." : `Uscita anticipata solo da ${v}R di guadagno.`)} style={[st.chip, (mg.config.exit_min_r ?? 0) === v && { backgroundColor: WARN }]}>
                      <Text style={[st.txt, { fontWeight: "700", color: (mg.config.exit_min_r ?? 0) === v ? "#000" : TXT }]}>{v === 0 ? "Spento" : `${v}R`}</Text>
                    </Pressable>
                  ))}
                </View>
'''
c = once(c, '                <Text style={[st.dim, { marginBottom: 6 }]}>Ogni quanti minuti rilegge il grafico a 1 ora:</Text>\n',
         UI + '                <Text style={[st.dim, { marginBottom: 6 }]}>Ogni quanti minuti rilegge il grafico a 1 ora:</Text>\n', "ui")
c = once(c, '{it.status === "done" ? `senza: ${it.shadow !== null ? usd(it.shadow) : "-"} (${it.outcome})` : "in attesa del confronto"}</Text>',
         '{it.status === "done" ? `senza: ${it.shadow !== null ? usd(it.shadow) : "-"} (${it.outcome})` : it.prov_delta !== null && it.prov_delta !== undefined ? "in attesa · provvisorio" : "in attesa del confronto"}</Text>', "riga1")
c = once(c, '<Text style={[st.txt, { fontWeight: "700", color: col(it.delta) }]}>{it.delta === null ? "-" : usd(it.delta)}</Text>',
         '<Text style={[st.txt, { fontWeight: "700", color: col(it.delta ?? it.prov_delta ?? 0), opacity: it.delta === null ? 0.6 : 1 }]}>{it.delta !== null ? usd(it.delta) : it.prov_delta !== null && it.prov_delta !== undefined ? usd(it.prov_delta) : "-"}</Text>', "riga2")
c = once(c, '                  {!!mg.summary.n_regime && ',
         '                  {!!mg.summary.n_prov && <Text style={st.dim}>Provvisorio sulle {mg.summary.n_prov} in attesa (se il prezzo restasse dove è ora): {(mg.summary.prov_net ?? 0) >= 0 ? "+" : "-"}${Math.abs(mg.summary.prov_net ?? 0).toFixed(2)} · ha aiutato {mg.summary.prov_helped ?? 0}, peggio {mg.summary.prov_hurt ?? 0}. Positivo = uscire ha fatto guadagnare di più. Non è definitivo: il prezzo può ancora muoversi.</Text>}\n'
         '                  {!!mg.summary.n_regime && ', "riga3")
os.makedirs("_backups/prima_uscita_minima", exist_ok=True)
for p in (S, A, C):
    shutil.copy(p, os.path.join("_backups/prima_uscita_minima", os.path.basename(p)))
for p, t in ((S, s), (A, a), (C, c)):
    open(p, "w", encoding="utf-8").write(t)
print("Fatto. Ora git add/commit/push. Il minimo parte SPENTO (0): scegli tu il valore dalla scheda Gestione.")
