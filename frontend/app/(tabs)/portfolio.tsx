import { useCallback, useEffect, useState } from "react";
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
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, PaperPosition, PaperTrade, Portfolio } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

type Tab = "positions" | "trades";

export default function PortfolioScreen() {
  const insets = useSafeAreaInsets();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState<Tab>("positions");
  const [resetting, setResetting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([api.portfolio(), api.paperTrades()]);
      setPortfolio(p);
      setTrades(t.trades);
    } catch {
      // silent
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 15_000);
    return () => clearInterval(iv);
  }, [load]);

  const onReset = async () => {
    setResetting(true);
    try {
      await api.paperReset();
      await load();
    } finally {
      setResetting(false);
    }
  };

  const onCloseManual = async (id: string) => {
    try {
      await api.paperClose(id);
      await load();
    } catch {
      // ignore
    }
  };

  if (loading || !portfolio) {
    return (
      <View style={[styles.root, styles.center, { paddingTop: insets.top }]}>
        <ActivityIndicator color={colors.brand} />
      </View>
    );
  }

  const equity = portfolio.equity;
  const totalReturn = portfolio.total_return_pct;
  const positive = totalReturn >= 0;

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={styles.body}
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
        {/* Header + big equity */}
        <View style={styles.headerCard} testID="portfolio-header">
          <View style={styles.headerTopRow}>
            <Text style={styles.headerLabel}>Paper Portfolio</Text>
            <View
              style={[
                styles.autoBadge,
                {
                  backgroundColor: portfolio.auto_execute
                    ? colors.success
                    : colors.surfaceTertiary,
                },
              ]}
            >
              <Ionicons
                name={portfolio.auto_execute ? "flash" : "flash-off"}
                size={11}
                color={portfolio.auto_execute ? "#fff" : colors.onSurfaceSecondary}
              />
              <Text
                style={[
                  styles.autoBadgeText,
                  {
                    color: portfolio.auto_execute
                      ? "#fff"
                      : colors.onSurfaceSecondary,
                  },
                ]}
              >
                {portfolio.auto_execute ? "AUTO ON" : "AUTO OFF"}
              </Text>
            </View>
          </View>
          <Text style={styles.equity} testID="portfolio-equity">
            ${equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </Text>
          <Text
            style={[
              styles.returnText,
              { color: positive ? colors.success : colors.error },
            ]}
          >
            {positive ? "+" : ""}
            {totalReturn}% total return
          </Text>
          <View style={styles.summaryRow}>
            <Summary label="Cash" value={`$${portfolio.cash.toFixed(2)}`} />
            <Summary
              label="Unrealized"
              value={fmtSigned(portfolio.unrealized_pnl)}
              color={
                portfolio.unrealized_pnl >= 0 ? colors.success : colors.error
              }
            />
            <Summary
              label="Realized"
              value={fmtSigned(portfolio.realized_pnl)}
              color={
                portfolio.realized_pnl >= 0 ? colors.success : colors.error
              }
            />
          </View>
          <View style={styles.summaryRow}>
            <Summary
              label="Open"
              value={String(portfolio.open_positions_count)}
            />
            <Summary
              label="Closed"
              value={String(portfolio.closed_trades_count)}
            />
            <Summary
              label="Win rate"
              value={`${portfolio.win_rate}%`}
              color={colors.brand}
            />
          </View>
          <Pressable
            onPress={onReset}
            disabled={resetting}
            style={({ pressed }) => [
              styles.resetBtn,
              pressed && { opacity: 0.6 },
            ]}
            testID="reset-portfolio-button"
          >
            <Ionicons
              name="refresh"
              size={14}
              color={colors.onSurfaceSecondary}
            />
            <Text style={styles.resetText}>
              {resetting ? "Resetting..." : "Reset Portfolio"}
            </Text>
          </Pressable>
        </View>

        {/* Segmented tab */}
        <View style={styles.segmented}>
          {(["positions", "trades"] as Tab[]).map((t) => {
            const active = tab === t;
            return (
              <Pressable
                key={t}
                onPress={() => setTab(t)}
                style={[styles.segItem, active && styles.segItemActive]}
                testID={`portfolio-tab-${t}`}
              >
                <Text
                  style={[styles.segText, active && styles.segTextActive]}
                >
                  {t === "positions"
                    ? `Open (${portfolio.open_positions_count})`
                    : `Closed (${portfolio.closed_trades_count})`}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {tab === "positions" ? (
          portfolio.positions.length === 0 ? (
            <View style={styles.empty} testID="empty-positions">
              <Ionicons name="wallet-outline" size={48} color={colors.onSurfaceTertiary} />
              <Text style={styles.emptyTitle}>No open positions</Text>
              <Text style={styles.muted}>
                Execute a signal manually from its detail screen, or enable
                auto-execute in Settings.
              </Text>
            </View>
          ) : (
            portfolio.positions.map((p) => (
              <PositionCard key={p.id} pos={p} onClose={onCloseManual} />
            ))
          )
        ) : trades.length === 0 ? (
          <View style={styles.empty} testID="empty-trades">
            <Ionicons name="albums-outline" size={48} color={colors.onSurfaceTertiary} />
            <Text style={styles.emptyTitle}>No closed trades yet</Text>
          </View>
        ) : (
          trades.map((t) => <TradeRow key={t.id} t={t} />)
        )}
      </ScrollView>
    </View>
  );
}

function Summary({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View style={styles.summaryItem}>
      <Text style={styles.summaryLabel}>{label}</Text>
      <Text style={[styles.summaryValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

function PositionCard({
  pos,
  onClose,
}: {
  pos: PaperPosition;
  onClose: (id: string) => void;
}) {
  const isLong = pos.side === "long";
  const pnlColor = pos.unrealized_pnl >= 0 ? colors.success : colors.error;
  return (
    <View style={styles.card} testID={`position-${pos.id}`}>
      <View style={styles.cardTop}>
        <View style={styles.cardLeft}>
          <Text style={styles.pair}>{pos.symbol}</Text>
          <View style={styles.tfTag}>
            <Text style={styles.tfTagText}>{pos.timeframe}</Text>
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

      <View style={styles.pnlRow}>
        <Text style={[styles.pnlBig, { color: pnlColor }]} testID="position-pnl">
          {fmtSigned(pos.unrealized_pnl)}{" "}
          <Text style={styles.pnlPct}>
            ({pos.unrealized_pnl_pct >= 0 ? "+" : ""}
            {pos.unrealized_pnl_pct}%)
          </Text>
        </Text>
      </View>

      <View style={styles.priceRow}>
        <Col label="Entry" value={fmtPrice(pos.entry)} />
        <Col label="Now" value={fmtPrice(pos.current_price)} color={colors.brand} />
        <Col label="SL" value={fmtPrice(pos.stop_loss)} color={colors.error} />
        <Col label="TP" value={fmtPrice(pos.take_profit)} color={colors.success} />
      </View>

      <View style={styles.metaRow}>
        <Text style={styles.metaText}>
          Qty {pos.quantity.toFixed(4)} · Risk ${pos.risk_usdt}
        </Text>
        <Pressable
          onPress={() => onClose(pos.id)}
          style={({ pressed }) => [
            styles.closeBtn,
            pressed && { opacity: 0.6 },
          ]}
          testID={`close-position-${pos.id}`}
        >
          <Text style={styles.closeBtnText}>Close</Text>
        </Pressable>
      </View>
    </View>
  );
}

function TradeRow({ t }: { t: PaperTrade }) {
  const positive = t.pnl_usdt >= 0;
  const color = positive ? colors.success : colors.error;
  return (
    <View style={styles.tradeRow} testID={`trade-${t.id}`}>
      <View style={{ flex: 1 }}>
        <View style={styles.rowTop}>
          <Text style={styles.tradePair}>{t.symbol}</Text>
          <View
            style={[
              styles.miniBadge,
              {
                backgroundColor:
                  t.side === "long" ? colors.success : colors.error,
              },
            ]}
          >
            <Text style={styles.sideText}>{t.side === "long" ? "L" : "S"}</Text>
          </View>
        </View>
        <Text style={styles.tradeMeta}>
          {fmtPrice(t.entry)} → {fmtPrice(t.exit)} · qty {t.quantity.toFixed(4)}
        </Text>
        <Text style={styles.tradeMeta}>
          {new Date(t.closed_at).toLocaleString()}
        </Text>
      </View>
      <View style={{ alignItems: "flex-end" }}>
        <Text style={[styles.tradePnl, { color }]}>{fmtSigned(t.pnl_usdt)}</Text>
        <Text style={[styles.tradePct, { color }]}>
          {t.pnl_pct >= 0 ? "+" : ""}
          {t.pnl_pct}%
        </Text>
        <View style={[styles.outcomeChip, { borderColor: color }]}>
          <Text style={[styles.outcomeText, { color }]}>
            {t.outcome.toUpperCase()}
          </Text>
        </View>
      </View>
    </View>
  );
}

function Col({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View style={{ flex: 1 }}>
      <Text style={styles.priceLabel}>{label}</Text>
      <Text
        style={[styles.priceValue, color ? { color } : null]}
        numberOfLines={1}
      >
        {value}
      </Text>
    </View>
  );
}

function fmtPrice(v: number): string {
  if (v >= 1) return v.toFixed(4);
  if (v >= 0.01) return v.toFixed(5);
  return v.toPrecision(4);
}

function fmtSigned(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toFixed(2)}`;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { alignItems: "center", justifyContent: "center" },
  body: { padding: spacing.lg, paddingBottom: spacing.xxl, gap: spacing.md },
  headerCard: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  headerTopRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  headerLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: font.sm,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    fontWeight: "700",
  },
  autoBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.pill,
  },
  autoBadgeText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  equity: { color: colors.onSurface, fontSize: 34, fontWeight: "800" },
  returnText: { fontSize: font.base, fontWeight: "700" },
  summaryRow: { flexDirection: "row", gap: spacing.sm, marginTop: 4 },
  summaryItem: {
    flex: 1,
    backgroundColor: colors.surfaceTertiary,
    padding: spacing.sm,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  summaryLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 0.4,
  },
  summaryValue: {
    color: colors.onSurface,
    fontWeight: "800",
    fontSize: font.base,
    marginTop: 2,
  },
  resetBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: spacing.sm,
    paddingVertical: spacing.sm,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  resetText: { color: colors.onSurfaceSecondary, fontWeight: "600", fontSize: font.sm },
  segmented: {
    flexDirection: "row",
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    padding: 3,
    borderWidth: 1,
    borderColor: colors.border,
  },
  segItem: {
    flex: 1,
    paddingVertical: 8,
    alignItems: "center",
    borderRadius: radius.sm,
  },
  segItemActive: { backgroundColor: colors.brand },
  segText: {
    color: colors.onSurfaceSecondary,
    fontWeight: "600",
    fontSize: font.sm,
  },
  segTextActive: { color: colors.onBrand },
  empty: {
    padding: spacing.xl,
    alignItems: "center",
    gap: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  emptyTitle: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  muted: { color: colors.onSurfaceSecondary, textAlign: "center" },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
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
  pnlRow: { marginTop: 2 },
  pnlBig: { fontSize: font.xl, fontWeight: "800" },
  pnlPct: { fontSize: font.base, fontWeight: "700" },
  priceRow: { flexDirection: "row", gap: spacing.sm },
  priceLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  priceValue: {
    color: colors.onSurface,
    fontSize: font.base,
    fontWeight: "700",
    marginTop: 2,
  },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 4,
  },
  metaText: { color: colors.onSurfaceTertiary, fontSize: 11 },
  closeBtn: {
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  closeBtnText: { color: colors.onSurface, fontWeight: "700", fontSize: font.sm },
  tradeRow: {
    flexDirection: "row",
    gap: spacing.md,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
  },
  rowTop: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  tradePair: { color: colors.onSurface, fontWeight: "800", fontSize: font.base },
  miniBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
  },
  tradeMeta: { color: colors.onSurfaceTertiary, fontSize: 11, marginTop: 2 },
  tradePnl: { fontWeight: "800", fontSize: font.lg },
  tradePct: { fontWeight: "700", fontSize: font.sm, marginTop: 2 },
  outcomeChip: {
    marginTop: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
    borderWidth: 1,
  },
  outcomeText: { fontWeight: "800", fontSize: 10, letterSpacing: 0.5 },
});
