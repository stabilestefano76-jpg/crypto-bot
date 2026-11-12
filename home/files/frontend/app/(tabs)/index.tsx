// OGGI-HOME: prima schermata riassuntiva (stato bot, capitale, posizioni aperte, segnali, scorciatoie)
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { api, crusApi, Cruscotto, ScanState, Signal } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const UP = colors.success;
const DOWN = colors.error;
const FILTERS = [
  { k: "all", t: "Tutti" },
  { k: "long", t: "Long" },
  { k: "short", t: "Short" },
] as const;
const TFS = ["Tutti", "15m", "1h", "4h", "1d"] as const;

const usd = (n?: number | null) =>
  n === undefined || n === null || isNaN(n) ? "-" : `${n < 0 ? "-" : n > 0 ? "+" : ""}$${Math.abs(n).toFixed(2)}`;
const usdp = (n?: number | null) =>
  n === undefined || n === null || isNaN(n) ? "-" : `$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
const col = (n?: number | null) => (n === undefined || n === null || n === 0 ? colors.onSurface : n > 0 ? UP : DOWN);

function timeAgo(iso: string): string {
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "adesso";
  if (m < 60) return `${m} min fa`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h fa`;
  return `${Math.floor(h / 24)} g fa`;
}

function formatPrice(v: number): string {
  if (v >= 1) return v.toFixed(4);
  if (v >= 0.01) return v.toFixed(5);
  return v.toPrecision(4);
}

const SHORTCUTS: { t: string; icon: any; route: string }[] = [
  { t: "Cruscotto", icon: "speedometer", route: "/cruscotto" },
  { t: "Report", icon: "document-text", route: "/report" },
  { t: "Analisi e rischio", icon: "shield-checkmark", route: "/desk" },
  { t: "Terminale", icon: "terminal", route: "/terminal" },
  { t: "Strategie", icon: "apps", route: "/strategies" },
  { t: "Academy", icon: "school", route: "/academy" },
];

