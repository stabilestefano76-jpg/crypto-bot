import { Candle } from "@/src/api";

export type TvLevels = {
  entry: number;
  sl: number;
  tp1?: number;
  tp2?: number;
  fvgTop: number;
  fvgBottom: number;
  side: "long" | "short";
};

export type TvCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

/** Map backend candles -> lightweight-charts format (unix seconds, ascending).
 *  When maxBars > 0, keep only the most recent bars so the price scale stays
 *  tight around the relevant setup (Entry/SL/TP/FVG) instead of being blown up
 *  by far-away historical price. */
export function toTvCandles(candles: Candle[], maxBars = 0): TvCandle[] {
  const src = maxBars > 0 ? candles.slice(-maxBars) : candles;
  return src
    .filter((c) => c && isFinite(c.t))
    .map((c) => ({
      time: Math.floor(c.t),
      open: c.o,
      high: c.h,
      low: c.l,
      close: c.c,
    }));
}

/** Number of most-recent bars to show on the detail chart. */
export const CHART_BARS = 60;

/**
 * Build a self-contained HTML document that renders a TradingView
 * Lightweight Charts candlestick chart with Entry/SL/TP1/TP2 price lines and a
 * shaded FVG zone. Live updates arrive via window.applyUpdate(payload) (native
 * injectJavaScript) or postMessage {type:'update', candles} (web iframe).
 */
export function buildTvChartHtml(
  candles: Candle[],
  levels: TvLevels,
  width: number,
  height: number
): string {
  const data = JSON.stringify(toTvCandles(candles, CHART_BARS));
  const lv = JSON.stringify(levels);
  const W = Math.max(50, Math.round(width));
  const H = Math.max(50, Math.round(height));
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no"/>
<style>
  html,body{margin:0;padding:0;height:100%;width:100%;background:#0E1116;overflow:hidden;}
  #wrap{position:relative;height:100%;width:100%;}
  #c{position:absolute;top:0;left:0;right:0;bottom:0;}
  #fvg{position:absolute;left:0;right:52px;pointer-events:none;display:none;
       border-top:1px dashed;border-bottom:1px dashed;box-sizing:border-box;}
  #fvglabel{position:absolute;left:6px;font:700 9px -apple-system,Roboto,sans-serif;
            padding:1px 4px;border-radius:3px;pointer-events:none;display:none;}
</style>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
<div id="wrap"><div id="c"></div><div id="fvg"></div><div id="fvglabel">FVG</div></div>
<script>
var DATA = ${data};
var LV = ${lv};
var W = ${W}, H = ${H};
var chart, series, fvgEl, fvgLabel;

function ready(){ if(!window.LightweightCharts){ return setTimeout(ready, 60); } init(); }

function init(){
  var el = document.getElementById('c');
  chart = LightweightCharts.createChart(el, {
    width: W, height: H,
    layout: { background: { color: '#0E1116' }, textColor: '#8A93A2', fontSize: 10 },
    grid: { vertLines: { color: '#161C24' }, horzLines: { color: '#161C24' } },
    rightPriceScale: { borderColor: '#1C2530' },
    timeScale: { borderColor: '#1C2530', timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
    handleScale: true, handleScroll: true
  });
  series = chart.addCandlestickSeries({
    upColor: '#00C076', downColor: '#FF554A', borderVisible: false,
    wickUpColor: '#00C076', wickDownColor: '#FF554A'
  });
  if (DATA && DATA.length) series.setData(DATA);
  addLevels();
  fvgEl = document.getElementById('fvg');
  fvgLabel = document.getElementById('fvglabel');
  var isLong = LV.side === 'long';
  fvgEl.style.background = isLong ? 'rgba(0,192,118,0.12)' : 'rgba(255,85,74,0.12)';
  fvgEl.style.borderColor = isLong ? '#00C076' : '#FF554A';
  fvgLabel.style.background = isLong ? 'rgba(0,192,118,0.22)' : 'rgba(255,85,74,0.22)';
  fvgLabel.style.color = isLong ? '#00C076' : '#FF554A';
  function fit(){
    try {
      // Nudge the size to force a full repaint (series pane sometimes stays
      // blank after the initial synchronous setData in a srcdoc iframe).
      chart.resize(W - 1, H); chart.resize(W, H);
      chart.timeScale().fitContent();
      drawFvg();
    } catch(e){}
  }
  fit();
  [60, 200, 500, 900, 1500].forEach(function(ms){ setTimeout(fit, ms); });
  setInterval(drawFvg, 200);
}

function addLevels(){
  function pl(price, color, title){
    if (price == null || !isFinite(price)) return;
    series.createPriceLine({ price: price, color: color, lineWidth: 1,
      lineStyle: 2, axisLabelVisible: true, title: title });
  }
  pl(LV.entry, '#3B82F6', 'Entry');
  pl(LV.sl, '#FF554A', 'SL');
  pl(LV.tp1, '#00C076', 'TP1');
  pl(LV.tp2, '#12B886', 'TP2');
}

function drawFvg(){
  if (!fvgEl || !series || LV.fvgTop == null) return;
  var yTop = series.priceToCoordinate(LV.fvgTop);
  var yBot = series.priceToCoordinate(LV.fvgBottom);
  if (yTop == null || yBot == null){ fvgEl.style.display='none'; fvgLabel.style.display='none'; return; }
  var top = Math.min(yTop, yBot), h = Math.abs(yBot - yTop);
  fvgEl.style.display = 'block'; fvgEl.style.top = top + 'px'; fvgEl.style.height = h + 'px';
  fvgLabel.style.display = 'block'; fvgLabel.style.top = (top + 2) + 'px';
}

function applyUpdate(d){
  try {
    if (d && d.candles && d.candles.length) series.setData(d.candles);
    if (d && d.levels) LV = d.levels;
    drawFvg();
  } catch (e) {}
}
window.applyUpdate = applyUpdate;

function onMsg(e){
  try {
    var d = typeof e.data === 'string' ? JSON.parse(e.data) : e.data;
    if (!d) return;
    if (d.type === 'update') applyUpdate(d);
    else if (d.type === 'resize' && chart && d.width > 0 && d.height > 0) {
      chart.resize(d.width, d.height);
      chart.timeScale().fitContent();
      drawFvg();
    }
  } catch (err) {}
}
window.addEventListener('message', onMsg);
document.addEventListener('message', onMsg);

ready();
</script>
</body>
</html>`;
}
