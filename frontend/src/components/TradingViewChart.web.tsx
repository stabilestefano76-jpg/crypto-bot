import React, { useEffect, useMemo, useRef, useState } from "react";
import { LayoutChangeEvent, StyleSheet, View } from "react-native";
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
  const iframeRef = useRef<any>(null);
  const [width, setWidth] = useState(0);

  const html = useMemo(
    () => (width > 0 ? buildTvChartHtml(candles, levels, width, height) : ""),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [width, height]
  );

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const c = await api.candles(symbol, timeframe);
        iframeRef.current?.contentWindow?.postMessage(
          { type: "update", candles: toTvCandles(c.candles, CHART_BARS) },
          "*"
        );
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
      {html
        ? React.createElement("iframe", {
            ref: iframeRef,
            srcDoc: html,
            title: "tv-chart",
            // @ts-ignore - DOM-only attributes rendered by react-dom on web
            sandbox: "allow-scripts allow-same-origin",
            style: {
              border: "none",
              display: "block",
              width: `${width}px`,
              height: `${height}px`,
              borderRadius: 12,
            },
          })
        : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { width: "100%", borderRadius: 12, overflow: "hidden", backgroundColor: "#0E1116" },
});
