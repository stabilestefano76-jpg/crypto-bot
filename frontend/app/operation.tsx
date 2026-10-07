import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { trackApi, PositionTrack } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const CHART_H = 180;
const MAX_COLS = 80;
const LEVEL_COLORS = {
  entry: "#E5E7EB",
  break_even: "#F59E0B",
  exit_level: "#10B981",
  trailing_stop: "#3B82F6",
  now: "#A78BFA",
};

// Price with enough decimals for cheap coins too.
function px(n?: number | null): string {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
}

function money(n?: number | null): string {
  if (n === undefined || n === null || isNaN(n)) return "-";
  return `${n >= 0 ? "+" : "-"}$${Math.abs(n).toFixed(2)}`;
}

function pct(n?: number | null): string {
  if (n === undefined || n === null || isNaN(n)) return "-";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

// 3725 -> "1h 2m", 90000 -> "1g 1h", 40 -> "40s"
function fmtDur(s?: number | null): string {
  if (s === undefined || s === null || isNaN(s)) return "-";
  const t = Math.max(0, Math.round(s));
  const d = Math.floor(t / 86400);
  const h = Math.floor((t % 86400) / 3600);
  const m = Math.floor((t % 3600) / 60);
  if (d > 0) return `${d}g ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${t}s`;
}

function fmtWhen(iso?: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "-";
  return d.toLocaleString("it-IT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function reasonLabel(r?: string | null): string {
  if (r === "target_rsi_raggiunto") return "target RSI";
  if (r === "trailing_stop") return "trailing";
  return r ?? "";
}

// At most `n` columns. From each group of samples keep the one farthest from the
// entry price, so a dip or a peak never disappears from the chart.
function bucketSamples(samples: number[][], n: number, entry: number): number[][] {
  if (samples.length <= n) return samples;
  const out: number[][] = [];
  const size = samples.length / n;
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * size);
    const b = Math.max(a + 1, Math.floor((i + 1) * size));
    const slice = samples.slice(a, b);
    let best = slice[0];
    for (const s of slice) if (Math.abs(s[1] - entry) > Math.abs(best[1] - entry)) best = s;
    out.push(best);
  }
  return out;
}

// Vertical range of the chart: the prices and the levels, with a little margin.
function chartRange(prices: number[], levels: number[]): [number, number] {
  const all = [...prices, ...levels].filter((v) => v > 0 && !isNaN(v));
  if (all.length === 0) return [0, 1];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const pad = (hi - lo) * 0.08 || hi * 0.01;
  return [lo - pad, hi + pad];
}

function PriceChart({ track }: { track: PositionTrack }) {
  const entry = track.levels.entry;
  const cols = useMemo(() => {
    const base = track.samples.map((s) => [s[0], s[1]]);
    if (track.status === "open" && track.current_price) base.push([Date.now() / 1000, track.current_price]);
    return bucketSamples(base, MAX_COLS, entry);
  }, [track, entry]);

  const levelLines: { key: string; v: number; color: string }[] = [
    { key: "entry", v: track.levels.entry, color: LEVEL_COLORS.entry },
    { key: "break_even", v: track.levels.break_even, color: LEVEL_COLORS.break_even },
    { key: "exit_level", v: track.levels.exit_level, color: LEVEL_COLORS.exit_level },
  ];
  if (track.levels.trailing_stop) levelLines.push({ key: "trailing_stop", v: track.levels.trailing_stop, color: LEVEL_COLORS.trailing_stop });
  if (track.status === "open" && track.current_price) levelLines.push({ key: "now", v: track.current_price, color: LEVEL_COLORS.now });

  const [lo, hi] = chartRange(cols.map((c) => c[1]), levelLines.map((l) => l.v));
  const y = (v: number) => Math.max(0, Math.min(CHART_H, ((v - lo) / (hi - lo)) * CHART_H));

  if (cols.length < 2) {
    return (
      <View style={[styles.chart, { alignItems: "center", justifyContent: "center" }]}>
        <Text style={styles.muted}>Il grafico si riempie col passare dei minuti.</Text>
      </View>
    );
  }
  return (
    <View>
      <View style={styles.chart}>
        {cols.map((c, i) => (
          <View
            key={i}
            style={{
              position: "absolute",
              bottom: 0,
              left: `${(i / cols.length) * 100}%` as any,
              width: `${100 / cols.length}%` as any,
              height: Math.max(1, y(c[1])),
              backgroundColor: c[1] >= entry ? colors.success + "88" : colors.error + "88",
            }}
          />
        ))}
        {levelLines.map((l) => (
          <View
            key={l.key}
            style={{ position: "absolute", left: 0, right: 0, bottom: y(l.v), height: 1.5, backgroundColor: l.color }}
          />
        ))}
      </View>
      <View style={styles.axisRow}>
        <Text style={styles.axisText}>{fmtWhen(new Date(cols[0][0] * 1000).toISOString())}</Text>
        <Text style={styles.axisText}>{track.status === "open" ? "adesso" : fmtWhen(track.closed_at)}</Text>
      </View>
    </View>
  );
}

