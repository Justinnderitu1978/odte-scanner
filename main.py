"""
main.py
=======
Orchestrator — called by GitHub Actions every 10 minutes during market hours.

Execution Flow
--------------
1. Check market hours — exit early if outside window
2. For each ticker in config:
   a. Fetch 1-min intraday bars + VIX
   b. Run signal engine
   c. If signal → enrich with options data → dispatch alerts → register trade
3. Check existing open trades for exit conditions
4. Print trade summary to GitHub Actions log
"""

import logging
import sys
import yaml
from datetime import datetime, time
from pathlib import Path
import pytz

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

ET = pytz.timezone("America/New_York")

# ── Config ──────────────────────────────────────────────────────────────────
CONFIG_PATH = Path("config/settings.yaml")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.warning("No config/settings.yaml found — using defaults")
        return {
            "tickers": ["SPY", "QQQ"],
            "strike_offset": 0,
            "score_threshold": 4,
            "vix_max": 25.0,
        }
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


# ── Market hours guard ────────────────────────────────────────────────────

MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(16, 0)


def is_market_open() -> bool:
    now = datetime.now(ET)
    # Skip weekends
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def is_too_late_for_entries() -> bool:
    """After 3:00 PM ET, only monitor exits — no new entries."""
    return datetime.now(ET).time() > time(15, 0)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    logger.info(f"0DTE Scanner starting — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")

    if not is_market_open():
        logger.info("Market is closed — nothing to do")
        return

    config = load_config()
    tickers        = config.get("tickers", ["SPY", "QQQ"])
    strike_offset  = int(config.get("strike_offset", 0))

    # ── Step 1: Check existing trade exits ──────────────────────────────
    from src.trade_manager import check_exits, print_trade_summary
    check_exits()

    # ── Step 2: Scan for new signals (only before 3:00 PM) ──────────────
    if not is_too_late_for_entries():
        from src.market_data    import get_intraday, get_vix
        from src.signal_engine  import run_scanner
        from src.options_analyzer import enrich_signal
        from src.alert_system   import dispatch_alerts
        from src.trade_manager  import open_trade

        vix = get_vix()
        logger.info(f"VIX: {vix:.1f}")

        for ticker in tickers:
            logger.info(f"Scanning {ticker}...")
            df = get_intraday(ticker, interval="1m")

            if df is None or df.empty:
                logger.warning(f"No data for {ticker} — skipping")
                continue

            signal = run_scanner(ticker, df, vix)

            if signal is None:
                logger.info(f"[{ticker}] No signal this cycle")
                continue

            # ── Got a signal ──────────────────────────────────────────
            logger.info(f"\n{signal}")

            # Enrich with options data
            signal = enrich_signal(signal, offset_strikes=strike_offset)

            # Send alerts (email + SMS)
            alert_results = dispatch_alerts(signal)
            logger.info(f"Alert results: {alert_results}")

            # Register open trade
            trade = open_trade(signal)
            if trade:
                logger.info(f"Trade registered: {trade.trade_id}")
    else:
        logger.info("Past 3:00 PM — skipping new entries, monitoring exits only")

    # ── Step 3: Print summary ────────────────────────────────────────────
    print_trade_summary()
    logger.info("Scan cycle complete")


if __name__ == "__main__":
    main()
