import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { crusApi, CrusReport, CrusTrade } from "@/src/api";

const BG = "#0B0E11";
const PANEL = "#12161C";
const LINE = "#1E2329";
const TXT = "#EAECEF";
const DIM = "#848E9C";
const UP = "#0ECB81";
const DOWN = "#F6465D";
const WARN = "#F0B90B";

const usd = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `${n < 0 ? "-" : n > 0 ? "+" : ""}$${Math.abs(n).toFixed(2)}`);
const col = (n?: number | null) => (n === undefined || n === null || n === 0 ? TXT : n > 0 ? UP : DOWN);
const px = (n?: number | null) => {
  if (n === undefined || n === null || isNaN(n)) return "-";
  const d = n >= 100 ? 2 : n >= 1 ? 4 : n >= 0.01 ? 5 : 7;
  return n.toFixed(d);
};
const when = (iso?: string) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)} ${iso.slice(11, 16)}` : "-");
const dur = (h?: number | null) => (h === null || h === undefined ? "-" : h < 1 ? `${Math.round(h * 60)} min` : h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} g`);
const why = (r?: string | null) => (r === "chiusura_manuale" ? "chiusa da te" : r === "obiettivo" ? "obiettivo" : r === "stop" ? "stop" : r ?? "-");

function Kpi({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={st.kpi}>
      <Text style={st.dim}>{label}</Text>
      <Text style={[st.kpiVal, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

function Chip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={[st.chip, on && { backgroundColor: WARN }]}>
      <Text style={[st.txt, { fontWeight: "700", color: on ? "#000" : TXT }]}>{label}</Text>
    </Pressable>
  );
}

function Row({ t }: { t: CrusTrade }) {
  const [open, setOpen] = useState(false);
  return (
    <Pressable onPress={() => setOpen(!open)} style={st.trade}>
      <View style={st.rowBetween}>
        <View style={{ flexDirection: "row", alignItems: "center", flex: 1 }}>
          <View style={[st.badge, { backgroundColor: t.side === "long" ? UP : DOWN }]}><Text style={st.badgeTxt}>{t.side === "long" ? "L" : "S"}</Text></View>
          <Text style={[st.txt, { fontWeight: "700", marginLeft: 8 }]}>{t.symbol.replace("USDC", "")}</Text>
          <Text style={[st.dim, { marginLeft: 8 }]}>{when(t.closed_at)} · {why(t.reason)}</Text>
        </View>
        <Text style={[st.txt, { fontWeight: "700", color: col(t.pnl) }]}>{usd(t.pnl)}</Text>
        <Ionicons name={open ? "chevron-up" : "chevron-down"} size={16} color={DIM} style={{ marginLeft: 6 }} />
      </View>
      {open && (
        <View style={{ marginTop: 8 }}>
          <Text style={st.dim}>Aperta {when(t.opened_at)} · chiusa {when(t.closed_at)} · durata {dur(t.hold_h)}</Text>
          <Text style={st.dim}>Ingresso {px(t.entry)} · uscita {px(t.exit)} · stop {px(t.stop)} · obiettivo {px(t.target)}</Text>
          <Text style={st.dim}>Importo ${t.notional?.toFixed(2) ?? "-"} · margine ${t.margin?.toFixed(2) ?? "-"} · leva {t.leverage}x{t.auto ? " · aperta dal bot" : " · aperta da te"}</Text>
          <Text style={st.dim}>
            Risultato {usd(t.pnl)}{t.pnl_pct_margin !== null ? ` (${t.pnl_pct_margin > 0 ? "+" : ""}${t.pnl_pct_margin}% del margine)` : ""}{t.r !== null ? ` · ${t.r > 0 ? "+" : ""}${t.r}R` : ""} · finanziamento ${t.carry ?? 0}
          </Text>
        </View>
      )}
    </Pressable>
  );
}

export default function ReportScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [side, setSide] = useState("");
  const [esito, setEsito] = useState("");
  const [symbol, setSymbol] = useState("");
  const [data, setData] = useState<CrusReport | null>(null);
  const [more, setMore] = useState<CrusTrade[]>([]);
  const [page, setPage] = useState(0);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await crusApi.report({ side, esito, symbol, page: 0 });
      setData(r); setMore([]); setPage(0); setErr("");
    } catch (e: any) { setErr(String(e?.message ?? "Errore")); }
  }, [side, esito, symbol]);
  useEffect(() => { load(); }, [load]);

  const loadMore = async () => {
    setBusy(true);
    try {
      const r = await crusApi.report({ side, esito, symbol, page: page + 1 });
      setMore((m) => [...m, ...r.trades]); setPage(page + 1);
    } catch (e: any) { setErr(String(e?.message ?? "Errore")); }
    setBusy(false);
  };

  const trades = data ? [...data.trades, ...more] : [];
  const sm = data?.summary;
  const maxDay = data ? Math.max(0.0001, ...data.by_day.map((d) => Math.abs(d.pnl))) : 1;

  return (
    <View style={[st.root, { paddingTop: insets.top }]}>
      <View style={st.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}><Ionicons name="chevron-back" size={26} color={TXT} /></Pressable>
        <Text style={st.title}>Report operazioni</Text>
        <View style={{ width: 26 }} />
      </View>
      {!data ? (
        <View style={{ flex: 1, justifyContent: "center" }}>{err ? <Text style={[st.dim, { textAlign: "center", padding: 20 }]}>{err}</Text> : <ActivityIndicator color={WARN} />}</View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 60 }}>
          {!!err && <Text style={[st.dim, { color: WARN, marginBottom: 8 }]}>{err}</Text>}

          <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
            <Chip label="Tutte" on={side === ""} onPress={() => setSide("")} />
            <Chip label="Long" on={side === "long"} onPress={() => setSide("long")} />
            <Chip label="Short" on={side === "short"} onPress={() => setSide("short")} />
            <Chip label="Vinte e perse" on={esito === ""} onPress={() => setEsito("")} />
            <Chip label="Solo vinte" on={esito === "vinte"} onPress={() => setEsito("vinte")} />
            <Chip label="Solo perse" on={esito === "perse"} onPress={() => setEsito("perse")} />
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 10 }}>
            <Chip label="Tutte le monete" on={symbol === ""} onPress={() => setSymbol("")} />
            {data.symbols.map((s) => <Chip key={s} label={s.replace("USDC", "")} on={symbol === s} onPress={() => setSymbol(s)} />)}
          </ScrollView>

          {!sm ? (
            <View style={st.card}><Text style={st.dim}>Nessuna operazione con questi filtri.</Text></View>
          ) : (<>
            <View style={st.card}>
              <Text style={st.dim}>Risultato totale ({sm.n} operazioni)</Text>
              <Text style={[st.hero, { color: col(sm.pnl) }]}>{usd(sm.pnl)}</Text>
            </View>
            <View style={st.grid}>
              <Kpi label="Operazioni vinte" value={sm.win_rate === null ? "-" : `${sm.win_rate.toFixed(0)}% (${sm.wins}/${sm.n})`} />
              <Kpi label="Profit factor" value={sm.profit_factor === null ? "-" : String(sm.profit_factor)} color={sm.profit_factor === null ? undefined : sm.profit_factor >= 1 ? UP : DOWN} />
              <Kpi label="Media per operazione" value={usd(sm.expectancy)} color={col(sm.expectancy)} />
              <Kpi label="Media vinta / persa" value={`${usd(sm.avg_win)} / ${usd(sm.avg_loss)}`} />
              <Kpi label="Migliore / peggiore" value={`${usd(sm.best)} / ${usd(sm.worst)}`} />
              <Kpi label="Perse di fila (max)" value={String(sm.max_loss_streak)} color={sm.max_loss_streak >= 4 ? DOWN : undefined} />
              <Kpi label="Durata media" value={dur(sm.avg_hold_h)} />
              <Kpi label="Finanziamento pagato" value={`$${sm.carry_total}`} />
            </View>

            <View style={st.card}>
              <Text style={st.h}>Long contro short</Text>
              {[{ l: "LONG", s: sm.long, c: UP }, { l: "SHORT", s: sm.short, c: DOWN }].map((x) => (
                <View key={x.l} style={[st.rowBetween, { marginTop: 8 }]}>
                  <Text style={[st.txt, { color: x.c, width: 64, fontWeight: "700" }]}>{x.l}</Text>
                  <Text style={[st.dim, { flex: 1 }]}>{x.s.n} chiuse · vinte {x.s.win_rate === null ? "-" : x.s.win_rate.toFixed(0) + "%"} · PF {x.s.profit_factor ?? "-"}</Text>
                  <Text style={[st.txt, { color: col(x.s.pnl), fontWeight: "700" }]}>{usd(x.s.pnl)}</Text>
                </View>
              ))}
              <Text style={[st.dim, { marginTop: 8 }]}>Aperte dal bot {sm.auto_n} · da te {sm.n - sm.auto_n}</Text>
            </View>

            <View style={st.card}>
              <Text style={st.h}>Risultato per giorno (ultimi 14)</Text>
              {data.by_day.map((d) => (
                <View key={d.day} style={[st.rowBetween, { marginTop: 6 }]}>
                  <Text style={[st.dim, { width: 52 }]}>{d.day.slice(8, 10)}/{d.day.slice(5, 7)}</Text>
                  <View style={{ flex: 1, height: 8, backgroundColor: LINE, borderRadius: 4, marginHorizontal: 8 }}>
                    <View style={{ width: `${Math.max(2, (Math.abs(d.pnl) / maxDay) * 100)}%`, height: 8, borderRadius: 4, backgroundColor: d.pnl >= 0 ? UP : DOWN }} />
                  </View>
                  <Text style={[st.txt, { color: col(d.pnl), width: 70, textAlign: "right" }]}>{usd(d.pnl)}</Text>
                </View>
              ))}
            </View>

            <View style={st.card}>
              <Text style={st.h}>Per moneta</Text>
              {data.by_symbol.map((x) => (
                <View key={x.symbol} style={[st.rowBetween, { marginTop: 6 }]}>
                  <Text style={[st.txt, { width: 70 }]}>{x.symbol.replace("USDC", "")}</Text>
                  <Text style={[st.dim, { flex: 1 }]}>{x.n} op. · vinte {x.win_rate === null ? "-" : x.win_rate.toFixed(0) + "%"}</Text>
                  <Text style={[st.txt, { color: col(x.pnl), fontWeight: "700" }]}>{usd(x.pnl)}</Text>
                </View>
              ))}
            </View>

            <View style={st.card}>
              <Text style={st.h}>Come si sono chiuse</Text>
              {data.by_reason.map((x) => (
                <View key={x.reason} style={[st.rowBetween, { marginTop: 6 }]}>
                  <Text style={[st.txt, { width: 120 }]}>{why(x.reason)}</Text>
                  <Text style={[st.dim, { flex: 1 }]}>{x.n} op. · vinte {x.win_rate === null ? "-" : x.win_rate.toFixed(0) + "%"}</Text>
                  <Text style={[st.txt, { color: col(x.pnl), fontWeight: "700" }]}>{usd(x.pnl)}</Text>
                </View>
              ))}
            </View>
          </>)}

          <View style={st.card}>
            <Text style={st.h}>Tutte le operazioni ({data.total})</Text>
            <Text style={[st.dim, { marginTop: 4 }]}>Tocca una riga per vedere i dettagli.</Text>
            {trades.map((t) => <Row key={t.id} t={t} />)}
            {trades.length < data.total && (
              <Pressable style={[st.chip, { marginTop: 12, alignItems: "center", opacity: busy ? 0.4 : 1 }]} disabled={busy} onPress={loadMore}>
                <Text style={st.txt}>{busy ? "Carico..." : `Carica altre (${data.total - trades.length})`}</Text>
              </Pressable>
            )}
          </View>
        </ScrollView>
      )}
    </View>
  );
}

