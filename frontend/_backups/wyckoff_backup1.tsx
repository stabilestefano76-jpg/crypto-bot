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
import { wyckoffApi, WyckoffPortfolio, WyckoffPosition } from "@/src/api";
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

export default function WyckoffScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [portfolio, setPortfolio] = useState<WyckoffPortfolio | null>(null);
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
      const p = await wyckoffApi.portfolio();
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
      await wyckoffApi.deposit(amount);
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
      await wyckoffApi.withdraw(amount);
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
      await wyckoffApi.reset();
      setResetVisible(false);
      await load();
    } finally {
      setResetting(false);
    }
  };

  const renderPosition = (p: WyckoffPosition, closed: boolean) => {
    const pnl = closed ? p.pnl_usdt ?? 0 : p.unrealized_pnl ?? 0;
    const pnlColor = pnl >= 0 ? colors.success : colors.error;
    return (
      <View key={p.id} style={styles.posCard}>
        <View style={styles.posTop}>
          <Text style={styles.posSymbol}>{p.symbol}</Text>
          <View style={styles.setupBadge}>
            <Text style={styles.setupBadgeText}>LPS</Text>
          </View>
        </View>
        <Text style={styles.posMeta}>
          Entrata {p.entry.toFixed(4)}
          {!closed && p.current_price ? `  ·  Attuale ${p.current_price.toFixed(4)}` : ""}
        </Text>
        <Text style={styles.posMeta}>
          SL {p.stop_loss.toFixed(4)}  ·  TP {p.take_profit.toFixed(4)}
          {!closed && p.trailing_active ? "  ·  trailing attivo" : ""}
        </Text>
        <View style={styles.posFooter}>
          <Text style={[styles.posPnl, { color: pnlColor }]}>
            {pnl >= 0 ? "+" : ""}
            {money(pnl)}
          </Text>
          <Text style={styles.posTime}>
            {closed ? p.close_reason : "aperta"} · {timeAgo(closed ? p.closed_at : p.opened_at)}
          </Text>
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
        <Text style={styles.title}>Wyckoff Spring</Text>
        <View style={{ width: 26 }} />
      </View>
      <Text style={styles.subtitle}>
        Range → Spring → Test → Sign of Strength → Last Point of Support
      </Text>

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
                <Text style={styles.walletLabel}>Saldo</Text>
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
            Storico ({portfolio?.closed_count ?? 0}) · Win rate {portfolio?.win_rate ?? 0}%
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
              Sposta fondi dal portafoglio principale a Wyckoff Spring.
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
              Sposta fondi da Wyckoff Spring al portafoglio principale.
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
            <Text style={styles.modalTitle}>Reset Wyckoff Spring</Text>
            <Text style={styles.modalSubtitle}>
              Cancella tutte le posizioni aperte e lo storico. Il saldo torna a zero. Azione irreversibile.
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
  setupBadge: {
    borderRadius: radius.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    backgroundColor: colors.brandTertiary,
  },
  setupBadgeText: { color: colors.brand, fontSize: 11, fontWeight: "700" },
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
