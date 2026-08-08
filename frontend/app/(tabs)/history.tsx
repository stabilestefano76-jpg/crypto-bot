import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, Signal } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

export default function HistoryScreen() {
  const insets = useSafeAreaInsets();
  const [items, setItems] = useState<Signal[]>([]);
  const [stats, setStats] = useState<{
    total: number;
    active: number;
    wins: number;
    losses: number;
    win_rate: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, st] = await Promise.all([
        api.signals({ status: "all" }),
        api.historyStats(),
      ]);
      setItems(s.signals);
      setStats(st);
    } catch {
      // silent
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Text style={styles.title} testID="history-title">
          Signal History
        </Text>
        {stats && (
          <View style={styles.statsRow} testID="history-stats">
            <StatBox label="Total" value={String(stats.total)} color={colors.onSurface} />
            <StatBox label="Active" value={String(stats.active)} color={colors.brand} />
            <StatBox label="Wins" value={String(stats.wins)} color={colors.success} />
            <StatBox label="Losses" value={String(stats.losses)} color={colors.error} />
            <StatBox
              label="Win Rate"
              value={`${stats.win_rate}%`}
              color={colors.brand}
            />
          </View>
        )}
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.brand} />
        </View>
      ) : items.length === 0 ? (
        <View style={styles.center} testID="history-empty">
          <Ionicons
            name="archive-outline"
            size={56}
            color={colors.onSurfaceTertiary}
          />
          <Text style={styles.emptyTitle}>No past signals yet</Text>
          <Text style={styles.muted}>
            Signals will appear here after the first scan finds confluence setups.
          </Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(i) => i.id}
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
          renderItem={({ item }) => <HistoryRow s={item} />}
        />
      )}
    </View>
  );
}

function StatBox({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <View style={styles.statBox}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, { color }]}>{value}</Text>
    </View>
  );
}

function HistoryRow({ s }: { s: Signal }) {
  const outcomeColor =
    s.outcome === "win"
      ? colors.success
      : s.outcome === "loss"
      ? colors.error
      : colors.onSurfaceTertiary;
  const outcomeLabel = s.outcome ? s.outcome.toUpperCase() : s.status.toUpperCase();
  const dateStr = new Date(s.created_at).toLocaleDateString();
  return (
    <View style={styles.row} testID={`history-row-${s.id}`}>
      <View style={{ flex: 1 }}>
        <View style={styles.rowTop}>
          <Text style={styles.rowPair}>{s.symbol}</Text>
          <Text style={styles.rowTf}>{s.timeframe}</Text>
        </View>
        <Text style={styles.rowDate}>{dateStr}</Text>
      </View>
      <View
        style={[
          styles.sideBadge,
          {
            backgroundColor:
              s.side === "long" ? colors.success : colors.error,
          },
        ]}
      >
        <Text style={styles.sideText}>{s.side === "long" ? "LONG" : "SHORT"}</Text>
      </View>
      <View style={[styles.outcomeBadge, { borderColor: outcomeColor }]}>
        <Text style={[styles.outcomeText, { color: outcomeColor }]}>
          {outcomeLabel}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    padding: spacing.lg,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    gap: spacing.md,
  },
  title: { fontSize: 22, fontWeight: "800", color: colors.onSurface },
  statsRow: { flexDirection: "row", gap: spacing.sm, flexWrap: "wrap" },
  statBox: {
    flex: 1,
    minWidth: 64,
    padding: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
  },
  statLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 0.4,
  },
  statValue: { fontSize: font.lg, fontWeight: "800", marginTop: 2 },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
    gap: spacing.sm,
  },
  emptyTitle: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  muted: { color: colors.onSurfaceSecondary, textAlign: "center" },
  listPad: { padding: spacing.lg, paddingBottom: spacing.xxl },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  rowTop: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  rowPair: { color: colors.onSurface, fontWeight: "800", fontSize: font.base },
  rowTf: {
    color: colors.onSurfaceSecondary,
    fontSize: 11,
    fontWeight: "600",
    backgroundColor: colors.surfaceTertiary,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
  },
  rowDate: { color: colors.onSurfaceTertiary, fontSize: 11, marginTop: 2 },
  sideBadge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.sm,
  },
  sideText: { color: "#fff", fontWeight: "800", fontSize: 11 },
  outcomeBadge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.sm,
    borderWidth: 1,
  },
  outcomeText: { fontWeight: "800", fontSize: 11 },
});