const st = StyleSheet.create({
  root: { flex: 1, backgroundColor: BG },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 14, paddingVertical: 10 },
  title: { color: TXT, fontSize: 18, fontWeight: "700" },
  card: { backgroundColor: PANEL, borderRadius: 10, padding: 14, marginBottom: 12, borderWidth: 1, borderColor: LINE },
  h: { color: TXT, fontSize: 15, fontWeight: "700" },
  txt: { color: TXT, fontSize: 14 },
  dim: { color: DIM, fontSize: 12, marginTop: 2 },
  hero: { color: TXT, fontSize: 30, fontWeight: "800" },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  grid: { flexDirection: "row", flexWrap: "wrap", marginHorizontal: -5, marginBottom: 7 },
  kpi: { width: "50%", paddingHorizontal: 5, marginBottom: 10 },
  kpiVal: { color: TXT, fontSize: 15, fontWeight: "700", marginTop: 2, backgroundColor: PANEL, paddingVertical: 8, paddingHorizontal: 10, borderRadius: 8, overflow: "hidden" },
  chip: { paddingHorizontal: 12, paddingVertical: 8, backgroundColor: PANEL, borderRadius: 8, marginRight: 8, marginBottom: 8, borderWidth: 1, borderColor: LINE },
  trade: { borderTopWidth: 1, borderTopColor: LINE, marginTop: 10, paddingTop: 10 },
  badge: { borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  badgeTxt: { color: "#000", fontSize: 11, fontWeight: "800" },
});
