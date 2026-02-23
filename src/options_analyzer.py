"""
Options Analyzer Module
Enriches signals with option contract details and Greeks
"""

import numpy as np
import logging
from scipy.stats import norm
from datetime import datetime, time
import pytz

from src.market_data import get_atm_options
from src.signal_engine import Signal

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


def _d1(S, K, T, r, sigma):
    """Black-Scholes d1"""
    if T <= 0 or sigma <= 0:
        return 0.0
    return (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))


def _d2(S, K, T, r, sigma):
    """Black-Scholes d2"""
    return _d1(S, K, T, r, sigma) - sigma * np.sqrt(T)


def bs_delta(S, K, T, r, sigma, option_type="call"):
    """Calculate option delta"""
    d1 = _d1(S, K, T, r, sigma)
    if option_type == "call":
        return float(norm.cdf(d1))
    else:
        return float(norm.cdf(d1) - 1)


def bs_theta(S, K, T, r, sigma, option_type="call"):
    """Calculate option theta (daily decay)"""
    if T <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)
    term1 = -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
    if option_type == "call":
        return float((term1 - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365)
    else:
        return float((term1 + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365)


def bs_vega(S, K, T, r, sigma):
    """Calculate option vega"""
    if T <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    return float(S * norm.pdf(d1) * np.sqrt(T) / 100)


def _time_to_expiry_years() -> float:
    """Calculate time to market close in years"""
    now = datetime.now(ET)
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now >= close:
        return 0.0
    seconds_left = (close - now).total_seconds()
    return seconds_left / (252 * 6.5 * 3600)


def enrich_signal(signal: Signal, offset_strikes: int = 0) -> Signal:
    """
    Enrich signal with option contract details and Greeks
    
    Args:
        signal: Base signal from scanner
        offset_strikes: 0=ATM, 1=1 strike OTM, etc.
    
    Returns:
        Enriched signal with contract details
    """
    try:
        # Get ATM option data
        atm_data = get_atm_options(
            ticker=signal.ticker,
            option_type=signal.direction,
            offset=offset_strikes
        )
        
        if not atm_data:
            logger.warning(f"[{signal.ticker}] Could not find ATM options")
            return signal
        
        # Extract option data
        premium = atm_data.get('premium', 0)
        strike = atm_data.get('strike', 0)
        bid = atm_data.get('bid', 0)
        ask = atm_data.get('ask', 0)
        oi = atm_data.get('openInterest', 0)
        
        if premium == 0 or strike == 0:
            logger.warning(f"[{signal.ticker}] Invalid option data")
            return signal
        
        # Check liquidity
        if bid > 0:
            spread_pct = (ask - bid) / premium
            if spread_pct > 0.30 and oi < 200:
                logger.warning(
                    f"[{signal.ticker}] Wide spread ({spread_pct*100:.1f}%) "
                    f"and low OI ({oi}), trying +1 OTM"
                )
                # Try one strike OTM
                atm_data = get_atm_options(
                    ticker=signal.ticker,
                    option_type=signal.direction,
                    offset=offset_strikes + 1
                )
                if atm_data:
                    premium = atm_data.get('premium', 0)
                    strike = atm_data.get('strike', 0)
        
        # Calculate Greeks
        T = _time_to_expiry_years()
        r = 0.05  # Risk-free rate
        S = signal.spot_price
        K = strike
        iv = 0.30  # Default IV (we don't have real IV from yfinance reliably)
        
        side = "call" if signal.direction == "CALL" else "put"
        delta = bs_delta(S, K, T, r, iv, side)
        theta = bs_theta(S, K, T, r, iv, side)
        vega = bs_vega(S, K, T, r, iv)
        
        # Build contract symbol (0DTE format)
        expiry_short = datetime.now(ET).strftime("%y%m%d")
        flag = "C" if signal.direction == "CALL" else "P"
        contract_sym = f"{signal.ticker}_{expiry_short}{flag}{int(strike)}"
        
        # Enrich signal object
        signal.strike = strike
        signal.premium = premium
        signal.iv = iv
        signal.contract = contract_sym
        
        # Add Greeks to reasons
        signal.reasons.append(
            f"Delta={delta:.2f} Theta={theta:.3f} Vega={vega:.3f} IV={iv*100:.0f}%"
        )
        
        logger.info(
            f"[{signal.ticker}] Contract: {contract_sym} | "
            f"Premium: ${premium:.2f} | Strike: ${strike} | "
            f"Bid/Ask: ${bid:.2f}/${ask:.2f} | OI: {oi}"
        )
        
        return signal
        
    except Exception as e:
        logger.error(f"[{signal.ticker}] Error enriching signal: {e}", exc_info=True)
        return signal
