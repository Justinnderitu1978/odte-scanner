import numpy as np
import logging
from scipy.stats import norm
from datetime import datetime, time
import pytz

from src.market_data import get_options_chain, get_atm_options
from src.signal_engine import Signal

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


def _d1(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    return (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))

def _d2(S, K, T, r, sigma):
    return _d1(S, K, T, r, sigma) - sigma * np.sqrt(T)

def bs_delta(S, K, T, r, sigma, option_type="call"):
    d1 = _d1(S, K, T, r, sigma)
    if option_type == "call":
        return float(norm.cdf(d1))
    else:
        return float(norm.cdf(d1) - 1)

def bs_theta(S, K, T, r, sigma, option_type="call"):
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
    if T <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    return float(S * norm.pdf(d1) * np.sqrt(T) / 100)


def _time_to_expiry_years() -> float:
    now   = datetime.now(ET)
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now >= close:
        return 0.0
    seconds_left = (close - now).total_seconds()
    return seconds_left / (252 * 6.5 * 3600)


def _mid_price(row) -> float:
    bid = float(row.get("bid", 0) or 0)
    ask = float(row.get("ask", 0) or 0)
    if ask == 0:
        return float(row.get("lastPrice", 0) or 0)
    return (bid + ask) / 2


def _is_liquid(row) -> bool:
    bid = float(row.get("bid", 0) or 0)
    ask = float(row.get("ask", 0) or 0)
    oi  = float(row.get("openInterest", 0) or 0)
    mid = _mid_price(row)
    if bid == 0 or mid == 0:
        return False
    spread_pct = (ask - bid) / mid
    if spread_pct > 0.30:
        return False
    if oi < 100:
        return False
    return True


def enrich_signal(signal: Signal, offset_strikes: int = 0) -> Signal:
    chain = get_options_chain(signal.ticker)
    if not chain:
        logger.warning(f"[{signal.ticker}] No options chain")
        return signal

    atm = get_atm_options(chain, offset_strikes=offset_strikes)
    if not atm:
        logger.warning(f"[{signal.ticker}] Could not find ATM options")
        return signal

    side = "call" if signal.direction == "CALL" else "put"
    contract_row = atm.get(side)
    if contract_row is None or contract_row.empty:
        return signal

    if not _is_liquid(contract_row):
        logger.warning(f"[{signal.ticker}] ATM {side} illiquid - trying 1-OTM")
        atm2 = get_atm_options(chain, offset_strikes=offset_strikes + 1)
        if not atm2:
            return signal
        contract_row = atm2.get(side)
        if contract_row is None:
            return signal

    premium = _mid_price(contract_row)
    strike  = float(contract_row.get("strike", 0))
    iv_raw  = float(contract_row.get("impliedVolatility", 0) or 0)
    if iv_raw == 0 or iv_raw > 2.0:
        iv_raw = 0.30

    T = _time_to_expiry_years()
    r = 0.05
    S = signal.spot_price
    K = strike

    delta = bs_delta(S, K, T, r, iv_raw, side)
    theta = bs_theta(S, K, T, r, iv_raw, side)
    vega  = bs_vega(S, K, T, r, iv_raw)

    expiry_short = datetime.now(ET).strftime("%y%m%d")
    flag         = "C" if signal.direction == "CALL" else "P"
    contract_sym = f"{signal.ticker}_{expiry_short}{flag}{int(strike)}"

    signal.strike   = strike
    signal.premium  = premium
    signal.iv       = iv_raw
    signal.contract = contract_sym

    signal.reasons.append(
        f"Delta={delta:.2f} Theta={theta:.3f} Vega={vega:.3f} IV={iv_raw*100:.0f}%"
    )

    logger.info(
        f"[{signal.ticker}] Contract: {contract_sym} | "
        f"Premium: ${premium:.2f} | Strike: ${strike} | IV: {iv_raw*100:.0f}%"
    )
    return signal
