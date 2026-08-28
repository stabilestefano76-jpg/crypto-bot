import { useCallback, useEffect, useState } from "react";
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
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import {
  scalpingWalletApi,
  scalpingWithdraw,
  ScalpingPortfolio,
  ScalpingPosition,
} from "@/src/api";
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

export default function ScalpingScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [portfolio, setPortfolio] = useState<ScalpingPortfolio | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [amountText, setAmountText] = useState("");
  const [transferring, setTransferring] = useState(false);
  const [transferMode, setTransferMode] = useState<"deposit" | "withdraw">("deposit");

  const load = useCallback(async () => {
    try {
      const data = await scalpingWalletApi.portfolio();
      setPortfolio(data);
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

  const onTransfer = async () => {
    const amount = parseFloat(amountText.replace(",", "."));
    if (!amount || amount <= 0) {
      Alert.alert("Importo non valido", "Inserisci un numero maggiore di zero.");
      return;
    }
    setTransferring(true);
    try {
      if (transferMode === "deposit") {
        await scalpingWalletApi.transfer(amount);
      } else {
        await scalpingWithdraw(amount);
      }
      setModalVisible(false);
      setAmountText("");
      await load();
    } catch (e: any) {
      Alert.alert("Trasferimento fallito", e?.message || "Errore sconosciuto");
    } finally {
      setTransferring(false);
    }
  };

  const renderPosition = (p: ScalpingPosition, closed: boolean) => {
    const pnl = closed ? p.pnl_usdt ?? 0 : p.unrealized_pnl ?? 0;
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
            <Text style={styles.sideBadgeText}>
              {p.side === "long" ? "LONG" : "SHORT"}
            </Text>
          </View>
        </View>
        <Text style={styles.posMeta}>
          Entrata {p.entry.toFixed(6)}
          {!closed && p.current_price ? `  ·  Attuale ${p.current_price.toFixed(6)}` : ""}
          {closed && p.close_price ? `  ·  Chiusa a ${p.close_price.toFixed(6)}` : ""}
        </Text>
        <Text style={styles.posMeta}>
          SL {p.stop_loss.toFixed(6)}  ·  TP {p.take_profit.toFixed(6)}
        </Text>
        <View style={styles.posFooter}>
          <Text style={[styles.posPnl, { color: pnlColor }]}>
            {pnl >= 0 ? "+" : ""}
            {money(pnl)}
            {!closed && p.unrealized_pnl_pct !== undefined
              ? ` (${p.unrealized_pnl_pct.toFixed(2)}%)`
              : ""}
          </Text>
          <Text style={styles.posTime}>
            {closed ? timeAgo(p.closed_at) : timeAgo(p.opened_at)}
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
        <Text style={styles.title}>Scalping Bot</Text>
        <View style={{ width: 26 }} />
      </View>
      <Text style={styles.subtitle}>VWAP + RSI(9) + Bollinger + EMA9/21 · 5m</Text>

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
          <View style={styles.walletCard}>
            <View style={styles.walletRow}>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Saldo</Text>
                <Text style={styles.walletValue}>{money(portfolio?.cash)}</Text>
              </View>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Allocato</Text>
                <Text style={styles.walletValue}>{money(portfolio?.allocated)}</Text>
              </View>
            </View>
            <View style={styles.walletRow}>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>Equity totale</Text>
                <Text style={styles.walletValueBig}>{money(portfolio?.equity)}</Text>
              </View>
              <View style={styles.walletStat}>
                <Text style={styles.walletLabel}>P&L realizzato</Text>
                <Text
                  style={[
                    styles.walletValue,
                    {
                      color:
                        (portfolio?.realized_pnl ?? 0) >= 0
                          ? colors.success
                          : colors.error,
                    },
                  ]}
                >
                  {money(portfolio?.realized_pnl)}
                </Text>
              </View>
            </View>
            <View style={styles.transferRow}>
              <Pressable
                style={({ pressed }) => [
                  styles.transferBtn,
                  { flex: 1 },
                  pressed && { opacity: 0.7 },
                ]}
                onPress={() => {
                  setTransferMode("deposit");
                  setModalVisible(true);
                }}
              >
                <Ionicons name="arrow-down-circle" size={16} color={colors.onBrand} />
                <Text style={styles.transferBtnText}>Deposita</Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [
                  styles.withdrawBtn,
                  { flex: 1 },
                  pressed && { opacity: 0.7 },
                ]}
                onPress={() => {
                  setTransferMode("withdraw");
                  setModalVisible(true);
                }}
              >
                <Ionicons name="arrow-up-circle" size={16} color={colors.onSurface} />
                <Text style={styles.withdrawBtnText}>Preleva</Text>
              </Pressable>
            </View>
          </View>

          <Text style={styles.sectionTitle}>
            Posizioni aperte ({portfolio?.open_count ?? 0})
          </Text>
          {(portfolio?.open_positions ?? []).length === 0 ? (
            <Text style={styles.emptyText}>Nessuna posizione aperta al momento.</Text>
          ) : (
            portfolio!.open_positions.map((p) => renderPosition(p, false))
          )}

          <Text style={styles.sectionTitle}>
            Storico chiuse ({portfolio?.closed_count ?? 0}) · Win rate{" "}
            {portfolio?.win_rate ?? 0}%
          </Text>
          {(portfolio?.closed_positions ?? []).length === 0 ? (
            <Text style={styles.emptyText}>Nessuna operazione chiusa ancora.</Text>
          ) : (
            portfolio!.closed_positions.slice(0, 20).map((p) => renderPosition(p, true))
          )}
        </ScrollView>
      )}

      <Modal visible={modalVisible} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>
              {transferMode === "deposit" ? "Deposita fondi" : "Preleva fondi"}
            </Text>
            <Text style={styles.modalSubtitle}>
              {transferMode === "deposit"
                ? "Sposta fondi dal portafoglio principale a quello dello Scalping Bot."
                : "Riporta fondi dallo Scalping Bot al portafoglio principale."}
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
                onPress={onTransfer}
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
  walletCard: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    marginBottom: spacing.lg,
  },
  walletRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: spacing.sm,
  },
  walletStat: { flex: 1 },
  walletLabel: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginBottom: 2 },
  walletValue: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  walletValueBig: { color: colors.onSurface, fontSize: font.xl, fontWeight: "800" },
  transferBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    backgroundColor: colors.brand,
    borderRadius: radius.pill,
    paddingVertical: spacing.sm,
  },
  transferBtnText: { color: colors.onBrand, fontWeight: "700", fontSize: font.base },
  transferRow: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.xs },
  withdrawBtn: {
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
  sideBadge: {
    borderRadius: radius.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  sideBadgeText: { color: colors.onSurface, fontSize: font.sm, fontWeight: "700" },
  posMeta: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginBottom: 2 },
  posFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: spacing.xs,
  },
  posPnl: { fontSize: font.base, fontWeight: "700" },
  posTime: { color: colors.onSurfaceSecondary, fontSize: font.sm },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    justifyContent: "center",
    alignItems: "center",
    padding: spacing.xl,
  },
  modalBox: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.xl,
    width: "100%",
  },
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
  modalBtn: {
    flex: 1,
    borderRadius: radius.md,
    paddingVertical: spacing.sm,
    alignItems: "center",
  },
  modalBtnText: { color: colors.onSurface, fontWeight: "700" },
});
