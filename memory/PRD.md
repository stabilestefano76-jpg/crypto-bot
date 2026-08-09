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
