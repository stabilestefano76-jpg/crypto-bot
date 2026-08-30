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
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { strategyApi, PaperTrade, StrategyName } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const STRATEGY_TITLES: Record<string, string> = {
  counter_trend: "Rev Pre-FVG",
  fvg_reversal: "FVG Reversal",
  rsi_reversion: "RSI Reversion",
};

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

export default function StrategyHistoryScreen() {
  const { name } = useLocalSearchParams<{ name: string }>();
  const strategy = (name as StrategyName) || "counter_trend";
  const title = STRATEGY_TITLES[strategy] || strategy;

  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [winRate, setWinRate] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const p = await strategyApi.portfolio(strategy);
      setTrades(p.closed_trades);
      setWinRate(p.win_rate);
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Errore di caricamento");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [strategy]);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const renderTrade = (t: PaperTrade) => {
    const pnlColor = t.pnl_usdt >= 0 ? colors.success : colors.error;
    return (
      <View key={t.id} style={styles.posCard}>
        <View style={styles.posTop}>
          <Text style={styles.posSymbol}>{t.symbol}</Text>
          <View
            style={[
              styles.sideBadge,
              { backgroundColor: t.side === "long" ? colors.success : colors.error },
            ]}
          >
            <Text style={styles.sideBadgeText}>{t.side === "long" ? "LONG" : "SHORT"}</Text>
          </View>
        </View>
        <Text style={styles.posMeta}>
          Entrata {t.entry.toFixed(6)}  ·  Uscita {t.exit.toFixed(6)}
        </Text>
        <Text style={styles.posMeta}>Esito: {t.outcome === "win" ? "Vinta" : "Persa"}</Text>
        <View style={styles.posFooter}>
          <Text style={[styles.posPnl, { color: pnlColor }]}>
            {t.pnl_usdt >= 0 ? "+" : ""}
            {money(t.pnl_usdt)}
          </Text>
          <Text style={styles.posTime}>{timeAgo(t.closed_at)}</Text>
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
        <Text style={styles.title}>Storico {title}</Text>
        <View style={{ width: 26 }} />
      </View>
      <Text style={styles.subtitle}>
        {trades.length} operazioni chiuse · Win rate {winRate}%
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
          {trades.length === 0 ? (
            <Text style={styles.emptyText}>Nessuna operazione chiusa ancora.</Text>
          ) : (
            trades.map(renderTrade)
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
  errorText: { color: colors.error, textAlign: "center", marginTop: 40, paddingHorizontal: spacing.lg },
  emptyText: { color: colors.onSurfaceSecondary, textAlign: "center", marginVertical: spacing.sm },
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
  sideBadge: { borderRadius: radius.pill, paddingHorizontal: spacing.sm, paddingVertical: 2 },
  sideBadgeText: { color: colors.onSurface, fontSize: font.sm, fontWeight: "700" },
  posMeta: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginBottom: 2 },
  posFooter: { flexDirection: "row", justifyContent: "space-between", marginTop: spacing.xs },
  posPnl: { fontSize: font.base, fontWeight: "700" },
  posTime: { color: colors.onSurfaceSecondary, fontSize: font.sm },
});
