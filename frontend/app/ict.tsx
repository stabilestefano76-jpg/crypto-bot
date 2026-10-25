import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, RefreshControl, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { ictApi, IctPortfolio, IctPosition } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const usd = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `${n < 0 ? "-" : ""}$${Math.abs(n).toFixed(2)}`);
const px = (n?: number | null) => {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
};
const reason = (r?: string) => (r === "target" ? "target" : r === "stop" ? "stop" : r === "tempo" ? "tempo massimo" : r === "chiusura_manuale_reale" ? "chiusura manuale" : r ?? "");
const TFS = ["5m", "15m", "30m", "1h"];
const RRS = [1.5, 2, 3];
const ALLOCS = [10, 25, 50];

export default function IctScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [pf, setPf] = useState<IctPortfolio | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setPf(await ictApi.portfolio());
    } catch (e: any) {
      setMsg(String(e?.message ?? "Errore"));
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [load]);

  const save = async (patch: Record<string, unknown>) => {
    setBusy(true);
    try {
      await ictApi.save(patch);
      setMsg("Salvato");
    } catch (e: any) {
      setMsg("ERRORE: " + String(e?.message ?? e));
    } finally {
      setBusy(false);
      load();
    }
  };

  if (!pf)
    return (
      <View style={[styles.root, { paddingTop: insets.top, justifyContent: "center" }]}>
        <ActivityIndicator color={colors.brand} />
      </View>
    );

  const s = pf.settings;
  const Chips = ({ values, current, onPick, fmt }: { values: (string | number)[]; current: string | number; onPick: (v: any) => void; fmt?: (v: any) => string }) => (
    <View style={{ flexDirection: "row", gap: spacing.sm, marginTop: spacing.xs }}>
      {values.map((v) => (
        <Pressable key={String(v)} disabled={busy} onPress={() => onPick(v)} style={[styles.chip, current === v && styles.chipOn]}>
          <Text style={[styles.chipText, current === v && { color: colors.onBrand }]}>{fmt ? fmt(v) : String(v)}</Text>
        </Pressable>
      ))}
    </View>
  );

  const posRow = (p: IctPosition, open: boolean) => (
    <View key={p.id} style={styles.card}>
      <View style={styles.row}>
        <Text style={styles.big}>{p.symbol}{p.is_real ? "  · REALE" : ""}</Text>
        {open ? (
          <Text style={[styles.big, { color: (p.floating_pnl ?? 0) >= 0 ? colors.success : colors.error }]}>{usd(p.floating_pnl)}</Text>
        ) : (
          <Text style={[styles.big, { color: (p.pnl_usdt ?? 0) >= 0 ? colors.success : colors.error }]}>{usd(p.pnl_usdt)}</Text>
        )}
      </View>
      <Text style={styles.line}>Entrata {px(p.fill_price)} · stop {px(p.stop)} · target {px(p.target)}</Text>
      <Text style={styles.note}>
        Rischio {p.risk_pct}% · RR 1:{p.rr} · importo {usd(p.notional)}
        {open ? ` · prezzo ora ${px(p.current_price)}` : ` · uscita ${px(p.close_price)} (${reason(p.close_reason)})`}
      </Text>
      <Text style={styles.note}>{new Date(open ? p.opened_at : p.closed_at ?? p.opened_at).toLocaleString("it-IT")}</Text>
    </View>
  );

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>ICT</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} tintColor={colors.brand} />}
      >
        <View style={[styles.card, { borderColor: pf.mode === "real" ? colors.error : colors.border }]}>
          <Text style={styles.big}>
            {pf.mode === "real" ? (pf.real_switch_on ? "REALE ACCESO" : "Reale scelto ma interruttore generale spento") : "Solo simulazione (paper)"}
          </Text>
          <Text style={styles.note}>
            Compra solo al rialzo. Cerca uno sweep dei minimi, la rottura di struttura e il ritorno nel gap. Per passare al reale usa la pagina Trading reale.
          </Text>
          <Text style={[styles.note, { color: colors.warning }]}>{pf.disclaimer}</Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.big}>Capitale ICT (simulato)</Text>
          <Text style={styles.line}>Equity {usd(pf.equity)} · cassa {usd(pf.cash)} · partenza {usd(pf.start_capital)}</Text>
          <Text style={styles.line}>Chiuse {pf.trades} · vinte {pf.win_rate === null ? "-" : pf.win_rate + "%"} · risultato {usd(pf.realized)} · aperto {usd(pf.unrealized)}</Text>
          <Text style={styles.note}>
            Ultimo controllo: {pf.last_scan ? new Date(pf.last_scan).toLocaleTimeString("it-IT") : "non ancora"}
          </Text>
        </View>

        <View style={styles.card}>
          <View style={styles.row}>
            <Text style={styles.big}>Strategia accesa</Text>
            <Switch value={s.ict_enabled} disabled={busy} onValueChange={(v) => save({ ict_enabled: v })} />
          </View>
          <View style={styles.row}>
            <Text style={styles.line}>Solo negli orari Londra/New York</Text>
            <Switch value={s.ict_kill_zones} disabled={busy} onValueChange={(v) => save({ ict_kill_zones: v })} />
          </View>
          <Text style={styles.line}>Timeframe</Text>
          <Chips values={TFS} current={s.ict_timeframe} onPick={(v) => save({ ict_timeframe: v })} />
          <Text style={[styles.line, { marginTop: spacing.md }]}>Rapporto guadagno/rischio (RR)</Text>
          <Chips values={RRS} current={s.ict_rr} onPick={(v) => save({ ict_rr: v })} fmt={(v) => `1:${v}`} />
          <Text style={[styles.line, { marginTop: spacing.md }]}>Quota del capitale per operazione</Text>
          <Chips values={ALLOCS} current={s.ict_alloc_pct} onPick={(v) => save({ ict_alloc_pct: v })} fmt={(v) => `${v}%`} />
          <Text style={[styles.note, { marginTop: spacing.md }]}>
            Monete: {s.ict_symbols.join(", ")} · massimo {s.ict_max_open} aperte · durata massima {s.ict_max_hold_h} ore
          </Text>
        </View>

        <Text style={[styles.big, { marginTop: spacing.sm }]}>Cosa sta osservando</Text>
        {Object.keys(pf.watching).length === 0 && <Text style={styles.note}>Nessun dato ancora.</Text>}
        {Object.entries(pf.watching).map(([sym, w]) => (
          <Text key={sym} style={styles.line}>
            {sym} · prezzo {px(w.price)} · {w.setups > 0 ? `setup pronto: ingresso se scende a ${px(w.fvg_top)} (stop ${px(w.stop)})` : "nessun setup"}
          </Text>
        ))}

        <Text style={[styles.big, { marginTop: spacing.lg }]}>Posizioni aperte ({pf.open_positions.length})</Text>
        {pf.open_positions.length === 0 && <Text style={styles.note}>Nessuna.</Text>}
        {pf.open_positions.map((p) => posRow(p, true))}

        <Text style={[styles.big, { marginTop: spacing.lg }]}>Storico ({pf.closed_positions.length})</Text>
        {pf.closed_positions.length === 0 && <Text style={styles.note}>Nessuna operazione chiusa.</Text>}
        {pf.closed_positions.map((p) => posRow(p, false))}

        {!!msg && <Text style={styles.msg}>{msg}</Text>}

        <Pressable
          style={[styles.btn, { backgroundColor: colors.surfaceTertiary, marginTop: spacing.lg }]}
          disabled={busy}
          onPress={() =>
            Alert.alert("Azzerare ICT?", "Cancella storico e posizioni simulate e rimette il capitale iniziale.", [
              { text: "Annulla", style: "cancel" },
              {
                text: "Azzera",
                style: "destructive",
                onPress: async () => {
                  try {
                    await ictApi.reset();
                    setMsg("Azzerato");
                  } catch (e: any) {
                    setMsg("ERRORE: " + String(e?.message ?? e));
                  }
                  load();
                },
              },
            ])
          }
        >
          <Text style={[styles.btnText, { color: colors.onSurface }]}>Azzera ICT</Text>
        </Pressable>
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
  btn: { backgroundColor: colors.brand, borderRadius: radius.lg, padding: spacing.lg, alignItems: "center", marginBottom: spacing.md },
  btnText: { color: colors.onBrand, fontWeight: "700", fontSize: font.base },
  msg: { color: colors.warning, fontSize: font.base, marginTop: spacing.sm },
  chip: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.md, backgroundColor: colors.surfaceTertiary },
  chipOn: { backgroundColor: colors.brand },
  chipText: { color: colors.onSurface, fontSize: font.sm, fontWeight: "600" },
});
