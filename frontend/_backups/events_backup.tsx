import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { createAudioPlayer } from "expo-audio";
import * as Haptics from "expo-haptics";
import { eventsApi, BotEvent } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

// Beep breve generato al volo (nessun file audio esterno necessario).
const BEEP_URI =
  "data:audio/wav;base64,UklGRmQLAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YUALAAAAAEEAyQANAZYAYv/6/Tn90P3H/1kCQgRXBDYCnP4m+535DPsZ/xIEmwfPBz0ER/6b+AH2/ff6/WUFyAppC6QGZv5h9m7yrfRs/E0GwA0cD2cJ+f6A9PDuJPFx+sUGeRDbEn4MAAAA85Lrbe0N+MwG6RKeFuEPewHl8V/okOlG9V0GCBVYGokTaAM38WDlmeUh8ngFzhb/HWwXxQX58J/ikuGk7hwEMxiIIYEbjQgv8Sfghd3Y6kkCMBnnJL4fvgve8QHef9nD5gAAwBkTKBokUA8G8zTcitVu4kT93BkAK4goPhOp9MrastHj3Rb6gRmmLf8sghfI9snZAc4r2Xz2qhj5L3QxExxi+TnZgspR1HnyVBfxMdo16iB2/B/ZQcdfzxTufxWGMyY6/SUAAIHZSMRgylTpKBOvNE0+Qyv+A2PaBMIYxsHk6g/GM98/py4FCLbdI8OAxCzh/QtPMf8/TzH9CyzhgMQjw7bdBQinLt8/xjPqD8HkGMYEwmPaBATPK34/CTbGE3Ho7McjwTXXAADLKN0+FDiPFzrs98mCwDHU/PudJfw96Dk/GxbwOswhwFnR+/dKIt08gDvUHgP0sc4BwLHOA/TUHoA73TxKIvv3WdEhwDrMFvA/G+g5/D2dJfz7MdSCwPfJOuyPFxQ43T7LKAAANdcjwezHcejGEwk2fj/PKwQEY9oEwhjGweTqD8Yz3z+nLgUItt0jw4DELOH9C08x/z9PMf0LLOGAxCPDtt0FCKcu3z/GM+oPweQYxgTCY9oEBM8rfj8JNsYTcejsxyPBNdcAAMso3T4UOI8XOuz3yYLAMdT8+50l/D3oOT8bFvA6zCHAWdH790oi3TyAO9QeA/SxzgHAsc4D9NQegDvdPEoi+/dZ0SHAOswW8D8b6Dn8PZ0l/Psx1ILA98k67I8XFDjdPssoAAA11yPB7Mdx6MYTCTZ+P88rBARj2gTCGMbB5OoPxjPfP6cuBQi23SPDgMQs4f0LTzH/P08x/Qss4YDEI8O23QUIpy7fP8Yz6g/B5BjGBMJj2gQEzyt+Pwk2xhNx6OzHI8E11wAAyyjdPhQ4jxc67PfJgsAx1Pz7nSX8Peg5PxsW8DrMIcBZ0fv3SiLdPIA71B4D9LHOAcCxzgP01B6AO908SiL791nRIcA6zBbwPxvoOfw9nSX8+zHUgsD3yTrsjxcUON0+yygAADXXI8Hsx3HoxhMJNn4/zysEBGPaBMIYxsHk6g/GM98/py4FCLbdI8OAxCzh/QtPMf8/TzH9CyzhgMQjw7bdBQinLt8/xjPqD8HkGMYEwmPaBATPK34/CTbGE3Ho7McjwTXXAADLKN0+FDiPFzrs98mCwDHU/PudJfw96Dk/GxbwOswhwFnR+/dKIt08gDvUHgP0sc4BwLHOA/TUHoA73TxKIvv3WdEhwDrMFvA/G+g5/D2dJfz7MdSCwPfJOuyPFxQ43T7LKAAANdcjwezHcejGEwk2fj/PKwQEY9oEwhjGweTqD8Yz3z+nLgUItt0jw4DELOH9C08x/z9PMf0LLOGAxCPDtt0FCKcu3z/GM+oPweQYxgTCY9oEBM8rfj8JNsYTcejsxyPBNdcAAMso3T4UOI8XOuz3yYLAMdT8+50l/D3oOT8bFvA6zCHAWdH790oi3TyAO9QeA/SxzgHAsc4D9NQegDvdPEoi+/dZ0SHAOswW8D8b6Dn8PZ0l/Psx1ILA98k67I8XFDjdPssoAAA11yPB7Mdx6MYTCTZ+P88rBARj2gTCGMbB5OoPxjPfP6cuBQi23SPDgMQs4f0LTzH/P08x/Qss4YDEI8O23QUIpy7fP8Yz6g/B5BjGBMJj2gQEzyt+Pwk2xhNx6OzHI8E11wAAyyjdPhQ4jxc67PfJgsAx1Pz7nSX8Peg5PxsW8DrMIcBZ0fv3SiLdPIA71B4D9LHOAcCxzgP01B6AO908SiL791nRIcA6zBbwPxvoOfw9nSX8+zHUgsD3yTrsjxcUON0+yygAADXXI8Hsx3HoxhMJNn4/zysEBGPaBMIYxsHk6g/GM98/py4FCLbdI8OAxCzh/QtPMf8/TzH9CyzhgMQjw7bdBQinLt8/xjPqD8HkGMYEwmPaBATPK34/CTbGE3Ho7McjwTXXAADLKN0+FDiPFzrs98mCwDHU/PudJfw96Dk/GxbwOswhwFnR+/dKIt08gDvUHgP0sc4BwLHOA/TUHoA73TxKIvv3WdEhwDrMFvA/G+g5/D2dJfz7MdSCwPfJOuyPFxQ43T7LKAAANdcjwezHcejGEwk2fj/PKwQEY9oEwhjGweTqD8Yz3z+nLgUItt0jw4DELOH9C08x/z9PMf0LLOGAxCPDtt0FCKcu3z/GM+oPweQYxgTCY9oEBM8rfj8JNsYTcejsxyPBNdcAAMso3T4UOI8XOuz3yYLAMdT8+50l/D3oOT8bFvA6zCHAWdH790oi3TyAO9QeA/SxzgHAsc4D9NQegDvdPEoi+/dZ0SHAOswW8D8b6Dn8PZ0l/Psx1ILA98k67I8XFDjdPssoAAA11yPB7Mdx6MYTCTZ+P88rBARj2gTCGMbB5OoPxjPfP6cuBQi23SPDgMQs4f0LTzH/P08x/Qss4YDEI8O23QUIpy7fP8Yz6g/B5BjGBMJj2gQEzyt+Pwk2xhNx6OzHI8E11wAAyyjdPhQ4jxc67PfJgsAx1Pz7nSX++2nU/MCCynnsNBcZN5s94CcAAFTYBsO+yUTpCBPfM8s81inTA0TcRcVIyVLm9Q6KMLg7gCt1BzLgs8ccyaPjAAseLWU63yzhChrkTco5yTrhLQejKdg48y0VDvTnC82cyRbfgQMdJhQ3vS4OEb7r6c9ByjrdAACSIh41Py/KE3Dv4tIly6TbrfwIH/wyey9GFgjz79VEzFTajPmEG7EwdC+CGID2DNmazUvZnvYMGEMuKy99GtP5Mtwkz4bY6POkFLgrpS42HAD9Xd/c0ATYa/FQERQp5C2tHQAAh+K90sPXKO8XDl0m6yziHtICrOXE1MLXIu37CpgjwCvXH3QFxejr1vzXWOsBCMogZiqLIOIHz+st2XDYzOksBfkd4CgBIRsKxO6F2xvZfuiAAikbNSc7IRwMofHv3fjZbOcAAF8YZyU6IeYNYvRk4ATbmOau/aEVfCMBIXYPAvfh4jzc/uWN+/ISeSGUIM0Qf/lg5Zrdn+We+VcQYx/0H+oR1fvc5xzfeOXj99QNPh0mH84SAf5R6rvghuVc9m0LDxssHnoTAAC67HXiyeUM9SUJ2xgMHe4T0QET70PkPOby8wAHphbIGy0UcgNY8SLm3eYO8wEFdhRmGjcU4wSE8w3oqOdf8isDTxLoGBAUIAaU9f/pmujl8X8BNRBVF7kTKweF9/Trr+mf8QAALQ6wFTUTAghU+ebt5OqL8bD+OQz9E4cSpgj9+tLvNOyo8Y79XwpCErMRGAl//LTxmu3z8Z38oQiDELwQWAnW/YbzE+9p8t37BAfEDqYPZwkC/0X1m/AJ8077iQUJDXUORwkAAO32LPLO8/D6MwRYCywN+wjQAHv4wvO29ML6BgO1CdALgwhxAer5WvW99cP6AgIiCGYK4wfjATn77fbf9vL6KQGlBvEIHgcmAmT8efgZ+E37fgBBBXUHNgY5Amn9+fln+dL7AAD6A/gFMAUeAkb+afvE+n/8sf/SAn4EDQTWAfj+xPwr/FL9kP/MAQsD0wJjAX7/CP6a/Uf+nf/sAKIBhQHFANf/L/8L/1v/2P80AEoAJwA=";

