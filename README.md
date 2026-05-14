<<<<<<< HEAD
# ⬡ Nifty Options Bot v2

A modular, production-ready algorithmic trading bot for **Nifty 50 index options** on Indian markets.  
Uses **ICICIdirect Breeze API** for live market data and executes all orders in **paper trading mode** (zero real money at risk).

---

## 📋 Table of Contents

- [What This Bot Does](#what-this-bot-does)
- [Architecture Overview](#architecture-overview)
- [Folder Structure](#folder-structure)
- [Quick Start](#quick-start)
- [Daily Morning Checklist](#daily-morning-checklist)
- [Configuration Guide](#configuration-guide)
- [Supported Strategies](#supported-strategies)
- [Dashboard Guide](#dashboard-guide)
- [Risk Management](#risk-management)
- [Broker Integration](#broker-integration)
- [Adding a New Strategy](#adding-a-new-strategy)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)

---

## What This Bot Does

- Connects to **ICICIdirect Breeze API** and pulls real Nifty spot price, live options chain LTP, IV, OI, and volume every few seconds
- At your configured entry time (default **9:20 AM IST**), automatically selects the ATM strike and places a **paper trade** (simulated, no real money)
- Monitors positions in real time and applies **per-position stop-loss and targets**
- Enforces a **daily loss limit** — the bot halts and squares off all positions if the limit is hit
- Displays everything on a **live 5-tab dashboard** at `http://127.0.0.1:8050`
- Squares off all positions at **3:10 PM IST** automatically

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                              │
│              (boot, wiring, startup sequence)               │
└──────┬──────────┬────────────┬──────────────┬──────────────┘
       │          │            │              │
  ┌────▼───┐ ┌───▼────┐ ┌─────▼─────┐ ┌─────▼──────┐
  │  Data  │ │ Order  │ │   Risk    │ │ Dashboard  │
  │  Feed  │ │Manager │ │  Engine   │ │  (Dash)    │
  └────┬───┘ └───┬────┘ └─────┬─────┘ └────────────┘
       │         │            │
  ┌────▼─────────▼────────────▼─────┐
  │         Strategy Engine         │
  │  (ATM Straddle / Strangle /     │
  │   Iron Condor / Naked C/P)      │
  └─────────────┬───────────────────┘
                │
  ┌─────────────▼───────────────────┐
  │    ICICIdirect Breeze API       │
  │  Live Data  |  Paper Orders     │
  └─────────────────────────────────┘
```

| Layer | File | Purpose |
|---|---|---|
| Data | `data/breeze_feed.py` | Live spot + options chain from Breeze |
| Broker | `broker/breeze_client.py` | Breeze API client + paper order execution |
| Strategy | `strategy/strategy.py` | Entry/exit logic for all strategies |
| Orders | `strategy/orders.py` | Order lifecycle, position tracking, trade log |
| Risk | `risk/risk_engine.py` | Daily loss halt, SL, target, trailing SL |
| Dashboard | `dashboard/app.py` | 5-tab Plotly Dash UI |
| Config | `core/config_loader.py` | YAML config singleton |
| Models | `core/models.py` | Order, Position, Greeks, RiskSnapshot dataclasses |

---

## Folder Structure

```
nifty_options_bot/
│
├── main.py                        ← Entry point
├── requirements.txt               ← Python dependencies
├── README.md                      ← This file
│
├── config/
│   └── config.yml                 ← All runtime parameters
│
├── core/
│   ├── config_loader.py           ← YAML loader (singleton)
│   └── models.py                  ← Data models (Order, Position, Greeks...)
│
├── data/
│   ├── data_feed.py               ← Paper/simulation data feed
│   └── breeze_feed.py             ← Live ICICIdirect data feed
│
├── broker/
│   ├── broker_client.py           ← Base class + PaperBrokerClient
│   └── breeze_client.py           ← ICICIdirect Breeze adapter
│
├── strategy/
│   ├── strategy.py                ← Strategy engine + all strategy classes
│   └── orders.py                  ← Order Management System (OMS)
│
├── risk/
│   └── risk_engine.py             ← Risk rules + halt logic
│
├── dashboard/
│   └── app.py                     ← Plotly Dash 5-tab dashboard
│
├── tools/
│   └── get_session_token.py       ← Morning session token helper
│
├── utils/
│   └── logger.py                  ← Rotating file + console logger
│
└── logs/                          ← Auto-created log files
```

---

## Quick Start

### 1. Clone / unzip the project
```bash
cd ~/Desktop
unzip nifty_options_bot_v2.zip
cd nifty_options_bot
```

### 2. Create and activate a virtual environment
```bash
# Create
python -m venv venv

# Activate (Windows Git Bash / MINGW64)
source venv/Scripts/activate

# Activate (Mac / Linux)
source venv/bin/activate
```

You should see `(venv)` at the start of your terminal prompt.

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the dashboard in demo mode (no credentials needed)
```bash
python dashboard/app.py
```

Open your browser at **http://127.0.0.1:8050**

---

## Daily Morning Checklist

Do this every trading day before **9:15 AM IST**.

### Step 1 — Get today's session token
Breeze session tokens expire every evening. Run this once each morning:
```bash
python tools/get_session_token.py
```
- Your browser opens the ICICI Direct login page
- Log in with your ICICI Direct credentials
- After login you are redirected to a URL like:
  ```
  https://api.icicidirect.com/?apisession=XXXXXXXXXX
  ```
- The script copies the token automatically into `config/config.yml`

### Step 2 — Start the bot
```bash
python main.py
```

### Step 3 — Open the dashboard
```
http://127.0.0.1:8050
```

### Step 4 — Watch the bot
- At **9:20 AM** the bot evaluates the ATM strike and enters the configured strategy
- Monitor the **Overview** and **Risk & Greeks** tabs throughout the session
- At **3:10 PM** the bot squares off all positions automatically

### Step 5 — End of day
Press `Ctrl + C` in the terminal to stop the bot.  
Log files are saved in the `logs/` folder.

---

## Configuration Guide

All parameters live in `config/config.yml`. You never need to touch the Python code for routine changes.

### Broker section
```yaml
broker:
  name: icicidirect          # icicidirect | paper
  paper_trading: true        # NEVER set this to false

  api_key: "YOUR_KEY"        # from https://api.icicidirect.com/
  api_secret: "YOUR_SECRET"
  session_token: "..."       # refreshed each morning by get_session_token.py

  slippage_pct: 0.1          # simulated fill slippage for paper orders
```

### Strategy section
```yaml
strategy:
  name: atm_straddle         # see Supported Strategies below
  entry_time: "09:20"        # HH:MM IST
  exit_time: "15:10"         # forced square-off time
  lots: 1                    # number of lots per leg
  atm_offset: 0              # 0 = ATM, 1 = 1 strike OTM, etc.
  strangle_width: 2          # strikes away from ATM (for strangle)
```

### Risk section
```yaml
risk:
  max_loss_per_day: 3000     # bot halts if MTM loss hits this (₹)
  target_profit_per_day: 5000
  position_stop_loss: 2000   # per-leg SL (₹)
  position_target: 1500      # per-leg target (₹)
  max_trades_per_day: 1      # max fresh entries per session
  max_open_positions: 2      # max simultaneous open legs
  trailing_sl: false         # set true to enable trailing stop-loss
  trailing_sl_trigger: 1500  # ₹ profit at which trailing activates
  trailing_sl_distance: 800  # ₹ trail distance from peak profit
```

---

## Supported Strategies

| Strategy Name | Description | Legs |
|---|---|---|
| `atm_straddle` | Short ATM CE + Short ATM PE | 2 |
| `atm_strangle` | Short OTM CE + Short OTM PE (configurable width) | 2 |
| `iron_condor` | Short inner strikes + Long outer strikes | 4 |
| `naked_call` | Short OTM CE only | 1 |
| `naked_put` | Short OTM PE only | 1 |

Change strategy in `config.yml`:
```yaml
strategy:
  name: atm_strangle
  strangle_width: 2          # ±2 strikes from ATM
```

---

## Dashboard Guide

Open **http://127.0.0.1:8050** after starting the bot.  
The dashboard auto-refreshes every 3–5 seconds.

### Tab 1 — Overview
- **KPI strip** at the top: Net PnL, Unrealised, Open Risk, Max Adverse Excursion, Win Rate, Expectancy, IV Rank, Nifty Spot
- **Rolling equity curve** with coloured markers for Entry, Exit, SL Hit, Hedge Added
- **Breakeven zones** overlaid on the Nifty spot chart
- **Performance stat boxes**: Win Rate, Avg Win, Avg Loss, Profit Factor, Expectancy, Max Drawdown

### Tab 2 — Risk & Greeks
- Real-time Greeks: Delta, Gamma, Theta, Vega with limit lines
- IV chart and IV Rank (52-week percentile)
- Delta drift over time with breach alerts
- Margin used, gross exposure, risk per trade %
- Expected move vs actual move for the session

### Tab 3 — Analytics
- PnL distribution histogram
- Edge analysis by hour of day, day of week, strike distance
- Leg-wise analytics: CE contribution, PE contribution, theta captured
- Slippage distribution

### Tab 4 — Positions
- Open positions with leg-wise Delta, IV, Slippage, Theta
- Live order status panel
- Bot health indicators: API latency, WS heartbeat, strategy loop status
- Slippage by order type

### Tab 5 — Strategy
- Current market regime: trend/range, IV environment, time of day, news event
- Bot mode: WAITING / ACTIVE / HOLD / HALTED
- Config snapshot (strategy version tag)
- Backtest vs live comparison table
- Trade journal and active alerts

---

## Risk Management

All limits are enforced automatically. The bot will **halt and square off all positions** if any of these are breached:

| Rule | Default | Config Key |
|---|---|---|
| Max daily loss | ₹3,000 | `risk.max_loss_per_day` |
| Daily profit target | ₹5,000 | `risk.target_profit_per_day` |
| Per-position stop-loss | ₹2,000 | `risk.position_stop_loss` |
| Per-position target | ₹1,500 | `risk.position_target` |
| Max trades per day | 1 | `risk.max_trades_per_day` |
| Max open legs | 2 | `risk.max_open_positions` |
| Forced exit time | 3:10 PM | `strategy.exit_time` |

When the daily loss limit is hit, a red **BOT HALTED** banner appears on the dashboard.

---

## Broker Integration

### ICICIdirect Breeze (active)

| Feature | Status |
|---|---|
| Nifty spot price | ✅ Live REST |
| Options chain LTP | ✅ Live REST |
| Websocket ticks | ✅ Live streaming |
| Order placement | 📄 Paper only |
| Position tracking | 📄 In-memory |

**SDK:** `breeze-connect`  
**Docs:** https://api.icicidirect.com/apiuser/home  
**GitHub:** https://github.com/Idirect-Tech/Breeze-Python-SDK

### Adding another broker
Subclass `BaseBrokerClient` in `broker/broker_client.py`:
```python
class ZerodhaClient(BaseBrokerClient):
    def login(self): ...
    def place_order(self, order): ...
    # implement all abstract methods
```
Then register it in the `create_broker_client()` factory function.

---

## Adding a New Strategy

1. Open `strategy/strategy.py`
2. Subclass `BaseStrategy`:
```python
class BullCallSpread(BaseStrategy):
    def get_legs(self, atm_strike, expiry, data_feed):
        # return list of (OptionContract, OrderSide) tuples
        buy_strike  = atm_strike
        sell_strike = atm_strike + 2 * self.strike_step
        # ... build contracts and return legs
        return [(buy_contract, OrderSide.BUY),
                (sell_contract, OrderSide.SELL)]
```
3. Register it:
```python
STRATEGY_REGISTRY["bull_call_spread"] = BullCallSpread
```
4. Set in config:
```yaml
strategy:
  name: bull_call_spread
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'dash'`
Your virtual environment is not active. Run:
```bash
source venv/Scripts/activate   # Windows
source venv/bin/activate        # Mac/Linux
```

### `ModuleNotFoundError: No module named 'breeze_connect'`
```bash
pip install breeze-connect
```

### `Breeze login failed`
- Session token expires every evening — run `python tools/get_session_token.py` each morning
- Check your API key and secret are correct in `config/config.yml`
- Make sure your ICICI Direct account has API access enabled

### Dashboard shows `0.0.0.0:8050` — site can't be reached
Use **http://127.0.0.1:8050** not `0.0.0.0:8050`

### Bot not entering trade at 9:20 AM
- Check system clock is IST
- Verify `strategy.entry_time` in config
- Check the terminal logs for risk block messages (max trades reached, etc.)

### `SyntaxError: positional argument follows keyword argument`
In `html.Div(style=..., [...])` — style must come after children in Dash.  
Always write: `html.Div([children...], style={...})`

---

## File Quick Reference

| Task | File to edit |
|---|---|
| Change strategy / times | `config/config.yml` |
| Add a new strategy | `strategy/strategy.py` |
| Change risk limits | `config/config.yml` |
| Add a new broker | `broker/broker_client.py` |
| Modify dashboard layout | `dashboard/app.py` |
| Change log level | `config/config.yml` → `logging.level` |
| Refresh session token | `python tools/get_session_token.py` |

---

## Disclaimer

> This software is for **educational and research purposes only**.  
> Options trading involves significant financial risk and may result in the loss of your entire capital.  
> This bot does **not** place real orders — `paper_trading: true` must always be set.  
> Always consult a SEBI-registered financial advisor before trading with real money.  
> The authors accept no responsibility for any financial losses whatsoever.

---

*Built with Python · Plotly Dash · ICICIdirect Breeze API*
=======
# nifty_options_bot_v2