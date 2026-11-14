#!/usr/bin/env python3
"""GESTIONE TRADE: ogni N minuti il bot rilegge il grafico delle posizioni aperte (Analisi e rischio) e, se la situazione peggiora,
stringe lo stop o chiude in anticipo SOLO in guadagno netto (oltre tutti i costi). Ogni intervento e' confrontato con l'operazione 'ombra'
(cosa sarebbe successo senza). Serve il Cruscotto con il Report gia' installato. Lancialo dalla cartella crypto-bot. Copie in _backups/prima_gestione."""
import os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
F = os.path.join(HERE, "files")
LINE = "# ============================================================================\n"
MNG = LINE + "# GESTIONE TRADE"
CRU = LINE + "# CRUSCOTTO"
A0, A1 = "// GESTIONE-INIZIO\n", "// GESTIONE-FINE\n"
BK = os.path.join("_backups", "prima_gestione")
SRV = os.path.join("backend", "server.py")
API = os.path.join("frontend", "src", "api.ts")
CRS = os.path.join("frontend", "app", "cruscotto.tsx")
REP = os.path.join("frontend", "app", "report.tsx")


def rd(*p):
    return open(os.path.join(F, *p), encoding="utf-8").read()


def once(t, old, new, label):
    if t.count(old) != 1:
        raise SystemExit("STOP: punto da modificare non trovato o duplicato (%s). Non tocco nulla." % label)
    return t.replace(old, new)


def patch_server(s):
    if s.count(CRU) != 1 or "crus_loop" not in s or "cruscotto_report" not in s:
        raise SystemExit("STOP: installa prima il Cruscotto con il Report. Non tocco nulla.")
    block = rd("backend", "gestione_block.py")
    if MNG in s:                                         # versione precedente: sostituisco il blocco
        i, j = s.index(MNG), s.index(CRU)
        if j < i:
            raise SystemExit("STOP: non riconosco la versione precedente. Non tocco nulla.")
        return s[:i] + block + s[j:]
    s = once(s, CRU, block + CRU, "blocco")
    s = once(s, "    crus_task = asyncio.create_task(crus_loop())\n",
             "    crus_task = asyncio.create_task(crus_loop())\n    mng_task = asyncio.create_task(mng_loop())\n", "avvio")
    s = once(s, "    crus_task.cancel()\n", "    crus_task.cancel()\n    mng_task.cancel()\n", "stop")
    s = once(s, '"opened_ts": o.get("opened_ts"), **v})', '"opened_ts": o.get("opened_ts"), "mng": mng_view(o), **v})', "posizioni")
    s = once(s, '[-14:]], "trades": trades}', '[-14:]], "trades": trades, "gestione": await mng_summary()}', "report")
    return s


def patch_api(a):
    block = A0 + rd("api_gestione.ts").split(A0, 1)[1].split(A1, 1)[0] + A1
    if A0 in a:
        i, j = a.index(A0), a.index(A1) + len(A1)
        return a[:i] + block + a[j:]
    return a.rstrip("\n") + "\n" + block


MNG_LINE_FN = '''function MngLine({ m }: { m?: MngView }) {
  if (!m) return null;
  if (!m.managed) return <Text style={[st.dim, { marginTop: 4 }]}>Gestione automatica: non attiva su questa posizione (viene dal Terminale).</Text>;
  if (m.danger === null || m.danger === undefined) return <Text style={[st.dim, { marginTop: 4 }]}>Gestione: primo controllo in arrivo (parte dopo 30 minuti dall'apertura).</Text>;
  const c = m.danger >= 4 ? DOWN : m.danger >= 2 ? WARN : UP;
  return (
    <View style={{ marginTop: 6 }}>
      <Text style={[st.dim, { color: c, fontWeight: "700" }]}>
        Gestione: {m.danger === 0 ? "tutto come all'apertura" : `${m.danger} segnali di pericolo`} · controllato {ago(m.at ?? undefined)} fa{m.tightened ? " · stop stretto" : ""}
      </Text>
      {m.signals.map((x, i) => <Text key={i} style={st.dim}>• {x}</Text>)}
      {!!m.reason && <Text style={st.dim}>{m.reason}</Text>}
    </View>
  );
}

'''

