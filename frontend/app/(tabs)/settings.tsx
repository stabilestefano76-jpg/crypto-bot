import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, Config, PaperConfig } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

export default function SettingsScreen() {
  const insets = useSafeAreaInsets();
  const [cfg, setCfg] = useState<Config | null>(null);
  const [pcfg, setPcfg] = useState<PaperConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([api.getConfig(), api.paperConfig()]);
      setCfg(c);
      setPcfg(p);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const update = (patch: Partial<Config>) => {
    setCfg((prev) => (prev ? { ...prev, ...patch } : prev));
    setSaved(false);
  };

  const updatePaper = (patch: Partial<PaperConfig>) => {
    setPcfg((prev) => (prev ? { ...prev, ...patch } : prev));
    setSaved(false);
  };

  const save = async () => {
    if (!cfg || !pcfg) return;
    setSaving(true);
    try {
      const [updated, updatedP] = await Promise.all([
        api.saveConfig(cfg),
        api.savePaperConfig(pcfg),
      ]);
      setCfg(updated);
      setPcfg(updatedP);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  const toggleTimeframe = (t: string) => {
    if (!cfg) return;
    const list = cfg.timeframes.includes(t)
      ? cfg.timeframes.filter((x) => x !== t)
      : [...cfg.timeframes, t];
    update({ timeframes: list });
  };

  if (loading || !cfg || !pcfg) {
    return (
      <View style={[styles.root, styles.center, { paddingTop: insets.top }]}>
        <ActivityIndicator color={colors.brand} />
      </View>
    );
  }

  const enabledStrategies: string[] =
    cfg.enabled_strategies && cfg.enabled_strategies.length
      ? cfg.enabled_strategies
      : ["counter_trend", "fvg_reversal", "rsi_reversion"];
  const stratOn = (val: string): boolean => enabledStrategies.includes(val);

  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <View style={[styles.root, { paddingTop: insets.top }]}>
        <View style={styles.header}>
          <Text style={styles.title} testID="settings-title">
            Settings
          </Text>
          <Text style={styles.subtitle}>Configure the signal engine</Text>
        </View>

        <ScrollView
          contentContainerStyle={styles.body}
          keyboardShouldPersistTaps="handled"
        >
          <Section title="Strategie segnali (parallele)">
            {(() => {
              const toggle = (val: string) => {
                const set = new Set(enabledStrategies);
                if (set.has(val)) set.delete(val);
                else set.add(val);
                update({ enabled_strategies: Array.from(set) });
              };
              return (
                <View style={styles.stratRow}>
                  {([
                    ["counter_trend", "Rev Pre-FVG"],
                    ["fvg_reversal", "FVG Reversal"],
                    ["rsi_reversion", "RSI Reversion"],
                  ] as const).map(([val, label]) => {
                    const active = stratOn(val);
                    return (
                      <Pressable
                        key={val}
                        onPress={() => toggle(val)}
                        style={[styles.stratChip, active && styles.stratChipActive]}
                        testID={`strategy-${val}`}
                      >
                        <Text
                          style={[styles.stratText, active && { color: colors.onBrand }]}
                        >
                          {label}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              );
            })()}
            <Text style={styles.scoreHintText}>
              Selezione multipla: girano in parallelo a capitale condiviso (a
              meno di allocare fondi dedicati dalla schermata Strategie). Rev
              Pre-FVG = breakout del consolidamento verso il fill FVG. FVG
              Reversal = contro-trend sul ritracciamento verso la FVG
              (parametri indipendenti). RSI Reversion = rientro da
              ipercomprato/ipervenduto confermato da divergenza.
            </Text>
          </Section>

          {stratOn("counter_trend") && (
            <Section title="Reversal Pre-FVG Strategy">
              <NumRow
                label="Min. candele consolidamento"
                value={cfg.consolidation_min_candles}
                onChange={(v) => update({ consolidation_min_candles: v })}
                testID="input-ct-consol-min"
              />
              <NumRow
                label="Max ampiezza box (×ATR)"
                value={cfg.consolidation_max_atr}
                onChange={(v) => update({ consolidation_max_atr: v })}
                step={0.1}
                testID="input-ct-consol-atr"
              />
              <NumRow
                label="Quota TP1 (%)"
                value={cfg.tp1_pct}
                onChange={(v) => update({ tp1_pct: v })}
                testID="input-ct-tp1-pct"
              />
              <NumRow
                label="Avanzamento post-TP1 (%)"
                value={cfg.post_tp1_advance_pct}
                onChange={(v) => update({ post_tp1_advance_pct: v })}
                step={0.1}
                testID="input-ct-post-tp1"
              />
              <Text style={styles.scoreHintText}>
                Dopo TP1 chiude {cfg.tp1_pct}%; quando il prezzo avanza di{" "}
                {cfg.post_tp1_advance_pct}% oltre TP1, lo stop del residuo si
                sposta a TP1 (netto commissioni). Altrimenti resta lo stop ATR.
                Il resto (TP2) chiude sul bordo opposto della FVG.
              </Text>
            </Section>
          )}

          {stratOn("fvg_reversal") && (
            <Section title="FVG Reversal Strategy">
              <NumRow
                label="Quota TP1 (%)"
                value={cfg.fvgr_tp1_pct}
                onChange={(v) => update({ fvgr_tp1_pct: v })}
                testID="input-fvgr-tp1"
              />
              <NumRow
                label="Quota TP2 (%)"
                value={cfg.fvgr_tp2_pct}
                onChange={(v) => update({ fvgr_tp2_pct: v })}
                testID="input-fvgr-tp2"
              />
              <NumRow
                label="Avanzamento post-TP1 (%)"
                value={cfg.fvgr_post_tp1_advance_pct}
                onChange={(v) => update({ fvgr_post_tp1_advance_pct: v })}
                step={0.1}
                testID="input-fvgr-post-tp1"
              />
              <NumRow
                label="Trailing stop (%)"
                value={cfg.fvgr_trailing_pct}
                onChange={(v) => update({ fvgr_trailing_pct: v })}
                step={0.1}
                testID="input-fvgr-trailing"
              />
              <NumRow
                label="SL ATR multiplier"
                value={cfg.fvgr_atr_sl_multiplier}
                onChange={(v) => update({ fvgr_atr_sl_multiplier: v })}
                step={0.1}
                testID="input-fvgr-atr"
              />
              <NumRow
                label="Min R:R"
                value={cfg.fvgr_min_rr_ratio}
                onChange={(v) => update({ fvgr_min_rr_ratio: v })}
                step={0.1}
                testID="input-fvgr-rr"
              />
              <Text style={styles.scoreHintText}>
                Contro-trend sul ritracciamento verso la FVG di impulso. Entrata
                sul pattern di reversal; target dentro la FVG. Dopo TP1 chiude{" "}
                {cfg.fvgr_tp1_pct}%; oltre +{cfg.fvgr_post_tp1_advance_pct}% da TP1
                lo stop va a TP1 (netto fee); trailing {cfg.fvgr_trailing_pct}% in
                profitto. Parametri indipendenti dalle altre strategie.
              </Text>
            </Section>
          )}

          {stratOn("rsi_reversion") && (
            <Section title="RSI Reversion Strategy">
              <NumRow
                label="Soglia ipercomprato"
                value={cfg.rsi_rev_overbought}
                onChange={(v) => update({ rsi_rev_overbought: v })}
                testID="input-rsirev-ob"
              />
              <NumRow
                label="Soglia ipervenduto"
                value={cfg.rsi_rev_oversold}
                onChange={(v) => update({ rsi_rev_oversold: v })}
                testID="input-rsirev-os"
              />
              <NumRow
                label="Min. candele in zona estrema"
                value={cfg.rsi_rev_min_extreme_candles}
                onChange={(v) => update({ rsi_rev_min_extreme_candles: v })}
                testID="input-rsirev-extreme"
              />
              <NumRow
                label="Stop catastrofico (×ATR)"
                value={cfg.rsi_rev_catastrophic_atr_mult}
                onChange={(v) => update({ rsi_rev_catastrophic_atr_mult: v })}
                step={0.5}
                testID="input-rsirev-atr"
              />
              <Text style={styles.scoreHintText}>
                Entra quando l&apos;RSI rientra dalla zona estrema (dopo almeno{" "}
                {cfg.rsi_rev_min_extreme_candles} candele oltre soglia) con
                divergenza confermata. Target = prezzo torna sulla propria
                media a {cfg.rsi_period} periodi (proxy di &quot;RSI torna a
                50&quot;). Nessuno stop stretto: solo lo stop catastrofico a{" "}
                {cfg.rsi_rev_catastrophic_atr_mult}×ATR, per i casi estremi.
              </Text>
            </Section>
          )}

          <Section title="Scan Engine">
            <NumRow
              label="Scan interval (min)"
              value={cfg.scan_interval_minutes}
              onChange={(v) => update({ scan_interval_minutes: v })}
              testID="input-scan-interval"
            />
            <NumRow
              label="Max pairs per scan"
              value={cfg.max_pairs_per_scan}
              onChange={(v) => update({ max_pairs_per_scan: v })}
              testID="input-max-pairs"
            />
            <TextRow
              label="Quote asset"
              value={cfg.quote_filter}
              onChange={(v) => update({ quote_filter: v.toUpperCase() })}
              testID="input-quote-asset"
            />
            <NumRow
              label="Min 24h volume (USDT)"
              value={cfg.min_24h_volume_usdt}
              onChange={(v) => update({ min_24h_volume_usdt: v })}
              testID="input-min-volume"
            />
            <NumRow
              label="Finestra validità segnali (candele)"
              value={cfg.signal_validity_candles}
              onChange={(v) => update({ signal_validity_candles: v })}
              testID="input-validity-window"
            />
            <NumRow
              label="FVG lookback (candele)"
              value={cfg.fvg_lookback}
              onChange={(v) => update({ fvg_lookback: v })}
              testID="input-fvg-lookback"
            />
          </Section>

          <Section title="Timeframes">
            <View style={styles.chipsRow}>
              {TIMEFRAMES.map((t) => {
                const active = cfg.timeframes.includes(t);
                return (
                  <Pressable
                    key={t}
                    onPress={() => toggleTimeframe(t)}
                    style={[styles.chip, active && styles.chipActive]}
                    testID={`tf-chip-${t}`}
                  >
                    <Text
                      style={[styles.chipText, active && styles.chipTextActive]}
                    >
                      {t}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </Section>

          <Section title="RSI (condiviso tra le strategie)">
            <NumRow
              label="Period"
              value={cfg.rsi_period}
              onChange={(v) => update({ rsi_period: v })}
              testID="input-rsi-period"
            />
            <NumRow
              label="Pivot window"
              value={cfg.pivot_window}
              onChange={(v) => update({ pivot_window: v })}
              testID="input-pivot-window"
            />
            <NumRow
              label="Overbought (Rev Pre-FVG/FVG Reversal)"
              value={cfg.rsi_overbought}
              onChange={(v) => update({ rsi_overbought: v })}
              testID="input-rsi-ob"
            />
            <NumRow
              label="Oversold (Rev Pre-FVG/FVG Reversal)"
              value={cfg.rsi_oversold}
              onChange={(v) => update({ rsi_oversold: v })}
              testID="input-rsi-os"
            />
          </Section>

          <Section title="Volume">
            <NumRow
              label="Volume MA period"
              value={cfg.volume_ma_period}
              onChange={(v) => update({ volume_ma_period: v })}
              testID="input-vol-period"
            />
            <NumRow
              label="Volume spike multiplier"
              value={cfg.volume_spike_multiplier}
              onChange={(v) => update({ volume_spike_multiplier: v })}
              step={0.1}
              testID="input-vol-mult"
            />
          </Section>

          <Section title="Risk">
            <NumRow
              label="R:R ratio (1 : x)"
              value={cfg.rr_ratio}
              onChange={(v) => update({ rr_ratio: v })}
              step={0.1}
              testID="input-rr"
            />
            <NumRow
              label="SL padding beyond FVG (%)"
              value={cfg.sl_padding_pct}
              onChange={(v) => update({ sl_padding_pct: v })}
              step={0.05}
              testID="input-sl-padding"
            />
            <NumRow
              label="ATR period"
              value={cfg.atr_period}
              onChange={(v) => update({ atr_period: v })}
              testID="input-atr-period"
            />
            <NumRow
              label="ATR SL multiplier"
              value={cfg.atr_sl_multiplier}
              onChange={(v) => update({ atr_sl_multiplier: v })}
              step={0.1}
              testID="input-atr-sl-mult"
            />
            <NumRow
              label="Min R:R (Rev Pre-FVG)"
              value={cfg.min_rr_ratio}
              onChange={(v) => update({ min_rr_ratio: v })}
              step={0.1}
              testID="input-min-rr"
            />
          </Section>

          <Section title="Live Safety">
            <NumRow
              label="Max size per trade (USDT)"
              value={pcfg.max_position_size_usdt}
              onChange={(v) => updatePaper({ max_position_size_usdt: v })}
              testID="input-max-size"
            />
            <ToggleRow
              label="One position per pair"
              value={pcfg.one_position_per_pair}
              onChange={(v) => updatePaper({ one_position_per_pair: v })}
              testID="toggle-one-per-pair"
            />
          </Section>

          <Section title="Paper Trading">
            <ToggleRow
              label="Auto-execute new signals"
              value={pcfg.auto_execute}
              onChange={(v) => updatePaper({ auto_execute: v })}
              testID="toggle-auto-execute"
            />
            <NumRow
              label="Initial capital (USDT)"
              value={pcfg.initial_capital}
              onChange={(v) => updatePaper({ initial_capital: v })}
              testID="input-initial-capital"
            />
            <NumRow
              label="Risk per trade (%)"
              value={pcfg.risk_per_trade_pct}
              onChange={(v) => updatePaper({ risk_per_trade_pct: v })}
              step={0.1}
              testID="input-risk-pct"
            />
            <NumRow
              label="Max open positions"
              value={pcfg.max_open_positions}
              onChange={(v) => updatePaper({ max_open_positions: v })}
              testID="input-max-positions"
            />
          </Section>

          <View style={styles.disclaimer} testID="disclaimer">
            <Ionicons name="warning-outline" size={16} color={colors.brand} />
            <Text style={styles.disclaimerText}>
              Strumento di analisi tecnica — non consulenza finanziaria. Il
              trading comporta rischio di perdita del capitale.
            </Text>
          </View>

          <Pressable
            onPress={save}
            disabled={saving}
            style={({ pressed }) => [
              styles.saveBtn,
              pressed && { opacity: 0.7 },
            ]}
            testID="save-config-button"
          >
            {saving ? (
              <ActivityIndicator color={colors.onBrand} />
            ) : (
              <>
                <Ionicons
                  name={saved ? "checkmark-circle" : "save"}
                  size={18}
                  color={colors.onBrand}
                />
                <Text style={styles.saveText}>
                  {saved ? "Saved!" : "Save Configuration"}
                </Text>
              </>
            )}
          </Pressable>
        </ScrollView>
      </View>
    </KeyboardAvoidingView>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={styles.sectionBody}>{children}</View>
    </View>
  );
}

function NumRow({
  label,
  value,
  onChange,
  step,
  testID,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  testID: string;
}) {
  const [txt, setTxt] = useState(String(value));
  useEffect(() => setTxt(String(value)), [value]);
  return (
    <View style={styles.formRow}>
      <Text style={styles.formLabel}>{label}</Text>
      <TextInput
        style={styles.input}
        value={txt}
        onChangeText={setTxt}
        onEndEditing={() => {
          const n = Number(txt);
          if (!Number.isFinite(n)) {
            setTxt(String(value));
            return;
          }
          onChange(step && step < 1 ? Math.round(n * 100) / 100 : n);
        }}
        keyboardType="decimal-pad"
        testID={testID}
        placeholderTextColor={colors.onSurfaceTertiary}
      />
    </View>
  );
}

function TextRow({
  label,
  value,
  onChange,
  testID,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  testID: string;
}) {
  return (
    <View style={styles.formRow}>
      <Text style={styles.formLabel}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChange}
        autoCapitalize="characters"
        testID={testID}
      />
    </View>
  );
}

function ToggleRow({
  label,
  value,
  onChange,
  testID,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
  testID: string;
}) {
  return (
    <View style={styles.formRow}>
      <Text style={styles.formLabel}>{label}</Text>
      <Switch
        value={value}
        onValueChange={onChange}
        trackColor={{ true: colors.brand, false: colors.surfaceTertiary }}
        thumbColor={colors.onSurface}
        testID={testID}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { alignItems: "center", justifyContent: "center" },
  header: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  title: { fontSize: 22, fontWeight: "800", color: colors.onSurface },
  subtitle: { color: colors.onSurfaceSecondary, marginTop: 2, fontSize: font.sm },
  body: { padding: spacing.lg, paddingBottom: spacing.xxl, gap: spacing.md },
  section: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: "hidden",
  },
  sectionTitle: {
    color: colors.brand,
    fontWeight: "800",
    fontSize: font.sm,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    paddingHorizontal: spacing.md,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
  },
  sectionBody: { paddingHorizontal: spacing.md, paddingBottom: spacing.md },
  formRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.divider,
    gap: spacing.md,
  },
  formLabel: { color: colors.onSurface, flex: 1, fontSize: font.base },
  input: {
    width: 110,
    color: colors.onSurface,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 8,
    fontSize: font.base,
    textAlign: "right",
    fontWeight: "600",
  },
  chipsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.sm,
    padding: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.divider,
  },
  chip: {
    height: 36,
    minWidth: 56,
    paddingHorizontal: spacing.md,
    borderRadius: radius.pill,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  chipActive: { borderColor: colors.brand, backgroundColor: colors.brandTertiary },
  chipText: { color: colors.onSurfaceSecondary, fontWeight: "600", fontSize: font.sm },
  chipTextActive: { color: colors.brand },
  disclaimer: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.sm,
    padding: spacing.md,
    backgroundColor: colors.brandTertiary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.brand,
  },
  disclaimerText: { color: colors.onSurface, flex: 1, fontSize: font.sm, lineHeight: 18 },
  scoreHint: {
    flexDirection: "row",
    gap: 6,
    padding: spacing.sm,
    backgroundColor: colors.brandTertiary,
    borderRadius: radius.sm,
    marginBottom: 4,
  },
  scoreHintText: { color: colors.onSurface, flex: 1, fontSize: 11, lineHeight: 16 },
  stratRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm, marginBottom: spacing.sm },
  stratChip: {
    flexGrow: 1,
    flexBasis: "30%",
    paddingVertical: 10,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
  },
  stratChipActive: { backgroundColor: colors.brand, borderColor: colors.brand },
  stratText: { color: colors.onSurfaceSecondary, fontWeight: "800", fontSize: font.sm },
  maxScoreRow: {
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.divider,
  },
  maxScoreText: { color: colors.onSurfaceSecondary, fontSize: font.sm },
  fillChip: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 8,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceTertiary,
    borderWidth: 1,
    borderColor: colors.border,
  },
  fillChipActive: { borderColor: colors.brand, backgroundColor: colors.brandTertiary },
  fillChipText: { color: colors.onSurfaceSecondary, fontWeight: "700", fontSize: font.sm },
  saveBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    backgroundColor: colors.brand,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
  },
  saveText: { color: colors.onBrand, fontWeight: "800", fontSize: font.lg },
});
