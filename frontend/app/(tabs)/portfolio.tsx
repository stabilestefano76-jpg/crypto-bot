import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
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
  const [capitalInput, setCapitalInput] = useState("");
  const [capitalSaving, setCapitalSaving] = useState(false);
  const [addFundsInput, setAddFundsInput] = useState("");
  const [addFundsSaving, setAddFundsSaving] = useState(false);
  const [addFundsMsg, setAddFundsMsg] = useState<string | null>(null);
  const [modeSaving, setModeSaving] = useState(false);
  const [connectVisible, setConnectVisible] = useState(false);
  const [exchange, setExchange] = useState<{
    connected: boolean;
    api_key_masked?: string;
    usdt_balance?: number;
  } | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, t, ex] = await Promise.all([
        api.portfolio(),
        api.paperTrades(),
        api.exchangeStatus(),
      ]);
      setPortfolio(p);
      setTrades(t.trades);
      setExchange(ex);
      setCapitalInput(String(p.initial_capital));
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

  const onSaveCapital = async () => {
    const n = Number(capitalInput);
    if (!Number.isFinite(n) || n <= 0) return;
    setCapitalSaving(true);
    try {
      await api.setCapital(n);
      await load();
    } finally {
      setCapitalSaving(false);
    }
  };

  const onAddFunds = async () => {
    const n = Number(addFundsInput);
    if (!Number.isFinite(n) || n <= 0) return;
    setAddFundsSaving(true);
    setAddFundsMsg(null);
    try {
      await api.addFunds(n);
      setAddFundsInput("");
      setAddFundsMsg(`+$${n.toFixed(2)} aggiunti`);
      setTimeout(() => setAddFundsMsg(null), 3000);
      await load();
    } catch (e: any) {
      setAddFundsMsg(e?.message || "Ricarica non riuscita");
      setTimeout(() => setAddFundsMsg(null), 4000);
    } finally {
      setAddFundsSaving(false);
    }
  };

  const onSetMode = async (mode: "manual" | "auto") => {
    setModeSaving(true);
    try {
      await api.setMode(mode);
      await load();
    } finally {
      setModeSaving(false);
    }
  };

  const [tradingModeMsg, setTradingModeMsg] = useState<string | null>(null);
  const onSetTradingMode = async (m: "spot" | "leverage") => {
    setTradingModeMsg(null);
    try {
      await api.setTradingMode(m);
      await load();
    } catch (e: any) {
      setTradingModeMsg(e.message || "Failed");
      setTimeout(() => setTradingModeMsg(null), 4000);
    }
  };

  const onDisconnect = async () => {
    await api.exchangeDisconnect();
    await load();
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
        {/* Quick controls: Mode toggle + Capital + Connect Bybit */}
        <View style={styles.controlsCard} testID="quick-controls">
          <Text style={styles.controlsLabel}>Market Type</Text>
          <View style={styles.modeRow}>
            <Pressable
              onPress={() => onSetTradingMode("spot")}
              style={[
                styles.modeBtn,
                portfolio.trading_mode === "spot" && styles.modeBtnActiveManual,
              ]}
              testID="market-spot"
            >
              <Ionicons
                name="cash"
                size={16}
                color={
                  portfolio.trading_mode === "spot"
                    ? "#fff"
                    : colors.onSurfaceSecondary
                }
              />
              <Text
                style={[
                  styles.modeText,
                  portfolio.trading_mode === "spot" && styles.modeTextActive,
                ]}
              >
                SPOT
              </Text>
            </Pressable>
            <Pressable
              onPress={() => onSetTradingMode("leverage")}
              style={[
                styles.modeBtn,
                portfolio.trading_mode === "leverage" && styles.modeBtnActiveAuto,
              ]}
              testID="market-leverage"
            >
              <Ionicons
                name="trending-up"
                size={16}
                color={
                  portfolio.trading_mode === "leverage"
                    ? "#000"
                    : colors.onSurfaceSecondary
                }
              />
              <Text
                style={[
                  styles.modeText,
                  portfolio.trading_mode === "leverage" && { color: "#000" },
                ]}
              >
                LEVERAGE
              </Text>
            </Pressable>
          </View>
          <Text style={styles.modeHelp}>
            {portfolio.trading_mode === "spot"
              ? "Real cash locked on open · long only · safest simulation."
              : "Futures-style PnL · long & short · larger position sizes."}
          </Text>
          {tradingModeMsg && (
            <Text style={{ color: colors.error, fontSize: 11, fontWeight: "700" }}>
              {tradingModeMsg}
            </Text>
          )}

          <View style={styles.divider} />

          <Text style={styles.controlsLabel}>Execution Mode</Text>
          <View style={styles.modeRow}>
            <Pressable
              onPress={() => onSetMode("manual")}
              disabled={modeSaving}
              style={[
                styles.modeBtn,
                !portfolio.auto_execute && styles.modeBtnActiveManual,
              ]}
              testID="mode-manual"
            >
              <Ionicons
                name="hand-left"
                size={16}
                color={!portfolio.auto_execute ? "#fff" : colors.onSurfaceSecondary}
              />
              <Text
                style={[
                  styles.modeText,
                  !portfolio.auto_execute && styles.modeTextActive,
                ]}
              >
                MANUAL
              </Text>
            </Pressable>
            <Pressable
              onPress={() => onSetMode("auto")}
              disabled={modeSaving}
              style={[
                styles.modeBtn,
                portfolio.auto_execute && styles.modeBtnActiveAuto,
              ]}
              testID="mode-auto"
            >
              <Ionicons
                name="flash"
                size={16}
                color={portfolio.auto_execute ? "#000" : colors.onSurfaceSecondary}
              />
              <Text
                style={[
                  styles.modeText,
                  portfolio.auto_execute && { color: "#000" },
                ]}
              >
                AUTO
              </Text>
            </Pressable>
          </View>
          <Text style={styles.modeHelp}>
            {portfolio.auto_execute
              ? "New confluence signals open positions automatically."
              : "Positions open only when you tap Execute on a signal."}
          </Text>

          <View style={styles.divider} />

          <Text style={styles.controlsLabel}>Ricarica fondi (senza reset)</Text>
          <View style={styles.capitalRow}>
            <TextInput
              style={styles.capitalInput}
              value={addFundsInput}
              onChangeText={setAddFundsInput}
              keyboardType="decimal-pad"
              placeholder="es. 500"
              placeholderTextColor={colors.onSurfaceTertiary}
              testID="input-add-funds"
            />
            <Pressable
              onPress={onAddFunds}
              disabled={addFundsSaving}
              style={({ pressed }) => [
                styles.capitalBtn,
                pressed && { opacity: 0.7 },
              ]}
              testID="add-funds-button"
            >
              {addFundsSaving ? (
                <ActivityIndicator size="small" color={colors.onBrand} />
              ) : (
                <Text style={styles.capitalBtnText}>Ricarica</Text>
              )}
            </Pressable>
          </View>
          <Text style={styles.modeHelp}>
            Aggiunge liquidità al saldo attuale: posizioni aperte e storico
            restano intatti. Usa &quot;Set &amp; Reset&quot; qui sotto solo se
            vuoi ripartire da zero.
          </Text>
          {addFundsMsg && (
            <Text
              style={{
                color: addFundsMsg.startsWith("+") ? colors.success : colors.error,
                fontSize: 11,
                fontWeight: "700",
              }}
            >
              {addFundsMsg}
            </Text>
          )}

          <View style={styles.divider} />

          <Text style={styles.controlsLabel}>Initial Capital (USDT)</Text>
          <View style={styles.capitalRow}>
            <TextInput
              style={styles.capitalInput}
              value={capitalInput}
              onChangeText={setCapitalInput}
              keyboardType="decimal-pad"
              placeholder="10000"
              placeholderTextColor={colors.onSurfaceTertiary}
              testID="input-initial-capital"
            />
            <Pressable
              onPress={onSaveCapital}
              disabled={capitalSaving}
              style={({ pressed }) => [
                styles.capitalBtn,
                pressed && { opacity: 0.7 },
              ]}
              testID="set-capital-button"
            >
              {capitalSaving ? (
                <ActivityIndicator size="small" color={colors.onBrand} />
              ) : (
                <Text style={styles.capitalBtnText}>Set & Reset</Text>
              )}
            </Pressable>
          </View>
          <Text style={styles.modeHelp}>
            Attenzione: cancella posizioni aperte e storico, e riparte da
            questo importo.
          </Text>

          <View style={styles.divider} />

          <Text style={styles.controlsLabel}>Exchange Connection</Text>
          {exchange?.connected ? (
            <View style={styles.connectedRow} testID="exchange-connected">
              <View style={styles.connectedLeft}>
                <View style={styles.connectedDot} />
                <View>
                  <Text style={styles.connectedTitle}>Bybit connected</Text>
                  <Text style={styles.connectedSub}>
                    {exchange.api_key_masked} · ${exchange.usdt_balance?.toFixed(2) ?? "0.00"} USDT
                  </Text>
                </View>
              </View>
              <Pressable
                onPress={onDisconnect}
                style={({ pressed }) => [
                  styles.disconnectBtn,
                  pressed && { opacity: 0.6 },
                ]}
                testID="disconnect-exchange"
              >
                <Text style={styles.disconnectText}>Disconnect</Text>
              </Pressable>
            </View>
          ) : (
            <Pressable
              onPress={() => setConnectVisible(true)}
              style={({ pressed }) => [
                styles.connectBtn,
                pressed && { opacity: 0.8 },
              ]}
              testID="connect-kucoin-button"
            >
              <Ionicons name="link" size={18} color={colors.onBrand} />
              <Text style={styles.connectBtnText}>Connect Bybit API</Text>
            </Pressable>
          )}
        </View>

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

      <ConnectModal
        visible={connectVisible}
        onClose={() => setConnectVisible(false)}
        onConnected={async () => {
          setConnectVisible(false);
          await load();
        }}
      />
    </View>
  );
}

