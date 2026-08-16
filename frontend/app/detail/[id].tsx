import { useEffect, useState } from "react";
import React from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import Svg, { Line, Rect, Path, Text as SvgText } from "react-native-svg";
import { api, Candle, Signal } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const CHART_HEIGHT = 220;
const RSI_HEIGHT = 80;

export default function DetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [signal, setSignal] = useState<Signal | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [rsi, setRsi] = useState<number[]>([]);
  const [chartWidth, setChartWidth] = useState(360);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [executing, setExecuting] = useState(false);
  const [executeMsg, setExecuteMsg] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const s = await api.signal(id!);
        setSignal(s);
        const c = await api.candles(s.symbol, s.timeframe);
        setCandles(c.candles);
        setRsi(c.rsi);
      } catch (e: any) {
        setError(e.message || "Load failed");
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  if (loading) {
    return (
      <View style={[styles.root, styles.center, { paddingTop: insets.top }]}>
        <ActivityIndicator color={colors.brand} />
      </View>
    );
  }
  if (error || !signal) {
    return (
      <View style={[styles.root, styles.center, { paddingTop: insets.top }]}>
        <Ionicons name="alert-circle" size={48} color={colors.error} />
        <Text style={styles.muted}>{error || "Signal not found"}</Text>
        <Pressable onPress={() => router.back()} style={styles.retryBtn}>
          <Text style={styles.retryText}>Back</Text>
        </Pressable>
      </View>
    );
  }

  const isLong = signal.side === "long";
  const last = candles[candles.length - 1];

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable
          onPress={() => router.back()}
          hitSlop={12}
          testID="back-button"
        >
          <Ionicons name="chevron-back" size={24} color={colors.onSurface} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.pair} testID="detail-pair">
            {signal.symbol}
          </Text>
          <Text style={styles.subline}>
            {signal.timeframe} · Last {last ? formatPrice(last.c) : "—"}
          </Text>
        </View>
        <View
          style={[
            styles.sideBadge,
            { backgroundColor: isLong ? colors.success : colors.error },
          ]}
        >
          <Text style={styles.sideText}>{isLong ? "LONG" : "SHORT"}</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={styles.body}>
        <View
          style={styles.chartCard}
          onLayout={(e) => setChartWidth(e.nativeEvent.layout.width)}
        >
          <PriceChart
            width={chartWidth}
            candles={candles}
            signal={signal}
          />
          <View style={styles.rsiCard}>
            <RsiChart width={chartWidth} rsi={rsi} />
          </View>
        </View>

        <View style={styles.gridRow}>
          <Metric label="Entry" value={formatPrice(signal.entry)} color={colors.brand} />
          <Metric
            label="Stop Loss"
            value={formatPrice(signal.stop_loss)}
            color={colors.error}
          />
        </View>
        <View style={styles.gridRow}>
          <Metric
            label="Take Profit"
            value={formatPrice(signal.take_profit)}
            color={colors.success}
          />
          <Metric label="R : R" value={`1 : ${signal.rr_ratio}`} color={colors.brand} />
        </View>
        <View style={styles.gridRow}>
          <Metric label="RSI" value={signal.rsi_value.toFixed(1)} color={colors.onSurface} />
          <Metric
            label="Volume Ratio"
            value={`${signal.volume_ratio}×`}
            color={colors.onSurface}
          />
        </View>
        {(signal.strategy === "impulse_fvg" || signal.strategy === "counter_trend") && (
          <View style={styles.gridRow}>
            <Metric label="TP1 (65%)" value={formatPrice(signal.tp1 || 0)} color={colors.success} />
            <Metric
              label={signal.tp2 ? "TP2 (35%)" : "TP2 → trailing"}
              value={signal.tp2 ? formatPrice(signal.tp2) : "trailing"}
              color={colors.brand}
            />
          </View>
        )}
        {signal.atr ? (
          <View style={styles.gridRow}>
            <Metric
              label="ATR"
              value={formatPrice(signal.atr)}
              color={colors.onSurface}
            />
            <Metric
              label="Stop distance"
              value={`${(
                Math.abs(signal.entry - signal.stop_loss) / signal.atr
              ).toFixed(2)}×ATR`}
              color={colors.brand}
            />
          </View>
        ) : null}

        <View style={styles.ctxCard}>
          <Text style={styles.ctxTitle}>Signal Reasoning</Text>
          <Text style={styles.reasonIntro}>
            Il bot ha aperto questo setup {isLong ? "LONG" : "SHORT"} perché{" "}
            {signal.confirmations.length} criteri tecnici indipendenti convergono
            sullo stesso scenario:
          </Text>
          {signal.confirmations.map((c) => (
            <View key={c} style={styles.reasonBlock}>
              <View style={styles.ctxRow}>
                <Ionicons name="checkmark-circle" size={16} color={colors.brand} />
                <Text style={[styles.ctxText, { fontWeight: "800" }]}>{c}</Text>
              </View>
              <Text style={styles.reasonText}>
                {reasonFor(c, signal, isLong)}
              </Text>
              {c === "FVG Reversal" &&
                signal.reversal_signals &&
                signal.reversal_signals.length > 0 && (
                  <View style={styles.revChips}>
                    {signal.reversal_signals.map((rs) => (
                      <View key={rs} style={styles.revChip}>
                        <Ionicons name="flash" size={9} color={colors.brand} />
                        <Text style={styles.revChipText}>{rs}</Text>
                      </View>
                    ))}
                  </View>
                )}
            </View>
          ))}
          <View style={[styles.ctxRow, { marginTop: 6 }]}>
            <Ionicons name="albums" size={16} color={colors.onSurfaceTertiary} />
            <Text style={styles.ctxText}>
              FVG zone: {formatPrice(signal.fvg_bottom)} —{" "}
              {formatPrice(signal.fvg_top)}
            </Text>
          </View>
          <View style={styles.ctxRow}>
            <Ionicons name="time" size={16} color={colors.onSurfaceTertiary} />
            <Text style={styles.ctxText}>
              {new Date(signal.created_at).toLocaleString()}
            </Text>
          </View>
        </View>

        <View style={styles.disclaimer}>
          <Ionicons name="warning-outline" size={14} color={colors.brand} />
          <Text style={styles.disclaimerText}>
            Solo analisi tecnica — non è consulenza finanziaria.
          </Text>
        </View>
      </ScrollView>

      {/* Sticky execute button */}
      <View style={[styles.executeBar, { paddingBottom: Math.max(insets.bottom, 12) }]}>
        {executeMsg && (
          <Text
            style={[
              styles.executeMsg,
              {
                color: executeMsg.startsWith("Position")
                  ? colors.success
                  : colors.error,
              },
            ]}
          >
            {executeMsg}
          </Text>
        )}
        <Pressable
          onPress={async () => {
            if (!signal || executing) return;
            setExecuting(true);
            setExecuteMsg(null);
            try {
              const res = await fetch(
                `${process.env.EXPO_PUBLIC_BACKEND_URL}/api/paper/execute/${signal.id}`,
                { method: "POST" }
              );
              if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                setExecuteMsg(err.detail || `Failed (${res.status})`);
              } else {
                setExecuteMsg("Position opened in paper portfolio");
              }
            } catch (e: any) {
              setExecuteMsg(e.message || "Network error");
            } finally {
              setExecuting(false);
              setTimeout(() => setExecuteMsg(null), 4000);
            }
          }}
          disabled={executing}
          style={({ pressed }) => [
            styles.executeBtn,
            {
              backgroundColor: isLong ? colors.success : colors.error,
              opacity: pressed ? 0.7 : 1,
            },
          ]}
          testID="paper-execute-button"
        >
          {executing ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <>
              <Ionicons name="flash" size={18} color="#fff" />
              <Text style={styles.executeText}>
                Execute Paper {isLong ? "LONG" : "SHORT"}
              </Text>
            </>
          )}
        </Pressable>
      </View>
    </View>
  );
}

