import { useRouter } from "expo-router";
import { ScrollView, StyleSheet, Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { colors, font, radius, spacing } from "@/src/theme";

type Lesson = {
  id: string;
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  body: string;
  key_points: string[];
};

const LESSONS: Lesson[] = [
  {
    id: "confluence",
    icon: "layers",
    title: "Confluenza — la regola d'oro",
    body:
      "Un singolo indicatore mente. Più segnali indipendenti che convergono sullo stesso scenario aumentano drasticamente la probabilità che il movimento sia reale. Questo bot apre un setup SOLO quando due o più criteri tecnici puntano nella stessa direzione.",
    key_points: [
      "1 conferma = ipotesi, non decisione",
      "2 conferme = setup accettabile",
      "3+ conferme = setup ad alta probabilità",
    ],
  },
  {
    id: "rsi",
    icon: "analytics",
    title: "RSI 14 (Relative Strength Index)",
    body:
      "L'RSI misura la forza del movimento su una scala 0-100. Sopra 70 = ipercomprato (probabile correzione). Sotto 30 = ipervenduto (probabile rimbalzo). Ma il vero segnale è la DIVERGENZA fra prezzo e RSI: quando il prezzo fa un nuovo minimo ma l'RSI no, il momentum ribassista sta finendo.",
    key_points: [
      "Divergenza rialzista: prezzo LL, RSI HL → long",
      "Divergenza ribassista: prezzo HH, RSI LH → short",
      "Usato con timeframe 1h e 4h per filtrare rumore",
    ],
  },
  {
    id: "fvg",
    icon: "layers-outline",
    title: "Fair Value Gap (FVG)",
    body:
      "Un FVG è un vuoto lasciato dal prezzo tra la candela A (i-2) e la candela C (i): il gap fra il high di A e il low di C (bullish) o viceversa (bearish). Il mercato tende a tornare a 'riempire' quei gap prima di continuare il trend. Sono zone naturali di entry.",
    key_points: [
      "Bullish FVG: high[i-2] < low[i] → zona di supporto",
      "Bearish FVG: low[i-2] > high[i] → zona di resistenza",
      "Il bot entra al bordo della zona ancora aperta",
    ],
  },
  {
    id: "volume",
    icon: "bar-chart",
    title: "Analisi dei volumi",
    body:
      "Il volume conferma la validità di un movimento. Uno spike di volume su una candela di svolta = grandi player stanno operando. Senza volume, il pattern tecnico è spesso una trappola.",
    key_points: [
      "Spike = volume corrente > 1.5× media(20)",
      "Volume basso su breakout = rischio fakeout",
      "Aumenta la 'strength' del segnale finale",
    ],
  },
  {
    id: "ema",
    icon: "trending-up",
    title: "Medie mobili EMA 20/50",
    body:
      "Le EMA identificano il trend dominante. Se EMA20 > EMA50 il trend è rialzista; viceversa ribassista. Operare CONTRO il trend generale ha probabilità ridotta: il bot preferisce setup allineati al trend.",
    key_points: [
      "EMA veloce sopra la lenta = trend up",
      "EMA veloce sotto la lenta = trend down",
      "Filtro opzionale ma consigliato",
    ],
  },
  {
    id: "risk",
    icon: "shield-checkmark",
    title: "Risk management (R:R)",
    body:
      "Il vero edge di un trader non è il win-rate ma il rapporto rischio/rendimento. Con R:R 1:2 basta un win-rate del 34% per essere in profitto. Il bot calcola sempre: rischio massimo per trade (% capitale) → quantità automatica in base alla distanza entry-SL.",
    key_points: [
      "SL sempre PRIMA di conoscere il TP",
      "Rischia solo % piccola del capitale (1-2%)",
      "R:R minimo 1:1.5, ideale 1:2 o superiore",
    ],
  },
  {
    id: "disclaimer",
    icon: "warning",
    title: "Disclaimer & responsabilità",
    body:
      "Questo strumento genera segnali basati su regole matematiche note. Non è consulenza finanziaria, non predice il futuro. Il mercato può muoversi in modi imprevisti (news, macro, manipolazione). Ogni operazione comporta rischio di perdita del capitale.",
    key_points: [
      "Nessun sistema tecnico ha un edge del 100%",
      "Backtest ≠ risultato futuro garantito",
      "Non rischiare mai denaro che non puoi permetterti di perdere",
    ],
  },
];

export default function AcademyScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable
          onPress={() => router.back()}
          hitSlop={12}
          testID="academy-back"
        >
          <Ionicons name="chevron-back" size={24} color={colors.onSurface} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.title} testID="academy-title">
            Academy
          </Text>
          <Text style={styles.subtitle}>Come funziona questo bot</Text>
        </View>
        <Ionicons name="school" size={22} color={colors.brand} />
      </View>
      <ScrollView contentContainerStyle={styles.body}>
        <Text style={styles.intro}>
          Ogni segnale prodotto nasce dall'incrocio di più criteri tecnici. Qui
          trovi le basi per capire cosa sta guardando l'algoritmo.
        </Text>
        {LESSONS.map((l, idx) => (
          <View key={l.id} style={styles.card} testID={`lesson-${l.id}`}>
            <View style={styles.cardHead}>
              <View style={styles.iconWrap}>
                <Ionicons name={l.icon} size={18} color={colors.brand} />
              </View>
              <Text style={styles.cardTitle}>
                {String(idx + 1).padStart(2, "0")} · {l.title}
              </Text>
            </View>
            <Text style={styles.cardBody}>{l.body}</Text>
            <View style={styles.pointsWrap}>
              {l.key_points.map((p) => (
                <View key={p} style={styles.pointRow}>
                  <Ionicons
                    name="checkmark-circle"
                    size={14}
                    color={colors.brand}
                  />
                  <Text style={styles.pointText}>{p}</Text>
                </View>
              ))}
            </View>
          </View>
        ))}
        <View style={styles.footer}>
          <Ionicons name="library" size={14} color={colors.onSurfaceTertiary} />
          <Text style={styles.footerText}>
            Applica queste regole prima di aprire operazioni con denaro reale.
          </Text>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  title: { color: colors.onSurface, fontWeight: "800", fontSize: font.xl },
  subtitle: { color: colors.onSurfaceSecondary, fontSize: font.sm },
  body: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  intro: { color: colors.onSurfaceSecondary, fontSize: font.base, lineHeight: 20 },
  card: {
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  cardHead: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  iconWrap: {
    width: 32,
    height: 32,
    borderRadius: radius.sm,
    backgroundColor: colors.brandTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  cardTitle: {
    color: colors.onSurface,
    fontWeight: "800",
    fontSize: font.base,
    flex: 1,
  },
  cardBody: {
    color: colors.onSurfaceSecondary,
    fontSize: font.sm,
    lineHeight: 20,
  },
  pointsWrap: { gap: 6, marginTop: 4 },
  pointRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  pointText: { color: colors.onSurface, fontSize: font.sm, flex: 1 },
  footer: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: spacing.md,
  },
  footerText: { color: colors.onSurfaceTertiary, fontSize: 11, flex: 1 },
});