function Spark({ data }: { data: [number, number][] }) {
  if (!data || data.length < 2) return null;
  const pts = data.slice(-40);
  const vals = pts.map((d) => d[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const c = vals[vals.length - 1] >= vals[0] ? UP : DOWN;
  return (
    <View style={{ height: 40, flexDirection: "row", alignItems: "flex-end", marginTop: spacing.md }}>
      {vals.map((v, i) => (
        <View
          key={i}
          style={{ flex: 1, marginHorizontal: 0.5, height: 4 + ((v - lo) / span) * 36, backgroundColor: c, opacity: 0.3 + 0.7 * (i / vals.length), borderRadius: 1 }}
        />
      ))}
    </View>
  );
}

function Mini({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={s.mini}>
      <Text style={s.miniLabel}>{label}</Text>
      <Text style={[s.miniVal, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

export default function OggiScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [status, setStatus] = useState<ScanState | null>(null);
  const [crus, setCrus] = useState<Cruscotto | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "long" | "short">("all");
  const [tf, setTf] = useState<(typeof TFS)[number]>("Tutti");
  const [scanning, setScanning] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [sg, st] = await Promise.all([api.signals({ status: "active" }), api.status()]);
      setSignals(sg.signals);
      setStatus(st);
    } catch (e: any) {
      setError(e?.message || "Errore di rete");
    }
    try {
      setCrus(await crusApi.get());
    } catch {
      // il Cruscotto non e' indispensabile per questa schermata
    }
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 30_000);
    return () => clearInterval(iv);
  }, [load]);

  const filtered = useMemo(() => {
    let out = signals;
    if (filter !== "all") out = out.filter((x) => x.side === filter);
    if (tf !== "Tutti") out = out.filter((x) => x.timeframe === tf);
    return [...out].sort((a, b) => b.strength - a.strength);
  }, [signals, filter, tf]);

  const onScan = async () => {
    setScanning(true);
    try {
      await api.triggerScan();
      setTimeout(load, 4000);
    } catch {
      // ignoro
    } finally {
      setTimeout(() => setScanning(false), 2000);
    }
  };

  const p = crus?.portfolio;
  const rk = crus?.risk;
  const bots = crus?.bots;
  const watch = useMemo(() => {
    const out: string[] = [];
    (bots?.scan_top || []).forEach((x) => out.push(x));
    (bots?.terminal?.last_setups || []).forEach((x) => out.push(`${x.symbol} ${x.side === "long" ? "↑" : "↓"}`));
    return out.slice(0, 12);
  }, [bots]);

  const scanTxt = status?.is_scanning
    ? "Scansione in corso..."
    : status?.last_scan_at
    ? `Ultima scansione ${timeAgo(status.last_scan_at)}`
    : "In attesa della prima scansione";

  return (
    <View style={[s.root, { paddingTop: insets.top }]}>
      <View style={s.header}>
        <View style={{ flex: 1 }}>
          <Text style={s.title} testID="app-title">Oggi</Text>
          <View style={s.statusRow}>
            <View style={[s.dot, { backgroundColor: error ? DOWN : status?.is_scanning ? colors.brand : UP }]} />
            <Text style={s.statusText} testID="scan-status">{error ? "Nessun collegamento" : scanTxt}</Text>
          </View>
        </View>
        <Pressable onPress={onScan} disabled={scanning} style={({ pressed }) => [s.scanBtn, pressed && { opacity: 0.6 }]} testID="scan-now-button">
          {scanning ? <ActivityIndicator color={colors.onBrand} size="small" /> : <Ionicons name="scan" size={16} color={colors.onBrand} />}
          <Text style={s.scanBtnText}>Scansiona</Text>
        </Pressable>
      </View>

      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={colors.brand} />}
      >
        {loading ? (
          <View style={s.center}><ActivityIndicator color={colors.brand} /></View>
        ) : (
          <>
            {error && (
              <View style={s.card}>
                <Text style={s.cardTitle}>Non riesco a collegarmi al bot</Text>
                <Text style={s.muted}>{error}</Text>
                <Pressable onPress={load} style={s.retryBtn} testID="retry-button"><Text style={s.retryText}>Riprova</Text></Pressable>
              </View>
            )}

            {/* Capitale */}
            {p && (
              <Pressable onPress={() => router.push("/cruscotto" as any)} style={s.card} testID="home-capital">
                <View style={s.rowBetween}>
                  <Text style={s.cardLabel}>CAPITALE SIMULATO</Text>
                  <Ionicons name="chevron-forward" size={16} color={colors.onSurfaceTertiary} />
                </View>
                <View style={[s.rowBetween, { alignItems: "flex-end", marginTop: 4 }]}>
                  <Text style={s.big}>{usdp(p.equity)}</Text>
                  <View style={{ alignItems: "flex-end" }}>
                    <Text style={[s.todayVal, { color: col(p.today_pnl) }]}>{usd(p.today_pnl)}</Text>
                    <Text style={s.miniLabel}>oggi</Text>
                  </View>
                </View>
                <Spark data={p.curve} />
                <View style={[s.rowBetween, { marginTop: spacing.md }]}>
                  <Mini label="Rendimento" value={p.return_pct === null ? "-" : `${p.return_pct > 0 ? "+" : ""}${p.return_pct.toFixed(1)}%`} color={col(p.return_pct)} />
                  <Mini label="Aperte ora" value={usd(p.floating)} color={col(p.floating)} />
                  <Mini label="Chiuse" value={usd(p.realized)} color={col(p.realized)} />
                </View>
              </Pressable>
            )}

            {/* Stato rischio */}
            {rk && (
              <View style={s.rowGap}>
                <View style={[s.card, s.third]}>
                  <Text style={s.cardLabel}>POSIZIONI</Text>
                  <Text style={s.mid}>{rk.open_n}/{rk.max_open}</Text>
                </View>
                <View style={[s.card, s.third]}>
                  <Text style={s.cardLabel}>OPERAZIONI OGGI</Text>
                  <Text style={s.mid}>{rk.auto_today}/{rk.max_trades_day}</Text>
                </View>
                <View style={[s.card, s.third]}>
                  <Text style={s.cardLabel}>RISCHIO APERTO</Text>
                  <Text style={s.mid}>{usdp(rk.open_risk)}</Text>
                </View>
              </View>
            )}
            {rk?.pause ? (
              <View style={[s.card, { borderColor: colors.warning }]}>
                <Text style={[s.cardTitle, { color: colors.warning }]}>Bot in pausa</Text>
                <Text style={s.muted}>{rk.pause}</Text>
              </View>
            ) : null}

            {/* Posizioni aperte */}
            {crus && (
              <View>
                <Text style={s.section}>Posizioni aperte ({crus.positions.length})</Text>
                {crus.positions.length === 0 ? (
                  <View style={s.card}><Text style={s.muted}>Nessuna posizione aperta in questo momento.</Text></View>
                ) : (
                  crus.positions.map((x) => {
                    const isL = x.side === "long";
                    const nowAt = Math.max(0, Math.min(100, x.progress ?? 50));
                    return (
                      <Pressable key={x.id} onPress={() => router.push("/cruscotto" as any)} style={s.card}>
                        <View style={s.rowBetween}>
                          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                            <View style={[s.badge, { backgroundColor: isL ? UP : DOWN }]}><Text style={s.badgeTxt}>{isL ? "LONG" : "SHORT"}</Text></View>
                            <Text style={s.pair}>{x.symbol}</Text>
                            {x.auto && <Text style={s.miniLabel}>BOT</Text>}
                          </View>
                          <Text style={[s.pair, { color: col(x.pnl) }]}>{usd(x.pnl)}</Text>
                        </View>
                        <View style={s.pbar}><View style={{ width: `${nowAt}%`, height: 6, backgroundColor: x.pnl >= 0 ? UP : DOWN, borderRadius: 3 }} /></View>
                        <View style={s.rowBetween}>
                          <Text style={[s.miniLabel, { color: DOWN }]}>stop -{x.to_stop_pct}%</Text>
                          <Text style={[s.miniLabel, { color: UP }]}>obiettivo +{x.to_target_pct}%</Text>
                        </View>
                      </Pressable>
                    );
                  })
                )}
              </View>
            )}

            {/* Segnali */}
            <View>
              <Text style={s.section}>Segnali attivi ({signals.length})</Text>
              {signals.length > 0 && (
                <View style={{ marginBottom: spacing.sm }}>
                  <View style={s.segmented} testID="side-filter">
                    {FILTERS.map((f) => {
                      const active = filter === f.k;
                      return (
                        <Pressable key={f.k} onPress={() => setFilter(f.k)} style={[s.segItem, active && s.segItemActive]} testID={`filter-${f.k}`}>
                          <Text style={[s.segText, active && s.segTextActive]}>{f.t}</Text>
                        </Pressable>
                      );
                    })}
                  </View>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }} testID="timeframe-chips">
                    {TFS.map((t) => {
                      const active = tf === t;
                      return (
                        <Pressable key={t} onPress={() => setTf(t)} style={[s.chip, active && s.chipActive]} testID={`chip-tf-${t}`}>
                          <Text style={[s.chipText, active && s.chipTextActive]}>{t}</Text>
                        </Pressable>
                      );
                    })}
                  </ScrollView>
                </View>
              )}
              {filtered.length === 0 ? (
                <View style={s.card} testID="empty-state">
                  <Text style={s.cardTitle}>{signals.length === 0 ? "Nessun segnale in questo momento" : "Nessun segnale con questi filtri"}</Text>
                  <Text style={s.muted}>
                    {signals.length === 0
                      ? "Il bot controlla il mercato di continuo: quando le condizioni sono giuste, il segnale compare qui."
                      : "Prova a cambiare filtro."}
                  </Text>
                  {signals.length === 0 && watch.length > 0 && (
                    <>
                      <Text style={[s.cardLabel, { marginTop: spacing.md }]}>MONETE IN OSSERVAZIONE</Text>
                      <View style={s.chipsWrap}>
                        {watch.map((w, i) => (
                          <View key={i} style={s.watchChip}><Text style={s.watchText}>{w}</Text></View>
                        ))}
                      </View>
                    </>
                  )}
                </View>
              ) : (
                filtered.map((sg) => (
                  <SignalCard
                    key={sg.id}
                    signal={sg}
                    onPress={() => router.push({ pathname: "/detail/[id]", params: { id: sg.id } })}
                    onExecute={async () => {
                      try { await api.paperExecute(sg.id); return "ok"; } catch (e: any) { return e?.message || "Errore"; }
                    }}
                  />
                ))
              )}
            </View>

            {/* Scorciatoie */}
            <View>
              <Text style={s.section}>Vai a</Text>
              <View style={s.grid} testID="header-actions">
                {SHORTCUTS.map((x) => (
                  <Pressable key={x.t} onPress={() => router.push(x.route as any)} style={({ pressed }) => [s.tile, pressed && { opacity: 0.6 }]}>
                    <Ionicons name={x.icon} size={22} color={colors.brand} />
                    <Text style={s.tileText} numberOfLines={2}>{x.t}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

const STRATEGY_LABELS: Record<string, string> = {
  counter_trend: "Rev Pre-FVG",
  fvg_reversal: "FVG Reversal",
  rsi_reversion: "RSI Reversion",
};
const STRATEGY_COLORS: Record<string, string> = {
  counter_trend: "#3B82F6",
  fvg_reversal: "#A855F7",
  rsi_reversion: "#F59E0B",
};

function SignalCard({ signal, onPress, onExecute }: { signal: Signal; onPress: () => void; onExecute: () => Promise<string> }) {
  const isLong = signal.side === "long";
  const [executing, setExecuting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const doExecute = async () => {
    if (executing) return;
    setExecuting(true);
    setMsg(null);
    const res = await onExecute();
    setMsg(res === "ok" ? "Aperta in simulazione" : res);
    setTimeout(() => setMsg(null), 3000);
    setExecuting(false);
  };
  const stratLabel = signal.strategy ? STRATEGY_LABELS[signal.strategy] : undefined;
  const stratColor = signal.strategy ? STRATEGY_COLORS[signal.strategy] : colors.brand;
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [s.card, pressed && { opacity: 0.7 }]} testID={`signal-card-${signal.id}`}>
      <View style={s.rowBetween}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: spacing.sm }}>
          <Text style={s.pair}>{signal.symbol}</Text>
          <View style={s.tfTag}><Text style={s.tfTagText}>{signal.timeframe}</Text></View>
          {stratLabel && (
            <View style={[s.stratTag, { borderColor: stratColor }]}>
              <Text style={[s.stratTagText, { color: stratColor }]}>{stratLabel}</Text>
            </View>
          )}
        </View>
        <View style={[s.badge, { backgroundColor: isLong ? UP : DOWN }]}>
          <Ionicons name={isLong ? "arrow-up" : "arrow-down"} size={12} color="#fff" />
          <Text style={s.badgeTxt}>{isLong ? "LONG" : "SHORT"}</Text>
        </View>
      </View>
      <View style={[s.rowBetween, { marginTop: 8 }]}>
        <PriceCol label="Entrata" value={signal.entry} color={colors.brand} />
        <PriceCol label="Stop" value={signal.stop_loss} color={DOWN} />
        <PriceCol label="Obiettivo" value={signal.take_profit} color={UP} />
        <View style={{ alignItems: "flex-end" }}>
          <Text style={s.priceLabel}>R:R</Text>
          <Text style={s.rr}>1:{signal.rr_ratio}</Text>
        </View>
      </View>
      <View style={s.confs}>
        {signal.confirmations.map((c) => (
          <View key={c} style={s.confChip}>
            <Ionicons name="checkmark" size={10} color={colors.brand} />
            <Text style={s.confText}>{c}</Text>
          </View>
        ))}
        <View style={s.strength}>
          <Ionicons name="star" size={9} color={colors.brand} />
          <Text style={s.strengthText}>{signal.score ?? signal.strength}/{signal.max_score ?? signal.strength}</Text>
        </View>
      </View>
      <View style={[s.rowBetween, { marginTop: 8 }]}>
        <Text style={s.miniLabel}>{timeAgo(signal.created_at)}</Text>
        <Pressable onPress={doExecute} disabled={executing} hitSlop={8} style={({ pressed }) => [s.execBtn, { backgroundColor: isLong ? UP : DOWN }, (pressed || executing) && { opacity: 0.7 }]} testID={`execute-${signal.id}`}>
          {executing ? <ActivityIndicator color="#fff" size="small" /> : (<><Ionicons name="flash" size={12} color="#fff" /><Text style={s.execText}>Esegui</Text></>)}
        </Pressable>
      </View>
      {msg && <Text style={[s.execMsg, { color: msg === "Aperta in simulazione" ? UP : DOWN }]}>{msg}</Text>}
    </Pressable>
  );
}

function PriceCol({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <View style={{ flex: 1 }}>
      <Text style={s.priceLabel}>{label}</Text>
      <Text style={[s.priceValue, { color }]} numberOfLines={1}>{formatPrice(value)}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md,
    borderBottomWidth: 1, borderBottomColor: colors.border, backgroundColor: colors.surface,
  },
  title: { fontSize: 26, fontWeight: "800", color: colors.onSurface },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 2 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  statusText: { color: colors.onSurfaceSecondary, fontSize: font.sm },
  scanBtn: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.brand, paddingHorizontal: spacing.md, paddingVertical: 10, borderRadius: radius.md },
  scanBtnText: { color: colors.onBrand, fontWeight: "700", fontSize: font.sm },
  body: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  center: { padding: spacing.xxl, alignItems: "center" },
  card: { backgroundColor: colors.surfaceSecondary, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md, marginBottom: spacing.sm },
  cardLabel: { color: colors.onSurfaceTertiary, fontSize: 10, fontWeight: "700", letterSpacing: 0.6 },
  cardTitle: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700", marginBottom: 4 },
  muted: { color: colors.onSurfaceSecondary, fontSize: font.base, lineHeight: 20 },
  big: { color: colors.onSurface, fontSize: 32, fontWeight: "800" },
  mid: { color: colors.onSurface, fontSize: font.xl, fontWeight: "800", marginTop: 4 },
  todayVal: { fontSize: font.xl, fontWeight: "800" },
  rowBetween: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  rowGap: { flexDirection: "row", gap: spacing.sm },
  third: { flex: 1, marginBottom: 0 },
  mini: { flex: 1 },
  miniLabel: { color: colors.onSurfaceTertiary, fontSize: 11 },
  miniVal: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700", marginTop: 2 },
  section: { color: colors.onSurface, fontSize: font.lg, fontWeight: "800", marginTop: spacing.sm, marginBottom: spacing.sm },
  badge: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: spacing.sm, paddingVertical: 4, borderRadius: radius.sm },
  badgeTxt: { color: "#fff", fontWeight: "800", fontSize: 11, letterSpacing: 0.5 },
  pair: { color: colors.onSurface, fontSize: font.lg, fontWeight: "800" },
  pbar: { height: 6, backgroundColor: colors.surfaceTertiary, borderRadius: 3, marginVertical: 8, overflow: "hidden" },
  segmented: { flexDirection: "row", backgroundColor: colors.surfaceSecondary, borderRadius: radius.md, padding: 3, marginBottom: spacing.sm },
  segItem: { flex: 1, paddingVertical: 8, alignItems: "center", borderRadius: radius.sm },
  segItemActive: { backgroundColor: colors.brand },
  segText: { color: colors.onSurfaceSecondary, fontWeight: "600", fontSize: font.sm },
  segTextActive: { color: colors.onBrand },
  chip: { height: 32, minWidth: 44, paddingHorizontal: spacing.md, borderRadius: radius.pill, backgroundColor: colors.surfaceSecondary, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  chipActive: { borderColor: colors.brand, backgroundColor: colors.brandTertiary },
  chipText: { color: colors.onSurfaceSecondary, fontWeight: "600", fontSize: font.sm },
  chipTextActive: { color: colors.brand },
  chipsWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  watchChip: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: colors.surfaceTertiary },
  watchText: { color: colors.onSurface, fontSize: font.sm, fontWeight: "600" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  tile: { width: "31.5%", flexGrow: 1, minHeight: 78, backgroundColor: colors.surfaceSecondary, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center", gap: 6, padding: spacing.sm },
  tileText: { color: colors.onSurface, fontSize: font.sm, fontWeight: "600", textAlign: "center" },
  retryBtn: { marginTop: spacing.md, alignSelf: "flex-start", paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, backgroundColor: colors.brand, borderRadius: radius.md },
  retryText: { color: colors.onBrand, fontWeight: "700" },
  tfTag: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: radius.sm, backgroundColor: colors.surfaceTertiary },
  tfTagText: { color: colors.onSurfaceSecondary, fontSize: 11, fontWeight: "600" },
  stratTag: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: radius.sm, borderWidth: 1 },
  stratTagText: { fontSize: 10, fontWeight: "700" },
  priceLabel: { color: colors.onSurfaceTertiary, fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5 },
  priceValue: { fontSize: font.base, fontWeight: "700", marginTop: 2 },
  rr: { color: colors.brand, fontWeight: "800", fontSize: font.base, marginTop: 2 },
  confs: { flexDirection: "row", flexWrap: "wrap", gap: 6, alignItems: "center", marginTop: 8 },
  confChip: { flexDirection: "row", alignItems: "center", gap: 2, paddingHorizontal: 6, paddingVertical: 3, backgroundColor: colors.brandTertiary, borderRadius: radius.sm },
  confText: { color: colors.brand, fontSize: 10, fontWeight: "700" },
  strength: { marginLeft: "auto", flexDirection: "row", alignItems: "center", gap: 3, paddingHorizontal: 6, paddingVertical: 2, borderRadius: radius.sm, backgroundColor: colors.surfaceTertiary },
  strengthText: { color: colors.brand, fontWeight: "800", fontSize: 11 },
  execBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: spacing.md, paddingVertical: 6, borderRadius: radius.sm },
  execText: { color: "#fff", fontWeight: "800", fontSize: font.sm, letterSpacing: 0.4 },
  execMsg: { fontSize: 11, fontWeight: "700", marginTop: 4, textAlign: "right" },
});