function PriceChart({
  width,
  candles,
  signal,
}: {
  width: number;
  candles: Candle[];
  signal: Signal;
}) {
  if (candles.length === 0) return null;
  const pad = 8;
  const h = CHART_HEIGHT;
  const w = width - spacing.lg * 2;
  const view = candles.slice(-80);
  const highs = view.map((c) => c.h);
  const lows = view.map((c) => c.l);
  const min = Math.min(...lows, signal.stop_loss, signal.fvg_bottom) * 0.998;
  const max = Math.max(...highs, signal.take_profit, signal.fvg_top) * 1.002;
  const range = max - min || 1;
  const scaleY = (v: number) => pad + ((max - v) / range) * (h - pad * 2);
  const barW = Math.max(2, (w - pad * 2) / view.length - 1);

  const fvgY1 = scaleY(signal.fvg_top);
  const fvgY2 = scaleY(signal.fvg_bottom);

  const entryY = scaleY(signal.entry);
  const slY = scaleY(signal.stop_loss);
  const tpY = scaleY(signal.take_profit);

  return (
    <Svg width={w} height={h}>
      {/* grid */}
      {[0.25, 0.5, 0.75].map((f) => (
        <Line
          key={f}
          x1={pad}
          x2={w - pad}
          y1={pad + f * (h - pad * 2)}
          y2={pad + f * (h - pad * 2)}
          stroke={colors.divider}
          strokeWidth={1}
        />
      ))}

      {/* FVG zone */}
      <Rect
        x={pad}
        y={Math.min(fvgY1, fvgY2)}
        width={w - pad * 2}
        height={Math.abs(fvgY2 - fvgY1)}
        fill={
          signal.side === "long" ? "rgba(0,192,118,0.15)" : "rgba(255,85,74,0.15)"
        }
        stroke={signal.side === "long" ? colors.success : colors.error}
        strokeDasharray="3 3"
        strokeWidth={1}
      />

      {/* candles */}
      {view.map((c, i) => {
        const x = pad + i * ((w - pad * 2) / view.length);
        const up = c.c >= c.o;
        const color = up ? colors.success : colors.error;
        const yHigh = scaleY(c.h);
        const yLow = scaleY(c.l);
        const yO = scaleY(c.o);
        const yC = scaleY(c.c);
        return (
          <React.Fragment key={`k${i}`}>
            <Line
              x1={x + barW / 2}
              x2={x + barW / 2}
              y1={yHigh}
              y2={yLow}
              stroke={color}
              strokeWidth={1}
            />
            <Rect
              x={x}
              y={Math.min(yO, yC)}
              width={barW}
              height={Math.max(1, Math.abs(yC - yO))}
              fill={color}
            />
          </React.Fragment>
        );
      })}

      {/* levels */}
      <LevelLine y={entryY} width={w} pad={pad} color={colors.brand} label="ENTRY" />
      <LevelLine y={slY} width={w} pad={pad} color={colors.error} label="SL" />
      <LevelLine y={tpY} width={w} pad={pad} color={colors.success} label="TP" />
    </Svg>
  );
}

