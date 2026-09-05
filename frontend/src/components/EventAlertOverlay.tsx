import { useCallback, useEffect, useRef, useState } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { createAudioPlayer } from "expo-audio";
import * as Haptics from "expo-haptics";
import { eventsApi, BotEvent } from "@/src/api";
import { colors, font, radius, spacing } from "@/src/theme";

// Beep breve generato al volo (nessun file audio esterno necessario).
const BEEP_URI =
  "data:audio/wav;base64,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";

const TRADITIONAL_SECTIONS = new Set(["counter_trend", "fvg_reversal", "rsi_reversion"]);

const SECTION_LABELS: Record<string, string> = {
  counter_trend: "Rev Pre-FVG",
  fvg_reversal: "FVG Reversal",
  rsi_reversion: "RSI Reversion",
};

function money(n?: number | null): string {
  if (n === undefined || n === null || isNaN(n)) return "";
  const sign = n >= 0 ? "+" : "";
  return `${sign}$${n.toFixed(2)}`;
}

/**
 * Overlay globale, montato una sola volta nel layout radice — quindi visibile
 * sopra QUALSIASI schermata dell'app, non solo dentro la scheda "Eventi".
 * Suono + popup scattano SOLO per le 3 strategie tradizionali (Rev Pre-FVG,
 * FVG Reversal, RSI Reversion), volutamente esclusi Scalping e Grid che sono
 * molto più frequenti e altrimenti renderebbero l'avviso rumore di fondo
 * invece di un segnale raro e significativo.
 */
export function EventAlertOverlay() {
  const insets = useSafeAreaInsets();
  const [soundOn, setSoundOn] = useState(true);
  const [current, setCurrent] = useState<BotEvent | null>(null);
  const queueRef = useRef<BotEvent[]>([]);
  const lastSeenAt = useRef<string | null>(null);
  const firstLoadDone = useRef(false);
  const soundOnRef = useRef(soundOn);
  soundOnRef.current = soundOn;
  const opacity = useRef(new Animated.Value(0)).current;
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showNextRef = useRef<() => void>(() => {});

  const playAlert = useCallback(async () => {
    if (soundOnRef.current) {
      try {
        const player = createAudioPlayer({ uri: BEEP_URI });
        player.play();
        setTimeout(() => {
          try {
            player.remove();
          } catch {
            // ignora: pulizia non critica
          }
        }, 1500);
      } catch {
        // silenzioso: il suono è un bonus, non deve mai bloccare l'app
      }
    }
    try {
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch {
      // idem
    }
  }, []);

  const showNext = useCallback(() => {
    setCurrent((prev) => {
      if (prev) return prev; // uno alla volta, non sovrapporre popup
      const next = queueRef.current.shift();
      if (!next) return null;
      opacity.setValue(0);
      Animated.timing(opacity, { toValue: 1, duration: 250, useNativeDriver: true }).start();
      playAlert();
      if (hideTimer.current) clearTimeout(hideTimer.current);
      hideTimer.current = setTimeout(() => {
        Animated.timing(opacity, { toValue: 0, duration: 250, useNativeDriver: true }).start(() => {
          setCurrent(null);
          setTimeout(() => showNextRef.current(), 300);
        });
      }, 15000);
      return next;
    });
  }, [opacity, playAlert]);
  showNextRef.current = showNext;

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await eventsApi.list(150);
        if (!alive) return;
        const fresh = res.events.filter((e) => TRADITIONAL_SECTIONS.has(e.section));

        if (!firstLoadDone.current) {
          // Al primo caricamento non avvisiamo per lo storico: registriamo
          // solo il punto di partenza da cui segnalare i NUOVI eventi.
          firstLoadDone.current = true;
          lastSeenAt.current = fresh[0]?.at ?? null;
        } else if (lastSeenAt.current) {
          const newOnes = fresh.filter((e) => e.at > lastSeenAt.current!);
          if (newOnes.length > 0) {
            queueRef.current.push(...newOnes.slice().reverse());
            lastSeenAt.current = fresh[0]?.at ?? lastSeenAt.current;
            showNextRef.current();
          }
        }
      } catch {
        // silenzioso: un fallimento di polling non deve disturbare l'app
      }
    };
    poll();
    const t = setInterval(poll, 10000);
    return () => {
      alive = false;
      clearInterval(t);
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, []);

  return (
    <>
      <Pressable
        style={[styles.muteBtn, { top: insets.top + 6 }]}
        onPress={() => setSoundOn((v) => !v)}
        hitSlop={10}
      >
        <Ionicons
          name={soundOn ? "volume-high" : "volume-mute"}
          size={16}
          color={colors.onSurfaceSecondary}
        />
      </Pressable>

      {current && (
        <Animated.View pointerEvents="none" style={[styles.popup, { top: insets.top + 42, opacity }]}>
          <View style={styles.popupIconWrap}>
            <Ionicons
              name={
                current.type === "close"
                  ? (current.pnl_usdt ?? 0) >= 0
                    ? "checkmark-circle"
                    : "close-circle"
                  : "arrow-forward-circle"
              }
              size={22}
              color={
                current.type === "close"
                  ? (current.pnl_usdt ?? 0) >= 0
                    ? colors.success
                    : colors.error
                  : colors.brand
              }
            />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.popupTitle}>
              {current.type === "close" ? "Posizione chiusa" : "Posizione aperta"} · {current.symbol}
            </Text>
            <Text style={styles.popupSubtitle}>
              {SECTION_LABELS[current.section] || current.section}
              {current.side ? ` · ${current.side === "long" ? "LONG" : "SHORT"}` : ""}
              {current.type === "close" && current.pnl_usdt !== undefined
                ? ` · ${money(current.pnl_usdt)}`
                : ""}
            </Text>
          </View>
        </Animated.View>
      )}
    </>
  );
}

const styles = StyleSheet.create({
  muteBtn: {
    position: "absolute",
    right: spacing.md,
    zIndex: 50,
    elevation: 50,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.pill,
    width: 28,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  popup: {
    position: "absolute",
    left: spacing.md,
    right: spacing.md,
    zIndex: 100,
    elevation: 100,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.surfaceSecondary,
    borderRadius: radius.lg,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: "#000",
    shadowOpacity: 0.3,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
  },
  popupIconWrap: { width: 32, height: 32, alignItems: "center", justifyContent: "center" },
  popupTitle: { color: colors.onSurface, fontWeight: "700", fontSize: font.base },
  popupSubtitle: { color: colors.onSurfaceSecondary, fontSize: font.sm, marginTop: 2 },
});
