# KuSignal Bot - PRD

## ⚠️ Migrazione Exchange: KuCoin → Bybit EU (in corso, Fase 1 completata)
- **Fase 1 (DONE)**: sostituito interamente il layer dati KuCoin con **Bybit EU v5** (`https://api.bybit.eu`, WS `wss://stream.bybit.eu/v5/public/{spot|linear}`). Nuovo `BybitClient` (get_symbols/get_tickers/get_klines normalizzati alle stesse shape di prima), `PriceFeed` riscritto su WS Bybit v5, `get_trade_fees`/`get_funding_rate`/`_current_spread` adattati, credenziali/`/exchange/*` passate a firma HMAC Bybit v5 (id doc "bybit", passphrase rimossa). `exchange.category` = spot|linear sincronizzato da `trading_mode`.
- **Vincoli Bybit EU (MiCA) scoperti**:
  - Spot EU quotato in **USDC** (113 coppie) / EUR, NON USDT (solo 4 USDT). Default `quote_filter`="USDC", `min_24h_volume_usdt`=100k.
  - **Nessun mercato leverage/perpetui (linear) su bybit.eu** (category=linear → 0 strumenti). La modalità "leverage" non ha mercato derivati sull'entità EU.
  - Simboli senza trattino ("BTCUSDC"). Klines: ms→s, colonne [t,o,h,l,c,v] rimappate a [t,o,c,h,l,v]; scartata l'ultima candela in formazione.
- Verifica Fase 1: `/api/candles` live (200 candele ascending), `/api/pairs` USDC, scan 19 coppie/3 segnali, WS connesso e prezzi in cache. Nessun riferimento KuCoin residuo.
## ✅ Fase 2 (DONE) — Strategia "FVG Reversal" + selezione parallela
- Nuova strategia `analyze_pair_fvg_reversal` (contro-trend sul ritracciamento verso la FVG d'impulso; entrata sul reversal, target dentro la FVG; SL oltre l'estremo dell'impulso; TP1=bordo vicino, TP2=midpoint 65/35). Parametri INDIPENDENTI `fvgr_*` (rsi extremes, tp1/tp2, post_tp1, trailing, atr mult, min_rr).
- Gestione dedicata `manage_fvg_reversal_position`: TP1 65% → SL invariato → oltre +fvgr_post_tp1_advance% da TP1 sposta SL a TP1(±fee) → trailing fvgr_trailing% in profitto → TP2 chiude il resto.
- **Selezione parallela**: `enabled_strategies: list[str]` (scoring|impulse_fvg|counter_trend|fvg_reversal). Lo scan esegue TUTTE le strategie abilitate in parallelo, capitale condiviso, ogni segnale taggato con `strategy`. Fallback legacy su `strategy_mode`.
- Coppie: quote multipli `USDC,EUR` (helper split). UI settings: chip multi-select + sezione "FVG Reversal Strategy" con i parametri indipendenti.
- Verifiche: scan parallelo 4 strategie senza errori; test sintetici FVG Reversal (entry short SL>entry, TP1/TP2 dentro FVG; gestione TP1 65% / post-TP1 SL→TP1-fee / TP2) TUTTI PASS. Coppie EUR presenti (18).





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

## Reset Signal History (Parte 3)
Pulsante "Reset" nell'header della Signal History → modal di conferma ("Azione irreversibile") → `DELETE /api/signals` cancella tutto lo storico segnali. Non tocca generazione segnali, Portfolio o Settings. Verificato dal testing agent (11/11 backend + flusso UI).

## Strategia 3: Reversal Pre-FVG con breakout del consolidamento (sostituisce la vecchia counter_trend)
`strategy_mode="counter_trend"` (chip UI "Rev Pre-FVG") — logica riscritta secondo spec-exact dell'utente:
- **Sequenza**: impulso → FVG non colmata lasciata indietro → **consolidamento** (box stretto ≤ `consolidation_max_atr`×ATR su `consolidation_min_candles`) → **pattern di reversal DENTRO il box** (engulfing/star) → **breakout in chiusura** del box.
- **Direzione del trade = direzione del breakout** (rottura sopra il massimo del box → LONG; sotto il minimo → SHORT). NON più basata sul market structure macro.
- **Target = FVG lasciata dall'impulso, nella direzione del breakout**: LONG → FVG ribassista sopra (fill verso l'alto); SHORT → FVG rialzista sotto (fill verso il basso).
- **TP2 = bordo opposto (lontano) della FVG** (35%); **TP1 = livello intermedio dentro la FVG** (midpoint, 65%). Entry = livello di breakout (box_high per long / box_low per short).
- **SL iniziale**: oltre il box (long: box_low − buffer; short: box_high + buffer), buffer = max(ATR×`atr_sl_multiplier`, entry×`sl_padding_pct`). Gate R:R su TP2 ≥ `min_rr_ratio`.
- **Conferme (AND)**: volume spike sul breakout + filtri RSI già definiti (HTF estremo, momentum turn sul TF del trade, divergenza coerente). Segnali rari per design.
- Rimossa la vecchia logica: entry immediato al pattern contro-trend, SL sull'estremo dell'impulso, TP1=bordo vicino/TP2=50%.

