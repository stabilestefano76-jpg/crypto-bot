# Crypto Trading Bot (Bybit EU)

Automated crypto trading bot with a mobile dashboard. It scans the market on
**Bybit EU (v5)** and produces signals from several technical-analysis
strategies, with paper-trading and (optional) real order execution.

- **Backend:** FastAPI (Python) + MongoDB — market scanning, WebSocket live
  prices, TA logic, paper-trading engine, order/position management.
- **Frontend:** Expo / React Native (Expo Router) — mobile dashboard for
  signals, portfolio, settings and a live TradingView chart.

## Trading strategies

All strategies can run **in parallel** on a shared capital, each with its own
independent parameters, filters and TP/SL management:

1. **Scoring** — weighted confluence (Divergence, FVG, Volume, EMA, RSI, …).
2. **Impulse FVG** — trend + impulse + consolidation breakout with TP1/TP2.
3. **Rev Pre-FVG** — reversal inside a consolidation, entry on the breakout
   toward the FVG fill.
4. **FVG Reversal** — counter-trend on the retracement back into the impulse
   FVG; entry on a reversal candle pattern, target inside the FVG.

### Order / risk management (per strategy, independent)
- Split take-profit: **TP1 65% / TP2 35%**.
- Dynamic ATR-based stop loss.
- **Post-TP1 stop:** after TP1 is hit, once price advances +0.50% beyond TP1
  the stop on the remainder is moved to TP1 (net of fees).
- **Trailing stop** (FVG Reversal): trails once the trade is in profit.
- Position sizing based on a configurable risk level.
- Paper trading by default; real execution via stored Bybit API credentials.

## Requirements

- Python 3.11+
- Node.js 18+ and Yarn
- MongoDB 5+ (local or Atlas)

## Backend — install & run

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then edit MONGO_URL / DB_NAME
uvicorn server:app --host 0.0.0.0 --port 8001
```

The API is served under the `/api` prefix (e.g. `GET /api/signals`).
Background loops (scanner, price feed, position monitor) start automatically.

> Exchange API keys are **not** needed for paper trading. To trade for real,
> open the app → Portfolio → **Connect Bybit API** and paste an API key/secret
> (read + trade only, never withdrawals). Credentials are encrypted at rest.

## Frontend — install & run

```bash
cd frontend
yarn install

cp .env.example .env      # set EXPO_PUBLIC_BACKEND_URL to your backend URL
yarn start                # or: npx expo start
```

Open the app in **Expo Go** (scan the QR code) or run on a simulator.
The app calls `EXPO_PUBLIC_BACKEND_URL` + `/api/...`.

## Environment variables

**backend/.env**

| Variable    | Description                |
|-------------|----------------------------|
| `MONGO_URL` | MongoDB connection string  |
| `DB_NAME`   | Database name              |

**frontend/.env**

| Variable                  | Description                                |
|---------------------------|--------------------------------------------|
| `EXPO_PUBLIC_BACKEND_URL` | Backend base URL (without trailing `/api`) |

## Notes

- **Bybit EU (MiCA)** offers **spot only** (no perpetual/leverage market) and
  quotes in **USDC / EUR** (not USDT).
- Signals are generated on **closed candles**; live prices come from the Bybit
  v5 public WebSocket.
- This software is provided as-is, for educational purposes. Trading crypto
  carries significant risk — use paper trading first and never risk funds you
  cannot afford to lose.
