import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
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
import { gridWalletApi, GridPosition } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

function timeAgo(iso?: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "adesso";
  if (m < 60) return `${m}m fa`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h fa`;
  return `${Math.floor(h / 24)}g fa`;
}

function money(n?: number): string {
  if (n === undefined || n === null || isNaN(n)) return "$0.00";
  return `$${n.toFixed(2)}`;
}

export default function GridHistoryScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [closed, setClosed] = useState<GridPosition[]>([]);
  const [winRate, setWinRate] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const p = await gridWalletApi.portfolio();
      setClosed(p.closed_positions);
      setWinRate(p.win_rate);
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Errore di caricamento");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const renderPosition = (p: GridPosition) => {
    const pnl = p.pnl_usdt ?? 0;
    const pnlColor = pnl >= 0 ? colors.success : colors.error;
    return (
      <View key={p.id} style={styles.posCard}>
        <View style={styles.posTop}>
          <Text style={styles.posSymbol}>{p.symbol}</Text>
          <View style={styles.cellBadge}>
            <Text style={styles.cellBadgeText}>Cella #{p.cell_index}</Text>
          </View>
        </View>
        <Text style={styles.posMeta}>
          Entrata {p.entry.toFixed(6)}
          {p.close_price ? `  ·  Chiusa a ${p.close_price.toFixed(6)}` : ""}
        </Text>
        <Text style={styles.posMeta}>
          Target {p.target.toFixed(6)}
          {p.close_reason ? `  ·  Motivo: ${p.close_reason}` : ""}
        </Text>
        <View style={styles.posFooter}>
          <Text style={[styles.posPnl, { color: pnlColor }]}>
            {pnl >= 0 ? "+" : ""}
            {money(pnl)}
          </Text>
          <Text style={styles.posTime}>{timeAgo(p.closed_at)}</Text>
        </View>
      </View>
    );
  };

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>Storico Grid Bot</Text>
        <View style={{ width: 26 }} />
      </View>
      <Text style={styles.subtitle}>
        {closed.length} operazioni chiuse · Win rate {winRate}%
      </Text>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.brand} />
      ) : error ? (
        <Text style={styles.errorText}>{error}</Text>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brand} />
          }
        >
          {closed.length === 0 ? (
            <Text style={styles.emptyText}>Nessuna operazione chiusa ancora.</Text>
          ) : (
            closed.map(renderPosition)
          )}
        </ScrollView>
      )}
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
  },
  title: { color: colors.onSurface, fontSize: font.xl, fontWeight: "700" },
  subtitle: {
    color: colors.onSurfaceSecondary,
    fontSize: font.sm,
    textAlign: "center",
    marginTop: 4,
    marginBottom: spacing.sm,
  },
  errorText: {
    color: colors.error,
    textAlign: "center",
    marginTop: 40,
    paddingHorizontal: spacing.lg,
  },
  emptyText: {
    color: colors.onSurfaceSecondary,
    textAlign: "center",
    marginVertical: spacing.sm,
  },
  posCard: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    marginBottom: spacing.sm,
  },
  posTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.xs,
  },
  posSymbol: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  cellBadge: {
    borderRadius: radius.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    backgroundColor: colors.surfaceTertiary,
  },
  cellBadgeText: { color: colors.onSurfaceSecondary, fontSize: font.sm, fontWeight: "700" },
  posMeta: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginBottom: 2 },
  posFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: spacing.xs,
  },
  posPnl: { fontSize: font.base, fontWeight: "700" },
  posTime: { color: colors.onSurfaceSecondary, fontSize: font.sm },
});
