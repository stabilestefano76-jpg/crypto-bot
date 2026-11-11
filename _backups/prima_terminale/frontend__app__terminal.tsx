import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, LayoutChangeEvent, Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { termApi, TermAnalysis, TermChart, TermLab, TermPair } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const BG = "#0B0E11";
const PANEL = "#12161C";
const GRID = "#1E2329";
const TXT = "#EAECEF";
const DIM = "#848E9C";
const UP = "#0ECB81";
const DN = "#F6465D";
const TFS = ["15m", "1h", "4h", "1d"];

const px = (n?: number | null) => {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 1000 ? 1 : n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
};
const big = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "K" : n.toFixed(2));
const pct = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`);
const nm = (s: string) => s.replace("USDC", "/USDC");

function Chart({ d, an, width }: { d: TermChart; an: TermAnalysis | null; width: number }) {
  const AX = 64, H = 220, VH = 46, RH = 64;
  const cs = d.candles;
  if (!cs.length || width < 100) return null;
  const W = width - AX;
  const bw = W / cs.length;
  const lines: { y: number; c: string; label: string }[] = [];
  const mine = an && an.symbol === d.symbol && an.verdict !== "PASSA" ? an : null;
  const extra: number[] = [];
  if (mine) extra.push(mine.stop ?? 0, mine.target ?? 0);
  if (d.position) extra.push(d.position.stop, d.position.target);
  const hi0 = Math.max(...cs.map((c) => c.h), ...extra.filter((x) => x > 0));
  const lo0 = Math.min(...cs.map((c) => c.l), ...extra.filter((x) => x > 0));
  const pad = (hi0 - lo0) * 0.04 || 1;
  const hi = hi0 + pad, lo = lo0 - pad;
  const Y = (p: number) => ((hi - p) / (hi - lo)) * H;
  if (mine) {
    lines.push({ y: Y(mine.stop ?? 0), c: DN, label: "STOP" }, { y: Y(mine.target ?? 0), c: UP, label: "OBIETTIVO" });
  }
  if (d.position) {
    lines.push({ y: Y(d.position.stop), c: DN, label: "stop pos." }, { y: Y(d.position.target), c: UP, label: "obiett. pos." });
  }
  const vmax = Math.max(...cs.map((c) => c.v)) || 1;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => hi - f * (hi - lo));
  return (
    <View>
      <View style={{ height: H, width, backgroundColor: BG }}>
        {ticks.map((p, i) => (
          <View key={i} style={{ position: "absolute", left: 0, top: Y(p), width: W, height: 1, backgroundColor: GRID }}>
            <Text style={[s.axis, { left: W + 4, top: -7 }]}>{px(p)}</Text>
          </View>
        ))}
        {cs.map((c, i) => {
          const up = c.c >= c.o;
          const x = i * bw;
          const top = Y(Math.max(c.o, c.c)), bot = Y(Math.min(c.o, c.c));
          return (
            <View key={i} style={{ position: "absolute", left: x, top: 0, width: bw, height: H }}>
              <View style={{ position: "absolute", left: bw / 2 - 0.5, top: Y(c.h), width: 1, height: Math.max(1, Y(c.l) - Y(c.h)), backgroundColor: up ? UP : DN }} />
              <View style={{ position: "absolute", left: Math.max(0, bw * 0.15), top, width: Math.max(1, bw * 0.7), height: Math.max(1, bot - top), backgroundColor: up ? UP : DN }} />
            </View>
          );
        })}
        <View style={{ position: "absolute", left: 0, top: Y(cs[cs.length - 1].c), width: W, height: 1, backgroundColor: DIM, opacity: 0.6 }} />
        {mine && mine.zone_from && mine.zone_to ? (
          <View style={{ position: "absolute", left: 0, width: W, top: Y(mine.zone_to), height: Math.max(2, Y(mine.zone_from) - Y(mine.zone_to)), backgroundColor: "#F0B90B", opacity: 0.25 }} />
        ) : null}
        {lines.map((l, i) => (
          <View key={i} style={{ position: "absolute", left: 0, top: l.y, width: W, height: 1, backgroundColor: l.c }}>
            <Text style={[s.axis, { left: 4, top: -13, color: l.c }]}>{l.label}</Text>
          </View>
        ))}
      </View>
      <View style={{ height: VH, width, backgroundColor: BG, borderTopWidth: 1, borderColor: GRID }}>
        <Text style={[s.axis, { left: 4, top: 1 }]}>Volume</Text>
        {cs.map((c, i) => {
          const h = Math.max(1, (c.v / vmax) * (VH - 6));
          return <View key={i} style={{ position: "absolute", left: i * bw + bw * 0.15, bottom: 0, width: Math.max(1, bw * 0.7), height: h, backgroundColor: c.c >= c.o ? UP : DN, opacity: 0.6 }} />;
        })}
      </View>
      <View style={{ height: RH, width, backgroundColor: BG, borderTopWidth: 1, borderColor: GRID }}>
        <Text style={[s.axis, { left: 4, top: 1 }]}>RSI 14: {d.rsi.length && d.rsi[d.rsi.length - 1] != null ? (d.rsi[d.rsi.length - 1] as number).toFixed(1) : "-"}</Text>
        {[30, 70].map((v) => (
          <View key={v} style={{ position: "absolute", left: 0, width: W, top: ((100 - v) / 100) * (RH - 4) + 2, height: 1, backgroundColor: GRID }}>
            <Text style={[s.axis, { left: W + 4, top: -7 }]}>{v}</Text>
          </View>
        ))}
        {d.rsi.map((r, i) => (r == null ? null : <View key={i} style={{ position: "absolute", left: i * bw + bw / 2 - 1.5, top: ((100 - r) / 100) * (RH - 4) + 2 - 1.5, width: 3, height: 3, borderRadius: 2, backgroundColor: "#B28DFF" }} />))}
      </View>
    </View>
  );
}

function Book({ d }: { d: TermChart }) {
  const bids = d.book.bids.slice(0, 8), asks = d.book.asks.slice(0, 8);
  const mx = Math.max(1e-12, ...bids.map((b) => b[1]), ...asks.map((a) => a[1]));
  const Col = ({ rows, c, title }: { rows: number[][]; c: string; title: string }) => (
    <View style={{ flex: 1 }}>
      <Text style={[s.axis, { position: "relative", marginBottom: 2 }]}>{title}</Text>
      {rows.map((r, i) => (
        <View key={i} style={{ height: 20, justifyContent: "center" }}>
          <View style={{ position: "absolute", right: 0, top: 1, bottom: 1, width: `${Math.max(3, (r[1] / mx) * 100)}%`, backgroundColor: c, opacity: 0.18 }} />
          <View style={{ flexDirection: "row", justifyContent: "space-between", paddingHorizontal: 4 }}>
            <Text style={{ color: c, fontSize: 12 }}>{px(r[0])}</Text>
            <Text style={{ color: TXT, fontSize: 12 }}>{r[1] >= 100 ? r[1].toFixed(0) : r[1].toPrecision(3)}</Text>
          </View>
        </View>
      ))}
    </View>
  );
  return (
    <View style={{ flexDirection: "row", gap: 8, backgroundColor: PANEL, padding: 8, borderRadius: 8, marginTop: spacing.md }}>
      <Col rows={bids} c={UP} title="Acquisto (prezzo · quantita')" />
      <Col rows={asks} c={DN} title="Vendita (prezzo · quantita')" />
    </View>
  );
}

export default function TerminalScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [pairs, setPairs] = useState<TermPair[]>([]);
  const [sym, setSym] = useState("BTCUSDC");
  const [tf, setTf] = useState("1h");
  const [chart, setChart] = useState<TermChart | null>(null);
  const [an, setAn] = useState<TermAnalysis | null>(null);
  const [lab, setLab] = useState<TermLab | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [w, setW] = useState(0);

  const loadPairs = useCallback(async () => {
    try {
      const r = await termApi.pairs();
      setPairs(r.pairs);
      setSym((c) => (r.pairs.length && !r.pairs.some((p) => p.symbol === c) ? r.pairs[0].symbol : c));
    } catch (e: any) { setMsg(String(e?.message ?? e)); }
  }, []);
  const loadChart = useCallback(async () => {
    try { setChart(await termApi.chart(sym, tf)); setMsg((m) => (m.startsWith("Grafico") ? "" : m)); }
    catch (e: any) { setMsg("Grafico: " + String(e?.message ?? e)); }
  }, [sym, tf]);
  const loadLab = useCallback(async () => { try { setLab(await termApi.lab()); } catch { /* ignore */ } }, []);

  useEffect(() => { loadPairs(); loadLab(); const t = setInterval(() => { loadPairs(); loadLab(); }, 20000); return () => clearInterval(t); }, [loadPairs, loadLab]);
  useEffect(() => { setChart(null); loadChart(); const t = setInterval(loadChart, 8000); return () => clearInterval(t); }, [loadChart]);

  const analyze = async () => {
    setBusy(true); setMsg(""); setAn(null);
    try { setAn(await termApi.analyze(sym)); } catch (e: any) { setMsg("ERRORE: " + String(e?.message ?? e)); }
    setBusy(false);
  };
  const open = async () => {
    if (!an?.setup_id) return;
    setBusy(true); setMsg("");
    try { await termApi.open(an.setup_id); setMsg("Aperta in simulazione. La trovi in 'Analisi e rischio'."); setAn(null); loadChart(); }
    catch (e: any) { setMsg(String(e?.message ?? e)); }
    setBusy(false);
  };
  const setCfg = async (c: Record<string, unknown>) => {
    try { await termApi.setConfig(c); loadLab(); } catch (e: any) { setMsg(String(e?.message ?? e)); }
  };

  const tk = chart?.ticker ?? {};
  const chg = tk.change ?? 0;
  const cur = pairs.find((p) => p.symbol === sym);
  const lastPx = tk.last || cur?.last;
  const verdictColor = an?.verdict === "LONG" ? UP : an?.verdict === "SHORT" ? DN : DIM;

  return (
    <View style={[s.root, { paddingTop: insets.top }]}>
      <View style={s.header}>
        <Pressable onPress={() => router.back()} hitSlop={10}><Ionicons name="chevron-back" size={24} color={TXT} /></Pressable>
        <Text style={s.title}>Terminale</Text>
        <View style={{ width: 24 }} />
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: insets.bottom + 40 }} onLayout={(e: LayoutChangeEvent) => setW(e.nativeEvent.layout.width - spacing.md * 2)}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: spacing.sm }}>
          {pairs.map((p) => (
            <Pressable key={p.symbol} onPress={() => { setSym(p.symbol); setAn(null); setMsg(""); }} style={[s.pair, p.symbol === sym && { borderColor: "#F0B90B" }]}>
              <Text style={{ color: TXT, fontWeight: "700", fontSize: 13 }}>{p.symbol.replace("USDC", "")}</Text>
              <Text style={{ color: p.change >= 0 ? UP : DN, fontSize: 11 }}>{pct(p.change)}</Text>
            </Pressable>
          ))}
        </ScrollView>

        <View style={s.stats}>
          <View style={{ flex: 1.3 }}>
            <Text style={{ color: TXT, fontSize: 18, fontWeight: "800" }}>{nm(sym)}</Text>
            <Text style={{ color: chg >= 0 ? UP : DN, fontSize: 22, fontWeight: "800" }}>{px(lastPx)}</Text>
            <Text style={{ color: chg >= 0 ? UP : DN, fontSize: 12 }}>{pct(chg)} in 24 ore</Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={s.dim}>Massimo 24h</Text><Text style={s.val}>{px(tk.high)}</Text>
            <Text style={s.dim}>Minimo 24h</Text><Text style={s.val}>{px(tk.low)}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <Text style={s.dim}>Scambiato 24h</Text><Text style={s.val}>{big(tk.turnover)}</Text>
          </View>
        </View>

        <View style={{ flexDirection: "row", marginVertical: spacing.sm }}>
          {TFS.map((t) => (
            <Pressable key={t} onPress={() => setTf(t)} style={[s.tf, t === tf && { backgroundColor: GRID }]}>
              <Text style={{ color: t === tf ? "#F0B90B" : DIM, fontWeight: "700", fontSize: 13 }}>{t}</Text>
            </Pressable>
          ))}
        </View>

        {chart ? <Chart d={chart} an={an} width={w} /> : <ActivityIndicator color={colors.brand} style={{ marginVertical: 60 }} />}
        {chart ? <Book d={chart} /> : null}

        {chart?.position ? (
          <Text style={[s.note, { color: "#F0B90B" }]}>Hai una posizione {chart.position.side.toUpperCase()} aperta su questa coppia (stop {px(chart.position.stop)}, obiettivo {px(chart.position.target)}).</Text>
        ) : null}
        {msg ? <Text style={[s.note, { color: "#F0B90B" }]}>{msg}</Text> : null}

        <Pressable onPress={analyze} disabled={busy} style={[s.btn, { backgroundColor: "#F0B90B", opacity: busy ? 0.6 : 1 }]}>
          {busy ? <ActivityIndicator color="#000" /> : <Text style={s.btnText}>ANALIZZA {sym.replace("USDC", "")}</Text>}
        </Pressable>

        {an ? (
          <View style={[s.card, { borderColor: verdictColor }]}>
            <Text style={{ color: verdictColor, fontSize: 22, fontWeight: "800" }}>
              {an.verdict === "PASSA" ? "PASSA: nessun setup" : an.verdict === "LONG" ? "APRI LONG" : "APRI SHORT (solo simulazione)"}
            </Text>
            {an.verdict !== "PASSA" && an.plan ? (
              <>
                <Text style={s.line}>Da {px(an.zone_from)} a {px(an.zone_to)}</Text>
                <Text style={s.line}>Stop {px(an.stop)}  ·  Obiettivo {px(an.target)}  ·  R/R {an.plan.rr ? an.plan.rr.toFixed(1) : "-"}</Text>
                <Text style={s.line}>Importo {an.plan.notional.toFixed(2)} USDC  ·  perdita massima {an.plan.max_loss.toFixed(2)} USDC</Text>
                <Text style={[s.line, { color: an.plan.checks.ok ? UP : "#F0B90B" }]}>
                  Controlli prima di entrare: {an.plan.checks.ok ? "OK" : "NON passano - " + an.plan.checks.reasons.join("; ")}
                </Text>
                {an.plan.warnings.map((x, i) => <Text key={i} style={s.note}>⚠ {x}</Text>)}
                {an.explain ? <Text style={s.note}>{an.explain}</Text> : null}
                <Pressable onPress={open} disabled={busy} style={[s.btn, { backgroundColor: an.verdict === "LONG" ? UP : DN }]}>
                  <Text style={[s.btnText, { color: "#fff" }]}>APRI IN SIMULAZIONE</Text>
                </Pressable>
              </>
            ) : null}
            {an.reasons.map((x, i) => <Text key={i} style={s.note}>• {x}</Text>)}
            {an.lab ? <Text style={s.note}>Archivio: {an.lab.tested} strategie provate, {an.lab.qualified} con vantaggio sufficiente (rigore {an.rigor}).</Text> : null}
          </View>
        ) : null}

        <View style={[s.card, { borderColor: GRID }]}>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
            <View style={{ flex: 1, paddingRight: 12 }}>
              <Text style={{ color: TXT, fontWeight: "700", fontSize: 15 }}>Analizza e apri in automatico</Text>
              <Text style={s.note}>Ogni {lab?.config.auto_every_min ?? 15} minuti analizza le tue coppie e apre da solo (solo simulazione), con gli stessi limiti di rischio di "Analisi e rischio".</Text>
            </View>
            <Switch value={!!lab?.config.auto_enabled} onValueChange={(v) => setCfg({ auto_enabled: v })} />
          </View>
          <Text style={[s.dim, { marginTop: spacing.md }]}>Rigore nella scelta delle strategie</Text>
          <View style={{ flexDirection: "row", marginTop: 4 }}>
            {[["normale", "Normale"], ["severo", "Severo"]].map(([k, l]) => (
              <Pressable key={k} onPress={() => setCfg({ rigor: k })} style={[s.tf, lab?.rigor === k && { backgroundColor: GRID }]}>
                <Text style={{ color: lab?.rigor === k ? "#F0B90B" : DIM, fontWeight: "700", fontSize: 13 }}>{l}</Text>
              </Pressable>
            ))}
          </View>
          <Text style={s.note}>Normale: almeno 60 operazioni passate e t≥2. Severo: almeno 100 e t≥3. In entrambi i casi serve guadagno positivo in entrambe le meta' e nell'ultimo terzo del periodo.</Text>
        </View>

        <View style={[s.card, { borderColor: GRID }]}>
          <Text style={{ color: TXT, fontWeight: "700", fontSize: 15 }}>Archivio strategie</Text>
          {lab?.tested === undefined ? (
            <Text style={s.note}>{lab?.running ? "Calcolo in corso (qualche minuto)..." : "Non ancora calcolato."}{lab?.error ? " Errore: " + lab.error : ""}</Text>
          ) : (
            <>
              <Text style={s.note}>{lab.tested} varianti provate su {lab.symbols?.length ?? 0} monete con costo {lab.fee_side}% per lato. {lab.qualified} qualificate. Aggiornato: {lab.at ? new Date(lab.at).toLocaleString() : "-"}</Text>
              {(lab.top ?? []).slice(0, 6).map((r) => (
                <View key={r.id + r.tf} style={{ marginTop: spacing.xs }}>
                  <Text style={{ color: r.qualified ? UP : TXT, fontSize: 13, fontWeight: "600" }}>{r.qualified ? "✓ " : ""}{r.name} · {r.tf} · {r.side}</Text>
                  <Text style={s.dim}>{r.n} operazioni · {r.net >= 0 ? "+" : ""}{r.net.toFixed(2)}R netto · t {r.t.toFixed(1)} · metà {r.h1.toFixed(2)} / {r.h2.toFixed(2)}</Text>
                </View>
              ))}
            </>
          )}
          <Pressable onPress={async () => { await termApi.refreshLab(); loadLab(); }} style={[s.btn, { backgroundColor: GRID }]}>
            <Text style={[s.btnText, { color: TXT }]}>{lab?.running ? "Calcolo in corso..." : "Aggiorna archivio ora"}</Text>
          </Pressable>
        </View>
        <Text style={s.note}>Solo simulazione: nessun ordine va a Bybit. Gli short non esistono in spot su Bybit UE. L'analisi e' basata su regole e statistica del passato, non e' una garanzia.</Text>
      </ScrollView>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: BG },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  title: { color: TXT, fontSize: font.lg, fontWeight: "700" },
  pair: { backgroundColor: PANEL, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 6, marginRight: 8, borderWidth: 1, borderColor: GRID, alignItems: "center" },
  stats: { flexDirection: "row", backgroundColor: PANEL, borderRadius: 8, padding: 10 },
  dim: { color: DIM, fontSize: 11 },
  val: { color: TXT, fontSize: 13, fontWeight: "600", marginBottom: 4 },
  tf: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 6, marginRight: 6 },
  axis: { position: "absolute", color: DIM, fontSize: 10 },
  btn: { borderRadius: radius.lg, padding: spacing.lg, alignItems: "center", marginTop: spacing.md },
  btnText: { color: "#000", fontWeight: "800", fontSize: font.base },
  card: { backgroundColor: PANEL, borderRadius: radius.lg, padding: spacing.lg, marginTop: spacing.md, borderWidth: 1 },
  line: { color: TXT, fontSize: font.base, marginTop: spacing.xs },
  note: { color: DIM, fontSize: font.sm, marginTop: spacing.sm },
});
