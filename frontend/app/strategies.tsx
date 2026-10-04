import { Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { colors, font, radius, spacing } from "@/src/theme";

type StrategyEntry = {
  key: string;
  title: string;
  subtitle: string;
  icon: keyof typeof Ionicons.glyphMap;
  route: string;
};

const STRATEGIES: StrategyEntry[] = [
  {
    key: "rsi_reversion",
    title: "RSI Reversion",
    subtitle: "Rientro da ipercomprato / ipervenduto",
    icon: "pulse",
    route: "/strategy/rsi_reversion",
  },
  {
    key: "s3360",
    title: "33/60",
    subtitle: "Compra al primo calo dell'RSI, vende quando risale",
    icon: "trending-up",
    route: "/s3360",
  },
  {
    key: "xrp_acc",
    title: "XRP Accumulation",
    subtitle: "Compra XRP con RSI basso, il profitto resta in XRP",
    icon: "diamond",
    route: "/xrp-accumulation",
  },
];

export default function StrategiesHubScreen() {
  const insets = useSafeAreaInsets();
  const router = useRouter();

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}>
          <Ionicons name="chevron-back" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>Strategie</Text>
        <View style={{ width: 26 }} />
      </View>
      <Text style={styles.subtitle}>Scegli una strategia per aprire la sua sezione</Text>

      <View style={styles.list}>
        {STRATEGIES.map((s) => (
          <Pressable
            key={s.key}
            onPress={() => router.push(s.route as any)}
            style={({ pressed }) => [styles.card, pressed && { opacity: 0.7 }]}
          >
            <View style={styles.iconWrap}>
              <Ionicons name={s.icon} size={22} color={colors.brand} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.cardTitle}>{s.title}</Text>
              <Text style={styles.cardSubtitle}>{s.subtitle}</Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={colors.onSurfaceTertiary} />
          </Pressable>
        ))}
      </View>
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
    marginBottom: spacing.lg,
  },
  list: { paddingHorizontal: spacing.lg, gap: spacing.sm },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.lg,
  },
  iconWrap: {
    width: 40,
    height: 40,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceTertiary,
    alignItems: "center",
    justifyContent: "center",
  },
  cardTitle: { color: colors.onSurface, fontSize: font.base, fontWeight: "700" },
  cardSubtitle: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: 2 },
});
