import { StyleSheet, View } from "react-native";
import { colors, spacing } from "@/src/theme";

/** Small row of dots showing position among the swipeable strategy screens. */
export default function SwipeDots({ index, total }: { index: number; total: number }) {
  if (index < 0) return null;
  return (
    <View style={styles.row}>
      {Array.from({ length: total }).map((_, i) => (
        <View
          key={i}
          style={[styles.dot, i === index && styles.dotActive]}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    justifyContent: "center",
    gap: 6,
    marginTop: 4,
    marginBottom: spacing.xs,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.surfaceTertiary,
  },
  dotActive: {
    backgroundColor: colors.brand,
    width: 16,
  },
});
