import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
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

type ExchangeStatus = Awaited<ReturnType<typeof api.exchangeStatus>>;
import { colors, font, radius, spacing } from "@/src/theme";

const TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"];
// 30m is offered only to the two independent strategies that were extended
// to support it (33/60 and XRP Accumulation); the others stay as they were.
const TIMEFRAMES_30M = ["5m", "15m", "30m", "1h", "4h", "1d"];

export default function SettingsScreen() {
  const insets = useSafeAreaInsets();
  const [cfg, setCfg] = useState<Config | null>(null);
  const [pcfg, setPcfg] = useState<PaperConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const [brokerStatus, setBrokerStatus] = useState<ExchangeStatus | null>(null);
  const [connectModalVisible, setConnectModalVisible] = useState(false);
  const [apiKeyText, setApiKeyText] = useState("");
  const [apiSecretText, setApiSecretText] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

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

  const loadBrokerStatus = useCallback(async () => {
    try {
      const s = await api.exchangeStatus();
      setBrokerStatus(s);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    load();
    loadBrokerStatus();
  }, [load, loadBrokerStatus]);

  const onConnectBroker = async () => {
    const key = apiKeyText.trim();
    const secret = apiSecretText.trim();
    if (!key || !secret) return;
    setConnecting(true);
    try {
      await api.exchangeConnect({ api_key: key, api_secret: secret });
      setConnectModalVisible(false);
      setApiKeyText("");
      setApiSecretText("");
      await loadBrokerStatus();
    } catch (e) {
      // The server tests the key with Bybit before saving it, so a wrong key
      // or missing permission comes back here with the reason.
      Alert.alert(
        "Collegamento non riuscito",
        e instanceof Error ? e.message : "Non è stato possibile salvare la connessione. Riprova."
      );
    } finally {
      setConnecting(false);
    }
  };

  const onDisconnectBroker = async () => {
    setDisconnecting(true);
    try {
      await api.exchangeDisconnect();
      await loadBrokerStatus();
    } finally {
      setDisconnecting(false);
    }
  };

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
    } catch (e: any) {
      Alert.alert(
        "Salvataggio non riuscito",
        e?.message || "Errore sconosciuto durante il salvataggio."
      );
    } finally {
      setSaving(false);
    }
  };

  const toggleTimeframeFor = (
    field:
      | "rsi_reversion_timeframes"
      | "s3360_timeframes"
      | "xrp_acc_timeframes",
    t: string
  ) => {
    if (!cfg) return;
    const current = cfg[field];
    const list = current.includes(t) ? current.filter((x) => x !== t) : [...current, t];
    update({ [field]: list } as Partial<Config>);
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
      : ["rsi_reversion"];
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
          <Section title="Connessione Bybit (trading reale)">
            {brokerStatus?.connected ? (
              <>
                <View style={styles.brokerStatusRow}>
                  <View style={styles.brokerDot} />
                  <Text style={styles.brokerStatusText}>
                    Connesso — chiave {brokerStatus.api_key_masked}
                  </Text>
                </View>
                <Pressable
                  style={({ pressed }) => [styles.disconnectBtn, pressed && { opacity: 0.7 }]}
                  onPress={() =>
                    Alert.alert(
                      "Disconnetti",
                      "Vuoi davvero rimuovere la chiave API salvata?",
                      [
                        { text: "Annulla", style: "cancel" },
                        { text: "Disconnetti", style: "destructive", onPress: onDisconnectBroker },
                      ]
                    )
                  }
                  disabled={disconnecting}
                >
                  {disconnecting ? (
                    <ActivityIndicator color={colors.error} size="small" />
                  ) : (
                    <Text style={styles.disconnectBtnText}>Disconnetti</Text>
                  )}
                </Pressable>
              </>
            ) : (
              <>
                <View style={styles.brokerStatusRow}>
                  <View style={[styles.brokerDot, { backgroundColor: colors.onSurfaceSecondary }]} />
                  <Text style={styles.brokerStatusText}>Non connesso — il bot opera solo in paper</Text>
                </View>
                <Pressable
                  style={({ pressed }) => [styles.connectBrokerBtn, pressed && { opacity: 0.7 }]}
                  onPress={() => setConnectModalVisible(true)}
                >
                  <Ionicons name="key" size={16} color="#000" />
                  <Text style={styles.connectBrokerBtnText}>Collega API</Text>
                </Pressable>
              </>
            )}
            <Text style={styles.scoreHintText}>
              Alla conferma il bot prova subito la chiave con Bybit: se è
              sbagliata o manca un permesso, non viene salvata e ti dice
              perché. Una volta salvata (cifrata) non è più leggibile per
              intero da nessuna parte, solo sostituibile o rimovibile. Questo
              passaggio salva solo le credenziali: quale strategia opera
              davvero in reale si decide qui sotto, una alla volta.
            </Text>
          </Section>

          <Section title="Modalità operativa (Paper / Reale)">
            <Text style={styles.scoreHintText}>
              Ogni strategia opera in Paper (simulato) finché non la passi
              esplicitamente a Reale. Questo interruttore è indipendente da
              quello di attivazione sopra — puoi avere una strategia
              spenta e impostata su Reale allo stesso tempo, resterà
              semplicemente ferma finché non la riaccendi.
            </Text>
            {([
              ["rsi_reversion", "RSI Reversion"],
              ["s3360", "33/60"],
              ["xrp_acc", "XRP Accumulation"],
            ] as const).map(([key, label]) => {
              const isLive = cfg.live_strategies.includes(key);
              const setLive = (live: boolean) => {
                if (live) {
                  if (!brokerStatus?.connected) {
                    Alert.alert(
                      "Collega prima l'API",
                      "Devi collegare la chiave API di Bybit (sezione sopra) prima di poter passare una strategia al trading reale."
                    );
                    return;
                  }
                  Alert.alert(
                    "Attenzione: soldi veri",
                    `${label} inizierà ad operare con denaro reale sul tuo account Bybit. Confermi?`,
                    [
                      { text: "Annulla", style: "cancel" },
                      {
                        text: "Conferma",
                        style: "destructive",
                        onPress: () => update({ live_strategies: [...cfg.live_strategies, key] }),
                      },
                    ]
                  );
                  return;
                }
                update({ live_strategies: cfg.live_strategies.filter((k) => k !== key) });
              };
              return (
                <View key={key} style={styles.liveModeRow}>
                  <Text style={styles.liveModeLabel}>{label}</Text>
                  <View style={styles.liveModeToggle}>
                    <Pressable
                      style={[styles.liveModeChip, !isLive && styles.liveModeChipActivePaper]}
                      onPress={() => setLive(false)}
                    >
                      <Text style={[styles.liveModeChipText, !isLive && styles.liveModeChipTextActive]}>
                        Paper
                      </Text>
                    </Pressable>
                    <Pressable
                      style={[styles.liveModeChip, isLive && styles.liveModeChipActiveLive]}
                      onPress={() => setLive(true)}
                    >
                      <Text style={[styles.liveModeChipText, isLive && styles.liveModeChipTextActive]}>
                        Reale
                      </Text>
                    </Pressable>
                  </View>
                </View>
              );
            })}
          </Section>

          <Section title="Motori attivi (tutte le sezioni)">
            {(() => {
              // RSI Reversion si accende/spegne tramite l'array
              // enabled_strategies; 33/60 e XRP Accumulation hanno invece un
              // proprio interruttore booleano indipendente — questa
              // funzione unifica entrambe le logiche in un solo gruppo di
              // bottoni, così ogni strategia si accende/spegne da un unico
              // pannello.
              const isActive = (val: string): boolean => {
                if (val === "s3360") return cfg.s3360_enabled;
                if (val === "xrp_acc") return cfg.xrp_acc_enabled;
                return stratOn(val);
              };
              const toggle = (val: string) => {
                if (val === "s3360") {
                  update({ s3360_enabled: !cfg.s3360_enabled });
                  return;
                }
                if (val === "xrp_acc") {
                  update({ xrp_acc_enabled: !cfg.xrp_acc_enabled });
                  return;
                }
                const set = new Set(enabledStrategies);
                if (set.has(val)) set.delete(val);
                else set.add(val);
                // Per il bot un elenco VUOTO significa "usa il predefinito"
                // (= RSI Reversion acceso): spegnere l'unica strategia
                // dell'elenco deve quindi lasciare un segnaposto non vuoto,
                // altrimenti il bot la riaccenderebbe da solo.
                update({ enabled_strategies: set.size ? Array.from(set) : ["none"] });
              };
              return (
                <View style={styles.stratRow}>
                  {([
                    ["rsi_reversion", "RSI Reversion"],
                    ["s3360", "33/60"],
                    ["xrp_acc", "XRP Accumulation"],
                  ] as const).map(([val, label]) => {
                    const active = isActive(val);
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
              Ogni bottone accende/spegne quella strategia in modo
              indipendente. RSI Reversion = rientro da
              ipercomprato/ipervenduto confermato da divergenza; usa il
              capitale condiviso a meno di allocare fondi dedicati dalla
              schermata Strategie. 33/60 e XRP Accumulation hanno ciascuna un
              portafoglio proprio separato. Qui puoi anche fermarle del
              tutto, utile per decidere quali strategie usare quando si
              passerà al reale.
            </Text>
          </Section>

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
                label="Candele per il livello strutturale (stop)"
                value={cfg.rsi_rev_structural_lookback}
                onChange={(v) => update({ rsi_rev_structural_lookback: v })}
                testID="input-rsirev-structural-lookback"
              />
              <NumRow
                label="Stop catastrofico (×ATR)"
                value={cfg.rsi_rev_catastrophic_atr_mult}
                onChange={(v) => update({ rsi_rev_catastrophic_atr_mult: v })}
                step={0.5}
                testID="input-rsirev-atr"
              />
              <NumRow
                label="R:R minimo richiesto"
                value={cfg.rsi_rev_min_rr_ratio}
                onChange={(v) => update({ rsi_rev_min_rr_ratio: v })}
                step={0.5}
                testID="input-rsirev-min-rr"
              />
              <NumRow
                label="Margine attivazione trailing (% oltre le commissioni stimate)"
                value={cfg.rsi_rev_trailing_activation_margin_pct}
                onChange={(v) => update({ rsi_rev_trailing_activation_margin_pct: v })}
                step={0.1}
                testID="input-rsirev-trailing-margin"
              />
              <NumRow
                label="Trailing profitto (×ATR)"
                value={cfg.rsi_rev_trailing_atr_mult}
                onChange={(v) => update({ rsi_rev_trailing_atr_mult: v })}
                step={0.5}
                testID="input-rsirev-trailing"
              />
              <Text style={styles.scoreHintText}>
                Entra quando l&apos;RSI rientra dalla zona estrema (dopo almeno{" "}
                {cfg.rsi_rev_min_extreme_candles} candele oltre soglia) con
                divergenza confermata. Target = prezzo torna sulla propria
                media a {cfg.rsi_period} periodi (proxy di &quot;RSI torna a
                50&quot;). Lo stop è legato a un livello strutturale — il
                massimo/minimo delle ultime {cfg.rsi_rev_structural_lookback}{" "}
                candele, il livello la cui rottura invalida davvero la tesi di
                inversione — non più solo una distanza ATR fissa. Lo stop
                catastrofico a {cfg.rsi_rev_catastrophic_atr_mult}×ATR resta
                come margine di sicurezza minimo, usato solo se il livello
                strutturale fosse troppo vicino all&apos;entrata. Prima di
                aprire, il rapporto naturale tra guadagno atteso (distanza dal
                target) e rischio (distanza dallo stop) deve raggiungere
                almeno {cfg.rsi_rev_min_rr_ratio}:1 — altrimenti l&apos;
                operazione viene scartata invece di forzare uno stop più
                stretto di quanto la struttura giustifichi. Il trailing si
                attiva appena il guadagno supera le commissioni stimate di
                andata/ritorno più un margine del{" "}
                {cfg.rsi_rev_trailing_activation_margin_pct}% — non più al
                primo centesimo di guadagno. Da quel momento l&apos;
                operazione corre senza tetto massimo, protetta da un trailing
                largo ({cfg.rsi_rev_trailing_atr_mult}×ATR) pensato per
                catturare solo un&apos;inversione vera, non le normali
                oscillazioni. Il target di ritorno alla media resta solo
                informativo. Usa l&apos;interruttore &quot;Trailing stop
                attivo&quot; più sotto per disattivare/riattivare tutto
                questo.
              </Text>
            </Section>
          )}

          {stratOn("rsi_reversion") && (
            <Section title="Divergenza RSI (RSI Reversion)">
              <ToggleRow
                label="Ricerca divergenza RSI attiva"
                value={cfg.rsi_divergence_check_enabled}
                onChange={(v) => update({ rsi_divergence_check_enabled: v })}
                testID="toggle-rsi-divergence-check"
              />
              <Text style={styles.scoreHintText}>
                Con l&apos;interruttore acceso, RSI Reversion entra in long
                solo se RSI e prezzo mostrano una divergenza rialzista: un
                semplice rientro dentro le bande non basta. Spento, il filtro
                non c&apos;è e il bot entra anche senza divergenza, quindi con
                più segnali ma meno selettivi.
              </Text>
            </Section>
          )}

          {cfg.s3360_enabled && (
            <Section title="33/60">
              <Text style={styles.fieldLabel}>Timeframe</Text>
              <View style={styles.chipsRow}>
                {TIMEFRAMES_30M.map((t) => {
                  const active = cfg.s3360_timeframes.includes(t);
                  return (
                    <Pressable
                      key={t}
                      onPress={() => toggleTimeframeFor("s3360_timeframes", t)}
                      style={[styles.chip, active && styles.chipActive]}
                      testID={`tf-select-s3360-${t}`}
                    >
                      <Text style={[styles.chipText, active && styles.chipTextActive]}>
                        {t}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
              <NumRow
                label="Soglia bassa (ingresso)"
                value={cfg.s3360_low_threshold}
                onChange={(v) => update({ s3360_low_threshold: v })}
                step={1}
                testID="input-s3360-low"
              />
              <NumRow
                label="Soglia alta (uscita)"
                value={cfg.s3360_high_threshold}
                onChange={(v) => update({ s3360_high_threshold: v })}
                step={1}
                testID="input-s3360-high"
              />
              <NumRow
                label="Numero di operazioni contemporanee"
                value={cfg.s3360_max_open_positions}
                onChange={(v) => update({ s3360_max_open_positions: v })}
                testID="input-s3360-max-positions"
              />
              <NumRow
                label="Crescita massima di oggi per entrare (%, 0 = spento)"
                value={cfg.s3360_max_daily_rise_pct}
                onChange={(v) => update({ s3360_max_daily_rise_pct: v })}
                step={0.5}
                testID="input-s3360-max-daily-rise"
              />
              <Text style={styles.scoreHintText}>
                Filtro sulla candela giornaliera: se la moneta è già salita
                più di questa percentuale dall&apos;apertura di oggi (00:00 UTC,
                le 02:00 in Italia), il bot non entra, anche con l&apos;RSI
                sotto la soglia. Con 0 il filtro è spento. Non cambia le
                uscite.
              </Text>
              <NumRow
                label="Volatilità minima per entrare (ATR %, 0 = spento)"
                value={cfg.s3360_min_atr_pct}
                onChange={(v) => update({ s3360_min_atr_pct: v })}
                step={0.01}
                testID="input-s3360-min-atr"
              />
              <Text style={styles.scoreHintText}>
                Filtro sul mercato troppo piatto: se il movimento tipico di
                una candela (ATR) è sotto questa percentuale del prezzo, il
                bot non entra, anche con l&apos;RSI sotto la soglia. Sul
                timeframe da 5 minuti, 0,10 esclude i mercati quasi fermi;
                sui timeframe più lunghi quel numero è quasi sempre superato.
                Con 0 il filtro è spento.
              </Text>
              <ToggleRow
                label="Non chiudere sotto il prezzo di entrata"
                value={cfg.s3360_hold_below_entry}
                onChange={(v) => update({ s3360_hold_below_entry: v })}
                testID="toggle-s3360-hold-below-entry"
              />
              <NumRow
                label="Margine minimo sopra l'entrata per chiudere (%)"
                value={cfg.s3360_min_exit_gain_pct}
                onChange={(v) => update({ s3360_min_exit_gain_pct: v })}
                step={0.05}
                testID="input-s3360-min-exit-gain"
              />
              <Text style={styles.scoreHintText}>
                Con l&apos;interruttore acceso, quando l&apos;RSI arriva al
                target ma il prezzo è ancora sotto l&apos;entrata (più il
                margine), l&apos;operazione resta aperta e aspetta: chiude
                solo quando le due condizioni sono vere insieme. Il margine di
                0,25% copre le commissioni di andata e ritorno (0,2%), così
                una chiusura non è mai in perdita netta. Attenzione: senza
                stop né limite di tempo, una moneta che non recupera tiene
                occupato il suo posto, e quel capitale resta bloccato finché
                non torna sopra l&apos;entrata. Le perdite non si realizzano
                più ma restano nel capitale come perdita sulla carta: guarda il
                capitale, non la percentuale di vincite.
              </Text>
              <Text style={styles.scoreHintText}>
                Entra appena l&apos;RSI scende sotto {cfg.s3360_low_threshold}{" "}
                (nessuna candela di conferma richiesta — scatta al primo
                attraversamento). Esce SOLO quando l&apos;RSI risale a{" "}
                {cfg.s3360_high_threshold} — non c&apos;è più né uno stop di
                sicurezza sul prezzo né un timeout: l&apos;operazione resta
                aperta anche per giorni o settimane se il prezzo scende e
                l&apos;RSI resta basso, finché prima o poi non risale. La
                taglia di ogni operazione non è più una percentuale fissa: si
                calcola dividendo il capitale totale (cassa più valore delle
                posizioni aperte) per il numero scelto sopra — con 1
                operazione usa tutto il capitale, con 2 metà ciascuna, con 4
                un quarto ciascuna, sempre riferito al totale e non a quanto
                resta libero in quel momento. Backtest su ~41 giorni di BTC
                1h: 14 casi trovati, 10 arrivati alla soglia alta (71%),
                guadagno medio +1,58% su quelli riusciti, nessuna perdita tra
                i riusciti.
              </Text>
            </Section>
          )}

          {cfg.xrp_acc_enabled && (
            <Section title="XRP Accumulation">
              <Text style={styles.fieldLabel}>Timeframe</Text>
              <View style={styles.chipsRow}>
                {TIMEFRAMES_30M.map((t) => {
                  const active = cfg.xrp_acc_timeframes.includes(t);
                  return (
                    <Pressable
                      key={t}
                      onPress={() => toggleTimeframeFor("xrp_acc_timeframes", t)}
                      style={[styles.chip, active && styles.chipActive]}
                      testID={`tf-select-xrp-acc-${t}`}
                    >
                      <Text style={[styles.chipText, active && styles.chipTextActive]}>
                        {t}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
              <NumRow
                label="Soglia bassa (ingresso)"
                value={cfg.xrp_acc_low_threshold}
                onChange={(v) => update({ xrp_acc_low_threshold: v })}
                step={1}
                testID="input-xrp-acc-low"
              />
              <NumRow
                label="Soglia alta (uscita)"
                value={cfg.xrp_acc_high_threshold}
                onChange={(v) => update({ xrp_acc_high_threshold: v })}
                step={1}
                testID="input-xrp-acc-high"
              />
              <Text style={styles.scoreHintText}>
                Strategia dedicata solo a XRPUSDC, con un obiettivo diverso
                dalle altre: non il guadagno in dollari, ma possedere più XRP
                nel tempo. Entra con TUTTO il capitale di trading appena
                l&apos;RSI scende a {cfg.xrp_acc_low_threshold} o sotto, esce
                con tutto quando l&apos;RSI risale a{" "}
                {cfg.xrp_acc_high_threshold} o sopra — stesso meccanismo in
                tempo reale di 33/60, nessuno stop, nessun timeout. Ad ogni
                vendita, il capitale originale torna in cassa per il giro
                successivo, e SOLO il profitto viene convertito subito in XRP
                e tenuto per sempre — così il capitale di trading non si
                gonfia mai restando bloccato in USDC, e la riserva di XRP
                cresce ad ogni ciclo vincente. Se una vendita chiude in
                perdita, il capitale di trading torna in cassa ridotto (non
                c&apos;è uno stop a proteggerlo), ma la riserva di XRP già
                accumulata non si tocca mai.
              </Text>
            </Section>
          )}

          <Section title="Regime di mercato condiviso">
            <NumRow
              label="Riduzione taglia fuori da regime rialzista (%)"
              value={cfg.regime_risk_reduction_pct}
              onChange={(v) => update({ regime_risk_reduction_pct: v })}
              step={5}
              testID="input-regime-reduction"
            />
            <Text style={styles.scoreHintText}>
              Guarda il regime di BTC su 1h: se non è chiaramente rialzista
              (fase laterale o ribassista), la taglia delle nuove operazioni
              di RSI Reversion e 33/60 viene ridotta del{" "}
              {cfg.regime_risk_reduction_pct}% — meno esposizione quando il
              mercato è incerto, piena taglia solo quando il quadro è
              chiaramente favorevole.
            </Text>
          </Section>

          <Section title="Trailing Stop">
            <ToggleRow
              label="Trailing stop attivo"
              value={cfg.trailing_enabled}
              onChange={(v) => update({ trailing_enabled: v })}
              testID="toggle-trailing-enabled"
            />
            <NumRow
              label="Distanza trailing (×ATR)"
              value={cfg.trailing_atr_mult}
              onChange={(v) => update({ trailing_atr_mult: v })}
              step={0.1}
              testID="input-trailing-atr-mult"
            />
            <Text style={styles.scoreHintText}>
              Quando il prezzo raggiunge il target originale, invece di
              chiudere subito l&apos;operazione resta aperta: tiene traccia
              del massimo raggiunto e chiude solo se il prezzo retrocede di
              questa distanza (in multipli di ATR) da quel massimo — così un
              movimento forte può continuare a rendere invece di fermarsi al
              primo obiettivo. Disattivalo per tornare alla chiusura secca al
              target, come prima.
            </Text>
          </Section>

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
          </Section>

          {stratOn("rsi_reversion") && (
            <Section title="Timeframes — RSI Reversion">
              <View style={styles.chipsRow}>
                {TIMEFRAMES.map((t) => {
                  const active = cfg.rsi_reversion_timeframes.includes(t);
                  return (
                    <Pressable
                      key={t}
                      onPress={() => toggleTimeframeFor("rsi_reversion_timeframes", t)}
                      style={[styles.chip, active && styles.chipActive]}
                      testID={`tf-chip-rsi-reversion-${t}`}
                    >
                      <Text style={[styles.chipText, active && styles.chipTextActive]}>
                        {t}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
              <Text style={styles.scoreHintText}>
                Ognuna delle tre strategie tradizionali ha ora il proprio
                timeframe indipendente — prima condividevano un unico
                elenco. Più timeframe attivi significa più occasioni
                controllate, ma anche più rumore su quelli più brevi.
              </Text>
            </Section>
          )}

          <Section title="RSI (RSI Reversion)">
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
          </Section>

          <Section title="Volume">
            <NumRow
              label="Volume MA period"
              value={cfg.volume_ma_period}
              onChange={(v) => update({ volume_ma_period: v })}
              testID="input-vol-period"
            />
          </Section>

          <Section title="Risk">
            <NumRow
              label="ATR period"
              value={cfg.atr_period}
              onChange={(v) => update({ atr_period: v })}
              testID="input-atr-period"
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

      <Modal visible={connectModalVisible} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>Collega l&apos;API di Bybit</Text>
            <Text style={styles.modalSubtitle}>
              Incolla qui la chiave API e il segreto generati su Bybit. Una
              volta salvati, non saranno più visibili per intero da nessuna
              parte dell&apos;app — solo le ultime cifre, per riconoscerli.
            </Text>
            <TextInput
              style={styles.modalInput}
              placeholder="Chiave API"
              placeholderTextColor={colors.onSurfaceSecondary}
              autoCapitalize="none"
              autoCorrect={false}
              value={apiKeyText}
              onChangeText={setApiKeyText}
            />
            <TextInput
              style={styles.modalInput}
              placeholder="Segreto API"
              placeholderTextColor={colors.onSurfaceSecondary}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
              value={apiSecretText}
              onChangeText={setApiSecretText}
            />
            <View style={styles.modalButtons}>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.surfaceTertiary }]}
                onPress={() => {
                  setConnectModalVisible(false);
                  setApiKeyText("");
                  setApiSecretText("");
                }}
              >
                <Text style={styles.modalBtnText}>Annulla</Text>
              </Pressable>
              <Pressable
                style={[styles.modalBtn, { backgroundColor: colors.brand }]}
                onPress={onConnectBroker}
                disabled={connecting}
              >
                {connecting ? (
                  <ActivityIndicator color={colors.onBrand} size="small" />
                ) : (
                  <Text style={[styles.modalBtnText, { color: colors.onBrand }]}>Salva</Text>
                )}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
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
  const commit = (raw: string) => {
    const n = Number(raw);
    if (!Number.isFinite(n)) return;
    onChange(step && step < 1 ? Math.round(n * 100) / 100 : n);
  };
  return (
    <View style={styles.formRow}>
      <Text style={styles.formLabel}>{label}</Text>
      <TextInput
        style={styles.input}
        value={txt}
        onChangeText={(t) => {
          setTxt(t);
          // Commit on every valid keystroke (not just on blur/end-editing):
          // onEndEditing/onBlur don't fire reliably on every platform (e.g.
          // web), which could leave a typed value un-saved even though it
          // looks confirmed on screen.
          commit(t);
        }}
        onEndEditing={() => {
          const n = Number(txt);
          if (!Number.isFinite(n)) {
            setTxt(String(value));
          }
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
  fieldLabel: { color: colors.onSurfaceSecondary, fontSize: font.sm, fontWeight: "600", marginBottom: spacing.xs },
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
  brokerStatusRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, marginBottom: spacing.sm },
  brokerDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.success },
  brokerStatusText: { color: colors.onSurface, fontSize: font.base, fontWeight: "600", flexShrink: 1 },
  connectBrokerBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    backgroundColor: colors.brand,
    borderRadius: radius.pill,
    paddingVertical: spacing.sm,
  },
  connectBrokerBtnText: { color: "#000", fontWeight: "700", fontSize: font.base },
  disconnectBtn: {
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radius.pill,
    paddingVertical: spacing.sm,
    borderWidth: 1,
    borderColor: colors.error,
  },
  disconnectBtnText: { color: colors.error, fontWeight: "700", fontSize: font.base },
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
    marginBottom: spacing.sm,
  },
  modalButtons: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  modalBtn: { flex: 1, borderRadius: radius.md, paddingVertical: spacing.sm, alignItems: "center" },
  modalBtnText: { color: colors.onSurface, fontWeight: "700" },
  liveModeRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.surfaceTertiary,
  },
  liveModeLabel: { color: colors.onSurface, fontSize: font.base, fontWeight: "600" },
  liveModeToggle: { flexDirection: "row", backgroundColor: colors.surfaceTertiary, borderRadius: radius.pill, padding: 2 },
  liveModeChip: { paddingHorizontal: spacing.md, paddingVertical: 6, borderRadius: radius.pill },
  liveModeChipActivePaper: { backgroundColor: colors.onSurfaceSecondary },
  liveModeChipActiveLive: { backgroundColor: colors.error },
  liveModeChipText: { color: colors.onSurfaceSecondary, fontSize: font.sm, fontWeight: "700" },
  liveModeChipTextActive: { color: "#fff" },
});
