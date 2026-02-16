"""
scanner_realtime.py
===================
Production scanner using the UnifiedFeed (Schwab SIP primary / Alpaca fallback).

This replaces scanner_loop.py when real-time credentials are available.

Architecture:
  UnifiedFeed
    └── SchwabStreamer (primary, SIP <50ms)
          ├── on_bar       → signal_engine → fire signal → enrich → alert → monitor
          └── on_option    → realtime_exits → fire exit alert on tick
    └── AlpacaStream (fallback, IEX <100ms)
          └── on_bar       → signal_engine (same path)

Signal latency target: <2 seconds from price event to alert in your inbox.
Exit  latency target:  <200ms from option price touching target/stop.
"""

import asyncio
import logging
import sys
import os
from datetime import datetime, time
import pytz
import yaml
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("realtime_scanner")

ET = pytz.timezone("America/New_York")


def load_config() -> dict:
    p = Path("config/settings.yaml")
    if p.exists():
        with p.open() as f:
            return yaml.safe_load(f)
    return {"tickers": ["SPY", "QQQ"], "strike_offset": 0}


def is_trading_hours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return time(9, 25) <= now.time() <= time(15, 50)


# ── Per-ticker signal state ───────────────────────────────────────────────────
# Track last signal time to enforce cooldown between bar callbacks
_last_signal: dict[str, datetime] = {}
_bar_counts:  dict[str, int]      = {}
_vix_cache   = {"value": 20.0, "updated": 0.0}

import time as _time


async def _refresh_vix():
    """Refresh VIX every 5 minutes in background."""
    while True:
        await asyncio.sleep(300)
        try:
            from src.market_data import get_vix
            _vix_cache["value"]   = get_vix()
            _vix_cache["updated"] = _time.monotonic()
            logger.debug(f"VIX refreshed: {_vix_cache['value']:.1f}")
        except Exception as e:
            logger.warning(f"VIX refresh failed: {e}")


# ── Bar callback — fires for every completed 1-minute bar ────────────────────

async def on_new_bar(ticker: str, bar: dict, source):
    """
    Called by UnifiedFeed every time a 1-minute bar completes for a ticker.
    Runs the full signal pipeline.
    """
    if not is_trading_hours():
        return

    now = datetime.now(ET)

    # Minimum bars needed before signalling (opening range = 15 bars minimum)
    _bar_counts[ticker] = _bar_counts.get(ticker, 0) + 1
    if _bar_counts[ticker] < 20:
        return

    # Time gate: no new entries after 3:00 PM ET
    if now.time() > time(15, 0):
        return

    try:
        from src.unified_feed        import get_feed
        from src.signal_engine       import run_scanner
        from src.options_analyzer    import enrich_signal
        from src.alert_system        import dispatch_alerts
        from src.trade_manager       import open_trade
        from src.realtime_exits      import register_exit_monitor, on_option_tick
        from src.schwab_stream       import format_option_symbol

        feed = get_feed()
        if feed is None:
            return

        df = feed.get_bars(ticker)
        if df is None or df.empty:
            return

        signal = run_scanner(ticker, df, _vix_cache["value"])
        if signal is None:
            return

        logger.info(f"\n{signal}")
        logger.info(f"  [Feed source: {feed.active_source}]")

        # Enrich with options data
        config         = load_config()
        signal         = enrich_signal(signal, offset_strikes=config.get("strike_offset", 0))

        # Send entry alerts
        dispatch_alerts(signal)

        # Register in trade manager
        trade = open_trade(signal)
        if trade and signal.premium and signal.strike:
            # Build OCC symbol for Schwab option streaming
            from datetime import datetime as dt
            expiry    = dt.now(ET)
            flag      = "C" if signal.direction == "CALL" else "P"
            occ_sym   = format_option_symbol(ticker, expiry, flag, signal.strike)

            # Subscribe to real-time option quotes
            await feed.subscribe_option(occ_sym)

            # Register real-time exit monitor
            register_exit_monitor(
                trade_id    = trade.trade_id,
                ticker      = ticker,
                direction   = signal.direction,
                occ_symbol  = occ_sym,
                entry_price = signal.premium,
                target_pct  = signal.target_pct,
                stop_pct    = signal.stop_pct,
            )

            logger.info(
                f"[{ticker}] Real-time exit monitor armed for {occ_sym} | "
                f"Target: ${signal.premium*(1+signal.target_pct):.2f} | "
                f"Stop: ${signal.premium*(1-signal.stop_pct):.2f}"
            )

    except Exception as e:
        logger.error(f"[{ticker}] Bar callback error: {e}", exc_info=True)


# ── Option quote callback — fires on every bid/ask tick ──────────────────────

async def on_option_quote(occ_symbol: str, quote, source):
    """
    Called by SchwabStreamer on every option quote tick.
    Routes to real-time exit monitor.
    """
    from src.realtime_exits import on_option_tick, clear_fired_monitors
    on_option_tick(occ_symbol, quote, source)
    clear_fired_monitors()


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    logger.info("="*60)
    logger.info("  0DTE Real-Time Scanner Starting")
    logger.info(f"  {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    logger.info("="*60)

    if not is_trading_hours():
        logger.info("Outside trading hours — exiting")
        return

    config  = load_config()
    tickers = config.get("tickers", ["SPY", "QQQ"])

    # Initialize feed
    from src.unified_feed import init_feed
    feed = init_feed(
        tickers            = tickers,
        on_bar_callback    = on_new_bar,
        on_option_callback = on_option_quote,
    )

    logger.info(f"Tickers: {tickers}")
    logger.info(f"Schwab:  {'✅ configured' if os.environ.get('SCHWAB_REFRESH_TOKEN') else '❌ not configured (add Secrets)'}")
    logger.info(f"Alpaca:  {'✅ configured' if os.environ.get('APCA_API_KEY_ID')       else '❌ not configured (add Secrets)'}")

    # Start VIX refresh in background
    asyncio.ensure_future(_refresh_vix())

    # Start the feed (blocks until session ends)
    await feed.start()

    logger.info("Scanner session complete")


if __name__ == "__main__":
    asyncio.run(main())
