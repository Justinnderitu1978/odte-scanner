import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

TICKERS = {
    "SPY":  {"name": "S&P 500 ETF",     "multiplier": 100},
    "QQQ":  {"name": "Nasdaq 100 ETF",   "multiplier": 100},
    "IWM":  {"name": "Russell 2000 ETF", "multiplier": 100},
}


def get_intraday(ticker: str, interval: str = "1m") -> pd.DataFrame:
    try:
        t  = yf.Ticker(ticker)
        df = t.history(period="1d", interval=interval, prepost=False)
        if df.empty:
            logger.warning(f"No intraday data for {ticker}")
            return pd.DataFrame()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(ET)
        else:
            df.index = df.index.tz_convert(ET)
        return df
    except Exception as e:
        logger.error(f"Error fetching intraday data for {ticker}: {e}")
        return pd.DataFrame()


def get_vix() -> float:
    try:
        vix  = yf.Ticker("^VIX")
        hist = vix.history(period="1d", interval="5m")
        if hist.empty:
            return 20.0
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.error(f"Error fetching VIX: {e}")
        return 20.0


def get_options_chain(ticker: str) -> dict:
    try:
        t         = yf.Ticker(ticker)
        today_str = datetime.now(ET).strftime("%Y-%m-%d")
        expirations = t.options
        if today_str not in expirations:
            near = [e for e in expirations if e >= today_str]
            if not near:
                logger.warning(f"No 0DTE chain for {ticker}")
                return {}
            today_str = near[0]

        chain = t.option_chain(today_str)
        hist  = t.history(period="1d", interval="1m")
        spot  = float(hist["Close"].iloc[-1]) if not hist.empty else 0.0

        return {
            "calls":  chain.calls.copy(),
            "puts":   chain.puts.copy(),
            "spot":   spot,
            "expiry": today_str,
        }
    except Exception as e:
        logger.error(f"Error fetching options chain for {ticker}: {e}")
        return {}


def get_atm_options(chain_data: dict, offset_strikes: int = 0) -> dict:
    if not chain_data:
        return {}
    spot  = chain_data["spot"]
    calls = chain_data["calls"].copy()
    puts  = chain_data["puts"].copy()

    band  = spot * 0.05
    calls = calls[(calls["strike"] >= spot - band) & (calls["strike"] <= spot + band)]
    puts  = puts[(puts["strike"]  >= spot - band) & (puts["strike"]  <= spot + band)]

    if calls.empty or puts.empty:
        return {}

    calls_above = calls[calls["strike"] >= spot].sort_values("strike")
    calls_below = calls[calls["strike"] <  spot].sort_values("strike", ascending=False)
    atm_calls   = pd.concat([calls_above, calls_below]).reset_index(drop=True)

    puts_below  = puts[puts["strike"] <= spot].sort_values("strike", ascending=False)
    puts_above  = puts[puts["strike"] >  spot].sort_values("strike")
    atm_puts    = pd.concat([puts_below, puts_above]).reset_index(drop=True)

    if len(atm_calls) <= offset_strikes or len(atm_puts) <= offset_strikes:
        return {}

    return {
        "call": atm_calls.iloc[offset_strikes],
        "put":  atm_puts.iloc[offset_strikes],
        "spot": spot,
    }
