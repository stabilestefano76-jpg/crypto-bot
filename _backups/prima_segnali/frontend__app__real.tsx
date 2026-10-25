import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { realApi, RealDiagnosi, RealStatus } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

const money = (n?: number | null) => (n === undefined || n === null || isNaN(n) ? "-" : `${n.toFixed(2)} USDC`);

function ask(title: string, msg: string, ok: string, run: () => void) {
  Alert.alert(title, msg, [
    { text: "Annulla", style: "cancel" },
    { text: ok, style: "destructive", onPress: run },
  ]);
}

export default function RealScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const [st, setSt] = useState<RealStatus | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [dg, setDg] = useState<RealDiagnosi | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await realApi.status());
      try {
        setDg(await realApi.diagnosi());
      } catch {}
    } catch (e: any) {
      setMsg(String(e?.message ?? "Errore"));
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  const run = async (f: () => Promise<any>, okText?: (r: any) => string) => {
    setBusy(true);
    try {
      const r = await f();
      setMsg(okText ? okText(r) : "Fatto");
    } catch (e: any) {
      setMsg("ERRORE: " + String(e?.message ?? e));
    } finally {
      setBusy(false);
      load();
    }
  };

  if (!st)
    return (
      <View style={[styles.root, { paddingTop: insets.top, justifyContent: "center" }]}>
        <ActivityIndicator color={colors.brand} />
      </View>
    );

  const live = st.live_strategies.includes("s3360");
  const liveIct = st.live_strategies.includes("ict");
  const toggleLive = (key: string, on: boolean) => {
    const rest = st.live_strategies.filter((x) => x !== key);
    return on ? [...rest, key] : rest;
  };
  const ready = st.password_active && st.key_connected;

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>Trading reale</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}>
        <View style={[styles.card, { borderColor: st.enabled && (live || liveIct) ? colors.error : colors.border }]}>
          <Text style={styles.big}>{st.enabled && (live || liveIct)
              ? "REALE ACCESO su " + [live ? "33/60" : "", liveIct ? "ICT" : ""].filter(Boolean).join(" + ")
              : "Reale spento (solo simulazione)"}</Text>
          <Text style={styles.line}>{st.password_active ? "✓ Password di accesso attiva" : "✗ Password di accesso NON attiva (serve BOT_ACCESS_KEY su Railway)"}</Text>
          <Text style={styles.line}>{st.key_connected ? "✓ Chiave Bybit collegata" : "✗ Chiave Bybit non collegata"}</Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.big}>Limiti</Text>
          <Text style={styles.line}>Per ogni operazione: massimo {money(st.max_order)}</Text>
          <Text style={styles.line}>Totale in gioco: massimo {money(st.max_total)} (ora {money(st.exposure)})</Text>
          <Text style={styles.line}>Perdita massima al giorno: {money(st.daily_loss_limit)} (oggi {st.daily_pnl.toFixed(2)})</Text>
        </View>

        <View style={styles.card}>
          <View style={styles.row}>
            <Text style={styles.big}>Interruttore generale</Text>
            <Switch
              value={st.enabled}
              disabled={busy || !ready}
              onValueChange={(v) =>
                v
                  ? ask("Accendere il reale?", "Gli ordini saranno SOLDI VERI su Bybit, entro i limiti indicati.", "Accendi", () =>
                      run(() => realApi.save({ real_trading_enabled: true })))
                  : run(() => realApi.save({ real_trading_enabled: false }))
              }
            />
          </View>
          <View style={styles.row}>
            <Text style={styles.big}>33/60 con soldi veri</Text>
            <Switch
              value={live}
              disabled={busy}
              onValueChange={(v) => run(() => realApi.save({ live_strategies: toggleLive("s3360", v) }))}
            />
          </View>
          <View style={styles.row}>
            <Text style={styles.big}>ICT con soldi veri</Text>
            <Switch
              value={liveIct}
              disabled={busy}
              onValueChange={(v) =>
                v
                  ? ask("ICT con soldi veri?", "ICT e' sperimentale e NON e' stata dimostrata dai test. Usa soldi veri entro i limiti indicati.", "Accendi", () =>
                      run(() => realApi.save({ live_strategies: toggleLive("ict", true) })))
                  : run(() => realApi.save({ live_strategies: toggleLive("ict", false) }))
              }
            />
          </View>
          <Text style={styles.note}>Il reale lavora solo se TUTTI e due sono accesi. Ricorda di mettere i soldi nel portafoglio di 33/60 (Deposita): e' il budget che il bot puo' usare.</Text>
        </View>

        <Pressable
          style={[styles.btn, { opacity: busy || !st.enabled ? 0.4 : 1 }]}
          disabled={busy || !st.enabled}
          onPress={() =>
            ask("Ordine di prova", "Compra 6 USDC di XRP e li rivende subito. Costa circa 0,3-0,5% (commissioni).", "Fai la prova", () =>
              run(() => realApi.test(6), (r) => `Prova ok: speso ${r.speso}, incassato ${r.incassato} (+ resti ${r.resti_rimasti}). Costo ${r.costo_totale} (${r.costo_pct}%)`))
          }
        >
          <Text style={styles.btnText}>Ordine di prova (6 USDC)</Text>
        </Pressable>

        <Pressable
          style={[styles.btn, { backgroundColor: colors.error }]}
          disabled={busy}
          onPress={() =>
            ask("STOP", "Spegne il reale e vende subito le posizioni reali aperte.", "STOP", () =>
              run(async () => {
                await realApi.stop();
                return realApi.closeAll();
              }, (r) => `Fermato. Chiuse ${r.chiuse}, non riuscite ${r.non_riuscite}`))
          }
        >
          <Text style={[styles.btnText, { color: colors.onError }]}>STOP: spegni e chiudi tutto</Text>
        </Pressable>

        {!!msg && <Text style={styles.msg}>{msg}</Text>}

        <Text style={[styles.big, { marginTop: spacing.lg }]}>Diagnosi: perche' non apre?</Text>
        {dg && (["xrp_acc", "s3360"] as const).map((k) => (
          <View key={k} style={styles.card}>
            <Text style={styles.big}>{k === "xrp_acc" ? "XRP Accumulation" : "33/60"}</Text>
            <Text style={styles.note}>
              Timeframe attivi: {(dg[k].timeframes || []).join(", ") || "nessuno"} · soglia RSI {dg[k].soglia}
            </Text>
            <Text style={styles.note}>
              Filtri: trend moneta {dg[k].filtro_trend_moneta ? "ACCESO" : "spento"} · trend BTC {dg[k].filtro_trend_btc ? "ACCESO" : "spento"} · sopra media lunga {dg[k].filtro_sopra_media_lunga ? "ACCESO" : "spento"}
            </Text>
            <Text style={[styles.line, { marginTop: spacing.sm }]}>Motivi di scarto (ultime 6 ore):</Text>
            {(dg.motivi[k] || []).length === 0 && <Text style={styles.note}>Nessun dato.</Text>}
            {(dg.motivi[k] || []).map((m, i) => (
              <Text key={i} style={styles.log}>{m.volte}x {m.motivo}</Text>
            ))}
          </View>
        ))}
        {dg && (
          <View style={styles.card}>
            <Text style={styles.big}>RSI minimo visto dal bot (XRP, per ora)</Text>
            {dg.rsi_ore.length === 0 && <Text style={styles.note}>Non ancora registrato: si riempie da adesso.</Text>}
            {dg.rsi_ore.slice(0, 24).map((r, i) => (
              <Text key={i} style={[styles.log, r.rsi_min !== null && r.rsi_min <= dg.xrp_acc.soglia && { color: colors.error }]}>
                {new Date(r.ora + ":00:00Z").toLocaleString("it-IT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })} · {r.tf} · minimo {r.rsi_min}
              </Text>
            ))}
          </View>
        )}

        <Text style={[styles.big, { marginTop: spacing.lg }]}>Posizioni reali aperte</Text>
        {st.open_positions.length === 0 && <Text style={styles.note}>Nessuna.</Text>}
        {st.open_positions.map((p, i) => (
          <Text key={i} style={styles.line}>
            {p.symbol} · {money(p.notional)} · entrata {p.fill_price}
          </Text>
        ))}

        <Text style={[styles.big, { marginTop: spacing.lg }]}>Registro</Text>
        {st.log.map((r, i) => (
          <Text key={i} style={[styles.log, r.level !== "info" && { color: colors.error }]}>
            {new Date(r.at).toLocaleString("it-IT")} — {r.text}
          </Text>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  title: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700" },
  card: { backgroundColor: colors.surfaceSecondary, borderRadius: radius.lg, padding: spacing.lg, marginBottom: spacing.md, borderWidth: 1, borderColor: colors.border },
  big: { color: colors.onSurface, fontSize: font.lg, fontWeight: "700", marginBottom: spacing.xs },
  line: { color: colors.onSurfaceSecondary, fontSize: font.base, marginTop: spacing.xs },
  note: { color: colors.onSurfaceTertiary, fontSize: font.sm, marginTop: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.sm },
  btn: { backgroundColor: colors.brand, borderRadius: radius.lg, padding: spacing.lg, alignItems: "center", marginBottom: spacing.md },
  btnText: { color: colors.onBrand, fontWeight: "700", fontSize: font.base },
  msg: { color: colors.warning, fontSize: font.base, marginTop: spacing.sm },
  log: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: spacing.xs },
});
