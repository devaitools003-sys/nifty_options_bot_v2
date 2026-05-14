"""
main.py
=======
Entry point — wires ICICIdirect Breeze live data with paper order execution.

Morning checklist
-----------------
1. Run:  python tools/get_session_token.py     ← do this first every morning
2. Run:  python main.py                        ← starts bot + dashboard
3. Open: http://127.0.0.1:8050                 ← watch it live

paper_trading is always enforced. No real orders are ever placed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.config_loader import get_config
from utils.logger import setup_logger, get_logger


def parse_args():
    p = argparse.ArgumentParser(description="Nifty Options Bot")
    p.add_argument("--config",       default="config/config.yml")
    p.add_argument("--no-dashboard", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    cfg  = get_config(str(ROOT / args.config))

    # ── Logging ───────────────────────────────────────────────────────────────
    log_cfg = cfg.as_dict().get("logging", {})
    setup_logger(
        level=log_cfg.get("level", "INFO"),
        log_to_file=log_cfg.get("log_to_file", True),
        log_dir=str(ROOT / log_cfg.get("log_dir", "logs")),
    )
    logger = get_logger("main")
    logger.info("=" * 60)
    logger.info("  NIFTY OPTIONS BOT")
    logger.info(f"  Strategy : {cfg.strategy.get('name')}")
    logger.info(f"  Broker   : {cfg.broker.get('name')}")
    logger.info(f"  Paper    : {cfg.broker.get('paper_trading')}  (safety lock)")
    logger.info("=" * 60)

    # ── Safety lock ───────────────────────────────────────────────────────────
    if not cfg.broker.get("paper_trading", True):
        logger.critical("paper_trading is FALSE — refusing to start. "
                        "Set paper_trading: true in config.yml")
        sys.exit(1)

    # ── Broker client ─────────────────────────────────────────────────────────
    broker_name = cfg.broker.get("name", "paper").lower()

    if broker_name == "icicidirect":
        from broker.breeze_client import BreezeDataClient
        ak = cfg.broker.get("api_key", "")
        sk = cfg.broker.get("api_secret", "")
        st = cfg.broker.get("session_token", "")
        if not all([ak, sk, st]) or ak == "YOUR_BREEZE_API_KEY":
            logger.error(
                "ICICIdirect credentials missing in config/config.yml\n"
                "Run: python tools/get_session_token.py"
            )
            sys.exit(1)
        broker = BreezeDataClient(
            api_key=ak, api_secret=sk, session_token=st,
            slippage_pct=cfg.broker.get("slippage_pct", 0.1),
        )
    else:
        from broker.broker_client import PaperBrokerClient
        broker = PaperBrokerClient()

    if not broker.login():
        logger.critical("Login failed")
        sys.exit(1)
    logger.info("Broker connected ✓")

    # ── Data feed ─────────────────────────────────────────────────────────────
    if broker_name == "icicidirect":
        from data.breeze_feed import BreezeFeed
        data_feed = BreezeFeed(
            breeze_client=broker,
            lot_size=cfg.instrument.get("lot_size", 50),
            strike_step=cfg.instrument.get("strike_step", 50),
        )
    else:
        from data.data_feed import MarketDataFeed
        data_feed = MarketDataFeed(
            broker_client=broker, paper_trading=True,
            lot_size=cfg.instrument.get("lot_size", 50),
        )

    atm    = data_feed.get_atm_strike()
    expiry = data_feed.get_nearest_expiry()
    chain  = data_feed.build_options_chain(
        expiry, atm, width=5,
        strike_step=cfg.instrument.get("strike_step", 50),
    )
    data_feed.start_streaming(list(chain.keys()), interval=2.0)
    logger.info(f"Feed started | ATM={atm} | Expiry={expiry} | {len(chain)} contracts")

    # ── Order manager ─────────────────────────────────────────────────────────
    from strategy.orders import OrderManager
    order_manager = OrderManager(broker, cfg.orders)

    # ── Risk engine ───────────────────────────────────────────────────────────
    from risk.risk_engine import RiskEngine
    capital_cfg = cfg.as_dict().get("capital", {})
    risk_engine = RiskEngine(cfg.risk, capital_config=capital_cfg)

    # ── Strategy ─────────────────────────────────────────────────────────────
    from strategy.strategy import StrategyEngine, create_strategy
    strategy = create_strategy(cfg.strategy)
    strategy.strike_step = cfg.instrument.get("strike_step", 50)
    strategy_engine = StrategyEngine(
        strategy=strategy, order_manager=order_manager,
        risk_engine=risk_engine, data_feed=data_feed,
        config=cfg.strategy,
    )
    strategy_engine._instrument_config = cfg.instrument
    strategy_engine._capital_config    = cfg.as_dict().get("capital", {})
    strategy_engine.start()
    logger.info("Strategy engine started ✓")

    # ── Dashboard ─────────────────────────────────────────────────────────────
    if not args.no_dashboard:
        from dashboard.app import inject_bot_engines, run_dashboard
        inject_bot_engines(
            strategy_engine=strategy_engine, order_manager=order_manager,
            risk_engine=risk_engine, data_feed=data_feed,
        )
        dash_cfg = cfg.dashboard
        logger.info(f"Dashboard → http://127.0.0.1:{dash_cfg.get('port', 8050)}")
        run_dashboard(host=dash_cfg.get("host", "0.0.0.0"),
                      port=dash_cfg.get("port", 8050), debug=False)
    else:
        logger.info("Headless. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            strategy_engine.stop()
            data_feed.stop_streaming()
            if hasattr(broker, "stop_websocket"):
                broker.stop_websocket()
            logger.info("Stopped cleanly ✓")


if __name__ == "__main__":
    main()
