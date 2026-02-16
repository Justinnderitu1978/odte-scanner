"""
scanner_loop.py
===============
Runs a continuous 60-second polling loop within a single GitHub Actions job.
Called ONCE at market open; loops internally until market close.

Advantage: eliminates the 1-5 min GitHub cron scheduler delay entirely.
After the initial runner spin-up (~90s), every poll fires within ±5 seconds.

GitHub Actions max job duration: 6 hours (free tier).
Market session 9:30 AM → 4:00 PM ET = 6.5 hours.
Solution: start at 9:25 AM, exit by 3:50 PM = 6h25m — within limit.
"""

import time
import logging
import sys
from datetime import datetime, time as dtime
import pytz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scanner_loop")

ET            = pytz.timezone("America/New_York")
POLL_INTERVAL = 60          # seconds between scans
LOOP_START    = dtime(9, 25)   # begin loop (before open to warm up)
LOOP_END      = dtime(15, 50)  # exit loop (5 min before hard close)
SIGNAL_START  = dtime(9, 50)   # earliest signal (same as signal_engine.py)


def market_is_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return dtime(9, 30) <= now.time() <= dtime(16, 0)


def within_loop_window() -> bool:
    t = datetime.now(ET).time()
    return LOOP_START <= t <= LOOP_END


def seconds_until_open() -> float:
    """Seconds until 9:25 AM ET today (or 0 if already past)."""
    now = datetime.now(ET)
    target = now.replace(hour=9, minute=25, second=0, microsecond=0)
    if now >= target:
        return 0
    return (target - now).total_seconds()


def run_scan_cycle():
    """One scan cycle — mirrors main.py logic but called in a tight loop."""
    from src.market_data      import get_intraday, get_vix
    from src.signal_engine    import run_scanner
    from src.options_analyzer import enrich_signal
    from src.alert_system     import dispatch_alerts
    from src.trade_manager    import check_exits, open_trade

    now = datetime.now(ET)
    logger.info(f"── Scan cycle {now.strftime('%H:%M:%S ET')} ──")

    # Always check exits on open trades
    check_exits()

    # Skip new signals outside the entry window
    if not (SIGNAL_START <= now.time() <= dtime(15, 0)):
        logger.debug("Outside signal window — exits only")
        return

    vix = get_vix()

    for ticker in ["SPY", "QQQ"]:
        try:
            df = get_intraday(ticker, interval="1m")
            if df is None or df.empty:
                continue

            signal = run_scanner(ticker, df, vix)
            if signal is None:
                continue

            # Enrich and alert
            signal = enrich_signal(signal)
            dispatch_alerts(signal)
            open_trade(signal)

        except Exception as e:
            logger.error(f"[{ticker}] Scan error: {e}", exc_info=True)


def main():
    logger.info("Scanner loop starting up...")

    # Wait until 9:25 AM if running early (workflow dispatched at 9:20 AM)
    wait = seconds_until_open()
    if wait > 0:
        logger.info(f"Market opens in {wait/60:.1f} min — sleeping...")
        time.sleep(wait)

    if not market_is_open() and not within_loop_window():
        logger.info("Not a trading day or outside window — exiting")
        return

    logger.info(f"Entering scan loop — polling every {POLL_INTERVAL}s")
    logger.info(f"Will exit automatically at {LOOP_END.strftime('%H:%M')} ET")

    cycle = 0
    while within_loop_window():
        cycle += 1
        start = time.monotonic()
        try:
            run_scan_cycle()
        except Exception as e:
            logger.error(f"Unhandled error in cycle {cycle}: {e}", exc_info=True)

        elapsed = time.monotonic() - start
        sleep_for = max(0, POLL_INTERVAL - elapsed)
        logger.debug(f"Cycle {cycle} took {elapsed:.1f}s — sleeping {sleep_for:.0f}s")
        time.sleep(sleep_for)

    logger.info(f"Loop ended after {cycle} cycles — market session complete")


if __name__ == "__main__":
    main()
