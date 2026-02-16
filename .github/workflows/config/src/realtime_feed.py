import asyncio
import logging
import os
import pandas as pd
import numpy as np
from datetime import datetime, time
from collections import deque
from typing import Callable, Optional
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

_bar_store: dict = {}
MAX_BARS = 390
TICKERS  = ["SPY", "QQQ", "IWM"]


def get_realtime_df(ticker: str) -> Optional[pd.DataFrame]:
    bars = _bar_store.get(ticker)
    if not bars:
        return None
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(list(bars))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(ET)
    else:
        df.index = df.index.tz_convert(ET)
    df = df.rename(columns={
        "open":   "Open",
        "high":   "High",
        "low":    "Low",
        "close":  "Close",
        "volume": "Volume",
    })
    return df[["Open", "High", "Low", "Close", "Volume"]]


async def bar_handler(bar):
    ticker = bar.symbol
    if ticker not in _bar_store:
        _bar_store[ticker] = deque(maxlen=MAX_BARS)
    _bar_store[ticker].append({
        "timestamp": bar.timestamp,
        "open":      float(bar.open),
        "high":      float(bar.high),
        "low":       float(bar.low),
        "close":     float(bar.close),
        "volume":    float(bar.volume),
    })
    logger.debug(f"Bar: {bar.symbol} close={bar.close}")


async def quote_handler(quote):
    ticker = quote.symbol
    if ticker in _bar_store and _bar_store[ticker]:
        mid  = (float(quote.bid_price) + float(quote.ask_price)) / 2
        last = dict(_bar_store[ticker][-1])
        last["close"] = mid
        last["high"]  = max(last["high"], mid)
        last["low"]   = min(last["low"],  mid)
        _bar_store[ticker][-1] = last


async def run_stream(signal_callback: Callable, poll_interval: int = 30):
    key    = os.environ.get("APCA_API_KEY_ID", "")
    secret = os.environ.get("APCA_API_SECRET_KEY", "")

    if not key or not secret:
        raise ValueError(
            "APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set"
        )

    try:
        from alpaca.data.live import StockDataStream
        from alpaca.data.enums import DataFeed
    except ImportError:
        raise ImportError("Install alpaca-py: pip install alpaca-py")

    stream = StockDataStream(key, secret, feed=DataFeed.IEX)
    stream.subscribe_bars(bar_handler, *TICKERS)
    stream.subscribe_quotes(quote_handler, *TICKERS)

    logger.info(f"Alpaca WebSocket connected - streaming {TICKERS}")

    async def evaluation_loop():
        from src.market_data   import get_vix
        from src.signal_engine import run_scanner
        import time as _time

        vix_cache = {"value": 20.0, "last_updated": 0}

        while True:
            await asyncio.sleep(poll_interval)
            now = datetime.now(ET)

            if _time.monotonic() - vix_cache["last_updated"] > 300:
                try:
                    vix_cache["value"]       = get_vix()
                    vix_cache["last_updated"] = _time.monotonic()
                except Exception:
                    pass

            for ticker in TICKERS:
                df = get_realtime_df(ticker)
                if df is None or df.empty:
                    continue
                try:
                    await signal_callback(ticker, df, vix_cache["value"])
                except Exception as e:
                    logger.error(f"Signal callback error [{ticker}]: {e}")

    await asyncio.gather(
        stream._run_forever(),
        evaluation_loop(),
    )


def start_realtime_scanner():
    from src.signal_engine    import run_scanner
    from src.options_analyzer import enrich_signal
    from src.alert_system     import dispatch_alerts
    from src.trade_manager    import check_exits, open_trade

    async def on_new_bars(ticker: str, df, vix: float):
        check_exits()
        signal = run_scanner(ticker, df, vix)
        if signal:
            signal = enrich_signal(signal)
            dispatch_alerts(signal)
            open_trade(signal)

    logger.info("Starting real-time Alpaca stream...")
    asyncio.run(run_stream(on_new_bars, poll_interval=30))
