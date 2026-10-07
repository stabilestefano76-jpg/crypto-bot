import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { xrpAccApi, XrpAccPortfolio, XrpAccPosition } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";
import { useSwipeNavigation } from "@/src/useSwipeNavigation";
import SwipeDots from "@/src/SwipeDots";

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

// Price with enough decimals for cheap coins too (0.0097, not 0.0097 rounded).
function px(n?: number | null): string {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
}

// Human label for why a trade was closed.
function reasonLabel(r?: string): string {
  if (r === "target_rsi_raggiunto") return "target RSI";
  if (r === "trailing_stop") return "trailing";
  return r ?? "";
}

function xrpAmount(n?: number): string {
  if (n === undefined || n === null || isNaN(n)) return "0 XRP";
  return `${n.toFixed(4)} XRP`;
}

export default function XrpAccumulationScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { panHandlers, index, total } = useSwipeNavigation("/xrp-accumulation");
  const [portfolio, setPortfolio] = useState<XrpAccPortfolio | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [depositVisible, setDepositVisible] = useState(false);
  const [depositText, setDepositText] = useState("");
  const [depositing, setDepositing] = useState(false);
  const [withdrawVisible, setWithdrawVisible] = useState(false);
  const [withdrawText, setWithdrawText] = useState("");
  const [withdrawing, setWithdrawing] = useState(false);
  const [resetVisible, setResetVisible] = useState(false);
  const [resetting, setResetting] = useState(false);

  const load = useCallback(async () => {
    try {
      const p = await xrpAccApi.portfolio();
      setPortfolio(p);
    } catch {
      // ignore
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

  const onDeposit = async () => {
    const amount = parseFloat(depositText.replace(",", "."));
    if (!amount || amount <= 0) return;
    setDepositing(true);
    try {
      await xrpAccApi.deposit(amount);
      setDepositVisible(false);
      setDepositText("");
      await load();
    } finally {
      setDepositing(false);
    }
  };

  const onWithdraw = async () => {
    const amount = parseFloat(withdrawText.replace(",", "."));
    if (!amount || amount <= 0) return;
    setWithdrawing(true);
    try {
      await xrpAccApi.withdraw(amount);
      setWithdrawVisible(false);
      setWithdrawText("");
      await load();
    } finally {
      setWithdrawing(false);
    }
  };

  const onReset = async () => {
    setResetting(true);
    try {
      await xrpAccApi.reset();
      setResetVisible(false);
      await load();
    } finally {
      setResetting(false);
    }
  };

  const renderPosition = (p: XrpAccPosition, closed: boolean) => {
    const pnl = closed ? p.profit_usdt ?? 0 : p.unrealized_pnl ?? 0;
    const pnlColor = pnl >= 0 ? colors.success : colors.error;
    return (
      <View key={p.id} style={styles.posCard}>
        <View style={styles.posTop}>
          <Text style={styles.posSymbol}>
            {p.symbol}
            {p.timeframe ? <Text style={styles.posTf}> · {p.timeframe.toUpperCase()}</Text> : null}
          </Text>
          {p.rsi_at_entry !== undefined && (
            <View style={styles.setupBadge}>
              <Text style={styles.setupBadgeText}>
                RSI {p.rsi_at_entry.toFixed(0)}
                {portfolio?.rsi_target !== undefined ? ` → ${portfolio.rsi_target}` : ""}
              </Text>
            </View>
          )}
        </View>
        <Text style={styles.posMeta}>
          Entrata {px(p.fill_price ?? p.entry)}
          {!closed && p.current_price ? `  ·  Attuale ${px(p.current_price)}` : ""}
          {closed && p.close_price ? `  ·  Uscita ${px(p.close_price)}` : ""}
        </Text>
        {!closed && p.exit_level !== undefined && (
          <Text style={styles.posMeta}>
            Chiude con RSI ≥ {portfolio?.rsi_target ?? ""} e vendita ≥ {px(p.exit_level)}
            {p.pct_to_exit_level !== undefined && p.pct_to_exit_level !== null
              ? p.pct_to_exit_level > 0
                ? `  ·  mancano ${p.pct_to_exit_level.toFixed(2)}%`
                : "  ·  livello raggiunto"
              : ""}
          </Text>
        )}
        {!closed && p.trailing_active && p.trailing_stop !== undefined && (
          <Text style={styles.posMeta}>
            Trailing attivo  ·  massimo {px(p.peak_price)}  ·  vende se scende a {px(p.trailing_stop)}
          </Text>
        )}
        <Text style={styles.posMeta}>Quantità {xrpAmount(p.quantity)}</Text>
        <View style={styles.posFooter}>
          <View>
            <Text style={[styles.posPnl, { color: pnlColor }]}>
              {pnl >= 0 ? "+" : ""}
              {money(pnl)}
              {p.notional ? ` (${pnl >= 0 ? "+" : ""}${((pnl / p.notional) * 100).toFixed(2)}%)` : ""}
            </Text>
            {closed && p.profit_xrp !== undefined && p.profit_xrp > 0 && (
              <Text style={styles.posXrpGain}>+{p.profit_xrp.toFixed(4)} XRP nella riserva</Text>
            )}
          </View>
          <Text style={styles.posTime}>
            {closed ? reasonLabel(p.close_reason) : "aperta"} · {timeAgo(closed ? p.closed_at : p.opened_at)}
          </Text>
        </View>
      </View>
    );
  };

  return (
    <View style={[styles.root, { paddingTop: insets.top }]} {...panHandlers}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>XRP Accumulation</Text>
        <View style={{ width: 26 }} />
      </View>
      <Text style={styles.subtitle}>
        {portfolio?.rsi_low_threshold !== undefined && portfolio?.rsi_target !== undefined
          ? `Compra tutto sotto RSI ${portfolio.rsi_low_threshold}, vende sopra RSI ${portfolio.rsi_target}` +
            (portfolio.hold_below_entry
              ? ` e solo con guadagno netto di almeno +${portfolio.min_net_profit_pct}% (commissioni ${portfolio.fee_pct}% per lato)`
              : "") +
            " — solo il profitto diventa XRP per sempre"
          : "Compra tutto con RSI basso, vende con RSI alto — solo il profitto diventa XRP per sempre"}
      </Text>
      <SwipeDots index={index} total={total} />

      {loading && !portfolio ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.brand} />
      ) : (
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
          <View style={styles.walletCard}>
            <View style={styles.walletRow}>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>XRP accumulato (per sempre)</Text>
                <Text style={styles.walletValueBig}>{xrpAmount(portfolio?.total_xrp)}</Text>
              </View>
            </View>
            <View style={styles.walletRow}>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Capitale di trading</Text>
                <Text style={styles.walletValue}>{money(portfolio?.cash)}</Text>
              </View>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Prezzo XRP</Text>
                <Text style={styles.walletValue}>{money(portfolio?.xrp_price)}</Text>
              </View>
            </View>
            <View style={styles.walletRow}>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Equity totale (in USD)</Text>
                <Text style={styles.walletValue}>{money(portfolio?.equity_usdt)}</Text>
              </View>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Posizioni aperte</Text>
                <Text style={styles.walletValue}>{portfolio?.open_count ?? 0}</Text>
              </View>
            </View>

            <View style={styles.depositWithdrawRow}>
              <Pressable
                style={({ pressed }) => [styles.depositBtn, pressed && { opacity: 0.7 }]}
                onPress={() => setDepositVisible(true)}
              >
                <Ionicons name="arrow-down-circle" size={16} color="#000" />
                <Text style={styles.depositBtnText}>Deposita</Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [styles.withdrawBtn, pressed && { opacity: 0.7 }]}
                onPress={() => setWithdrawVisible(true)}
              >
                <Ionicons name="arrow-up-circle" size={16} color={colors.onSurface} />
                <Text style={styles.withdrawBtnText}>Preleva</Text>
              </Pressable>
            </View>
            <Text style={styles.walletHint}>
              Il prelievo attinge solo dal capitale di trading — la riserva permanente di XRP non si può prelevare da qui.
            </Text>
          </View>

          <View style={styles.historyHeaderRow}>
            <Text style={styles.sectionTitle}>Posizioni aperte ({portfolio?.open_count ?? 0})</Text>
            <Pressable onPress={() => setResetVisible(true)} hitSlop={12}>
              <Ionicons name="trash-outline" size={18} color={colors.error} />
            </Pressable>
          </View>
          {(portfolio?.open_positions ?? []).length === 0 ? (
            <Text style={styles.emptyText}>Nessuna posizione aperta al momento.</Text>
          ) : (
            portfolio!.open_positions.map((p) => renderPosition(p, false))
          )}

          <Text style={styles.sectionTitle}>
            Storico ({portfolio?.closed_count ?? 0}) · Totale XRP guadagnato dal trading: {xrpAmount(portfolio?.total_profit_xrp)}
          </Text>
          {(portfolio?.closed_positions ?? []).length === 0 ? (
            <Text style={styles.emptyText}>Nessuna operazione chiusa ancora.</Text>
          ) : (
            portfolio!.closed_positions.slice(0, 30).map((p) => renderPosition(p, true))
          )}
        </ScrollView>
      )}

      <Modal visible={depositVisible} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>Deposita</Text>
            <Text style={styles.modalSubtitle}>
              Sposta fondi dal portafoglio principale al capitale di trading di XRP Accumulation.
            </Text>
            <TextInput
              style={styles.modalInput}
              placeholder="Importo in USDT"
              placeholderTextColor={colors.onSurfaceSecondary}
              keyboardType="decimal-pad"
              value={depositText}
              onChangeText={setDepositText}
            />
            <View style={styles.modalButtons}>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.surfaceTertiary }]}
                onPress={() => {
                  setDepositVisible(false);
                  setDepositText("");
                }}
              >
                <Text style={styles.modalBtnText}>Annulla</Text>
              </Pressable>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.brand }]}
                onPress={onDeposit}
                disabled={depositing}
              >
                {depositing ? (
                  <ActivityIndicator color={colors.onBrand} size="small" />
                ) : (
                  <Text style={[styles.modalBtnText, { color: colors.onBrand }]}>Conferma</Text>
                )}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      <Modal visible={withdrawVisible} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>Preleva</Text>
            <Text style={styles.modalSubtitle}>
              Sposta fondi dal capitale di trading al portafoglio principale. Non tocca la riserva permanente di XRP.
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
                  setWithdrawVisible(false);
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

      <Modal visible={resetVisible} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>Reset XRP Accumulation</Text>
            <Text style={styles.modalSubtitle}>
              Cancella tutte le posizioni aperte e lo storico. Il capitale di trading E la riserva di XRP tornano a zero. Azione irreversibile.
            </Text>
            <View style={styles.modalButtons}>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.surfaceTertiary }]}
                onPress={() => setResetVisible(false)}
              >
                <Text style={styles.modalBtnText}>Annulla</Text>
              </Pressable>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.error }]}
                onPress={onReset}
                disabled={resetting}
              >
                {resetting ? (
                  <ActivityIndicator color="#fff" size="small" />
                ) : (
                  <Text style={[styles.modalBtnText, { color: "#fff" }]}>Cancella tutto</Text>
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
    paddingHorizontal: spacing.lg,
  },
  emptyText: { color: colors.onSurfaceSecondary, textAlign: "center", marginVertical: spacing.sm },
  walletCard: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    marginBottom: spacing.lg,
  },
  walletRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: spacing.sm },
  walletStat: { flex: 1 },
  walletLabel: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginBottom: 2 },
  walletValue: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  walletValueBig: { color: colors.onSurface, fontSize: font.xl, fontWeight: "800" },
  walletHint: { color: colors.onSurfaceSecondary, fontSize: 11, marginTop: spacing.sm },
  depositWithdrawRow: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.xs },
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
  sectionTitle: {
    color: colors.onSurface,
    fontSize: font.base,
    fontWeight: "700",
    marginTop: spacing.lg,
    marginBottom: spacing.sm,
  },
  historyHeaderRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: spacing.lg,
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
  posTf: { color: colors.onSurfaceSecondary, fontSize: font.sm, fontWeight: "600" },
  setupBadge: {
    borderRadius: radius.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    backgroundColor: colors.brandTertiary,
  },
  setupBadgeText: { color: colors.brand, fontSize: 11, fontWeight: "700" },
  posMeta: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginBottom: 2 },
  posFooter: { flexDirection: "row", justifyContent: "space-between", marginTop: spacing.xs, alignItems: "flex-end" },
  posPnl: { fontSize: font.base, fontWeight: "700" },
  posXrpGain: { color: colors.brand, fontSize: 11, fontWeight: "600", marginTop: 2 },
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