export default function OperationScreen() {
  const { kind, id } = useLocalSearchParams<{ kind: string; id: string }>();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [track, setTrack] = useState<PositionTrack | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!kind || !id) return;
    try {
      const t = await trackApi.get(String(kind), String(id));
      setTrack(t);
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Errore di caricamento");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [kind, id]);

  useEffect(() => {
    load();
  }, [load]);

  // While the operation is open, refresh by itself every 15 seconds.
  const isOpen = track?.status === "open";
  useEffect(() => {
    if (!isOpen) return;
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [isOpen, load]);

  const strategyName = track?.kind === "xrp_acc" ? "XRP Accumulation" : "33/60";
  const closed = track?.status === "closed";
  const headline = closed ? track?.pnl_usdt : track?.pct_vs_entry;
  const headColor = (headline ?? 0) >= 0 ? colors.success : colors.error;
  const lv = track?.levels;
  const st = track?.stats;
  const missing =
    track && track.current_price && lv ? (lv.exit_level / track.current_price - 1) * 100 : null;

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <View style={{ alignItems: "center" }}>
          <Text style={styles.title}>{track?.symbol ?? "Operazione"}</Text>
          {track ? (
            <Text style={styles.subtitle}>
              {strategyName}
              {track.timeframe ? ` · ${track.timeframe.toUpperCase()}` : ""}
            </Text>
          ) : null}
        </View>
        <View style={{ width: 26 }} />
      </View>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.brand} />
      ) : error && !track ? (
        <Text style={styles.errorText}>{error}</Text>
      ) : track && lv && st ? (
        <ScrollView
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load();
              }}
              tintColor={colors.brand}
            />
          }
        >
          <View style={styles.card}>
            <View style={styles.rowBetween}>
              <View style={[styles.badge, { backgroundColor: closed ? colors.surfaceTertiary : colors.brand }]}>
                <Text style={styles.badgeText}>{closed ? "CHIUSA" : "APERTA"}</Text>
              </View>
              <Text style={styles.muted}>{closed ? reasonLabel(track.close_reason) : `da ${fmtDur(st.age_s)}`}</Text>
            </View>
            <Text style={[styles.big, { color: headColor }]}>{closed ? money(headline) : pct(headline)}</Text>
            <Text style={styles.muted}>
              {closed
                ? `Guadagno netto dopo le commissioni${track.profit_xrp ? ` · +${track.profit_xrp} XRP` : ""}`
                : "Rispetto al prezzo pagato (prima delle commissioni di vendita)"}
            </Text>
            <Text style={styles.line}>
              Pagato {px(track.fill_price)}
              {closed ? `  ·  Venduto ${px(track.close_price)}` : `  ·  Attuale ${px(track.current_price)}`}
            </Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Percorso del prezzo</Text>
            <PriceChart track={track} />
            <View style={styles.legend}>
              <Text style={[styles.legendItem, { color: LEVEL_COLORS.entry }]}>━ Pagato {px(lv.entry)}</Text>
              <Text style={[styles.legendItem, { color: LEVEL_COLORS.break_even }]}>━ Pareggio costi {px(lv.break_even)}</Text>
              <Text style={[styles.legendItem, { color: LEVEL_COLORS.exit_level }]}>━ Uscita minima {px(lv.exit_level)}</Text>
              {lv.trailing_stop ? (
                <Text style={[styles.legendItem, { color: LEVEL_COLORS.trailing_stop }]}>━ Trailing {px(lv.trailing_stop)}</Text>
              ) : null}
              {!closed && track.current_price ? (
                <Text style={[styles.legendItem, { color: LEVEL_COLORS.now }]}>━ Adesso {px(track.current_price)}</Text>
              ) : null}
            </View>
          </View>

          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Numeri dell&apos;operazione</Text>
            <Row label="Punto più basso" value={`${px(st.min_price)}  (${pct(st.max_drawdown_pct)})`} sub={fmtWhen(st.min_at)} />
            <Row label="Punto più alto" value={`${px(st.max_price)}  (${pct(st.max_gain_pct)})`} sub={fmtWhen(st.max_at)} />
            <Row label="Tempo sotto il prezzo pagato" value={fmtDur(st.time_below_entry_s)} />
            <Row label={closed ? "Durata" : "Aperta da"} value={fmtDur(st.age_s)} sub={fmtWhen(track.opened_at)} />
            <Row label="Capitale investito" value={`$${(track.notional ?? 0).toFixed(2)}`} />
            <Row label="RSI all'ingresso" value={track.rsi_at_entry !== undefined ? track.rsi_at_entry.toFixed(1) : "-"} />
            <Row label="Punti registrati" value={String(st.samples)} />
          </View>

          {!closed ? (
            <View style={styles.card}>
              <Text style={styles.sectionTitle}>Cosa deve succedere per chiudere</Text>
              <Text style={styles.line}>
                1) RSI ≥ {lv.rsi_target} e prezzo di vendita ≥ {px(lv.exit_level)}
                {missing !== null ? (missing > 0 ? `  (mancano ${missing.toFixed(2)}%)` : "  (livello raggiunto)") : ""}
              </Text>
              <Text style={styles.line}>
                2) {lv.trailing_stop
                  ? `Trailing attivo: vende se il prezzo scende a ${px(lv.trailing_stop)} (mai sotto il pareggio ${px(lv.break_even)})`
                  : `Trailing: si attiva quando il prezzo supera ${px(lv.exit_level)}`}
              </Text>
            </View>
          ) : null}
        </ScrollView>
      ) : null}
    </View>
  );
}