const SECTION_LABELS: Record<string, string> = {
  counter_trend: "Rev Pre-FVG",
  fvg_reversal: "FVG Reversal",
  rsi_reversion: "RSI Reversion",
  scalping: "Scalping",
  grid: "Grid",
};

const SECTION_COLORS: Record<string, string> = {
  counter_trend: "#3B82F6",
  fvg_reversal: "#A855F7",
  rsi_reversion: "#F59E0B",
  scalping: "#10B981",
  grid: "#EC4899",
};

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "adesso";
  if (m < 60) return `${m}m fa`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h fa`;
  return `${Math.floor(h / 24)}g fa`;
}

function money(n?: number | null): string {
  if (n === undefined || n === null || isNaN(n)) return "";
  const sign = n >= 0 ? "+" : "";
  return `${sign}$${n.toFixed(2)}`;
}

export default function EventsScreen() {
  const insets = useSafeAreaInsets();
  const [events, setEvents] = useState<BotEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [soundOn, setSoundOn] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const lastSeenAt = useRef<string | null>(null);
  const firstLoadDone = useRef(false);
  const soundOnRef = useRef(soundOn);
  soundOnRef.current = soundOn;

  const playAlert = useCallback(async (positive: boolean | null) => {
    if (soundOnRef.current) {
      try {
        const player = createAudioPlayer({ uri: BEEP_URI });
        player.play();
        setTimeout(() => {
          try {
            player.remove();
          } catch {
            // ignora: la pulizia del player non è critica
          }
        }, 1500);
      } catch {
        // silenzioso: il suono è un bonus, non deve mai bloccare l'app
      }
    }
    try {
      if (positive === true) {
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      } else if (positive === false) {
        await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      } else {
        await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      }
    } catch {
      // idem: la vibrazione non deve mai bloccare l'app
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await eventsApi.list(150);
      const fresh = res.events;
      setError(null);

      if (!firstLoadDone.current) {
        // Al primo caricamento non suoniamo per lo storico: registriamo solo
        // il punto di partenza da cui iniziare a segnalare i NUOVI eventi.
        firstLoadDone.current = true;
        lastSeenAt.current = fresh[0]?.at ?? null;
      } else if (lastSeenAt.current) {
        const newOnes = fresh.filter((e) => e.at > lastSeenAt.current!);
        if (newOnes.length > 0) {
          // Suona una volta sola anche se sono arrivati più eventi insieme.
          const anyNegativeClose = newOnes.some(
            (e) => e.type === "close" && (e.pnl_usdt ?? 0) < 0
          );
          const anyPositiveClose = newOnes.some(
            (e) => e.type === "close" && (e.pnl_usdt ?? 0) >= 0
          );
          const anyOpen = newOnes.some((e) => e.type === "open");
          const positive = anyPositiveClose && !anyNegativeClose
            ? true
            : anyNegativeClose && !anyPositiveClose && !anyOpen
              ? false
              : null;
          playAlert(positive);
          lastSeenAt.current = fresh[0]?.at ?? lastSeenAt.current;
        }
      }
      setEvents(fresh);
    } catch (e: any) {
      setError(e?.message || "Errore di caricamento");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [playAlert]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Eventi</Text>
          <Text style={styles.subtitle}>
            Aperture e chiusure di tutte le sezioni, in ordine cronologico
          </Text>
        </View>
        <View style={styles.soundRow}>
          <Ionicons
            name={soundOn ? "volume-high" : "volume-mute"}
            size={18}
            color={colors.onSurfaceSecondary}
          />
          <Switch
            value={soundOn}
            onValueChange={setSoundOn}
            trackColor={{ true: colors.brand, false: colors.surfaceTertiary }}
            thumbColor={colors.onSurface}
          />
        </View>
      </View>

      {loading && events.length === 0 ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.brand} />
      ) : error && events.length === 0 ? (
        <Text style={styles.errorText}>{error}</Text>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.brand} />
          }
        >
          {events.length === 0 ? (
            <View style={styles.empty}>
              <Ionicons name="notifications-off-outline" size={40} color={colors.onSurfaceTertiary} />
              <Text style={styles.emptyText}>Nessun evento ancora.</Text>
            </View>
          ) : (
            events.map((e) => {
              const isClose = e.type === "close";
              const pnl = e.pnl_usdt ?? null;
              const pnlColor = pnl !== null ? (pnl >= 0 ? colors.success : colors.error) : colors.onSurfaceSecondary;
              const sectionColor = SECTION_COLORS[e.section] || colors.brand;
              return (
                <View key={e.id} style={styles.card}>
                  <View style={[styles.iconWrap, { backgroundColor: sectionColor + "22" }]}>
                    <Ionicons
                      name={isClose ? (pnl !== null && pnl >= 0 ? "checkmark-circle" : "close-circle") : "arrow-forward-circle"}
                      size={20}
                      color={isClose ? pnlColor : sectionColor}
                    />
                  </View>
                  <View style={{ flex: 1 }}>
                    <View style={styles.cardTopRow}>
                      <Text style={styles.symbol}>{e.symbol}</Text>
                      <View style={[styles.sectionTag, { borderColor: sectionColor }]}>
                        <Text style={[styles.sectionTagText, { color: sectionColor }]}>
                          {SECTION_LABELS[e.section] || e.section}
                        </Text>
                      </View>
                    </View>
                    <Text style={styles.metaText}>
                      {isClose ? "Chiusura" : "Apertura"}
                      {e.side ? ` · ${e.side === "long" ? "LONG" : "SHORT"}` : ""}
                    </Text>
                  </View>
                  <View style={{ alignItems: "flex-end" }}>
                    {isClose && pnl !== null && (
                      <Text style={[styles.pnl, { color: pnlColor }]}>{money(pnl)}</Text>
                    )}
                    <Text style={styles.time}>{timeAgo(e.at)}</Text>
                  </View>
                </View>
              );
            })
          )}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
  },
  title: { color: colors.onSurface, fontSize: font.xl, fontWeight: "700" },
  subtitle: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: 2, maxWidth: 260 },
  soundRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 },
  errorText: { color: colors.error, textAlign: "center", marginTop: 40, paddingHorizontal: spacing.lg },
  empty: { alignItems: "center", gap: spacing.sm, marginTop: 60 },
  emptyText: { color: colors.onSurfaceSecondary },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  iconWrap: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
  },
  cardTopRow: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
  symbol: { color: colors.onSurface, fontSize: font.base, fontWeight: "700" },
  sectionTag: {
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  sectionTagText: { fontSize: 10, fontWeight: "700" },
  metaText: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: 2 },
  pnl: { fontSize: font.base, fontWeight: "800" },
  time: { color: colors.onSurfaceTertiary, fontSize: 11, marginTop: 2 },
});