MNG_CARD = '''            <View style={st.card}>
              <Text style={st.h}>Gestione delle posizioni aperte</Text>
              <Text style={[st.dim, { marginTop: 6 }]}>
                Ogni {mg?.config.every_min ?? 15} minuti il bot rilegge il grafico di ogni posizione aperta. Se la situazione peggiora può stringere lo stop o chiudere, ma SOLO in guadagno netto, cioè oltre tutti i costi. In perdita non interviene: decide lo stop di sempre. Non vale per le posizioni del Terminale.
              </Text>
              {mg && (<>
                <View style={{ flexDirection: "row", flexWrap: "wrap", marginTop: 10 }}>
                  {[{ k: true, t: "Acceso" }, { k: false, t: "Spento" }].map((o) => (
                    <Pressable key={String(o.k)} onPress={() => saveMng({ enabled: o.k }, o.k ? "Gestione accesa." : "Gestione spenta.")} style={[st.chip, mg.config.enabled === o.k && { backgroundColor: WARN }]}>
                      <Text style={[st.txt, { fontWeight: "700", color: mg.config.enabled === o.k ? "#000" : TXT }]}>{o.t}</Text>
                    </Pressable>
                  ))}
                </View>
                <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
                  {[{ k: "chiudi", t: "Stringe o chiude" }, { k: "stringi", t: "Solo stringe lo stop" }, { k: "avvisa", t: "Solo avvisa" }].map((o) => (
                    <Pressable key={o.k} onPress={() => saveMng({ mode: o.k as any }, "Modalità cambiata.")} style={[st.chip, mg.config.mode === o.k && { backgroundColor: WARN }]}>
                      <Text style={[st.txt, { fontWeight: "700", color: mg.config.mode === o.k ? "#000" : TXT }]}>{o.t}</Text>
                    </Pressable>
                  ))}
                </View>
                <Text style={[st.dim, { marginBottom: 6 }]}>Ogni quanti minuti rilegge il grafico:</Text>
                <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
                  {[5, 15, 30, 60].map((v) => (
                    <Pressable key={v} onPress={() => saveMng({ every_min: v }, `Controllo ogni ${v} minuti.`)} style={[st.chip, mg.config.every_min === v && { backgroundColor: WARN }]}>
                      <Text style={[st.txt, { fontWeight: "700", color: mg.config.every_min === v ? "#000" : TXT }]}>{v} min</Text>
                    </Pressable>
                  ))}
                </View>
                <Text style={[st.txt, { marginTop: 8, fontWeight: "700" }]}>Ha davvero aiutato?</Text>
                {mg.summary.n_done + mg.summary.n_pending === 0 ? (
                  <Text style={[st.dim, { marginTop: 4 }]}>Nessun intervento ancora. Dopo il primo stop stretto o la prima uscita anticipata, qui compare il confronto con "cosa sarebbe successo senza".</Text>
                ) : (<>
                  <Text style={[st.txt, { marginTop: 4, color: col(mg.summary.net) }]}>
                    Effetto totale: {mg.summary.net >= 0 ? "+" : "-"}${Math.abs(mg.summary.net).toFixed(2)} su {mg.summary.n_done} interventi confrontati
                  </Text>
                  <Text style={st.dim}>Ha aiutato {mg.summary.helped} volte, ha fatto peggio {mg.summary.hurt} volte, {mg.summary.n_pending} ancora in attesa. Positivo = senza la gestione avresti guadagnato di meno.</Text>
                  <Text style={st.dim}>Uscite anticipate: {mg.summary.net_uscita >= 0 ? "+" : "-"}${Math.abs(mg.summary.net_uscita).toFixed(2)} ({mg.summary.n_uscita}) · stop stretti: {mg.summary.net_stop >= 0 ? "+" : "-"}${Math.abs(mg.summary.net_stop).toFixed(2)} ({mg.summary.n_stop})</Text>
                  {mg.summary.items.slice(0, 5).map((it, i) => (
                    <View key={i} style={[st.rowBetween, { marginTop: 6 }]}>
                      <Text style={[st.txt, { width: 70 }]}>{it.symbol.replace("USDC", "")}</Text>
                      <Text style={[st.dim, { flex: 1 }]}>{it.kind === "uscita" ? "uscita anticipata" : "stop stretto"} · {it.status === "done" ? `senza: ${it.shadow !== null ? usd(it.shadow) : "-"} (${it.outcome})` : "in attesa del confronto"}</Text>
                      <Text style={[st.txt, { fontWeight: "700", color: col(it.delta) }]}>{it.delta === null ? "-" : usd(it.delta)}</Text>
                    </View>
                  ))}
                </>)}
                <Text style={[st.dim, { marginTop: 6 }]}>Il confronto è una stima: l'operazione "ombra" usa prezzi controllati ogni 30 secondi e non simula il libro ordini.</Text>
              </>)}
              {!!mgMsg && <Text style={[st.dim, { color: WARN, marginTop: 8 }]}>{mgMsg}</Text>}
            </View>

'''

