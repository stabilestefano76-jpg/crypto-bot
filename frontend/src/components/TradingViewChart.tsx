import { useEffect, useMemo, useRef, useState } from "react";
import { LayoutChangeEvent, StyleSheet, View } from "react-native";
import { WebView } from "react-native-webview";
import { api, Candle } from "@/src/api";
import { buildTvChartHtml, CHART_BARS, toTvCandles, TvLevels } from "@/src/utils/tvChart";

type Props = {
  symbol: string;
  timeframe: string;
  candles: Candle[];
  levels: TvLevels;
  height?: number;
};

export default function TradingViewChart({
  symbol,
  timeframe,
  candles,
  levels,
  height = 260,
}: Props) {
  const ref = useRef<WebView>(null);
  const [width, setWidth] = useState(0);

  const html = useMemo(
    () => (width > 0 ? buildTvChartHtml(candles, levels, width, height) : ""),
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
          testID="tv-chart"
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { width: "100%", borderRadius: 12, overflow: "hidden", backgroundColor: "#0E1116" },
  wv: { flex: 1, backgroundColor: "#0E1116" },
});
