import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { manualApi, ManualList, ManualParse, ManualRow } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const usd = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `${n < 0 ? "-" : ""}$${Math.abs(n).toFixed(2)}`);
const px = (n?: number | null) => {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
};
const reason = (r?: string) => (r === "target" ? "target" : r === "stop" ? "stop" : r === "chiusura_manuale" ? "chiusa da te" : r === "chiusura_manuale_reale" ? "chiusura manuale (STOP)" : r ?? "");

const EXAMPLE = "SETUP SUIUSDC\nIngresso (limite) 0.9050\nStop 0.8800\nTarget 0.9600\nImporto 10";

export default function ManualScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [text, setText] = useState("");
  const [res, setRes] = useState<ManualParse | null>(null);
  const [list, setList] = useState<ManualList | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState<null | "paper" | "real">(null);
  const [askClose, setAskClose] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setList(await manualApi.list());
    } catch (e: any) {
      setMsg(String(e?.message ?? "Errore"));
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const analyze = async () => {
    setBusy(true);
    setMsg("");
    setAsk(null);
    try {
      setRes(await manualApi.parse(text));
    } catch (e: any) {
      setRes(null);
      setMsg("ERRORE: " + String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const execute = async (mode: "paper" | "real") => {
    if (!res || !res.ok) return;
    setBusy(true);
    try {
      const r = await manualApi.create(res.setup, mode);
      setMsg(r.status === "open" ? "Operazione aperta." : "Ordine a limite in attesa: scatta quando il prezzo scende al livello.");
      setRes(null);
      setText("");
    } catch (e: any) {
      setMsg("ERRORE: " + String(e?.message ?? e));
    } finally {
      setBusy(false);
      setAsk(null);
      load();
    }
  };

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      setMsg(ok);
    } catch (e: any) {
      setMsg("ERRORE: " + String(e?.message ?? e));
    } finally {
      setBusy(false);
      setAskClose(null);
      load();
    }
  };

  const card = (p: ManualRow, kind: "waiting" | "open" | "closed") => (
    <View key={p.id} style={styles.card}>
      <View style={styles.row}>
        <Text style={styles.big}>{p.symbol}{p.mode === "real" ? "  · REALE" : "  · simulazione"}</Text>
        {kind === "open" && <Text style={[styles.big, { color: (p.floating_pnl ?? 0) >= 0 ? colors.success : colors.error }]}>{usd(p.floating_pnl)}</Text>}
        {kind === "closed" && p.status === "closed" && <Text style={[styles.big, { color: (p.pnl_usdt ?? 0) >= 0 ? colors.success : colors.error }]}>{usd(p.pnl_usdt)}</Text>}
      </View>
      <Text style={styles.line}>
        {kind === "waiting" ? `Ordine a limite ${px(p.entry)}` : `Entrata ${px(p.fill_price ?? p.entry)}`} · stop {px(p.stop)} · target {px(p.target)} · importo {usd(p.amount)}
      </Text>
      {kind === "waiting" && <Text style={styles.note}>Prezzo ora {px(p.current_price)}. Compra quando scende a {px(p.entry)}.</Text>}
      {kind === "open" && <Text style={styles.note}>Prezzo ora {px(p.current_price)}</Text>}
      {kind === "closed" && (
        <Text style={styles.note}>
          {p.status === "cancelled" ? `Annullato: ${p.close_reason ?? ""}` : `Uscita ${px(p.close_price)} (${reason(p.close_reason)})`}
        </Text>
      )}
      {kind === "waiting" && (
        <Pressable style={[styles.btn, styles.btnGhost]} disabled={busy} onPress={() => act(() => manualApi.cancel(p.id), "Annullato.")}>
          <Text style={[styles.btnText, { color: colors.onSurface }]}>Annulla ordine</Text>
        </Pressable>
      )}
      {kind === "open" && askClose !== p.id && (
        <Pressable style={[styles.btn, styles.btnGhost]} disabled={busy} onPress={() => setAskClose(p.id)}>
          <Text style={[styles.btnText, { color: colors.onSurface }]}>Chiudi ora</Text>
        </Pressable>
      )}
      {kind === "open" && askClose === p.id && (
        <View>
          <Text style={[styles.note, { color: colors.warning }]}>Vendere adesso a mercato {p.symbol}?</Text>
          <Pressable style={styles.btn} disabled={busy} onPress={() => act(() => manualApi.close(p.id), "Chiusa.")}>
            <Text style={styles.btnText}>Sì, vendi ora</Text>
          </Pressable>
          <Pressable style={[styles.btn, styles.btnGhost]} onPress={() => setAskClose(null)}>
            <Text style={[styles.btnText, { color: colors.onSurface }]}>No</Text>
          </Pressable>
        </View>
      )}
    </View>
  );

  const p = res && res.ok ? res.plan : null;
  const blocked = !!p && p.errors.length > 0;

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>Setup manuale</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} tintColor={colors.brand} />}
      >
        <View style={styles.card}>
          <Text style={styles.big}>Incolla il segnale</Text>
          <Text style={styles.note}>Solo acquisti (rialzo). Scrivi moneta, ingresso, stop, target e importo. Per comprare subito scrivi "mercato" al posto dell&apos;ingresso. Puoi aggiungere "rischio 3" per un limite alla perdita.</Text>
          <TextInput
            style={styles.input}
            multiline
            value={text}
            onChangeText={(v) => { setText(v); setRes(null); setAsk(null); }}
            placeholder={EXAMPLE}
            placeholderTextColor={colors.onSurfaceTertiary}
            autoCapitalize="none"
            autoCorrect={false}
            testID="manual-text"
          />
          <Pressable style={styles.btn} disabled={busy || !text.trim()} onPress={analyze}>
            {busy ? <ActivityIndicator color={colors.onBrand} /> : <Text style={styles.btnText}>Analizza</Text>}
          </Pressable>
        </View>

        {res && !res.ok && (
          <View style={[styles.card, { borderColor: colors.error }]}>
            <Text style={[styles.line, { color: colors.error }]}>{res.error}</Text>
          </View>
        )}

        {res && res.ok && p && (
          <View style={styles.card}>
            <Text style={styles.big}>{res.setup.symbol} · prezzo ora {px(res.price)}</Text>
            <Text style={styles.line}>
              Ingresso: {res.setup.entry === null ? "a mercato, subito" : `ordine a limite ${px(res.setup.entry)}`}
            </Text>
            <Text style={styles.line}>Stop {px(res.setup.stop)} · Target {px(res.setup.target)} · Importo {usd(res.setup.amount)}</Text>
            {!blocked && (
              <View style={{ marginTop: spacing.sm }}>
                <Text style={[styles.line, { color: colors.error }]}>Se va male (stop) perdi circa {usd(-(p.loss_net ?? 0))} ({p.risk_pct}% sul prezzo)</Text>
                <Text style={[styles.line, { color: colors.success }]}>Se arriva al target guadagni circa {usd(p.gain_net)} (+{p.gain_pct}% sul prezzo)</Text>
                <Text style={styles.line}>Rapporto guadagno/rischio {p.rr === null || p.rr === undefined ? "-" : `${p.rr} a 1`} · commissioni a giro circa {p.fees_roundtrip_pct}%</Text>
                <Text style={styles.note}>Pareggio (dopo le commissioni): {px(p.breakeven)}</Text>
              </View>
            )}
            {p.errors.map((e, i) => (
              <Text key={"e" + i} style={[styles.line, { color: colors.error }]}>✖ {e}</Text>
            ))}
            {p.warnings.map((w, i) => (
              <Text key={"w" + i} style={[styles.line, { color: colors.warning }]}>⚠ {w}</Text>
            ))}
            {!blocked && ask === null && (
              <View style={{ marginTop: spacing.md }}>
                <Pressable style={styles.btn} disabled={busy} onPress={() => setAsk("paper")}>
                  <Text style={styles.btnText}>Esegui in simulazione</Text>
                </Pressable>
                <Pressable
                  style={[styles.btn, { backgroundColor: res.real.available ? colors.error : colors.surfaceTertiary }]}
                  disabled={busy || !res.real.available}
                  onPress={() => setAsk("real")}
                >
                  <Text style={[styles.btnText, !res.real.available && { color: colors.onSurfaceTertiary }]}>Esegui con soldi VERI</Text>
                </Pressable>
                {!res.real.available && <Text style={styles.note}>Reale non disponibile: {res.real.why}</Text>}
              </View>
            )}
            {!blocked && ask !== null && (
              <View style={{ marginTop: spacing.md }}>
                <Text style={[styles.big, { color: ask === "real" ? colors.error : colors.onSurface }]}>
                  Confermi? {ask === "real" ? "SOLDI VERI" : "Simulazione"}
                </Text>
                <Text style={styles.line}>
                  {res.setup.symbol} · {res.setup.entry === null ? "compra subito" : `compra se scende a ${px(res.setup.entry)}`} · stop {px(res.setup.stop)} · target {px(res.setup.target)} · {usd(res.setup.amount)}
                </Text>
                {ask === "real" && (
                  <Text style={[styles.note, { color: colors.warning }]}>
                    Stop e target li controlla il bot ogni 3 secondi: non sono ordini su Bybit. Se il bot è spento, non scattano.
                  </Text>
                )}
                <Pressable style={styles.btn} disabled={busy} onPress={() => execute(ask)}>
                  {busy ? <ActivityIndicator color={colors.onBrand} /> : <Text style={styles.btnText}>Sì, esegui</Text>}
                </Pressable>
                <Pressable style={[styles.btn, styles.btnGhost]} onPress={() => setAsk(null)}>
                  <Text style={[styles.btnText, { color: colors.onSurface }]}>No</Text>
                </Pressable>
              </View>
            )}
          </View>
        )}

        {!!msg && <Text style={styles.msg}>{msg}</Text>}

        {list && (
          <>
            <Text style={[styles.big, { marginTop: spacing.lg }]}>In attesa ({list.waiting.length})</Text>
            {list.waiting.length === 0 && <Text style={styles.note}>Nessuno.</Text>}
            {list.waiting.map((p) => card(p, "waiting"))}
            <Text style={[styles.big, { marginTop: spacing.lg }]}>Aperte ({list.open.length})</Text>
            {list.open.length === 0 && <Text style={styles.note}>Nessuna.</Text>}
            {list.open.map((p) => card(p, "open"))}
            <Text style={[styles.big, { marginTop: spacing.lg }]}>Storico · chiuse {list.trades} · vinte {list.wins} · risultato {usd(list.realized)}</Text>
            {list.closed.length === 0 && <Text style={styles.note}>Nessuna.</Text>}
            {list.closed.map((p) => card(p, "closed"))}
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
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.sm },
  btn: { backgroundColor: colors.brand, borderRadius: radius.lg, padding: spacing.lg, alignItems: "center", marginTop: spacing.sm },
  btnGhost: { backgroundColor: colors.surfaceTertiary },
  btnText: { color: colors.onBrand, fontWeight: "700", fontSize: font.base },
  msg: { color: colors.warning, fontSize: font.base, marginTop: spacing.sm },
  input: { minHeight: 130, backgroundColor: colors.surfaceTertiary, color: colors.onSurface, borderRadius: radius.md, padding: spacing.md, marginTop: spacing.md, fontSize: font.base, textAlignVertical: "top" },
});
