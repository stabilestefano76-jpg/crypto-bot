import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { maApi, MaCoin, MaConfig, MaRow, MaStatus } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const usd = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `${n < 0 ? "-" : ""}$${Math.abs(n).toFixed(2)}`);
const px = (n?: number | null) => {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
};
const why = (r?: string) =>
  r === "incrocio_ribasso" ? "media veloce sotto la lenta" : r === "stop_sicurezza" ? "stop di sicurezza" : r === "chiusura_manuale" ? "chiusa da te" : r ?? "";
const PRESETS: { label: string; fast: number; slow: number }[] = [
  { label: "5 / 20 (veloce)", fast: 5, slow: 20 },
  { label: "10 / 50", fast: 10, slow: 50 },
  { label: "50 / 200 (lenta)", fast: 50, slow: 200 },
];

export default function MaScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [st, setSt] = useState<MaStatus | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [askClose, setAskClose] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [kind, setKind] = useState<"SMA" | "EMA">("SMA");
  const [tf, setTf] = useState<"1h" | "4h" | "1d">("1d");
  const [fast, setFast] = useState("10");
  const [slow, setSlow] = useState("50");
  const [amount, setAmount] = useState("50");
  const [maxPos, setMaxPos] = useState("4");
  const [stopPct, setStopPct] = useState("0");
  const [symbols, setSymbols] = useState("");
  const [enabled, setEnabled] = useState(false);
  const filled = useRef(false);

  const fill = (c: MaConfig) => {
    setKind(c.kind); setTf(c.tf); setFast(String(c.fast)); setSlow(String(c.slow)); setAmount(String(c.amount));
    setMaxPos(String(c.max_positions)); setStopPct(String(c.stop_pct)); setSymbols(c.symbols.join(", ")); setEnabled(c.enabled);
  };
  const load = useCallback(async () => {
    try {
      const s = await maApi.status();
      setSt(s);
      if (!filled.current) {
        filled.current = true;
        fill(s.config);
      }
    } catch (e: any) {
      setMsg(String(e?.message ?? "Errore"));
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 7000);
    return () => clearInterval(t);
  }, [load]);

  const save = async (extra?: Partial<MaConfig>) => {
    setBusy(true);
    setMsg("");
    try {
      const body: Partial<MaConfig> = {
        kind, tf, fast: Number(fast), slow: Number(slow), amount: Number(amount.replace(",", ".")),
        max_positions: Number(maxPos), stop_pct: Number(stopPct.replace(",", ".")),
        symbols: symbols.split(/[\s,;]+/).filter(Boolean), ...extra,
      };
      const r = await maApi.setConfig(body);
      fill(r.config);
      setMsg("Salvato.");
    } catch (e: any) {
      setMsg("ERRORE: " + String(e?.message ?? e));
    } finally {
      setBusy(false);
      load();
    }
  };

  const chip = (label: string, on: boolean, onPress: () => void) => (
    <Pressable key={label} onPress={onPress} style={[styles.chip, on && { backgroundColor: colors.brand }]}>
      <Text style={[styles.chipText, on && { color: colors.onBrand }]}>{label}</Text>
    </Pressable>
  );
  const field = (label: string, v: string, set: (s: string) => void, id: string, w = 90) => (
    <View style={{ marginRight: spacing.md, marginTop: spacing.sm }}>
      <Text style={styles.note}>{label}</Text>
      <TextInput style={[styles.inp, { width: w }]} value={v} onChangeText={set} keyboardType="decimal-pad" testID={id} />
    </View>
  );

  const posCard = (p: MaRow, open: boolean) => (
    <View key={p.id} style={styles.card}>
      <View style={styles.row}>
        <Text style={styles.big}>{p.symbol} · {p.kind} {p.fast}/{p.slow} · {p.timeframe}</Text>
        <Text style={[styles.big, { color: ((open ? p.floating_pnl : p.pnl_usdt) ?? 0) >= 0 ? colors.success : colors.error }]}>
          {usd(open ? p.floating_pnl : p.pnl_usdt)}
        </Text>
      </View>
      <Text style={styles.line}>Entrata {px(p.fill_price)} · importo {usd(p.notional)}</Text>
      {open ? <Text style={styles.note}>Prezzo ora {px(p.current_price)}</Text> : <Text style={styles.note}>Uscita {px(p.close_price)} ({why(p.close_reason)})</Text>}
      {open && askClose !== p.id && (
        <Pressable style={[styles.btn, styles.btnGhost]} disabled={busy} onPress={() => setAskClose(p.id)}>
          <Text style={[styles.btnText, { color: colors.onSurface }]}>Chiudi ora</Text>
        </Pressable>
      )}
      {open && askClose === p.id && (
        <View>
          <Text style={[styles.note, { color: colors.warning }]}>Chiudere adesso {p.symbol} (simulazione)?</Text>
          <Pressable style={styles.btn} disabled={busy} onPress={async () => { setBusy(true); try { await maApi.close(p.id); setMsg("Chiusa."); } catch (e: any) { setMsg("ERRORE: " + String(e?.message ?? e)); } setBusy(false); setAskClose(null); load(); }}>
            <Text style={styles.btnText}>Sì, chiudi</Text>
          </Pressable>
          <Pressable style={[styles.btn, styles.btnGhost]} onPress={() => setAskClose(null)}>
            <Text style={[styles.btnText, { color: colors.onSurface }]}>No</Text>
          </Pressable>
        </View>
      )}
    </View>
  );

  const coinLine = (c: MaCoin) => (
    <View key={c.symbol} style={styles.row}>
      <Text style={styles.line}>{c.symbol}{c.in_position ? " ●" : ""}</Text>
      <Text style={[styles.line, { color: c.above === undefined ? colors.onSurfaceTertiary : c.above ? colors.success : colors.error }]}>
        {c.above === undefined ? c.note || "-" : `${c.above ? "veloce SOPRA" : "veloce SOTTO"} (${(c.dist_pct ?? 0) > 0 ? "+" : ""}${c.dist_pct}%)${c.cross === "up" ? " · INCROCIO!" : ""}`}
      </Text>
    </View>
  );

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>Medie mobili</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} tintColor={colors.brand} />}
      >
        <View style={styles.card}>
          <View style={styles.row}>
            <Text style={styles.big}>Strategia {enabled ? "ACCESA" : "spenta"}</Text>
            <Switch value={enabled} disabled={busy} onValueChange={(v) => { setEnabled(v); save({ enabled: v }); }} />
          </View>
          <Text style={styles.note}>
            Solo SIMULAZIONE (nessun soldo vero). Compra quando la media veloce incrocia verso l&apos;alto quella lenta, a candela chiusa. Vende quando la veloce scende sotto la lenta. I test sul passato non hanno mostrato un vantaggio sicuro: serve a vedere come va dal vivo.
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.big}>Impostazioni</Text>
          <Text style={styles.note}>Tipo di media</Text>
          <View style={styles.chips}>{chip("SMA (semplice)", kind === "SMA", () => setKind("SMA"))}{chip("EMA (esponenziale)", kind === "EMA", () => setKind("EMA"))}</View>
          <Text style={styles.note}>Timeframe (ogni candela)</Text>
          <View style={styles.chips}>{(["1h", "4h", "1d"] as const).map((t) => chip(t, tf === t, () => setTf(t)))}</View>
          <Text style={styles.note}>Coppie di medie consigliate</Text>
          <View style={styles.chips}>{PRESETS.map((p) => chip(p.label, Number(fast) === p.fast && Number(slow) === p.slow, () => { setFast(String(p.fast)); setSlow(String(p.slow)); }))}</View>
          <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
            {field("Media veloce", fast, setFast, "ma-fast")}
            {field("Media lenta", slow, setSlow, "ma-slow")}
            {field("Importo per operazione", amount, setAmount, "ma-amount", 110)}
            {field("Posizioni contemporanee", maxPos, setMaxPos, "ma-maxpos", 110)}
            {field("Stop di sicurezza % (0 = spento)", stopPct, setStopPct, "ma-stop", 110)}
          </View>
          <Text style={styles.note}>Monete (coppie USDC, separate da virgola)</Text>
          <TextInput style={[styles.inp, { width: "100%" }]} value={symbols} onChangeText={setSymbols} autoCapitalize="characters" autoCorrect={false} testID="ma-symbols" />
          <Pressable style={styles.btn} disabled={busy} onPress={() => save()}>
            {busy ? <ActivityIndicator color={colors.onBrand} /> : <Text style={styles.btnText}>Salva impostazioni</Text>}
          </Pressable>
          {!!msg && <Text style={styles.msg}>{msg}</Text>}
          <Text style={styles.note}>Sul giornaliero gli incroci sono rari (qualche operazione all&apos;anno per moneta): è normale che per giorni non succeda nulla.</Text>
        </View>

        {st && (
          <>
            <View style={styles.card}>
              <Text style={styles.big}>Situazione delle monete</Text>
              {st.coins.map(coinLine)}
              <Text style={styles.note}>● = posizione aperta. &quot;veloce SOPRA&quot; = trend al rialzo.</Text>
            </View>
            <Text style={[styles.big, { marginTop: spacing.sm }]}>Aperte ({st.open.length})</Text>
            {st.open.length === 0 && <Text style={styles.note}>Nessuna.</Text>}
            {st.open.map((p) => posCard(p, true))}
            <Text style={[styles.big, { marginTop: spacing.lg }]}>Storico · chiuse {st.trades} · vinte {st.wins} · risultato {usd(st.realized)}</Text>
            {st.closed.length === 0 && <Text style={styles.note}>Nessuna.</Text>}
            {st.closed.map((p) => posCard(p, false))}
            <Text style={[styles.big, { marginTop: spacing.lg }]}>Ultimi eventi</Text>
            {st.events.length === 0 && <Text style={styles.note}>Nessuno.</Text>}
            {st.events.map((e, i) => (
              <Text key={i} style={styles.note}>{e.at.slice(5, 16).replace("T", " ")} · {e.symbol} · {e.text}</Text>
            ))}
          </>
        )}
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
  msg: { color: colors.warning, fontSize: font.base, marginTop: spacing.sm },
  chips: { flexDirection: "row", flexWrap: "wrap", marginTop: spacing.xs },
  chip: { backgroundColor: colors.surfaceTertiary, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, marginRight: spacing.sm, marginTop: spacing.xs },
  chipText: { color: colors.onSurface, fontSize: font.sm, fontWeight: "600" },
  inp: { backgroundColor: colors.surfaceTertiary, color: colors.onSurface, borderRadius: radius.md, padding: spacing.sm, marginTop: spacing.xs, fontSize: font.base },
});
