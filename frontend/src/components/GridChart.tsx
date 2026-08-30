import { useEffect, useMemo, useRef, useState } from "react";
import { LayoutChangeEvent, StyleSheet, View } from "react-native";
import { WebView } from "react-native-webview";
import { api, Candle } from "@/src/api";
import { buildGridChartHtml, CHART_BARS, toTvCandles, GridChartLevels } from "@/src/utils/tvChart";

type Props = {
  symbol: string;
  timeframe: string;
  candles: Candle[];
  levels: GridChartLevels;
  height?: number;
};

/** Same rendering approach as TradingViewChart, but draws the grid's buy/sell
 * cell levels instead of a single Entry/SL/TP/FVG setup. Candle updates are
 * polled internally (every 5s); grid level updates (cells filling/closing)
 * are pushed in via the `levels` prop as soon as the parent screen refreshes. */
export default function GridChart({
  symbol,
  timeframe,
  candles,
  levels,
  height = 260,
}: Props) {
  const ref = useRef<WebView>(null);
  const [width, setWidth] = useState(0);

  const html = useMemo(
    () => (width > 0 ? buildGridChartHtml(candles, levels, width, height) : ""),
    // rebuild only when the measured width is first known
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [width, height]
  );

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const c = await api.candles(symbol, timeframe);
        const payload = JSON.stringify({
          type: "update",
          candles: toTvCandles(c.candles, CHART_BARS),
        });
        ref.current?.injectJavaScript(`window.applyUpdate(${payload});true;`);
      } catch {
        /* ignore transient poll errors */
      }
    }, 5000);
    return () => clearInterval(id);
  }, [symbol, timeframe]);

  useEffect(() => {
    const payload = JSON.stringify({ type: "update", levels });
    ref.current?.injectJavaScript(`window.applyUpdate(${payload});true;`);
  }, [levels]);

  const onLayout = (e: LayoutChangeEvent) => {
    const w = Math.round(e.nativeEvent.layout.width);
    if (w > 0 && w !== width) setWidth(w);
  };

  return (
    <View style={[styles.wrap, { height }]} onLayout={onLayout}>
      {html ? (
        <WebView
          ref={ref}
          originWhitelist={["*"]}
          source={{ html }}
          style={styles.wv}
          javaScriptEnabled
          domStorageEnabled
          scrollEnabled={false}
          androidLayerType="hardware"
          setSupportMultipleWindows={false}
          testID="grid-chart"
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { width: "100%", borderRadius: 12, overflow: "hidden", backgroundColor: "#0E1116" },
  wv: { flex: 1, backgroundColor: "#0E1116" },
});