MNG_REPORT = '''            {(data as any).gestione && ((data as any).gestione.n_done + (data as any).gestione.n_pending) > 0 && (() => {
              const g = (data as any).gestione;
              return (
                <View style={st.card}>
                  <Text style={st.h}>Gestione del trade: ha aiutato?</Text>
                  <Text style={[st.txt, { marginTop: 6, color: col(g.net), fontWeight: "700" }]}>Effetto totale {usd(g.net)} su {g.n_done} interventi</Text>
                  <Text style={st.dim}>Ha aiutato {g.helped} volte, peggio {g.hurt} volte, {g.n_pending} in attesa. Positivo = senza la gestione avresti guadagnato di meno.</Text>
                  <Text style={st.dim}>Uscite anticipate {usd(g.net_uscita)} ({g.n_uscita}) · stop stretti {usd(g.net_stop)} ({g.n_stop})</Text>
                </View>
              );
            })()}

'''


def patch_cruscotto(t):
    if "MngLine" in t:
        return t
    t = once(t, 'import { crusApi, Cruscotto, CrusConfronto, CrusPart, CrusPos } from "@/src/api";',
             'import { crusApi, Cruscotto, CrusConfronto, CrusPart, CrusPos, mngApi, MngConfig, MngSummary, MngView } from "@/src/api";', "import")
    t = once(t, "function SideRow(", MNG_LINE_FN + "function SideRow(", "MngLine")
    # la riga va dentro PosCard, prima della chiusura del suo <View>: cerco la fine di PosCard
    i = t.index("function PosCard(")
    j = t.index("\n    </View>\n  );\n}\n", i)
    t = t[:j] + "\n      <MngLine m={(p as any).mng} />" + t[j:]
    t = once(t, '  const [costMsg, setCostMsg] = useState("");\n',
             '  const [costMsg, setCostMsg] = useState("");\n'
             '  const [mg, setMg] = useState<{ config: MngConfig; summary: MngSummary } | null>(null);\n'
             '  const [mgMsg, setMgMsg] = useState("");\n'
             '  const saveMng = async (b: Partial<MngConfig>, ok: string) => {\n'
             '    setMgMsg("");\n'
             '    try { await mngApi.set(b); setMg(await mngApi.get()); setMgMsg(ok); } catch (e: any) { setMgMsg(String(e?.message ?? "Errore")); }\n'
             '  };\n', "stato")
    t = once(t, "    try { setD(await crusApi.get()); setErr(\"\"); } catch (e: any) { setErr(String(e?.message ?? \"Errore\")); }\n",
             "    try { setD(await crusApi.get()); setErr(\"\"); } catch (e: any) { setErr(String(e?.message ?? \"Errore\")); }\n"
             "    mngApi.get().then(setMg).catch(() => {});\n", "load")
    t = once(t, '            <View style={st.card}>\n              <Text style={st.h}>Posizioni aperte (', MNG_CARD + '            <View style={st.card}>\n              <Text style={st.h}>Posizioni aperte (', "scheda")
    t = once(t, '{c.reason === "chiusura_manuale" ? "chiusa da te" : c.reason ?? ""}',
             '{c.reason === "chiusura_manuale" ? "chiusa da te" : c.reason === "uscita_anticipata" ? "uscita anticipata" : c.reason ?? ""}', "motivo")
    return t


def patch_report(t):
    if "Gestione del trade" in t:
        return t
    t = once(t, '            <View style={st.card}>\n              <Text style={st.h}>Come si sono chiuse</Text>',
             MNG_REPORT + '            <View style={st.card}>\n              <Text style={st.h}>Come si sono chiuse</Text>', "scheda report")
    t = once(t, 'r === "stop" ? "stop" : r ?? "-"', 'r === "stop" ? "stop" : r === "uscita_anticipata" ? "uscita anticipata (bot)" : r ?? "-"', "motivo report")
    return t


def main():
    for p in (SRV, API, CRS, REP):
        if not os.path.exists(p):
            raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot (manca %s). Non tocco nulla." % p)
    s = open(SRV, encoding="utf-8").read()
    a = open(API, encoding="utf-8").read()
    c = open(CRS, encoding="utf-8").read()
    r = open(REP, encoding="utf-8").read()
    s2, a2, c2, r2 = patch_server(s), patch_api(a), patch_cruscotto(c), patch_report(r)      # prima calcolo tutto: se qualcosa non va, non scrivo niente
    os.makedirs(BK, exist_ok=True)
    for p in (SRV, API, CRS, REP):
        dst = os.path.join(BK, p.replace(os.sep, "__"))
        if not os.path.exists(dst):
            shutil.copy(p, dst)
    open(SRV, "w", encoding="utf-8").write(s2)
    open(API, "w", encoding="utf-8").write(a2)
    open(CRS, "w", encoding="utf-8").write(c2)
    open(REP, "w", encoding="utf-8").write(r2)
    print("Fatto: gestione del trade installata. Ora git add/commit/push.")


main()
