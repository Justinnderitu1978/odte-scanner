"""
main.py
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
        logger.info("Market is closed")
        return
    
    if now.time() >= time(15, 0):
        logger.info("Past 3 PM - exits only")
        check_exits()
        print_trade_summary()
        return
    
    settings = load_config()
    symbol_list = settings.get("tickers", ["SPY", "QQQ", "IWM"])
    strike_offset = settings.get("strike_offset", 0)
    
    logger.info(f"Symbols: {symbol_list}")
    
    current_vix = get_vix()
    logger.info(f"VIX: {current_vix:.1f}")
    
    check_exits()
    
    for ticker_symbol in symbol_list:
        try:
            logger.info(f"Scanning {ticker_symbol}...")
            
            price_data = get_intraday(ticker_symbol, interval="1m")
            if price_data is None or price_data.empty:
                logger.warning(f"[{ticker_symbol}] No data")
                continue
            
            primary_signal = run_scanner(ticker_symbol, price_data, current_vix)
            
            if primary_signal and primary_signal.score >= 4:
                logger.info(f"[{ticker_symbol}] 4/5 SIGNAL!")
                enriched = enrich_signal(primary_signal, offset_strikes=strike_offset)
                dispatch_alerts(enriched)
                open_trade(enriched)
                continue
            
            range_high, range_low = _calc_opening_range(price_data)
            volume_wap = _calc_vwap(price_data)
            current_score = primary_signal.score if primary_signal else 0
            
            secondary_signal = evaluate_fast_entry(
                ticker=ticker_symbol,
                df=price_data,
                vix=current_vix,
                main_score=current_score,
                or_high=range_high,
                or_low=range_low,
                vwap=volume_wap,
            )
            
            if secondary_signal:
                logger.info(f"[{ticker_symbol}] 3/5 FAST ENTRY!")
                enriched = enrich_signal(secondary_signal, offset_strikes=strike_offset)
                dispatch_alerts(enriched)
                open_trade(enriched)
                continue
            
            logger.info(f"[{ticker_symbol}] No signal")
            
        except Exception as e:
            logger.error(f"[{ticker_symbol}] Error: {e}", exc_info=True)
    
    print_trade_summary()
    logger.info("="*55)
    logger.info("Scan complete")
    logger.info("="*55)


if __name__ == "__main__":
    main()
