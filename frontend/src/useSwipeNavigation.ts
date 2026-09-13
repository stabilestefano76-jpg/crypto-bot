import { useRef } from "react";
import { PanResponder } from "react-native";
import { useRouter } from "expo-router";

// Order strategies appear in when swiping left/right. Keep this the single
// source of truth — every strategy screen imports it, so adding/reordering
// a strategy here updates the swipe sequence everywhere at once.
export const STRATEGY_ORDER = [
  "/scalping",
  "/grid",
  "/top10",
  "/rsi-rebound",
  "/wyckoff",
  "/rsi-reversion",
] as const;

export type StrategyPath = (typeof STRATEGY_ORDER)[number];

const SWIPE_DISTANCE_THRESHOLD = 50; // px of horizontal movement to count as a swipe
const DIRECTION_RATIO = 2; // horizontal movement must dominate vertical by this much before we claim the gesture, so vertical scrolling in the page is never interrupted

/**
 * Lets a strategy screen be swiped left/right to the next/previous one in
 * STRATEGY_ORDER. Spread the returned panHandlers onto the screen's root
 * View. Swiping past either end of the list does nothing (no wrap-around).
 */
export function useSwipeNavigation(current: StrategyPath) {
  const router = useRouter();
  const index = STRATEGY_ORDER.indexOf(current);

  const panResponder = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_evt, gesture) =>
        Math.abs(gesture.dx) > 20 &&
        Math.abs(gesture.dx) > Math.abs(gesture.dy) * DIRECTION_RATIO,
      onPanResponderRelease: (_evt, gesture) => {
        if (gesture.dx <= -SWIPE_DISTANCE_THRESHOLD && index < STRATEGY_ORDER.length - 1) {
          router.replace(STRATEGY_ORDER[index + 1] as any);
        } else if (gesture.dx >= SWIPE_DISTANCE_THRESHOLD && index > 0) {
          router.replace(STRATEGY_ORDER[index - 1] as any);
        }
      },
    })
  ).current;

  return { panHandlers: panResponder.panHandlers, index, total: STRATEGY_ORDER.length };
}