function LevelLine({
  y,
  width,
  pad,
  color,
  label,
}: {
  y: number;
  width: number;
  pad: number;
  color: string;
  label: string;
}) {
  return (
    <>
      <Line
        x1={pad}
        x2={width - pad}
        y1={y}
        y2={y}
        stroke={color}
        strokeDasharray="4 4"
        strokeWidth={1.2}
      />
      <SvgText x={width - pad - 30} y={y - 2} fill={color} fontSize={9} fontWeight="700">
        {label}
      </SvgText>
    </>
  );
}

function RsiChart({ width, rsi }: { width: number; rsi: number[] }) {
  if (rsi.length === 0) return null;
  const pad = 8;
  const h = RSI_HEIGHT;
  const w = width - spacing.lg * 2;
  const view = rsi.slice(-80);
  const scaleX = (i: number) => pad + i * ((w - pad * 2) / (view.length - 1 || 1));
  const scaleY = (v: number) => pad + ((100 - v) / 100) * (h - pad * 2);
  let d = "";
  view.forEach((v, i) => {
    const x = scaleX(i);
    const y = scaleY(v);
    d += i === 0 ? `M${x},${y}` : ` L${x},${y}`;
  });
  return (
    <Svg width={w} height={h}>
      <Line
        x1={pad}
        x2={w - pad}
        y1={scaleY(70)}
        y2={scaleY(70)}
        stroke={colors.error}
        strokeDasharray="3 3"
        strokeWidth={1}
      />
      <Line
        x1={pad}
        x2={w - pad}
        y1={scaleY(30)}
        y2={scaleY(30)}
        stroke={colors.success}
        strokeDasharray="3 3"
        strokeWidth={1}
      />
      <Path d={d} stroke={colors.brand} strokeWidth={1.5} fill="none" />
      <SvgText x={4} y={12} fill={colors.onSurfaceTertiary} fontSize={9}>
        RSI 14
      </SvgText>
    </Svg>
  );
}

