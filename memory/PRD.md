# KuSignal Bot - PRD

## Overview
Mobile React Native / Expo app che scansiona periodicamente le coppie spot USDT su KuCoin e genera segnali di trading in caso di **confluenza** tra: divergenza RSI, Fair Value Gap (FVG) aperto, spike di volume ed allineamento EMA.

Scope MVP: **solo motore segnali** (no esecuzione ordini, no auth, notifiche in-app).

## Backend (FastAPI + MongoDB + KuCoin REST)
- `GET /api/status` — stato scan (last_scan_at, signals_found, is_scanning)
- `GET /api/config` / `PUT /api/config` — configurazione parametri
- `GET /api/pairs` — coppie disponibili con prezzo/volume
- `GET /api/signals?side&timeframe&status` — segnali (default active)
- `GET /api/signals/{id}` — dettaglio segnale
- `GET /api/candles/{symbol}?timeframe=1h` — OHLCV + RSI
- `POST /api/scan` — trigger scan manuale
- `GET /api/history/stats` — statistiche win-rate

Scheduler asyncio in background esegue `run_scan()` ogni `scan_interval_minutes` (default 5). Ogni scan:
1. Fetch tickers 24h → filtro coppie USDT con volume ≥ soglia
2. Ordina per volume, cappa a `max_pairs_per_scan`
3. Batch 20 coppie in parallelo × timeframe (default 1h, 4h)
4. Per ciascuna coppia: RSI Wilder, pivot detection, divergenza, FVG, volume spike, EMA fast/slow
5. Genera Signal quando divergenza + FVG dello stesso segno allineati; entry al bordo FVG, SL oltre zona con padding %, TP a R:R configurabile

## Frontend (Expo Router)
- **(tabs)/index** — Home Signals: header sticky, segmented Long/Short, chip TF, pulsante Scan Now, cards ordinati per forza
- **(tabs)/portfolio** — Paper Trading portfolio: equity, cash, realized/unrealized PnL, win rate, posizioni aperte con Close manuale, storico trades
- **(tabs)/history** — Storico + stats win-rate
- **(tabs)/settings** — Form completo per parametri + sezione Paper Trading (auto-execute, initial capital, risk %, max positions) + disclaimer
- **detail/[id]** — Grafico candlestick SVG con zone FVG, linee entry/SL/TP, sub-grafico RSI + bottone sticky "Execute Paper"

## FVG Reversal Entry (estensione condizioni, non sostituzione)
Nuova condizione di entry **integrata nel sistema a punteggio esistente** (stesso calcolo di soglia, nessun secondo scoring):
- **"FVG Reversal"** = inversione confermata dentro la zona FVG, con peso configurabile (default 2 → max score ora 8)
- Sotto-segnali (riusano la logica esistente dove possibile): **Rejection Candle** (wick lungo nella direzione del fill), **Reversal Volume** (spike vs media), **Change of Character** (rottura mini swing locale), **RSI Divergence (FVG)** (riusa `detect_rsi_divergence`). Conferma se ≥ `reversal_min_signals` (default 2)
- **Target sul fill**: quando la condizione è attiva, il take-profit diventa il bordo opposto della FVG (o il 50% "consequent encroachment", via `fvg_fill_mode`). R:R rivalidato con lo **stop ATR invariato**; scarta se sotto `min_rr_ratio` o se il target è già superato/troppo vicino
- **Invalidazione**: se il prezzo rompe la FVG dal lato opposto al fill (chiusura oltre il bordo strutturale), il setup viene scartato subito
- **Logging**: i sotto-segnali contribuenti sono salvati su ogni segnale (`reversal_signals`) e nei setup scartati (stesso formato del debug esistente)
- **Stop-loss ATR/strutturale: NON modificato.** Sistema a punteggio: NON duplicato
- UI: peso "Punti inversione FVG" + sezione "FVG Reversal Entry" (min segnali, wick ratio, target fill) in Settings; chip dei sotto-segnali nel Detail