## Parte 2: Gestione Stop-Loss post-TP1 (per la strategia Reversal Pre-FVG)
`manage_counter_position` (nuovo, sostituisce il breakeven generico per queste posizioni):
- A TP1 (chiusura candela oltre TP1) chiude `tp1_pct` 65%; **lo SL resta lo stop ATR originale** (NIENTE breakeven immediato — rimosso il vecchio "breakeven al secondo/doppio tocco").
- Per il residuo 35%: quando il prezzo avanza di **`post_tp1_advance_pct` (default 0.5%) oltre TP1**, lo SL si sposta esattamente a **TP1 + commissioni** (maker+taker via API KuCoin). Se il +0.5% non viene mai raggiunto, lo SL ATR resta.
- TP2 chiude il residuo (gestito sia a chiusura candela in `manage_counter_position` sia su prezzo live nel monitor).
- Config nuovo: `post_tp1_advance_pct`. Monitor `tp_check` esteso a `counter_trend` (come impulse_fvg).
- UI: sezione "Reversal Pre-FVG Strategy" in Settings (min candele, ampiezza box, TP1/TP2, avanzamento post-TP1); TP1/TP2 mostrati nel Detail anche per counter_trend + spiegazioni pattern/RSI. Chip rinominato "Rev Pre-FVG" (key invariata `counter_trend`).
- Validato con test sintetici: entry LONG breakout (SL sotto box, TP1=midpoint FVG, TP2=bordo lontano) e macchina a stati post-TP1 (TP1 65% → SL resta ATR → +0.5% → SL a TP1+fee → TP2 win).

## Bugfix RSI alignment
`get_klines` scarta la candela in formazione → logica di segnale e grafico usano le stesse candele CHIUSE con la stessa `rsi_wilder`. Verificato dal testing agent (22 passati).

## Strategia 2: Impulse FVG + Consolidamento + TP multipli (additiva, selezionabile)
Nuova strategia selezionabile via `strategy_mode` ("scoring" | "impulse_fvg" | "both"), senza modificare quella a punteggio né lo stop ATR:
- **Trend** da struttura di mercato: HH+HL = up, LH+LL = down, altrimenti range (nessun setup)
- **Candela d'impulso** → FVG d'origine = target finale TP2 (FVG non colmato di gap maggiore in direzione trend)
- **Consolidamento** = rettangolo trigger (≥ `consolidation_min_candles` entro canale < `consolidation_max_atr`×ATR)
- **Entry** su rottura in CHIUSURA del box, SOLO nella direzione del trend (long in up, short in down — mai invertito)
- **SL** sotto/sopra il box (non l'FVG) + buffer ATR/spread esistente
- **TP1** = bordo FVG non colmato più vicino (chiude `tp1_pct` 65%, poi sposta il resto a breakeven a chiusura oltre TP1); **TP2** = bordo FVG d'origine (35%); fallback trailing se manca TP2
- Esecuzione TP1/TP2 gestita in `manage_impulse_position` nel monitor paper (il timeout/1R-partial generico non gira per queste posizioni)
- Config: `strategy_mode`, `impulse_atr_mult`, `consolidation_min_candles`, `consolidation_max_atr`, `tp1_pct`, `tp2_pct`. Endpoint `POST /api/strategy-mode`
- UI: selettore strategia (3 vie) + sezione "Impulse FVG Strategy" in Settings; TP1/TP2 e ragionamento (Market Structure / Impulse FVG / Consolidation Breakout) nel Detail
- Verificato: 15/15 backend, invarianti di direzione rispettati (segnali impulse rari per design)

## Position Management: Timeout + Breakeven + Trailing (moduli additivi)
Tre moduli nuovi, integrati SOLO nel monitor paper esistente (nessuna modifica a FVG/RSI/scoring/stop ATR):
- **TimeoutManager**: tabella per timeframe (15m=14, 1h=7, 4h=5, 1d=3 candele). Se candele trascorse ≥ timeout e profit_in_R < 0.3 → sposta stop a breakeven
- **BreakevenCalculator**: SPOT = entry + (fee_maker+fee_taker via API KuCoin + spread medio da level1 + margine 0.05%); LEVERAGE aggiunge funding accumulato = funding_rate×(ore/8) via API futures. Fallback con warning se fee/funding non disponibili (non blocca)
- **TrailingStopManager**: attivo quando profit_in_R ≥ 1.0; trailing = ultimo swing (riusa detect_pivots) ∓ ATR(14)×1.2; ricalcolo solo a chiusura nuova candela sul TF del trade; non arretra mai; clamp leverage a ≥25% dalla liquidazione quando nota
- **Chiusura parziale** 35% al raggiungimento di 1R (una volta), il resto continua col trailing
- Nuovi campi Config (timeout_*, breakeven_safety_pct, default_fee_rate, trailing_*, partial_close_*, liq_min_distance_pct) e PaperPosition (current_stop, initial_risk, breakeven_active, trailing_active, partial_closed, liquidation_price)
- UI: badge "Breakeven attivo" / "Trailing attivo" / "Parziale 35%" sulla card della posizione (unica modifica UI)
- Testato spot 1h come da spec (8/8 backend passati)

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
