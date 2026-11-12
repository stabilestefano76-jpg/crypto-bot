import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { crusApi, Cruscotto, CrusConfronto, CrusPart, CrusPos } from "@/src/api";

const BG = "#0B0E11";
const PANEL = "#12161C";
const LINE = "#1E2329";
const TXT = "#EAECEF";
const DIM = "#848E9C";
const UP = "#0ECB81";
const DOWN = "#F6465D";
const WARN = "#F0B90B";

const usd = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `${n < 0 ? "-" : n > 0 ? "+" : ""}$${Math.abs(n).toFixed(2)}`);
const usdp = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `$${Math.abs(n).toFixed(2)}`);
const pct = (n?: number | null, d = 1) => (n === undefined || n === null || isNaN(n) ? "-" : `${n > 0 ? "+" : ""}${n.toFixed(d)}%`);
const px = (n?: number | null) => {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
};
const col = (n?: number | null) => (n === undefined || n === null || n === 0 ? TXT : n > 0 ? UP : DOWN);
const ago = (ts?: number) => {
  if (!ts) return "";
  const m = Math.max(0, (Date.now() / 1000 - ts) / 60);
  return m < 60 ? `${Math.round(m)} min` : m < 1440 ? `${(m / 60).toFixed(1)} h` : `${(m / 1440).toFixed(1)} g`;
};

function Gauge({ label, used, limit, text }: { label: string; used: number; limit: number; text: string }) {
  const r = limit > 0 ? Math.max(0, Math.min(1, used / limit)) : 0;
  const c = r >= 0.9 ? DOWN : r >= 0.6 ? WARN : UP;
  return (
    <View style={{ marginTop: 10 }}>
      <View style={st.rowBetween}>
        <Text style={st.dim}>{label}</Text>
        <Text style={[st.txt, { color: c }]}>{text}</Text>
      </View>
      <View style={st.track}><View style={{ width: `${r * 100}%`, height: 6, backgroundColor: c, borderRadius: 3 }} /></View>
    </View>
  );
}

