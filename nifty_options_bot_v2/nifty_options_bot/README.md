# Nifty Options Bot 🇮🇳📈

A modular, production-ready algorithmic trading bot for **Nifty 50 index options** on Indian brokers, with a real-time interactive dashboard.

---

## Folder Structure

```
nifty_options_bot/
│
├── main.py                    ← Entry point — boots all engines + dashboard
├── requirements.txt
├── README.md
│
├── config/
│   └── config.yml             ← All runtime parameters (strategy, risk, broker)
│
├── core/
│   ├── config_loader.py       ← YAML config loader (singleton)
│   └── models.py              ← Data models: Order, Position, Greeks, RiskSnapshot
│
├── data/
│   └── data_feed.py           ← Market data: spot price, options chain, LTP, streaming
│
├── broker/
│   └── broker_client.py       ← Broker adapters: Upstox, PaperBroker + factory
│
├── strategy/
│   ├── strategy.py            ← Strategy engine + ATMStraddle/Strangle/IronCondor/etc.
│   └── orders.py              ← Order Management System (OMS)
│
├── risk/
│   └── risk_engine.py         ← Daily loss limit, trailing SL, position SL/target
│
├── dashboard/
│   └── app.py                 ← Plotly Dash dashboard (auto-refreshes every 5s)
│
├── utils/
│   └── logger.py              ← Rotating file + console logger
│
└── logs/                      ← Log files (auto-created)
```

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure
Edit `config/config.yml`:
```yaml
broker:
  name: upstox
  paper_trading: true          # ← start with paper trading!
  api_key: "YOUR_KEY"
  api_secret: "YOUR_SECRET"

strategy:
  name: atm_straddle           # atm_straddle | atm_strangle | iron_condor
  entry_time: "09:20"
  lots: 1
```

### 3. Run
```bash
# Full bot + dashboard
python main.py

# Headless (no dashboard)
python main.py --no-dashboard

# Dashboard only (demo mode, no live bot)
python dashboard/app.py
```

### 4. Open dashboard
Navigate to: **http://localhost:8050**

---

## Supported Strategies

| Strategy | Description |
|---|---|
| `atm_straddle` | Short ATM CE + Short ATM PE |
| `atm_strangle` | Short OTM CE + OTM PE (configurable width) |
| `iron_condor` | Short inner + Long outer strikes (4-leg, risk-defined) |
| `naked_call` | Short OTM CE only |
| `naked_put` | Short OTM PE only |

---

## Supported Brokers

| Broker | Status | SDK |
|---|---|---|
| Upstox v2 | ✅ Full | `upstox-python-sdk` |
| Paper Trading | ✅ Built-in | — |
| ICICI Breeze | 🔧 Extend `BaseBrokerClient` | `breeze-connect` |
| Zerodha Kite | 🔧 Extend `BaseBrokerClient` | `kiteconnect` |

To add a broker: subclass `BaseBrokerClient` in `broker/broker_client.py` and register it in the factory.

---

## Dashboard Features

- 📈 **Live PnL chart** — intraday mark-to-market
- 📉 **Nifty spot chart** — streaming price feed
- 🧾 **Open positions** — strike, side, lots, entry, LTP, PnL, Delta, IV
- 📋 **Trade log** — every order with time, price, status
- ⚠️ **Risk gauges** — loss used %, target %, drawdown, exposure
- 🟢 **Status LEDs** — API, Order Engine, Strategy, Data Feed
- ⛔ **Halt banner** — appears when daily loss limit is hit

---

## Risk Controls

All configurable in `config.yml → risk:`:

| Parameter | Description |
|---|---|
| `max_loss_per_day` | Bot halts + squares off if MTM loss hits this |
| `target_profit_per_day` | Auto square-off on hitting target |
| `position_stop_loss` | Per-position SL in ₹ |
| `position_target` | Per-position target in ₹ |
| `max_trades_per_day` | Maximum fresh entries per session |
| `max_open_positions` | Maximum simultaneous legs |
| `trailing_sl` | Enable trailing stop-loss |

---

## ⚠️ Disclaimer

This software is for **educational and research purposes only**.  
Options trading involves significant financial risk.  
**Always test thoroughly in paper trading mode before using real funds.**  
The authors are not responsible for any financial losses.
