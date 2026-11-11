import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { deskApi, DeskAnalysis, DeskPlan, DeskRow, DeskStatus } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const usd = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `${n < 0 ? "-" : ""}$${Math.abs(n).toFixed(2)}`);
const px = (n?: number | null) => {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
};
const biasText = (b: string) => (b === "long" ? "COMPRARE (long)" : b === "short" ? "VENDERE allo scoperto (short)" : "ASPETTARE");
const reasonText = (r?: string) => (r === "chiusura_manuale" ? "chiusa da te" : r ?? "");

export default function DeskScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [st, setSt] = useState<DeskStatus | null>(null);
  const [sym, setSym] = useState("");
  const [lev, setLev] = useState("1");
  const [risk, setRisk] = useState("1");
  const [an, setAn] = useState<DeskAnalysis | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [capital, setCapital] = useState("");
  const [af, setAf] = useState<Record<string, string> | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await deskApi.status();
      setSt(s);
      setSym((c) => (c && s.symbols_active.includes(c) ? c : s.symbols_active[0] ?? c));
      setCapital((c) => c || String(s.config.capital));
      setAf((c) => c ?? {
        auto_leverage: String(s.config.auto_leverage), auto_risk_pct: String(s.config.auto_risk_pct),
        max_total_risk_pct: String(s.config.max_total_risk_pct), daily_loss_pct: String(s.config.daily_loss_pct),
        max_trades_day: String(s.config.max_trades_day), lots: String(s.config.lots), tp1_r: String(s.config.tp1_r),
        cooldown_min: String(s.config.cooldown_min), scan_top: String(s.config.scan_top), scan_pool: String(s.config.scan_pool),
        scan_min_volume: String(s.config.scan_min_volume), scan_every_min: String(s.config.scan_every_min),
      });
    } catch (e: any) {
      setMsg(String(e?.message ?? "Errore"));
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 7000);
    return () => clearInterval(t);
  }, [load]);

  const num = (x: string) => Number(String(x).replace(",", "."));
  const analyze = async () => {
    setBusy(true); setMsg(""); setAn(null);
    try { setAn(await deskApi.analisi(sym, num(lev), num(risk))); }
    catch (e: any) { setMsg("ERRORE: " + String(e?.message ?? e)); }
    setBusy(false);
  };
  const open = async (side: "long" | "short", force: boolean) => {
    setBusy(true); setMsg("");
    try { await deskApi.open({ symbol: sym, side, leverage: num(lev), risk_pct: num(risk), force }); setMsg("Aperta in simulazione."); setAn(null); }
    catch (e: any) { setMsg(String(e?.message ?? e)); }
    setBusy(false); load();
  };
  const closePos = async (id: string) => {
    setBusy(true);
    try { await deskApi.close(id); setMsg("Chiusa."); } catch (e: any) { setMsg("ERRORE: " + String(e?.message ?? e)); }
    setBusy(false); load();
  };
  const setAuto = async (patch: Record<string, unknown>) => {
    setBusy(true);
    try { await deskApi.setConfig(patch); setMsg("Salvato."); }
    catch (e: any) { setMsg("ERRORE: " + String(e?.message ?? e)); }
    setBusy(false); load();
  };
  const saveAutoNums = () => {
    if (!af) return;
    const patch: Record<string, number> = {};
    for (const k of Object.keys(af)) {
      const n = num(af[k]);
      if (!isFinite(n) || n < 0) { setMsg("ERRORE: scrivi numeri validi."); return; }
      patch[k] = n;
    }
    setAuto(patch);
  };
  const autoField = (label: string, k: string) => (
    <View key={k}>
      <Text style={styles.note}>{label}</Text>
      <TextInput style={styles.inp} value={af?.[k] ?? ""} onChangeText={(v) => setAf((c) => (c ? { ...c, [k]: v } : c))} keyboardType="decimal-pad" testID={"desk-auto-" + k} />
    </View>
  );
  const saveCap = async () => {
    setBusy(true);
    try { await deskApi.setConfig({ capital: num(capital) }); setMsg("Capitale della simulazione aggiornato."); }
    catch (e: any) { setMsg("ERRORE: " + String(e?.message ?? e)); }
    setBusy(false); load();
  };

  const chip = (label: string, on: boolean, onPress: () => void) => (
    <Pressable key={label} onPress={onPress} style={[styles.chip, on && { backgroundColor: colors.brand }]}>
      <Text style={[styles.chipText, on && { color: colors.onBrand }]}>{label}</Text>
    </Pressable>
  );

  const planCard = (p: DeskPlan, bias: string) => {
    const ok = p.checks.ok && bias === p.side;
    return (
      <View key={p.side} style={[styles.card, { borderColor: ok ? colors.success : colors.border }]}>
        <Text style={styles.big}>{p.side === "long" ? "Se COMPRI (long)" : "Se VENDI allo scoperto (short)"}</Text>
        <Text style={styles.line}>Stop (esci in perdita): {px(p.stop)} · -{p.risk_pct_price.toFixed(2)}%</Text>
        <Text style={styles.line}>Obiettivo: {px(p.target)} · +{p.gain_pct_price.toFixed(2)}%</Text>
        <Text style={styles.line}>Rischio/rendimento: {p.rr ? p.rr.toFixed(1) : "-"}</Text>
        <Text style={styles.line}>Importo {usd(p.notional)} · margine {usd(p.margin)} · leva {p.leverage}x</Text>
        <Text style={styles.line}>Perdita massima allo stop: {usd(-p.max_loss)} · guadagno atteso: {usd(p.expected_gain)}</Text>
        <Text style={styles.line}>Liquidazione: {p.liquidation ? px(p.liquidation) + " (" + (p.liq_dist_pct ?? 0).toFixed(1) + "% lontano)" : "nessuna (senza leva)"}</Text>
        <Text style={styles.note}>Leva consigliata per questo stop: {p.safe_leverage}x · costi stimati {p.cost_pct.toFixed(2)}%</Text>
        {p.warnings.map((w, i) => <Text key={i} style={[styles.note, { color: colors.warning }]}>⚠ {w}</Text>)}
        {p.checks.reasons.map((w, i) => <Text key={"r" + i} style={[styles.note, { color: colors.error }]}>✗ {w}</Text>)}
        {bias !== p.side && <Text style={[styles.note, { color: colors.error }]}>✗ Il mercato dice: {biasText(bias)}</Text>}
        {ok && <Text style={[styles.note, { color: colors.success }]}>✓ Tutti i controlli passati</Text>}
        <Pressable style={[styles.btn, { opacity: busy ? 0.4 : 1 }, !ok && styles.btnGhost]} disabled={busy} onPress={() => open(p.side, false)}>
          <Text style={[styles.btnText, !ok && { color: colors.onSurface }]}>Apri in simulazione</Text>
        </Pressable>
        {bias !== p.side && p.checks.ok && (
          <Pressable style={[styles.btn, styles.btnGhost, { opacity: busy ? 0.4 : 1 }]} disabled={busy} onPress={() => open(p.side, true)}>
            <Text style={[styles.btnText, { color: colors.warning }]}>Forza (contro la tendenza)</Text>
          </Pressable>
        )}
      </View>
    );
  };

  const posCard = (p: DeskRow, isOpen: boolean) => (
    <View key={p.id} style={styles.card}>
      <View style={styles.row}>
        <Text style={styles.big}>{p.symbol} · {p.side.toUpperCase()} {p.leverage}x{p.auto ? " · BOT" : ""}</Text>
        <Text style={[styles.big, { color: ((isOpen ? p.floating_pnl : p.pnl_usdt) ?? 0) >= 0 ? colors.success : colors.error }]}>{usd(isOpen ? p.floating_pnl : p.pnl_usdt)}</Text>
      </View>
      <Text style={styles.line}>Entrata {px(p.fill_price)} · importo {usd(p.notional)} · margine {usd(p.margin)}</Text>
      <Text style={styles.note}>Stop {px(p.stop)} · obiettivo {px(p.target)} · liquidazione {px(p.liquidation)}</Text>
      {!!p.lots_total && p.lots_total > 1 && (
        <Text style={styles.note}>Lotti {p.lots_left}/{p.lots_total} · primo lotto a {px(p.tp1)}{p.tp1_done ? ` (fatto, incassato ${usd(p.realized_partial)})` : ""}</Text>
      )}
      {isOpen ? <Text style={styles.note}>Prezzo ora {px(p.current_price)}</Text> : <Text style={styles.note}>Uscita {px(p.close_price)} ({reasonText(p.close_reason)})</Text>}
      {isOpen && (
        <Pressable style={[styles.btn, styles.btnGhost]} disabled={busy} onPress={() => closePos(p.id)}>
          <Text style={[styles.btnText, { color: colors.onSurface }]}>Chiudi ora</Text>
        </Pressable>
      )}
    </View>
  );

  if (!st) return <View style={[styles.root, { paddingTop: insets.top, justifyContent: "center" }]}><ActivityIndicator color={colors.brand} /></View>;
  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}><Ionicons name="chevron-back" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.title}>Analisi e rischio</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}>
        <View style={styles.card}>
          <Text style={styles.big}>Simulazione con long, short e leva</Text>
          <Text style={styles.note}>Nessun ordine va a Bybit. Il bot analizza il mercato, controlla costi, rischio e libro ordini, e ti dice stop, obiettivo, importo e leva. Apri tu, col bottone.</Text>
          <Text style={styles.line}>Capitale libero {usd(st.cash)} · totale {usd(st.equity)} · chiuse {st.trades} (vinte {st.wins}) · risultato {usd(st.realized)}</Text>
        </View>

        <View style={[styles.card, { borderColor: st.config.auto_enabled ? colors.success : colors.border }]}>
          <View style={styles.row}>
            <Text style={styles.big}>Apertura automatica</Text>
            <Switch value={st.config.auto_enabled} disabled={busy} onValueChange={(v) => setAuto({ auto_enabled: v })} testID="desk-auto-switch" />
          </View>
          <Text style={styles.note}>Il bot apre da solo (in simulazione) quando il mercato indica long o short e passano TUTTI i controlli. Divide la posizione in lotti: il primo si chiude al primo obiettivo, poi lo stop va a pareggio.</Text>
          <Text style={styles.line}>Rischio aperto {usd(st.risk.open_risk)} su massimo {usd(st.risk.open_risk_limit)}</Text>
          <Text style={styles.line}>Risultato di oggi {usd(st.risk.today_pnl)} (limite {usd(st.risk.daily_limit)}) · aperture del bot oggi {st.risk.auto_today}</Text>
          {!!st.risk.pause && <Text style={[styles.note, { color: colors.warning }]}>⏸ {st.risk.pause}</Text>}
          {autoField("Leva massima del bot (si abbassa da sola se serve)", "auto_leverage")}
          {autoField("Rischio per operazione (% del capitale)", "auto_risk_pct")}
          {autoField("Rischio totale massimo di tutte le posizioni (% del capitale)", "max_total_risk_pct")}
          {autoField("Perdita massima al giorno (% del capitale)", "daily_loss_pct")}
          {autoField("Aperture automatiche al giorno (massimo)", "max_trades_day")}
          {autoField("Lotti (1-3)", "lots")}
          {autoField("Primo lotto chiuso quando il guadagno vale (volte il rischio)", "tp1_r")}
          {autoField("Attesa su una moneta dopo una chiusura (minuti)", "cooldown_min")}
          <View style={styles.row}>
            <Text style={styles.line}>Stop a pareggio dopo il primo lotto</Text>
            <Switch value={st.config.be_after_tp1} disabled={busy} onValueChange={(v) => setAuto({ be_after_tp1: v })} testID="desk-be-switch" />
          </View>
          <Pressable style={[styles.btn, { opacity: busy || !af ? 0.4 : 1 }]} disabled={busy || !af} onPress={saveAutoNums}>
            <Text style={styles.btnText}>Salva impostazioni</Text>
          </Pressable>
          <Text style={styles.note}>Dopo {st.config.streak_pause} perdite di fila il bot si ferma per {st.config.streak_pause_h} ore. Il rischio totale e il limite giornaliero valgono anche se apri tu.</Text>
        </View>

        <View style={[styles.card, { borderColor: st.config.scan_enabled ? colors.success : colors.border }]}>
          <View style={styles.row}>
            <Text style={styles.big}>Pre-analisi di tutto il mercato</Text>
            <Switch value={st.config.scan_enabled} disabled={busy} onValueChange={(v) => setAuto({ scan_enabled: v })} testID="desk-scan-switch" />
          </View>
          <Text style={styles.note}>Il bot scandaglia tutte le coppie USDC di Bybit, tiene le piu' scambiate, le analizza a fondo (1d/4h/1h + controlli) e sceglie le {st.config.scan_top} migliori. Poi analizza e opera solo su quelle. Rifa' la scelta ogni {st.config.scan_every_min} minuti. Le posizioni gia' aperte restano gestite.</Text>
          {autoField("Quante coppie scegliere (1-12)", "scan_top")}
          {autoField("Quante coppie analizzare a fondo (le piu' scambiate, 10-80)", "scan_pool")}
          {autoField("Volume minimo nelle 24 ore (USDC)", "scan_min_volume")}
          {autoField("Rifai la scelta ogni (minuti)", "scan_every_min")}
          <Pressable style={[styles.btn, { opacity: busy || !af ? 0.4 : 1 }]} disabled={busy || !af} onPress={saveAutoNums}>
            <Text style={styles.btnText}>Salva impostazioni</Text>
          </Pressable>
          <Pressable style={[styles.btn, styles.btnGhost, { opacity: busy || st.scan.running ? 0.4 : 1 }]} disabled={busy || st.scan.running} onPress={async () => { setBusy(true); try { await deskApi.scan(); setMsg("Scansione avviata: ci vuole un minuto circa."); } catch (e: any) { setMsg("ERRORE: " + String(e?.message ?? e)); } setBusy(false); load(); }}>
            <Text style={[styles.btnText, { color: colors.onSurface }]}>{st.scan.running ? "Scansione in corso..." : "Scandaglia ora"}</Text>
          </Pressable>
          {st.scan.at ? (
            <Text style={styles.note}>Ultima scansione {st.scan.at.slice(5, 16).replace("T", " ")} UTC: {st.scan.total_pairs} coppie USDC su Bybit, {st.scan.analyzed} analizzate a fondo.</Text>
          ) : <Text style={styles.note}>Nessuna scansione ancora: accendi l'interruttore o tocca "Scandaglia ora".</Text>}
          {st.scan.rows.map((r) => (
            <View key={r.symbol} style={styles.row}>
              <Text style={styles.line}>{r.symbol} · {r.bias === "long" ? "LONG" : "SHORT"}{r.checks_ok ? " ✓" : ""}</Text>
              <Text style={styles.note}>forza {r.score} · 24h {r.change24h >= 0 ? "+" : ""}{r.change24h.toFixed(1)}%</Text>
            </View>
          ))}
        </View>

        <View style={styles.card}>
          <Text style={styles.big}>1. Scegli la moneta</Text>
          <View style={styles.chips}>{st.symbols_active.map((s) => chip(s, s === sym, () => { setSym(s); setAn(null); }))}</View>
          <Text style={styles.note}>Leva (1 = senza leva, massimo 10)</Text>
          <TextInput style={styles.inp} value={lev} onChangeText={setLev} keyboardType="decimal-pad" testID="desk-lev" />
          <Text style={styles.note}>Quanto vuoi rischiare in questa operazione (% del capitale)</Text>
          <TextInput style={styles.inp} value={risk} onChangeText={setRisk} keyboardType="decimal-pad" testID="desk-risk" />
          <Pressable style={[styles.btn, { opacity: busy ? 0.4 : 1 }]} disabled={busy} onPress={analyze}>
            <Text style={styles.btnText}>Analizza il mercato</Text>
          </Pressable>
        </View>
        {!!msg && <Text style={styles.msg}>{msg}</Text>}

        {an && (
          <View>
            <View style={styles.card}>
              <Text style={styles.big}>2. Il mercato dice: {biasText(an.analysis.bias)}</Text>
              <Text style={styles.line}>{an.symbol} a {px(an.price)}</Text>
              {an.analysis.notes.map((n, i) => <Text key={i} style={styles.note}>• {n}</Text>)}
            </View>
            <Text style={[styles.big, { marginBottom: spacing.sm }]}>3. Controlli e gestione del rischio</Text>
            {planCard(an.plans.long, an.analysis.bias)}
            {planCard(an.plans.short, an.analysis.bias)}
          </View>
        )}

        <Text style={[styles.big, { marginTop: spacing.md }]}>Posizioni aperte</Text>
        {st.open.length === 0 ? <Text style={styles.note}>Nessuna.</Text> : st.open.map((p) => posCard(p, true))}
        <Text style={[styles.big, { marginTop: spacing.md }]}>Chiuse</Text>
        {st.closed.length === 0 ? <Text style={styles.note}>Nessuna.</Text> : st.closed.slice(0, 15).map((p) => posCard(p, false))}
        <View style={styles.card}>
          <Text style={styles.big}>Cosa e' successo</Text>
          {st.events.map((e, i) => <Text key={i} style={styles.note}>{e.at.slice(5, 16).replace("T", " ")} UTC · {e.symbol} · {e.text}</Text>)}
        </View>
        <View style={styles.card}>
          <Text style={styles.big}>Capitale della simulazione (USDC)</Text>
          <TextInput style={styles.inp} value={capital} onChangeText={setCapital} keyboardType="decimal-pad" testID="desk-capital" />
          <Pressable style={[styles.btn, styles.btnGhost]} disabled={busy} onPress={saveCap}>
            <Text style={[styles.btnText, { color: colors.onSurface }]}>Salva capitale</Text>
          </Pressable>
          <Text style={styles.note}>Si cambia solo senza posizioni aperte e riparte da questo importo.</Text>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  title: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  card: { backgroundColor: colors.surfaceSecondary, borderRadius: radius.lg, padding: spacing.lg, marginBottom: spacing.md, borderWidth: 1, borderColor: colors.border },
  big: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700", marginBottom: spacing.xs },
  line: { color: colors.onSurfaceSecondary, fontSize: font.base, marginTop: spacing.xs },
  note: { color: colors.onSurfaceTertiary, fontSize: font.sm, marginTop: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.xs },
  btn: { backgroundColor: colors.brand, borderRadius: radius.lg, padding: spacing.lg, alignItems: "center", marginTop: spacing.md },
  btnGhost: { backgroundColor: colors.surfaceTertiary },
  btnText: { color: colors.onBrand, fontWeight: "700", fontSize: font.base },
  msg: { color: colors.warning, fontSize: font.base, marginBottom: spacing.md },
  chips: { flexDirection: "row", flexWrap: "wrap", marginTop: spacing.xs },
  chip: { backgroundColor: colors.surfaceTertiary, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, marginRight: spacing.sm, marginTop: spacing.xs },
  chipText: { color: colors.onSurface, fontSize: font.sm, fontWeight: "600" },
  inp: { backgroundColor: colors.surfaceTertiary, color: colors.onSurface, borderRadius: radius.md, padding: spacing.sm, marginTop: spacing.xs, fontSize: font.base },
});
