"""
Market Data Module
Fetches real-time and historical market data
Uses Schwab API when available, falls back to yfinance
"""

import yfinance as yf
import pandas as pd
import logging
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

# Try to import Schwab client
try:
    from src.schwab_client import get_schwab_client
    SCHWAB_AVAILABLE = True
except ImportError:
    SCHWAB_AVAILABLE = False
    logger.warning("Schwab client not available - using yfinance only")


def get_current_price(ticker):
    """Get current real-time price for a ticker"""
    if SCHWAB_AVAILABLE:
        client = get_schwab_client()
        if client.enabled:
            quote = client.get_quote(ticker)
            if quote and ticker in quote:
                data = quote[ticker]["quote"]
                return data.get("lastPrice") or data.get("mark")
    
    # Fallback to yfinance
    try:
        data = yf.Ticker(ticker).history(period="1d", interval="1m")
        if not data.empty:
            return data['Close'].iloc[-1]
    except Exception as e:
        logger.error(f"Error fetching price for {ticker}: {e}")
    
    return None


def get_intraday(ticker, interval="1m"):
    """
    Fetch intraday data for a ticker
    Uses yfinance for historical bars (Schwab streaming would be overkill for this)
    """
    try:
        data = yf.Ticker(ticker).history(period="1d", interval=interval)
        if data.empty:
            logger.warning(f"No intraday data for {ticker}")
            return None
        return data
    except Exception as e:
        logger.error(f"Error fetching intraday data for {ticker}: {e}")
        return None


def get_vix():
    """Get current VIX value"""
    vix = get_current_price("^VIX")
    return vix if vix else 20.0  # Default fallback


def get_option_premium(ticker, strike, expiry, option_type):
    """
    Get current option premium
    Uses Schwab API for real-time pricing when available
    """
    if SCHWAB_AVAILABLE:
        client = get_schwab_client()
        if client.enabled:
            chain = client.get_option_chain(
                symbol=ticker,
                strike=strike,
                contract_type="CALL" if option_type == "CALL" else "PUT"
            )
            
            if chain:
                # Parse Schwab option chain response
                try:
                    contract_map = chain.get("callExpDateMap" if option_type == "CALL" else "putExpDateMap", {})
                    
                    # Find the right expiry and strike
                    for exp_date, strikes in contract_map.items():
                        for strike_price, contracts in strikes.items():
                            if abs(float(strike_price) - strike) < 0.01:
                                contract = contracts[0]
                                # Return mid price or last
                                return contract.get("mark") or contract.get("last")
                except Exception as e:
                    logger.error(f"Error parsing Schwab option chain: {e}")
    
    # Fallback to yfinance
    try:
        ticker_obj = yf.Ticker(ticker)
        options = ticker_obj.option_chain(expiry)
        
        chain = options.calls if option_type == "CALL" else options.puts
        
        # Find closest strike
        chain['strike_diff'] = abs(chain['strike'] - strike)
        closest = chain.loc[chain['strike_diff'].idxmin()]
        
        # Return mid price or last
        return (closest['bid'] + closest['ask']) / 2 if closest['bid'] > 0 else closest['lastPrice']
        
    except Exception as e:
        logger.error(f"Error fetching option premium: {e}")
        return None