## Sistema a punteggio (sostituisce logica AND rigida)
Un trade si apre in base a un **punteggio di confluenza** pesato, non richiedendo più tutti i filtri insieme:
- Pesi (tutti configurabili da Settings): FVG=2, RSI divergence=2, Volume=1, EMA cross=1 → max 6
- **Soglia minima** configurabile (default 4): basta FVG+RSI (2+2) OPPURE FVG+Volume+EMA (2+1+1)
- FVG resta l'ancora strutturale (definisce entry/SL) su lookback configurabile (`fvg_lookback` 40); nessuna condizione richiesta sulla stessa candela (`signal_validity_candles` 5)
- **Log setup scartati** (`setup_debug_log`, azzerato a ogni scan): per ogni setto sotto soglia salva passed/failed + score. Endpoint `/api/setup-debug/log` con analisi del **collo di bottiglia** (filtro che fallisce più spesso) + `DELETE` per pulizia
- Config aggiuntivi: `score_fvg`, `score_rsi_divergence`, `score_volume`, `score_ma_cross`, `min_score_threshold`, `signal_validity_candles`, `fvg_lookback`
- Signal ora include `score` e `max_score`
- UI: sezione "Scoring System" in Settings (pesi+soglia editabili), badge score/max sulle card, pannello "Setup Bottleneck" nella History con barre di fallimento per filtro

## Stop-Loss ATR-based (overhaul)
Per ridurre gli stop prematuri:
- **ATR (Wilder, periodo 14 configurabile)** calcolato sul timeframe di entry
- SL posizionato **oltre il bordo opposto della zona FVG** (long: sotto il fondo; short: sopra il top) con buffer = **max(1.5×ATR, sl_padding_pct 0.3%)** → resistente ai wick di liquidità nel retest
- **Gate R:R minimo 1:1.5**: i setup sotto la soglia vengono scartati prima dell'apertura
- **Stop-debug log**: ogni stop-loss salva entry/stop/ATR/distanza-in-ATR; un checker in background (ogni 120s) guarda avanti fino a `premature_lookahead` candele (default 20) e marca lo stop `premature` (il target sarebbe stato raggiunto) o `valid`. Endpoint `/api/stop-debug/log` con premature_rate e avg_stop_distance_atr
- Config aggiuntivi: `atr_period`, `atr_sl_multiplier`, `min_rr_ratio`, `premature_lookahead`
- **Percorso unico di calcolo SL** in `analyze_pair` (confluenza RSI-divergence + FVG): nessun modulo duplicato
- UI: pannello "Stop-Loss Debug" nella History + metriche ATR/Stop-distance nel Detail

## Real-time & Live-trading safety (WebSocket)
- **KuCoin WebSocket** (`/api/v1/bullet-public` → wss ticker feed): cache prezzi in tempo reale, auto-subscribe ai simboli delle posizioni aperte, ping/reconnect automatici, fallback REST se il socket cade. Monitor SL/TP ora gira ogni **3s** sui prezzi live invece dei 60s REST.
- **Esecuzione immediata**: la posizione viene riempita al prezzo di mercato live (`fill_price`) nell'istante del segnale, non al prezzo pianificato.
- **Sicurezza**: `max_position_size_usdt` (default 10) cappa il notional per singola operazione; `one_position_per_pair` impedisce più posizioni sulla stessa coppia.
- **Slippage log** (`slippage_log` collection): salva signal_price vs fill_price per ogni esecuzione, con % e fonte (ws/rest). Endpoint `/api/slippage/log` (con aggregati avg%/total impact) e `/api/feed/status`. Pannello "Slippage Monitor" + badge LIVE WS nella schermata History; sezione "Live Safety" in Settings.

## Paper Trading
Portfolio virtuale simulato con **due modalità di mercato**:

- **SPOT** (default): cash lockato all'apertura, solo posizioni LONG, quantità limitata dal cash disponibile → simulazione realistica del mercato spot
- **LEVERAGE**: PnL stile futures, LONG & SHORT ammessi, position sizing basato solo sul risk (no cash locking) → esplora scenari più aggressivi

Il cambio di modalità è bloccato quando ci sono posizioni aperte (evita inconsistenze contabili).

**Controlli rapidi in cima al Portfolio**:
1. **Market Type** — Spot vs Leverage
2. **Execution Mode** — Manual vs Auto
3. **Initial Capital** — input + "Set & Reset"
4. **Connect KuCoin API** — bottom-sheet per collegamento sicuro, credenziali cifrate at-rest (Fernet)

Endpoint: `/api/paper/config`, `/api/paper/portfolio`, `/api/paper/execute/{id}`, `/api/paper/positions/{id}/close`, `/api/paper/trades`, `/api/paper/reset`, `/api/paper/set-capital`, `/api/paper/mode`, `/api/paper/trading-mode`, `/api/exchange/status`, `/api/exchange/connect`, `/api/exchange/disconnect`.

## Design
Dark-first utility (personality 7): base #0b0e14, brand ambra #F5B300, success #00C076, error #FF554A. Nessun blu/viola. Testo Rajdhani per numeri, IBM Plex Sans per UI.

## Disclaimer visibile
In Settings e in Detail — "Solo analisi tecnica, non consulenza finanziaria".