function ConnectModal({
  visible,
  onClose,
  onConnected,
}: {
  visible: boolean;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await api.exchangeConnect({
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
      });
      setApiKey("");
      setApiSecret("");
      onConnected();
    } catch (e: any) {
      setError(e.message || "Connection failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.modalRoot}
      >
        <Pressable style={styles.modalBackdrop} onPress={onClose} />
        <View style={styles.modalCard} testID="connect-modal">
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Connect Bybit</Text>
            <Pressable onPress={onClose} hitSlop={12}>
              <Ionicons name="close" size={22} color={colors.onSurface} />
            </Pressable>
          </View>
          <Text style={styles.modalHint}>
            Paste the API key created in Bybit → API Management. Use{" "}
            <Text style={{ color: colors.brand, fontWeight: "800" }}>
              read + trade permissions only
            </Text>
            . NEVER enable withdrawals.
          </Text>

          <TextInput
            style={styles.modalInput}
            placeholder="API Key"
            placeholderTextColor={colors.onSurfaceTertiary}
            value={apiKey}
            onChangeText={setApiKey}
            autoCapitalize="none"
            autoCorrect={false}
            testID="input-api-key"
          />
          <TextInput
            style={styles.modalInput}
            placeholder="API Secret"
            placeholderTextColor={colors.onSurfaceTertiary}
            value={apiSecret}
            onChangeText={setApiSecret}
            secureTextEntry
            autoCapitalize="none"
            autoCorrect={false}
            testID="input-api-secret"
          />

          {error && <Text style={styles.modalError}>{error}</Text>}

          <Pressable
            onPress={submit}
            disabled={submitting || !apiKey || !apiSecret}
            style={({ pressed }) => [
              styles.modalSubmit,
              (submitting || !apiKey || !apiSecret) && {
                opacity: 0.5,
              },
              pressed && { opacity: 0.7 },
            ]}
            testID="submit-connect"
          >
            {submitting ? (
              <ActivityIndicator color={colors.onBrand} />
            ) : (
              <>
                <Ionicons name="shield-checkmark" size={18} color={colors.onBrand} />
                <Text style={styles.modalSubmitText}>Connect Securely</Text>
              </>
            )}
          </Pressable>

          <Text style={styles.modalFootnote}>
            Credentials are encrypted at rest and never shown again.
          </Text>
        </View>
      </KeyboardAvoidingView>
    </Modal>
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

      {(pos.breakeven_active || pos.trailing_active || pos.partial_closed) && (
        <View style={styles.mgmtRow} testID={`mgmt-${pos.id}`}>
          {pos.breakeven_active && (
            <View style={[styles.mgmtChip, { borderColor: colors.brand }]}>
              <Ionicons name="shield-half" size={10} color={colors.brand} />
              <Text style={[styles.mgmtText, { color: colors.brand }]}>
                Breakeven attivo
              </Text>
            </View>
          )}
          {pos.trailing_active && (
            <View style={[styles.mgmtChip, { borderColor: colors.success }]}>
              <Ionicons name="trending-up" size={10} color={colors.success} />
              <Text style={[styles.mgmtText, { color: colors.success }]}>
                Trailing attivo
              </Text>
            </View>
          )}
          {pos.partial_closed && (
            <View style={[styles.mgmtChip, { borderColor: colors.onSurfaceSecondary }]}>
              <Ionicons name="pie-chart" size={10} color={colors.onSurfaceSecondary} />
              <Text style={[styles.mgmtText, { color: colors.onSurfaceSecondary }]}>
                Parziale 35%
              </Text>
            </View>
          )}
        </View>
      )}

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
  mgmtRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  mgmtChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    paddingHorizontal: 6,
    paddingVertical: 3,
    borderRadius: radius.sm,
    borderWidth: 1,
  },
  mgmtText: { fontSize: 10, fontWeight: "800" },
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
  // Quick controls
  controlsCard: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  controlsLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    fontWeight: "700",
  },
  modeRow: { flexDirection: "row", gap: spacing.sm },
  modeBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 12,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  modeBtnActiveManual: {
    backgroundColor: colors.info,
    borderColor: colors.borderStrong,
  },
  modeBtnActiveAuto: {
    backgroundColor: colors.brand,
    borderColor: colors.brand,
  },
  modeText: {
    color: colors.onSurfaceSecondary,
    fontWeight: "800",
    fontSize: font.sm,
    letterSpacing: 0.6,
  },
  modeTextActive: { color: "#fff" },
  modeHelp: { color: colors.onSurfaceSecondary, fontSize: 11 },
  divider: {
    height: 1,
    backgroundColor: colors.divider,
    marginVertical: spacing.sm,
  },
  capitalRow: { flexDirection: "row", gap: spacing.sm },
  capitalInput: {
    flex: 1,
    color: colors.onSurface,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    fontSize: font.lg,
    fontWeight: "800",
  },
  capitalBtn: {
    paddingHorizontal: spacing.lg,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: colors.brand,
    borderRadius: radius.sm,
  },
  capitalBtnText: { color: colors.onBrand, fontWeight: "800", fontSize: font.sm },
  connectBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 14,
    borderRadius: radius.md,
    backgroundColor: colors.brand,
  },
  connectBtnText: {
    color: colors.onBrand,
    fontWeight: "800",
    fontSize: font.lg,
    letterSpacing: 0.4,
  },
  connectedRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: spacing.md,
    backgroundColor: colors.surfaceTertiary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.success,
  },
  connectedLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    flex: 1,
  },
  connectedDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: colors.success,
  },
  connectedTitle: {
    color: colors.onSurface,
    fontWeight: "800",
    fontSize: font.base,
  },
  connectedSub: { color: colors.onSurfaceSecondary, fontSize: 11, marginTop: 2 },
  disconnectBtn: {
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  disconnectText: {
    color: colors.onSurfaceSecondary,
    fontWeight: "700",
    fontSize: font.sm,
  },
  // Modal
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  modalBackdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.65)",
  },
  modalCard: {
    backgroundColor: colors.surfaceSecondary,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    padding: spacing.lg,
    paddingBottom: spacing.xl,
    borderTopWidth: 1,
    borderLeftWidth: 1,
    borderRightWidth: 1,
    borderColor: colors.borderStrong,
    gap: spacing.md,
  },
  modalHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  modalTitle: {
    color: colors.onSurface,
    fontSize: font.xl,
    fontWeight: "800",
  },
  modalHint: {
    color: colors.onSurfaceSecondary,
    fontSize: font.sm,
    lineHeight: 18,
  },
  modalInput: {
    color: colors.onSurface,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: 12,
    fontSize: font.base,
    fontWeight: "600",
  },
  modalError: {
    color: colors.error,
    fontSize: font.sm,
    fontWeight: "700",
    textAlign: "center",
  },
  modalSubmit: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    backgroundColor: colors.brand,
    paddingVertical: 14,
    borderRadius: radius.md,
  },
  modalSubmitText: { color: colors.onBrand, fontWeight: "800", fontSize: font.lg },
  modalFootnote: {
    color: colors.onSurfaceTertiary,
    fontSize: 11,
    textAlign: "center",
  },
});
