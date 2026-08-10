import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, ScanState, Signal } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const FILTERS = ["All", "Long", "Short"] as const;
type Filter = (typeof FILTERS)[number];

const TIMEFRAMES = ["All", "15m", "1h", "4h", "1d"] as const;

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function SignalsScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [status, setStatus] = useState<ScanState | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("All");
  const [tf, setTf] = useState<(typeof TIMEFRAMES)[number]>("All");
  const [scanning, setScanning] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, st] = await Promise.all([
        api.signals({ status: "active" }),
        api.status(),
      ]);
      setSignals(s.signals);
      setStatus(st);
    } catch (e: any) {
      setError(e.message || "Network error");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 30_000);
    return () => clearInterval(iv);
  }, [load]);

  const filtered = useMemo(() => {
    let out = signals;
    if (filter === "Long") out = out.filter((s) => s.side === "long");
    else if (filter === "Short") out = out.filter((s) => s.side === "short");
    if (tf !== "All") out = out.filter((s) => s.timeframe === tf);
    return [...out].sort((a, b) => b.strength - a.strength);
  }, [signals, filter, tf]);

  const onScan = async () => {
    setScanning(true);
    try {
      await api.triggerScan();
      setTimeout(load, 4000);
    } catch (e) {
      // ignore
    } finally {
      setTimeout(() => setScanning(false), 2000);
    }
  };

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      {/* Sticky header */}
      <View style={styles.header}>
        <View style={styles.headerRow}>
          <View>
            <Text style={styles.title} testID="app-title">
              KuSignal Bot
            </Text>
            <View style={styles.statusRow}>
              <View
                style={[
                  styles.dot,
                  {
                    backgroundColor: status?.is_scanning
                      ? colors.brand
                      : colors.success,
                  },
                ]}
              />
              <Text style={styles.statusText} testID="scan-status">
                {status?.is_scanning
                  ? "Scanning..."
                  : status?.last_scan_at
                  ? `Last scan ${timeAgo(status.last_scan_at)} · ${
                      status.last_signals_found
                    } signals`
                  : "Awaiting first scan"}
              </Text>
            </View>
          </View>
          <Pressable
            onPress={() => router.push("/academy")}
            style={({ pressed }) => [
              styles.scanBtn,
              { backgroundColor: colors.surfaceTertiary, marginRight: 8 },
              pressed && { opacity: 0.6 },
            ]}
            testID="academy-button"
          >
            <Ionicons name="school" size={16} color={colors.brand} />
          </Pressable>
          <Pressable
            onPress={onScan}
            disabled={scanning}
            style={({ pressed }) => [
              styles.scanBtn,
              pressed && { opacity: 0.6 },
            ]}
            testID="scan-now-button"
          >
            {scanning ? (
              <ActivityIndicator color={colors.onBrand} size="small" />
            ) : (
              <Ionicons name="scan" size={16} color={colors.onBrand} />
            )}
            <Text style={styles.scanBtnText}>Scan</Text>
          </Pressable>
        </View>

        {/* Side filter segmented */}
        <View style={styles.segmented} testID="side-filter">
          {FILTERS.map((f) => {
            const active = filter === f;
            return (
              <Pressable
                key={f}
                onPress={() => setFilter(f)}
                style={[styles.segItem, active && styles.segItemActive]}
                testID={`filter-${f.toLowerCase()}`}
              >
                <Text
                  style={[styles.segText, active && styles.segTextActive]}
                >
                  {f}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {/* Timeframe chips */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipsRow}
          style={styles.chipsScroll}
          testID="timeframe-chips"
        >
          {TIMEFRAMES.map((t) => {
            const active = tf === t;
            return (
              <Pressable
                key={t}
                onPress={() => setTf(t)}
                style={[styles.chip, active && styles.chipActive]}
                testID={`chip-tf-${t}`}
              >
                <Text style={[styles.chipText, active && styles.chipTextActive]}>
                  {t}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      {loading ? (
        <View style={styles.center} testID="loading">
          <ActivityIndicator color={colors.brand} />
          <Text style={styles.muted}>Scanning markets...</Text>
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Ionicons name="cloud-offline" size={48} color={colors.error} />
          <Text style={styles.muted}>{error}</Text>
          <Pressable onPress={load} style={styles.retryBtn} testID="retry-button">
            <Text style={styles.retryText}>Retry</Text>
          </Pressable>
        </View>
      ) : filtered.length === 0 ? (
        <View style={styles.center} testID="empty-state">
          <Ionicons name="scan-circle-outline" size={64} color={colors.onSurfaceTertiary} />
          <Text style={styles.emptyTitle}>No active signals</Text>
          <Text style={styles.muted}>
            Waiting for criteria match. Tap Scan to trigger a new run.
          </Text>
        </View>
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.listPad}
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
          renderItem={({ item }) => (
            <SignalCard
              signal={item}
              onPress={() =>
                router.push({
                  pathname: "/detail/[id]",
                  params: { id: item.id },
                })
              }
              onExecute={async () => {
                try {
                  await api.paperExecute(item.id);
                  return "ok";
                } catch (e: any) {
                  return e.message || "Failed";
                }
              }}
            />
          )}
        />
      )}
    </View>
  );
}

function SignalCard({
  signal,
  onPress,
  onExecute,
}: {
  signal: Signal;
  onPress: () => void;
  onExecute: () => Promise<string>;
}) {
  const isLong = signal.side === "long";
  const [executing, setExecuting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const doExecute = async () => {
    if (executing) return;
    setExecuting(true);
    setMsg(null);
    const res = await onExecute();
    setMsg(res === "ok" ? "Opened in paper" : res);
    setTimeout(() => setMsg(null), 3000);
    setExecuting(false);
  };
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.7 }]}
      testID={`signal-card-${signal.id}`}
    >
      <View style={styles.cardTop}>
        <View style={styles.cardLeft}>
          <Text style={styles.pair}>{signal.symbol}</Text>
          <View style={styles.tfTag}>
            <Text style={styles.tfTagText}>{signal.timeframe}</Text>
          </View>
        </View>
        <View
          style={[
            styles.sideBadge,
            { backgroundColor: isLong ? colors.success : colors.error },
          ]}
        >
          <Ionicons
            name={isLong ? "arrow-up" : "arrow-down"}
            size={12}
            color="#fff"
          />
          <Text style={styles.sideText}>{isLong ? "LONG" : "SHORT"}</Text>
        </View>
      </View>

      <View style={styles.priceRow}>
        <PriceCol label="Entry" value={signal.entry} color={colors.brand} />
        <PriceCol label="Stop" value={signal.stop_loss} color={colors.error} />
        <PriceCol label="Target" value={signal.take_profit} color={colors.success} />
        <View style={styles.rrCol}>
          <Text style={styles.rrLabel}>R:R</Text>
          <Text style={styles.rrValue}>1:{signal.rr_ratio}</Text>
        </View>
      </View>

      <View style={styles.confirmations}>
        {signal.confirmations.map((c) => (
          <View key={c} style={styles.confChip}>
            <Ionicons name="checkmark" size={10} color={colors.brand} />
            <Text style={styles.confText}>{c}</Text>
          </View>
        ))}
        <View style={styles.strengthBox}>
          <Text style={styles.strengthText}>×{signal.strength}</Text>
        </View>
      </View>

      <View style={styles.cardBottom}>
        <Text style={styles.timeAgo}>{timeAgo(signal.created_at)}</Text>
        <Pressable
          onPress={doExecute}
          disabled={executing}
          hitSlop={8}
          style={({ pressed }) => [
            styles.execBtn,
            { backgroundColor: isLong ? colors.success : colors.error },
            (pressed || executing) && { opacity: 0.7 },
          ]}
          testID={`execute-${signal.id}`}
        >
          {executing ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <>
              <Ionicons name="flash" size={12} color="#fff" />
              <Text style={styles.execBtnText}>Execute</Text>
            </>
          )}
        </Pressable>
      </View>
      {msg && (
        <Text
          style={[
            styles.execMsg,
            {
              color: msg === "Opened in paper" ? colors.success : colors.error,
            },
          ]}
        >
          {msg}
        </Text>
      )}
    </Pressable>
  );
}

function PriceCol({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <View style={styles.priceCol}>
      <Text style={styles.priceLabel}>{label}</Text>
      <Text style={[styles.priceValue, { color }]} numberOfLines={1}>
        {formatPrice(value)}
      </Text>
    </View>
  );
}

function formatPrice(v: number): string {
  if (v >= 1) return v.toFixed(4);
  if (v >= 0.01) return v.toFixed(5);
  return v.toPrecision(4);
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.sm,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
  },
  title: { fontSize: 22, fontWeight: "800", color: colors.onSurface, letterSpacing: 0.5 },
  statusRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: spacing.xs,
    gap: 6,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  statusText: { color: colors.onSurfaceSecondary, fontSize: font.sm },
  scanBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.brand,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
  },
  scanBtnText: { color: colors.onBrand, fontWeight: "700", fontSize: font.sm },
  segmented: {
    flexDirection: "row",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    padding: 3,
    marginBottom: spacing.sm,
  },
  segItem: {
    flex: 1,
    paddingVertical: 8,
    alignItems: "center",
    borderRadius: radius.sm,
  },
  segItemActive: { backgroundColor: colors.brand },
  segText: { color: colors.onSurfaceSecondary, fontWeight: "600", fontSize: font.sm },
  segTextActive: { color: colors.onBrand },
  chipsScroll: { maxHeight: 40 },
  chipsRow: { gap: spacing.sm, paddingHorizontal: 2 },
  chip: {
    height: 32,
    minWidth: 44,
    paddingHorizontal: spacing.md,
    borderRadius: radius.pill,
    backgroundColor: colors.surfaceSecondary,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  },
  chipActive: { borderColor: colors.brand, backgroundColor: colors.brandTertiary },
  chipText: { color: colors.onSurfaceSecondary, fontWeight: "600", fontSize: font.sm },
  chipTextActive: { color: colors.brand },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
    gap: spacing.md,
  },
  muted: { color: colors.onSurfaceSecondary, textAlign: "center" },
  emptyTitle: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  retryBtn: {
    marginTop: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    backgroundColor: colors.brand,
    borderRadius: radius.md,
  },
  retryText: { color: colors.onBrand, fontWeight: "700" },
  listPad: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.md,
    gap: spacing.sm,
  },
  cardTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  cardLeft: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  pair: { color: colors.onSurface, fontSize: font.lg, fontWeight: "800" },
  tfTag: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceTertiary,
  },
  tfTagText: { color: colors.onSurfaceSecondary, fontSize: 11, fontWeight: "600" },
  sideBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.sm,
  },
  sideText: { color: "#fff", fontWeight: "800", fontSize: 11, letterSpacing: 0.5 },
  priceRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 4,
  },
  priceCol: { flex: 1 },
  priceLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  priceValue: { fontSize: font.base, fontWeight: "700", marginTop: 2 },
  rrCol: { alignItems: "flex-end" },
  rrLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    textTransform: "uppercase",
  },
  rrValue: {
    color: colors.brand,
    fontWeight: "800",
    fontSize: font.base,
    marginTop: 2,
  },
  confirmations: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    alignItems: "center",
  },
  confChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
    paddingHorizontal: 6,
    paddingVertical: 3,
    backgroundColor: colors.brandTertiary,
    borderRadius: radius.sm,
  },
  confText: { color: colors.brand, fontSize: 10, fontWeight: "700" },
  strengthBox: {
    marginLeft: "auto",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceTertiary,
  },
  strengthText: { color: colors.brand, fontWeight: "800", fontSize: 11 },
  cardBottom: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 4,
  },
  timeAgo: { color: colors.onSurfaceTertiary, fontSize: 11 },
  execBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.sm,
  },
  execBtnText: {
    color: "#fff",
    fontWeight: "800",
    fontSize: font.sm,
    letterSpacing: 0.4,
  },
  execMsg: {
    fontSize: 11,
    fontWeight: "700",
    marginTop: 4,
    textAlign: "right",
  },
});
