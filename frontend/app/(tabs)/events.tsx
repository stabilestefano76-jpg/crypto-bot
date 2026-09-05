import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { eventsApi, BotEvent } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const SECTION_LABELS: Record<string, string> = {
  counter_trend: "Rev Pre-FVG",
  fvg_reversal: "FVG Reversal",
  rsi_reversion: "RSI Reversion",
  scalping: "Scalping",
  grid: "Grid",
};

const SECTION_COLORS: Record<string, string> = {
  counter_trend: "#3B82F6",
  fvg_reversal: "#A855F7",
  rsi_reversion: "#F59E0B",
  scalping: "#10B981",
  grid: "#EC4899",
};

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "adesso";
  if (m < 60) return `${m}m fa`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h fa`;
  return `${Math.floor(h / 24)}g fa`;
}

function money(n?: number | null): string {
  if (n === undefined || n === null || isNaN(n)) return "";
  const sign = n >= 0 ? "+" : "";
  return `${sign}$${n.toFixed(2)}`;
}

/**
 * Elenco cronologico consultabile di tutte e cinque le sezioni. Il suono e
 * il popup per i nuovi eventi sono gestiti a livello globale (vedi
 * src/components/EventAlertOverlay.tsx, montato nel layout radice), non qui
 * — così restano visibili/attivi anche quando non sei su questa schermata.
 */
export default function EventsScreen() {
  const insets = useSafeAreaInsets();
  const [events, setEvents] = useState<BotEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await eventsApi.list(150);
      setEvents(res.events);
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
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Text style={styles.title}>Eventi</Text>
        <Text style={styles.subtitle}>
          Aperture e chiusure di tutte le sezioni, in ordine cronologico
        </Text>
      </View>

      {loading && events.length === 0 ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.brand} />
      ) : error && events.length === 0 ? (
        <Text style={styles.errorText}>{error}</Text>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brand} />
          }
        >
          {events.length === 0 ? (
            <View style={styles.empty}>
              <Ionicons name="notifications-off-outline" size={40} color={colors.onSurfaceTertiary} />
              <Text style={styles.emptyText}>Nessun evento ancora.</Text>
            </View>
          ) : (
            events.map((e) => {
              const isClose = e.type === "close";
              const pnl = e.pnl_usdt ?? null;
              const pnlColor = pnl !== null ? (pnl >= 0 ? colors.success : colors.error) : colors.onSurfaceSecondary;
              const sectionColor = SECTION_COLORS[e.section] || colors.brand;
              return (
                <View key={e.id} style={styles.card}>
                  <View style={[styles.iconWrap, { backgroundColor: sectionColor + "22" }]}>
                    <Ionicons
                      name={isClose ? (pnl !== null && pnl >= 0 ? "checkmark-circle" : "close-circle") : "arrow-forward-circle"}
                      size={20}
                      color={isClose ? pnlColor : sectionColor}
                    />
                  </View>
                  <View style={{ flex: 1 }}>
                    <View style={styles.cardTopRow}>
                      <Text style={styles.symbol}>{e.symbol}</Text>
                      <View style={[styles.sectionTag, { borderColor: sectionColor }]}>
                        <Text style={[styles.sectionTagText, { color: sectionColor }]}>
                          {SECTION_LABELS[e.section] || e.section}
                        </Text>
                      </View>
                    </View>
                    <Text style={styles.metaText}>
                      {isClose ? "Chiusura" : "Apertura"}
                      {e.side ? ` · ${e.side === "long" ? "LONG" : "SHORT"}` : ""}
                    </Text>
                  </View>
                  <View style={{ alignItems: "flex-end" }}>
                    {isClose && pnl !== null && (
                      <Text style={[styles.pnl, { color: pnlColor }]}>{money(pnl)}</Text>
                    )}
                    <Text style={styles.time}>{timeAgo(e.at)}</Text>
                  </View>
                </View>
              );
            })
          )}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
  },
  title: { color: colors.onSurface, fontSize: font.xl, fontWeight: "700" },
  subtitle: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: 2 },
  errorText: { color: colors.error, textAlign: "center", marginTop: 40, paddingHorizontal: spacing.lg },
  empty: { alignItems: "center", gap: spacing.sm, marginTop: 60 },
  emptyText: { color: colors.onSurfaceSecondary },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  iconWrap: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
  },
  cardTopRow: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
  symbol: { color: colors.onSurface, fontSize: font.base, fontWeight: "700" },
  sectionTag: {
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  sectionTagText: { fontSize: 10, fontWeight: "700" },
  metaText: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: 2 },
  pnl: { fontSize: font.base, fontWeight: "800" },
  time: { color: colors.onSurfaceTertiary, fontSize: 11, marginTop: 2 },
});
