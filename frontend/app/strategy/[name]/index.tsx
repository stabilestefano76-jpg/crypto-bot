import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import {
  api,
  strategyApi,
  strategyWalletApi,
  Candle,
  PaperPosition,
  StrategyName,
  StrategyPortfolio,
  StrategyWalletsStatus,
} from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";
import TradingViewChart from "@/src/components/TradingViewChart";
import { TvLevels } from "@/src/utils/tvChart";

const STRATEGY_META: Record<string, { title: string; subtitle: string }> = {
  counter_trend: { title: "Rev Pre-FVG", subtitle: "Rottura pre-FVG in contro-tendenza" },
  fvg_reversal: { title: "FVG Reversal", subtitle: "Ritracciamento verso la FVG del trend" },
  rsi_reversion: { title: "RSI Reversion", subtitle: "Rientro da ipercomprato / ipervenduto" },
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

export default function StrategyScreen() {
  const { name } = useLocalSearchParams<{ name: string }>();
  const strategy = (name as StrategyName) || "counter_trend";
  const meta = STRATEGY_META[strategy] || { title: strategy, subtitle: "" };

  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [portfolio, setPortfolio] = useState<StrategyPortfolio | null>(null);
  const [walletStatus, setWalletStatus] = useState<StrategyWalletsStatus | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [fvg, setFvg] = useState<{ top: number; bottom: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [amountText, setAmountText] = useState("");
  const [transferring, setTransferring] = useState(false);
  const [withdrawModalVisible, setWithdrawModalVisible] = useState(false);
  const [withdrawText, setWithdrawText] = useState("");
  const [withdrawing, setWithdrawing] = useState(false);

  const selected = useMemo(
    () => portfolio?.open_positions.find((p) => p.id === selectedId) ?? portfolio?.open_positions[0] ?? null,
    [portfolio, selectedId]
  );

  const walletInfo = useMemo(
    () => walletStatus?.strategies.find((s) => s.strategy === strategy) ?? null,
    [walletStatus, strategy]
  );

  const load = useCallback(async () => {
    try {
      const [p, w] = await Promise.all([
        strategyApi.portfolio(strategy),
        strategyWalletApi.status(),
      ]);
      setPortfolio(p);
      setWalletStatus(w);
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
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!selected) {
      setCandles([]);
      setFvg(null);
      return;
    }
    let cancelled = false;
    api.candles(selected.symbol, selected.timeframe).then((c) => {
      if (!cancelled) setCandles(c.candles);
    }).catch(() => {});
    api.signal(selected.signal_id).then((s) => {
      if (!cancelled) setFvg({ top: s.fvg_top, bottom: s.fvg_bottom });
    }).catch(() => {
      if (!cancelled) setFvg(null);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const onAllocate = async () => {
    const amount = parseFloat(amountText.replace(",", "."));
    if (!amount || amount <= 0) {
      Alert.alert("Importo non valido", "Inserisci un numero maggiore di zero.");
      return;
    }
    setTransferring(true);
    try {
      await strategyWalletApi.allocate(strategy, amount);
      setModalVisible(false);
      setAmountText("");
      await load();
    } catch (e: any) {
      Alert.alert("Allocazione fallita", e?.message || "Errore sconosciuto");
    } finally {
      setTransferring(false);
    }
  };

  const onWithdraw = async () => {
    const amount = parseFloat(withdrawText.replace(",", "."));
    if (!amount || amount <= 0) {
      Alert.alert("Importo non valido", "Inserisci un numero maggiore di zero.");
      return;
    }
    setWithdrawing(true);
    try {
      await strategyWalletApi.withdraw(strategy, amount);
      setWithdrawModalVisible(false);
      setWithdrawText("");
      await load();
    } catch (e: any) {
      Alert.alert("Prelievo fallito", e?.message || "Errore sconosciuto");
    } finally {
      setWithdrawing(false);
    }
  };

  const renderPosition = (p: PaperPosition, closed: boolean, pnlOverride?: number) => {
    const pnl = closed ? pnlOverride ?? 0 : p.unrealized_pnl ?? 0;
    const pnlColor = pnl >= 0 ? colors.success : colors.error;
    return (
      <View key={p.id} style={styles.posCard}>
        <View style={styles.posTop}>
          <Text style={styles.posSymbol}>{p.symbol}</Text>
          <View
            style={[
              styles.sideBadge,
              { backgroundColor: p.side === "long" ? colors.success : colors.error },
            ]}
          >
            <Text style={styles.sideBadgeText}>{p.side === "long" ? "LONG" : "SHORT"}</Text>
          </View>
        </View>
        <Text style={styles.posMeta}>
          Entrata {p.entry.toFixed(6)}
          {!closed && p.current_price ? `  ·  Attuale ${p.current_price.toFixed(6)}` : ""}
        </Text>
        <Text style={styles.posMeta}>
          SL {(p.current_stop || p.stop_loss).toFixed(6)}  ·  TP {p.take_profit.toFixed(6)}
        </Text>
        <View style={styles.posFooter}>
          <Text style={[styles.posPnl, { color: pnlColor }]}>
            {pnl >= 0 ? "+" : ""}
            {money(pnl)}
            {!closed && p.unrealized_pnl_pct !== undefined ? ` (${p.unrealized_pnl_pct.toFixed(2)}%)` : ""}
          </Text>
          <Text style={styles.posTime}>{timeAgo(p.opened_at)}</Text>
        </View>
      </View>
    );
  };

  const chartLevels: TvLevels = {
    entry: selected?.entry ?? 0,
    sl: (selected?.current_stop || selected?.stop_loss) ?? 0,
    tp1: selected?.tp1 || selected?.take_profit,
    tp2: selected?.tp2 || undefined,
    fvgTop: fvg?.top ?? selected?.entry ?? 0,
    fvgBottom: fvg?.bottom ?? selected?.entry ?? 0,
    side: selected?.side ?? "long",
  };

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>{meta.title}</Text>
        <View style={{ width: 26 }} />
      </View>
      <Text style={styles.subtitle}>{meta.subtitle}</Text>

      {loading && !portfolio ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.brand} />
      ) : error && !portfolio ? (
        <Text style={styles.errorText}>{error}</Text>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brand} />
          }
        >
          {portfolio && portfolio.open_positions.length > 1 && (
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              style={styles.chipRow}
              contentContainerStyle={{ gap: spacing.xs }}
            >
              {portfolio.open_positions.map((p) => (
                <Pressable
                  key={p.id}
                  onPress={() => setSelectedId(p.id)}
                  style={[styles.chip, p.id === selected?.id && styles.chipActive]}
                >
                  <Text style={[styles.chipText, p.id === selected?.id && styles.chipTextActive]}>
                    {p.symbol}
                  </Text>
                </Pressable>
              ))}
            </ScrollView>
          )}

          {selected ? (
            <TradingViewChart
              key={selected.id}
              symbol={selected.symbol}
              timeframe={selected.timeframe}
              candles={candles}
              levels={chartLevels}
              height={260}
            />
          ) : (
            <View style={styles.emptyChart}>
              <Text style={styles.emptyText}>
                Nessuna posizione aperta al momento per questa strategia.
              </Text>
            </View>
          )}

          <View style={styles.walletCard}>
            <View style={styles.walletRow}>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>
                  {portfolio?.wallet_type === "isolated" ? "Saldo isolato" : "Saldo condiviso"}
                </Text>
                <Text style={styles.walletValue}>{money(portfolio?.cash)}</Text>
              </View>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Equity</Text>
                <Text style={styles.walletValueBig}>{money(portfolio?.equity)}</Text>
              </View>
            </View>
            <View style={styles.walletRow}>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>P&L realizzato</Text>
                <Text
                  style={[
                    styles.walletValue,
                    { color: (portfolio?.realized_pnl ?? 0) >= 0 ? colors.success : colors.error },
                  ]}
                >
                  {money(portfolio?.realized_pnl)}
                </Text>
              </View>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Posizioni aperte</Text>
                <Text style={styles.walletValue}>{portfolio?.open_count ?? 0}</Text>
              </View>
            </View>

            <View style={styles.depositWithdrawRow}>
              <Pressable
                style={({ pressed }) => [styles.depositBtn, pressed && { opacity: 0.7 }]}
                onPress={() => setModalVisible(true)}
              >
                <Ionicons name="arrow-down-circle" size={16} color="#000" />
                <Text style={styles.depositBtnText}>Deposita</Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [styles.withdrawBtn, pressed && { opacity: 0.7 }]}
                onPress={() => setWithdrawModalVisible(true)}
                disabled={portfolio?.wallet_type !== "isolated"}
              >
                <Ionicons name="arrow-up-circle" size={16} color={colors.onSurface} />
                <Text style={styles.withdrawBtnText}>Preleva</Text>
              </Pressable>
            </View>
            {portfolio?.wallet_type === "shared" && (
              <Text style={styles.sharedNote}>
                Condiviso con le altre strategie tradizionali · principale: {money(walletStatus?.shared_cash)}.
                Deposita per isolare fondi dedicati a questa strategia.
              </Text>
            )}
          </View>

          <Text style={styles.sectionTitle}>Posizioni aperte ({portfolio?.open_count ?? 0})</Text>
          {(portfolio?.open_positions ?? []).length === 0 ? (
            <Text style={styles.emptyText}>Nessuna posizione aperta al momento.</Text>
          ) : (
            portfolio!.open_positions.map((p) => renderPosition(p, false))
          )}

          <View style={styles.historyHeaderRow}>
            <Text style={styles.sectionTitle}>
              Storico recente ({portfolio?.closed_count ?? 0}) · Win rate {portfolio?.win_rate ?? 0}%
            </Text>
            <Pressable onPress={() => router.push(`/strategy/${strategy}/history` as any)} hitSlop={8}>
              <Text style={styles.historyLink}>Vedi tutto ›</Text>
            </Pressable>
          </View>
          {(portfolio?.closed_trades ?? []).length === 0 ? (
            <Text style={styles.emptyText}>Nessuna operazione chiusa ancora.</Text>
          ) : (
            portfolio!.closed_trades
              .slice(0, 10)
              .map((t) =>
                renderPosition(
                  {
                    id: t.id,
                    signal_id: t.signal_id,
                    symbol: t.symbol,
                    timeframe: "",
                    side: t.side,
                    entry: t.entry,
                    stop_loss: t.entry,
                    take_profit: t.exit,
                    quantity: t.quantity,
                    risk_usdt: 0,
                    opened_at: t.opened_at,
                    current_price: t.exit,
                    unrealized_pnl: 0,
                    unrealized_pnl_pct: 0,
                  } as PaperPosition,
                  true,
                  t.pnl_usdt
                )
              )
          )}
        </ScrollView>
      )}

      <Modal visible={modalVisible} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>Deposita</Text>
            <Text style={styles.modalSubtitle}>
              Sposta fondi dal portafoglio principale a questa strategia. Da quel momento userà
              solo il saldo dedicato, indipendente dalle altre.
            </Text>
            <TextInput
              style={styles.modalInput}
              placeholder="Importo in USDT"
              placeholderTextColor={colors.onSurfaceSecondary}
              keyboardType="decimal-pad"
              value={amountText}
              onChangeText={setAmountText}
            />
            <View style={styles.modalButtons}>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.surfaceTertiary }]}
                onPress={() => {
                  setModalVisible(false);
                  setAmountText("");
                }}
              >
                <Text style={styles.modalBtnText}>Annulla</Text>
              </Pressable>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.brand }]}
                onPress={onAllocate}
                disabled={transferring}
              >
                {transferring ? (
                  <ActivityIndicator color={colors.onBrand} size="small" />
                ) : (
                  <Text style={[styles.modalBtnText, { color: colors.onBrand }]}>Conferma</Text>
                )}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      <Modal visible={withdrawModalVisible} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>Preleva</Text>
            <Text style={styles.modalSubtitle}>
              Sposta fondi dal saldo isolato di questa strategia al portafoglio principale.
              Se prelevi tutto, la strategia torna condivisa.
            </Text>
            <TextInput
              style={styles.modalInput}
              placeholder="Importo in USDT"
              placeholderTextColor={colors.onSurfaceSecondary}
              keyboardType="decimal-pad"
              value={withdrawText}
              onChangeText={setWithdrawText}
            />
            <View style={styles.modalButtons}>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.surfaceTertiary }]}
                onPress={() => {
                  setWithdrawModalVisible(false);
                  setWithdrawText("");
                }}
              >
                <Text style={styles.modalBtnText}>Annulla</Text>
              </Pressable>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.brand }]}
                onPress={onWithdraw}
                disabled={withdrawing}
              >
                {withdrawing ? (
                  <ActivityIndicator color={colors.onBrand} size="small" />
                ) : (
                  <Text style={[styles.modalBtnText, { color: colors.onBrand }]}>Conferma</Text>
                )}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
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
  emptyChart: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    marginBottom: spacing.lg,
    minHeight: 140,
    justifyContent: "center",
  },
  chipRow: { marginBottom: spacing.sm },
  chip: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    borderRadius: radius.pill,
    backgroundColor: colors.surfaceTertiary,
  },
  chipActive: { backgroundColor: colors.brand },
  chipText: { color: colors.onSurfaceSecondary, fontSize: font.sm, fontWeight: "600" },
  chipTextActive: { color: colors.onBrand },
  walletCard: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    marginTop: spacing.lg,
    marginBottom: spacing.lg,
  },
  walletRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: spacing.sm },
  walletStat: { flex: 1 },
  walletLabel: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginBottom: 2 },
  walletValue: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  walletValueBig: { color: colors.onSurface, fontSize: font.xl, fontWeight: "800" },
  depositWithdrawRow: {
    flexDirection: "row",
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  depositBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    backgroundColor: colors.brand,
    borderRadius: radius.pill,
    paddingVertical: spacing.sm,
  },
  depositBtnText: { color: "#000", fontWeight: "700", fontSize: font.base },
  withdrawBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.pill,
    paddingVertical: spacing.sm,
  },
  withdrawBtnText: { color: colors.onSurface, fontWeight: "700", fontSize: font.base },
  sharedNote: {
    color: colors.onSurfaceTertiary,
    fontSize: 11,
    textAlign: "center",
    marginTop: spacing.sm,
  },
  sectionTitle: {
    color: colors.onSurface,
    fontSize: font.base,
    fontWeight: "700",
    marginTop: spacing.lg,
    marginBottom: spacing.sm,
  },
  historyHeaderRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  historyLink: { color: colors.brand, fontSize: font.sm, fontWeight: "700" },
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
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    justifyContent: "center",
    alignItems: "center",
    padding: spacing.xl,
  },
  modalBox: { backgroundColor: colors.surfaceSecondary, borderRadius: radius.lg, padding: spacing.xl, width: "100%" },
  modalTitle: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700", marginBottom: 4 },
  modalSubtitle: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginBottom: spacing.lg },
  modalInput: {
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.md,
    padding: spacing.sm,
    color: colors.onSurface,
    fontSize: font.lg,
    marginBottom: spacing.lg,
  },
  modalButtons: { flexDirection: "row", gap: spacing.sm },
  modalBtn: { flex: 1, borderRadius: radius.md, paddingVertical: spacing.sm, alignItems: "center" },
  modalBtnText: { color: colors.onSurface, fontWeight: "700" },
});