function Kpi({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={st.kpi}>
      <Text style={st.dim}>{label}</Text>
      <Text style={[st.kpiVal, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

function Spark({ data }: { data: [number, number][] }) {
  if (data.length < 2) return <Text style={[st.dim, { marginTop: 8 }]}>La curva si riempie col passare del tempo (un punto ogni pochi minuti).</Text>;
  const vals = data.map((d) => d[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const last = vals[vals.length - 1], first = vals[0];
  const c = last >= first ? UP : DOWN;
  return (
    <View style={{ height: 64, flexDirection: "row", alignItems: "flex-end", marginTop: 10 }}>
      {vals.map((v, i) => (
        <View key={i} style={{ flex: 1, marginHorizontal: 0.5, height: 6 + ((v - lo) / span) * 58, backgroundColor: c, opacity: 0.35 + 0.65 * (i / vals.length), borderRadius: 1 }} />
      ))}
    </View>
  );
}

function PosCard({ p }: { p: CrusPos }) {
  const span = p.target - p.stop;
  const entryAt = span !== 0 ? Math.max(0, Math.min(100, ((p.fill_price - p.stop) / span) * 100)) : 50;
  const nowAt = Math.max(0, Math.min(100, p.progress ?? 50));
  const isL = p.side === "long";
  return (
    <View style={st.pos}>
      <View style={st.rowBetween}>
        <View style={{ flexDirection: "row", alignItems: "center" }}>
          <View style={[st.badge, { backgroundColor: isL ? UP : DOWN }]}><Text style={st.badgeTxt}>{isL ? "LONG" : "SHORT"}</Text></View>
          <Text style={[st.txt, { fontWeight: "700", marginLeft: 8 }]}>{p.symbol}</Text>
          <Text style={[st.dim, { marginLeft: 6 }]}>{p.leverage}x{p.auto ? " · BOT" : ""}</Text>
        </View>
        <Text style={[st.txt, { fontWeight: "700", color: col(p.pnl) }]}>{usd(p.pnl)}</Text>
      </View>
      <View style={st.pbar}>
        <View style={[st.pfill, { width: `${nowAt}%`, backgroundColor: p.pnl >= 0 ? UP : DOWN }]} />
        <View style={[st.pmark, { left: `${entryAt}%`, backgroundColor: TXT }]} />
      </View>
      <View style={st.rowBetween}>
        <Text style={[st.dim, { color: DOWN }]}>STOP {px(p.stop)}</Text>
        <Text style={st.dim}>ora {px(p.current_price)}</Text>
        <Text style={[st.dim, { color: UP }]}>OBIETTIVO {px(p.target)}</Text>
      </View>
      <Text style={[st.dim, { marginTop: 4 }]}>
        Allo stop -{p.to_stop_pct}% · all'obiettivo +{p.to_target_pct}%{p.to_liq_pct ? ` · alla liquidazione ${p.to_liq_pct}%` : ""}
      </Text>
      <Text style={st.dim}>
        Movimento {pct(p.move_pct, 2)} · {p.r_now !== null ? `${p.r_now > 0 ? "+" : ""}${p.r_now}R` : ""} · importo {usdp(p.notional)}
        {p.lots_total && p.lots_total > 1 ? ` · lotti ${p.lots_left}/${p.lots_total}${p.tp1_done ? " (1° incassato)" : ""}` : ""} · da {ago(p.opened_ts)}
      </Text>
    </View>
  );
}

function SideRow({ label, s, c }: { label: string; s: CrusPart; c: string }) {
  return (
    <View style={[st.rowBetween, { marginTop: 8 }]}>
      <Text style={[st.txt, { color: c, width: 60, fontWeight: "700" }]}>{label}</Text>
      <Text style={[st.dim, { flex: 1 }]}>{s.n} chiuse · vinte {s.win_rate === null ? "-" : s.win_rate.toFixed(0) + "%"}</Text>
      <Text style={[st.txt, { color: col(s.pnl), fontWeight: "700" }]}>{usd(s.pnl)}</Text>
    </View>
  );
}

export default function CruscottoScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [d, setD] = useState<Cruscotto | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"sim" | "real">("sim");
  const [krKey, setKrKey] = useState("");
  const [krSec, setKrSec] = useState("");
  const [krMsg, setKrMsg] = useState("");
  const [krBusy, setKrBusy] = useState(false);
  const [costMsg, setCostMsg] = useState("");
  const [capIn, setCapIn] = useState("");
  const [rrIn, setRrIn] = useState("");
  const [setMsg, setSetMsg] = useState("");
  const saveSettings = async (b: { capital?: number; rr?: number }, ok: string) => {
    setSetMsg("");
    try { await crusApi.setSettings(b); await load(); setSetMsg(ok); } catch (e: any) { setSetMsg(String(e?.message ?? "Errore")); }
  };
  const num = (t: string) => parseFloat(t.replace(",", "."));
  const [conf, setConf] = useState<CrusConfronto | null>(null);
  const [confBusy, setConfBusy] = useState(false);
  const loadConf = async () => {
    setConfBusy(true);
    try { setConf(await crusApi.confronto()); } catch (e: any) { setCostMsg(String(e?.message ?? "Errore")); }
    setConfBusy(false);
  };
  const setCosts = async (m: "bybit" | "kraken") => {
    setCostMsg("");
    try { await crusApi.setCosts(m); await load(); setCostMsg("Salvato."); } catch (e: any) { setCostMsg(String(e?.message ?? "Errore")); }
  };
  const krConnect = async () => {
    if (!krKey.trim() || !krSec.trim()) { setKrMsg("Scrivi chiave e segreto."); return; }
    setKrBusy(true); setKrMsg("");
    try { await crusApi.krakenConnect(krKey.trim(), krSec.trim()); setKrKey(""); setKrSec(""); setKrMsg("Collegato."); await load(); }
    catch (e: any) { setKrMsg(String(e?.message ?? "Errore")); }
    setKrBusy(false);
  };
  const krDisconnect = async () => {
    setKrBusy(true);
    try { await crusApi.krakenDisconnect(); setKrMsg("Scollegato."); await load(); } catch (e: any) { setKrMsg(String(e?.message ?? "Errore")); }
    setKrBusy(false);
  };

  const load = useCallback(async () => {
    try { setD(await crusApi.get()); setErr(""); } catch (e: any) { setErr(String(e?.message ?? "Errore")); }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [load]);

  const tabBtn = (k: "sim" | "real", label: string) => (
    <Pressable key={k} onPress={() => setTab(k)} style={[st.tab, tab === k && { backgroundColor: WARN }]}>
      <Text style={[st.txt, { fontWeight: "700", color: tab === k ? "#000" : TXT }]}>{label}</Text>
    </Pressable>
  );

  return (
    <View style={[st.root, { paddingTop: insets.top }]}>
      <View style={st.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}><Ionicons name="chevron-back" size={26} color={TXT} /></Pressable>
        <Text style={st.title}>Cruscotto</Text>
        <Pressable onPress={() => router.push("/desk")} hitSlop={12}><Ionicons name="shield-checkmark" size={22} color={DIM} /></Pressable>
      </View>
      {!d ? (
        <View style={{ flex: 1, justifyContent: "center" }}>{err ? <Text style={[st.dim, { textAlign: "center", padding: 20 }]}>{err}</Text> : <ActivityIndicator color={WARN} />}</View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 60 }}>
          <View style={{ flexDirection: "row", marginBottom: 12 }}>{tabBtn("sim", "Portafoglio simulato")}{tabBtn("real", "Reale (sola lettura)")}</View>
          {!!err && <Text style={[st.dim, { color: WARN, marginBottom: 8 }]}>{err}</Text>}

          {tab === "sim" && (<>
            <View style={st.card}>
              <Text style={st.dim}>Capitale totale (simulazione)</Text>
              <View style={st.rowBetween}>
                <Text style={st.hero}>{usdp(d.portfolio.equity)}</Text>
                <View style={{ alignItems: "flex-end" }}>
                  <Text style={[st.txt, { color: col(d.portfolio.return_pct), fontWeight: "700" }]}>{pct(d.portfolio.return_pct, 2)} dall'inizio</Text>
                  <Text style={[st.dim, { color: col(d.portfolio.today_pnl) }]}>oggi {usd(d.portfolio.today_pnl)}</Text>
                </View>
              </View>
              <Text style={st.dim}>Partenza {usdp(d.portfolio.capital0)}</Text>
              <Spark data={d.portfolio.curve} />
            </View>

            <View style={st.grid}>
              <Kpi label="Contante libero" value={usdp(d.portfolio.cash)} />
              <Kpi label="Margine usato" value={usdp(d.portfolio.margin)} />
              <Kpi label="Aperto (fluttuante)" value={usd(d.portfolio.floating)} color={col(d.portfolio.floating)} />
              <Kpi label="Realizzato" value={usd(d.portfolio.realized)} color={col(d.portfolio.realized)} />
              <Kpi label="Calo massimo" value={d.portfolio.max_drawdown_pct ? `-${d.portfolio.max_drawdown_pct}%` : "0%"} color={d.portfolio.max_drawdown_pct > 5 ? DOWN : TXT} />
              <Kpi label="Operazioni vinte" value={d.stats.win_rate === null ? "-" : `${d.stats.win_rate.toFixed(0)}% (${d.stats.n})`} />
              <Kpi label="Profit factor" value={d.stats.profit_factor === null ? "-" : String(d.stats.profit_factor)} color={d.stats.profit_factor === null ? undefined : d.stats.profit_factor >= 1 ? UP : DOWN} />
              <Kpi label="Media per operazione" value={usd(d.stats.expectancy)} color={col(d.stats.expectancy)} />
            </View>

            <View style={st.card}>
              <Text style={st.h}>Esposizione</Text>
              <View style={st.expo}>
                <View style={{ flex: Math.max(d.exposure.long, 0.0001), backgroundColor: UP, height: 14 }} />
                <View style={{ flex: Math.max(d.exposure.short, 0.0001), backgroundColor: DOWN, height: 14 }} />
              </View>
              <View style={st.rowBetween}>
                <Text style={[st.dim, { color: UP }]}>LONG {usdp(d.exposure.long)} ({d.exposure.n_long})</Text>
                <Text style={[st.dim, { color: DOWN }]}>SHORT {usdp(d.exposure.short)} ({d.exposure.n_short})</Text>
              </View>
              <Text style={[st.dim, { marginTop: 6 }]}>
                Netto {usd(d.exposure.net)} · lordo {usdp(d.exposure.gross)}{d.exposure.gross_x_equity !== null ? ` · ${d.exposure.gross_x_equity}x il capitale` : ""}
              </Text>
            </View>

            <View style={st.card}>
              <Text style={st.h}>Limiti di rischio</Text>
              <Gauge label="Rischio aperto (perdita se tutte vanno a stop)" used={d.risk.open_risk} limit={d.risk.open_risk_limit} text={`${usdp(d.risk.open_risk)} / ${usdp(d.risk.open_risk_limit)}`} />
              <Gauge label="Perdita di oggi" used={Math.max(0, -d.risk.today_pnl)} limit={Math.abs(d.risk.daily_limit)} text={`${usdp(Math.max(0, -d.risk.today_pnl))} / ${usdp(Math.abs(d.risk.daily_limit))}`} />
              <Gauge label="Posizioni aperte" used={d.risk.open_n} limit={d.risk.max_open} text={`${d.risk.open_n} / ${d.risk.max_open}`} />
              <Gauge label="Aperture automatiche oggi" used={d.risk.auto_today} limit={d.risk.max_trades_day} text={`${d.risk.auto_today} / ${d.risk.max_trades_day}`} />
              {!!d.risk.pause && <Text style={[st.dim, { color: WARN, marginTop: 10 }]}>⏸ {d.risk.pause}</Text>}
            </View>

            <View style={st.card}>
              <Text style={st.h}>Dati e costi della simulazione</Text>
              <Text style={[st.dim, { marginTop: 6 }]}>
                Ora Analisi e rischio usa {d.costs.mode === "kraken" ? "prezzi, candele e libro ordini di KRAKEN derivati" : "prezzi, candele e libro ordini di BYBIT"} e paga {d.costs.fee_side_pct}% per lato ({d.costs.round_trip_pct}% in tutto).
              </Text>
              <View style={{ flexDirection: "row", marginTop: 10 }}>
                <Pressable onPress={() => setCosts("bybit")} style={[st.tab, d.costs.mode === "bybit" && { backgroundColor: WARN }]}>
                  <Text style={[st.txt, { fontWeight: "700", color: d.costs.mode === "bybit" ? "#000" : TXT }]}>Bybit spot</Text>
                </Pressable>
                <Pressable onPress={() => setCosts("kraken")} style={[st.tab, { marginRight: 0 }, d.costs.mode === "kraken" && { backgroundColor: WARN }]}>
                  <Text style={[st.txt, { fontWeight: "700", color: d.costs.mode === "kraken" ? "#000" : TXT }]}>Kraken derivati</Text>
                </Pressable>
              </View>
              <Text style={[st.dim, { marginTop: 8 }]}>Kraken derivati: 0,05% per lato (taker, ordini a mercato; il maker costa 0,02%). Le monete che Kraken non ha restano su Bybit. Si cambia solo senza posizioni aperte. Il finanziamento si regola in "Analisi e rischio".</Text>
              <Pressable style={[st.tab, { marginTop: 10, marginRight: 0, opacity: confBusy ? 0.4 : 1 }]} disabled={confBusy} onPress={loadConf}>
                <Text style={st.txt}>{confBusy ? "Controllo..." : "Confronta i prezzi ora"}</Text>
              </Pressable>
              {conf && !conf.kraken_ok && <Text style={[st.dim, { color: WARN, marginTop: 8 }]}>Kraken non risponde adesso.</Text>}
              {conf && conf.rows.map((r) => (
                <View key={r.symbol} style={{ marginTop: 8 }}>
                  <View style={st.rowBetween}>
                    <Text style={st.txt}>{r.symbol.replace("USDC", "")}</Text>
                    <Text style={st.dim}>Bybit {px(r.bybit)} · Kraken {r.kraken === null ? "non c'è" : px(r.kraken)}</Text>
                  </View>
                  {r.kraken !== null && (
                    <Text style={st.dim}>
                      Differenza {r.diff_pct === null ? "-" : (r.diff_pct > 0 ? "+" : "") + r.diff_pct + "%"} · scarto Kraken {r.kraken_spread_pct ?? "-"}% · finanziamento {r.funding_day_pct === null ? "-" : r.funding_day_pct + "%/giorno"}
                    </Text>
                  )}
                </View>
              ))}
              {conf && <Text style={[st.dim, { marginTop: 8 }]}>{conf.note}</Text>}
              {!!costMsg && <Text style={[st.dim, { color: WARN, marginTop: 6 }]}>{costMsg}</Text>}
            </View>

            <View style={st.card}>
              <Text style={st.h}>Capitale e rendimento/rischio</Text>
              <Text style={[st.dim, { marginTop: 8 }]}>Capitale del portafoglio simulato: ${d.settings.capital.toFixed(2)}</Text>
              <View style={{ flexDirection: "row", marginTop: 8 }}>
                <TextInput style={[st.inp, { flex: 1, marginTop: 0 }]} placeholder="Nuovo capitale (10 - 100000)" placeholderTextColor={DIM} value={capIn} onChangeText={setCapIn} keyboardType="decimal-pad" />
                <Pressable style={[st.tab, { marginLeft: 8, marginRight: 0, flex: 0, paddingHorizontal: 14, opacity: d.settings.can_change_capital ? 1 : 0.4 }]} disabled={!d.settings.can_change_capital}
                  onPress={() => { const v = num(capIn); if (isNaN(v)) { setSetMsg("Scrivi un numero."); return; } saveSettings({ capital: v }, "Capitale cambiato: il portafoglio riparte da qui."); setCapIn(""); }}>
                  <Text style={[st.txt, { fontWeight: "700" }]}>Imposta</Text>
                </Pressable>
              </View>
              <Text style={[st.dim, { marginTop: 6 }]}>
                {d.settings.can_change_capital ? "Il contante riparte dal nuovo importo e la curva si azzera. Lo storico delle operazioni resta." : "Per cambiare il capitale chiudi prima le posizioni aperte."}
              </Text>

              <Text style={[st.txt, { marginTop: 16, fontWeight: "700" }]}>Rendimento/rischio (RR) delle nuove analisi</Text>
              <Text style={[st.dim, { marginTop: 4 }]}>
                {d.settings.rr ? `Ora: 1 a ${d.settings.rr}. L'obiettivo sta a ${d.settings.rr} volte la distanza dello stop.` : "Ora: automatico (circa da 1,6 a 2,7, secondo la volatilità)."}
              </Text>
              <View style={{ flexDirection: "row", flexWrap: "wrap", marginTop: 8 }}>
                {[0, 1.5, 2, 3, 4].map((v) => {
                  const on = v === 0 ? !d.settings.rr : d.settings.rr === v;
                  return (
                    <Pressable key={v} onPress={() => saveSettings({ rr: v }, v ? `RR impostato a 1 a ${v}.` : "RR automatico.")} style={[st.chip, on && { backgroundColor: WARN }]}>
                      <Text style={[st.txt, { fontWeight: "700", color: on ? "#000" : TXT }]}>{v === 0 ? "Auto" : `1:${v}`}</Text>
                    </Pressable>
                  );
                })}
              </View>
              <View style={{ flexDirection: "row", marginTop: 8 }}>
                <TextInput style={[st.inp, { flex: 1, marginTop: 0 }]} placeholder="Altro valore (0,8 - 6)" placeholderTextColor={DIM} value={rrIn} onChangeText={setRrIn} keyboardType="decimal-pad" />
                <Pressable style={[st.tab, { marginLeft: 8, marginRight: 0, flex: 0, paddingHorizontal: 14 }]}
                  onPress={() => { const v = num(rrIn); if (isNaN(v)) { setSetMsg("Scrivi un numero."); return; } saveSettings({ rr: v }, `RR impostato a 1 a ${v}.`); setRrIn(""); }}>
                  <Text style={[st.txt, { fontWeight: "700" }]}>Imposta</Text>
                </Pressable>
              </View>
              <Text style={[st.dim, { marginTop: 6 }]}>Vale per i nuovi setup e le nuove aperture di Analisi e rischio, non per le posizioni già aperte. Un RR alto mette l'obiettivo più lontano: guadagni di più quando vinci, ma vinci più di rado. Non è un vantaggio in sé. Il Terminale sceglie il suo RR da solo.</Text>
              {!!setMsg && <Text style={[st.dim, { color: WARN, marginTop: 8 }]}>{setMsg}</Text>}
            </View>

            <View style={st.card}>
              <Text style={st.h}>Posizioni aperte ({d.positions.length})</Text>
              {d.positions.length === 0 && <Text style={[st.dim, { marginTop: 6 }]}>Nessuna posizione aperta.</Text>}
              {d.positions.map((p) => <PosCard key={p.id} p={p} />)}
            </View>

            <View style={st.card}>
              <Text style={st.h}>Long contro short (chiuse)</Text>
              <SideRow label="LONG" s={d.stats.long} c={UP} />
              <SideRow label="SHORT" s={d.stats.short} c={DOWN} />
              <Text style={[st.dim, { marginTop: 8 }]}>
                Migliore {usd(d.stats.best)} · peggiore {usd(d.stats.worst)} · media vinta {usd(d.stats.avg_win)} · media persa {usd(d.stats.avg_loss)}
              </Text>
            </View>

            <View style={st.card}>
              <Text style={st.h}>Bot e Terminale</Text>
              <View style={[st.rowBetween, { marginTop: 8 }]}><Text style={st.txt}>Apertura automatica (Analisi e rischio)</Text><Text style={[st.txt, { color: d.bots.desk_auto ? UP : DIM, fontWeight: "700" }]}>{d.bots.desk_auto ? "ACCESA" : "spenta"}</Text></View>
              <View style={[st.rowBetween, { marginTop: 6 }]}><Text style={st.txt}>Pre-analisi del mercato</Text><Text style={[st.txt, { color: d.bots.scan_enabled ? UP : DIM, fontWeight: "700" }]}>{d.bots.scan_enabled ? "accesa" : "spenta"}</Text></View>
              {!!d.bots.scan_top.length && <Text style={[st.dim, { marginTop: 4 }]}>Candidate: {d.bots.scan_top.join(", ")}</Text>}
              {d.bots.terminal.installed ? (<>
                <View style={[st.rowBetween, { marginTop: 6 }]}><Text style={st.txt}>Terminale automatico</Text><Text style={[st.txt, { color: d.bots.terminal.auto ? UP : DIM, fontWeight: "700" }]}>{d.bots.terminal.auto ? "ACCESO" : "spento"}</Text></View>
                <Text style={[st.dim, { marginTop: 4 }]}>
                  Archivio: {d.bots.terminal.qualified ?? "-"} strategie su {d.bots.terminal.tested ?? "-"} superano il filtro {d.bots.terminal.rigor ?? ""}
                </Text>
                {!!d.bots.terminal.last_setups?.length && <Text style={st.dim}>Ultimi setup: {d.bots.terminal.last_setups.map((s) => `${s.symbol} ${s.side}`).join(", ")}</Text>}
              </>) : <Text style={[st.dim, { marginTop: 6 }]}>Terminale non installato.</Text>}
            </View>

            {!!d.recent_closed.length && (
              <View style={st.card}>
                <Text style={st.h}>Ultime chiusure</Text>
                {d.recent_closed.map((c, i) => (
                  <View key={i} style={[st.rowBetween, { marginTop: 6 }]}>
                    <Text style={st.txt}>{c.symbol} · {c.side.toUpperCase()}</Text>
                    <Text style={[st.dim, { flex: 1, textAlign: "center" }]}>{c.reason === "chiusura_manuale" ? "chiusa da te" : c.reason ?? ""}</Text>
                    <Text style={[st.txt, { color: col(c.pnl), fontWeight: "700" }]}>{usd(c.pnl)}</Text>
                  </View>
                ))}
              </View>
            )}

            <View style={st.card}>
              <Text style={st.h}>Ultimi eventi</Text>
              {d.events.length === 0 && <Text style={[st.dim, { marginTop: 6 }]}>Ancora nulla.</Text>}
              {d.events.map((e, i) => (
                <Text key={i} style={[st.dim, { marginTop: 6 }]}>{(e.at || "").slice(11, 16)} · {e.symbol !== "-" ? e.symbol + " · " : ""}{e.text}</Text>
              ))}
            </View>
          </>)}

          {tab === "real" && (
            <View style={st.card}>
              <Text style={st.dim}>Portafoglio Kraken (sola lettura)</Text>
              {d.kraken.connected ? (<>
                <Text style={st.hero}>{d.kraken.total_eur === null ? "-" : `€${d.kraken.total_eur.toFixed(2)}`}</Text>
                <Text style={st.dim}>Chiave {d.kraken.masked}</Text>
                {d.kraken.coins.length === 0 && <Text style={[st.dim, { marginTop: 8 }]}>Nessun saldo sul conto spot.</Text>}
                {d.kraken.coins.map((c) => (
                  <View key={c.coin} style={[st.rowBetween, { marginTop: 8 }]}>
                    <Text style={st.txt}>{c.coin}</Text>
                    <Text style={st.txt}>{c.qty < 1 ? c.qty.toFixed(6) : c.qty.toFixed(4)}</Text>
                  </View>
                ))}
                <Pressable style={[st.tab, { marginTop: 14, marginRight: 0, opacity: krBusy ? 0.4 : 1 }]} disabled={krBusy} onPress={krDisconnect}>
                  <Text style={st.txt}>Scollega Kraken</Text>
                </Pressable>
              </>) : (<>
                <Text style={[st.txt, { marginTop: 8 }]}>Non collegato.</Text>
                <Text style={[st.dim, { marginTop: 6 }]}>Incolla qui la chiave e il segreto creati su Kraken con i soli permessi di lettura. Restano salvati cifrati sul server.</Text>
                <TextInput style={st.inp} placeholder="Chiave (API key)" placeholderTextColor={DIM} value={krKey} onChangeText={setKrKey} autoCapitalize="none" autoCorrect={false} />
                <TextInput style={st.inp} placeholder="Segreto (private key)" placeholderTextColor={DIM} value={krSec} onChangeText={setKrSec} autoCapitalize="none" autoCorrect={false} secureTextEntry />
                <Pressable style={[st.tab, { marginTop: 12, marginRight: 0, backgroundColor: WARN, opacity: krBusy ? 0.4 : 1 }]} disabled={krBusy} onPress={krConnect}>
                  <Text style={[st.txt, { color: "#000", fontWeight: "700" }]}>{krBusy ? "Controllo..." : "Collega Kraken"}</Text>
                </Pressable>
                {!!d.kraken.error && d.kraken.error !== "Nessuna chiave Kraken collegata" && <Text style={[st.dim, { color: WARN, marginTop: 8 }]}>{d.kraken.error}</Text>}
              </>)}
              {!!krMsg && <Text style={[st.dim, { color: WARN, marginTop: 8 }]}>{krMsg}</Text>}
              <Text style={[st.dim, { marginTop: 12 }]}>Questa chiave legge solo i saldi: non ha il permesso di fare ordini né prelievi. Lo short vero sui derivati si aggiunge dopo.</Text>
            </View>
          )}

          {tab === "real" && (
            <View style={st.card}>
              <Text style={st.dim}>Portafoglio reale su Bybit</Text>
              {d.real.connected ? (<>
                <Text style={st.hero}>{usdp(d.real.total_usd)}</Text>
                {d.real.coins.map((c) => (
                  <View key={c.coin} style={[st.rowBetween, { marginTop: 8 }]}>
                    <Text style={st.txt}>{c.coin}</Text>
                    <Text style={st.dim}>{c.qty < 1 ? c.qty.toFixed(6) : c.qty.toFixed(4)}</Text>
                    <Text style={[st.txt, { fontWeight: "700" }]}>{usdp(c.usd)}</Text>
                  </View>
                ))}
              </>) : <Text style={[st.txt, { color: WARN, marginTop: 8 }]}>Non collegato: {d.real.error ?? "chiave non disponibile"}</Text>}
              <Text style={[st.dim, { marginTop: 14 }]}>{d.real.note}</Text>
              <Text style={[st.dim, { marginTop: 6 }]}>Da qui non parte nessun ordine. Per lo short vero serve un conto su un exchange con derivati regolamentati in UE: la scelta si fa fuori da questa schermata.</Text>
            </View>
          )}
        </ScrollView>
      )}
    </View>
  );
}

const st = StyleSheet.create({
  root: { flex: 1, backgroundColor: BG },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 14, paddingVertical: 10 },
  title: { color: TXT, fontSize: 18, fontWeight: "700" },
  card: { backgroundColor: PANEL, borderRadius: 10, padding: 14, marginBottom: 12, borderWidth: 1, borderColor: LINE },
  h: { color: TXT, fontSize: 15, fontWeight: "700" },
  txt: { color: TXT, fontSize: 14 },
  dim: { color: DIM, fontSize: 12 },
  hero: { color: TXT, fontSize: 30, fontWeight: "800" },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  grid: { flexDirection: "row", flexWrap: "wrap", marginHorizontal: -5, marginBottom: 7 },
  kpi: { width: "50%", paddingHorizontal: 5, marginBottom: 10 },
  kpiVal: { color: TXT, fontSize: 17, fontWeight: "700", marginTop: 2, backgroundColor: PANEL, paddingVertical: 8, paddingHorizontal: 10, borderRadius: 8, overflow: "hidden" },
  track: { height: 6, backgroundColor: LINE, borderRadius: 3, marginTop: 4 },
  expo: { flexDirection: "row", marginTop: 10, marginBottom: 6, borderRadius: 7, overflow: "hidden" },
  tab: { flex: 1, alignItems: "center", paddingVertical: 9, backgroundColor: PANEL, borderRadius: 8, marginRight: 8, borderWidth: 1, borderColor: LINE },
  pos: { borderTopWidth: 1, borderTopColor: LINE, marginTop: 10, paddingTop: 10 },
  badge: { borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  badgeTxt: { color: "#000", fontSize: 11, fontWeight: "800" },
  pbar: { height: 8, backgroundColor: LINE, borderRadius: 4, marginVertical: 8, overflow: "visible" },
  pfill: { height: 8, borderRadius: 4, opacity: 0.8 },
  chip: { paddingHorizontal: 14, paddingVertical: 8, backgroundColor: BG, borderRadius: 8, marginRight: 8, marginBottom: 8, borderWidth: 1, borderColor: LINE },
  inp: { backgroundColor: BG, color: TXT, borderRadius: 8, padding: 10, marginTop: 10, borderWidth: 1, borderColor: LINE, fontSize: 14 },
  pmark: { position: "absolute", top: -3, width: 2, height: 14 },
});
