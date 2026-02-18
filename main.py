"""
main.py
=======
0DTE Options Scanner - Entry Point
"""

import logging
import sys
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

logger = logging.getLogger("main")
ET = pytz.timezone("America/New_York")


def load_config():
    config_path = Path("config/settings.yaml")
    if config_path.exists():
        with config_path.open() as f:
            return yaml.safe_load(f)
    return {"tickers": ["SPY", "QQQ", "IWM"], "strike_offset": 0}


def is_market_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    market_open = time(9, 30)
    market_close = time(16, 0)
    return market_open <= now.time() <= market_close


def main():
    from src.market_data import get_intraday, get_vix
    from src.signal_engine import run_scanner, _calc_opening_range, _calc_vwap
    from src.fast_entry_signal import evaluate_fast_entry
    from src.options_analyzer import enrich_signal
    from src.alert_system import dispatch_alerts
    from src.trade_manager import open_trade, check_exits, print_trade_summary
    
    now = datetime.now(ET)
    logger.info("="*55)
    logger.info(f"0DTE Scanner starting — {now.strftime('%Y-%m-%d %H:%M ET')}")
    logger.info("="*55)
    
    if not is_market_open():
        logger.info("Market is closed — nothing to do")
        return
    
    if now.time() >= time(15, 0):
        logger.info("Past 3:00 PM — skipping new entries, monitoring exits only")
        check_exits()
        print_trade_summary()
        return
    
    config = load_config()
    tickers = config.get("tickers", ["SPY", "QQQ", "IWM"])
    
    logger.info(f"Monitoring: {', '.join(tickers)}")
    
    vix = get_vix()
    logger.info(f"VIX: {vix:.1f}")
    
    check_exits()
    
    for symbol in tickers:
        try:
            logger.info(f"Scanning {symbol}...")
            
            df = get_intraday(symbol, interval="1m")
            if df is None or df.empty:
                logger.warning(f"[{symbol}] No market data available")
                continue
            
            main_signal = run_scanner(symbol, df, vix)
            
            if main_signal and main_signal.score >= 4:
                logger.info(f"[{symbol}] 4/5 signal detected!")
                signal = enrich_signal(main_signal, offset_strikes=config.get("strike_offset", 0))
                dispatch_alerts(signal)
                open_trade(signal)
                continue
            
            or_high, or_low = _calc_opening_range(df)
            vwap = _calc_vwap(df)
            main_score = main_signal.score if main_signal else 0
            
            fast_signal = evaluate_fast_entry(
                ticker=symbol,
                df=df,
                vix=vix,
                main_score=main_score,
                or_high=or_high,
                or_low=or_low,
                vwap=vwap,
            )
            
            if fast_signal:
                logger.info(f"[{symbol}] 3/5 fast entry detected!")
                signal = enrich_signal(fast_signal, offset_strikes=config.get("strike_offset", 0))
                dispatch_alerts(signal)
                open_trade(signal)
                continue
            
            logger.info(f"[{symbol}] No signal this cycle")
            
        except Exception as e:
            logger.error(f"[{symbol}] Error: {e}", exc_info=True)
    
    print_trade_summary()
    
    logger.info("="*55)
    logger.info("Scan cycle complete")
    logger.info("="*55)


if __name__ == "__main__":
    main()