function Metric({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={[styles.metricValue, { color }]} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

function formatPrice(v: number): string {
  if (v >= 1) return v.toFixed(4);
  if (v >= 0.01) return v.toFixed(5);
  return v.toPrecision(4);
}

function reasonFor(criterion: string, s: Signal, isLong: boolean): string {
  const rsi = s.rsi_value;
  const vol = s.volume_ratio;
  switch (criterion) {
    case "RSI Divergence":
      return isLong
        ? `Il prezzo ha fatto un minimo più basso mentre RSI (${rsi.toFixed(1)}) ha fatto un minimo più alto: il momentum ribassista si sta esaurendo, tipico segnale di inversione al rialzo.`
        : `Il prezzo ha fatto un massimo più alto mentre RSI (${rsi.toFixed(1)}) ha fatto un massimo più basso: la spinta rialzista sta perdendo forza, probabile inversione ribassista.`;
    case "FVG Zone":
      return `Rilevato un Fair Value Gap ${
        isLong ? "rialzista" : "ribassista"
      } ancora aperto tra ${formatPrice(s.fvg_bottom)} e ${formatPrice(
        s.fvg_top
      )}. Il prezzo tende a tornare a testare queste zone: entry al bordo, stop appena oltre.`;
    case "Volume Spike":
      return `Volume attuale ${vol}× la media 20 periodi: c'è partecipazione istituzionale/reale sul movimento, non un fakeout a bassa liquidità.`;
    case "EMA Trend Up":
      return "EMA veloce sopra la lenta: il trend maggiore è rialzista, il long è allineato con la corrente principale del mercato.";
    case "EMA Trend Down":
      return "EMA veloce sotto la lenta: il trend maggiore è ribassista, lo short è allineato con la direzione dominante.";
    case "FVG Reversal":
      return isLong
        ? "Inversione confermata dentro la zona FVG: il prezzo ha respinto il bordo e sta risalendo per colmare il gap. Il target è impostato sul fill della zona."
        : "Inversione confermata dentro la zona FVG: il prezzo ha respinto il bordo e sta scendendo per colmare il gap. Il target è impostato sul fill della zona.";
    case "Market Structure":
      return isLong
        ? "Struttura di mercato rialzista: massimi e minimi crescenti confermano l'uptrend."
        : "Struttura di mercato ribassista: massimi e minimi decrescenti confermano il downtrend.";
    case "Impulse FVG":
      return "Candela d'impulso che ha originato il trend: il suo FVG non colmato è il target finale (TP2).";
    case "Consolidation Breakout":
      return isLong
        ? "Rottura in chiusura sopra il box di consolidamento: trigger d'ingresso long."
        : "Rottura in chiusura sotto il box di consolidamento: trigger d'ingresso short.";
    case "Bullish Engulfing":
    case "Morning Star":
      return "Pattern di reversal rialzista dentro il consolidamento: la spinta ribassista dell'impulso si esaurisce prima della FVG.";
    case "Bearish Engulfing":
    case "Evening Star":
      return "Pattern di reversal ribassista dentro il consolidamento: la spinta rialzista dell'impulso si esaurisce prima della FVG.";
    case "RSI HTF Extreme":
      return isLong
        ? "RSI del timeframe superiore in ipervenduto: contesto favorevole a un rimbalzo."
        : "RSI del timeframe superiore in ipercomprato: contesto favorevole a una discesa.";
    case "RSI Momentum Turn":
      return "RSI sta invertendo la direzione sul timeframe del trade: momentum in cambiamento a favore dell'ingresso.";
    default:
      return "Conferma tecnica aggiuntiva rilevata dall'algoritmo.";
  }
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: {
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
    gap: spacing.md,
  },
  muted: { color: colors.onSurfaceSecondary },
  retryBtn: {
    marginTop: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    backgroundColor: colors.brand,
    borderRadius: radius.md,
  },
  retryText: { color: colors.onBrand, fontWeight: "700" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  pair: { color: colors.onSurface, fontSize: font.lg, fontWeight: "800" },
  subline: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: 2 },
  sideBadge: {
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.sm,
  },
  sideText: { color: "#fff", fontWeight: "800", fontSize: font.sm, letterSpacing: 0.5 },
  body: { padding: spacing.lg, gap: spacing.md, paddingBottom: 120 },
  chartCard: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  rsiCard: { marginTop: spacing.sm },
  gridRow: { flexDirection: "row", gap: spacing.md },
  metric: {
    flex: 1,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: 4,
  },
  metricLabel: {
    color: colors.onSurfaceTertiary,
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  metricValue: { fontSize: font.xl, fontWeight: "800" },
  ctxCard: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  ctxTitle: {
    color: colors.brand,
    fontWeight: "800",
    fontSize: font.sm,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    marginBottom: 4,
  },
  ctxRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  ctxText: { color: colors.onSurface, fontSize: font.sm, flex: 1 },
  reasonIntro: {
    color: colors.onSurfaceSecondary,
    fontSize: font.sm,
    lineHeight: 20,
    marginBottom: 4,
  },
  reasonBlock: {
    gap: 4,
    paddingLeft: 0,
    paddingBottom: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.divider,
  },
  reasonText: {
    color: colors.onSurfaceSecondary,
    fontSize: font.sm,
    lineHeight: 18,
    paddingLeft: 22,
  },
  revChips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    paddingLeft: 22,
    marginTop: 2,
  },
  revChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    paddingHorizontal: 6,
    paddingVertical: 3,
    backgroundColor: colors.brandTertiary,
    borderRadius: radius.sm,
  },
  revChipText: { color: colors.brand, fontSize: 10, fontWeight: "700" },
  disclaimer: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: spacing.sm,
  },
  disclaimerText: { color: colors.onSurfaceSecondary, fontSize: 11, flex: 1 },
  executeBar: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    padding: spacing.md,
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    gap: spacing.sm,
  },
  executeMsg: { fontSize: font.sm, fontWeight: "700", textAlign: "center" },
  executeBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingVertical: 14,
    borderRadius: radius.md,
  },
  executeText: { color: "#fff", fontWeight: "800", fontSize: font.lg, letterSpacing: 0.5 },
});