function Row({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <View style={styles.statRow}>
      <Text style={styles.statLabel}>{label}</Text>
      <View style={{ alignItems: "flex-end" }}>
        <Text style={styles.statValue}>{value}</Text>
        {sub && sub !== "-" ? <Text style={styles.statSub}>{sub}</Text> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
  },
  title: { color: colors.onSurface, fontSize: font.xl, fontWeight: "700" },
  subtitle: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: 2 },
  errorText: { color: colors.error, textAlign: "center", marginTop: 40, paddingHorizontal: spacing.lg },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    marginBottom: spacing.md,
  },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  badge: { borderRadius: radius.pill, paddingHorizontal: spacing.sm, paddingVertical: 2 },
  badgeText: { color: colors.onSurface, fontSize: font.sm, fontWeight: "800" },
  big: { fontSize: 36, fontWeight: "800", marginTop: spacing.sm },
  muted: { color: colors.onSurfaceSecondary, fontSize: font.sm },
  line: { color: colors.onSurface, fontSize: font.base, marginTop: spacing.xs },
  sectionTitle: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700", marginBottom: spacing.sm },
  chart: {
    height: CHART_H,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    overflow: "hidden",
  },
  axisRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 4 },
  axisText: { color: colors.onSurfaceTertiary, fontSize: 11 },
  legend: { marginTop: spacing.sm, gap: 2 },
  legendItem: { fontSize: font.sm, fontWeight: "600" },
  statRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  statLabel: { color: colors.onSurfaceSecondary, fontSize: font.sm, flex: 1 },
  statValue: { color: colors.onSurface, fontSize: font.base, fontWeight: "700" },
  statSub: { color: colors.onSurfaceTertiary, fontSize: 11, marginTop: 1 },
});
