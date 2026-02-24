"""
Signal Engine - Main 4/5 Scoring System
"""

import pandas as pd
import logging
from datetime import datetime
import pytz
from dataclasses import dataclass
from typing import Optional

# Track recent signals to prevent duplicates
_recent_signals = {}
SIGNAL_COOLDOWN_MINUTES = 10

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

SIGNAL_SCORE_THRESHOLD = 4


@dataclass
class Signal:
    ticker: str
    direction: str
    spot_price: float
    score: int
    timestamp: datetime
    vwap: float
    rsi: float
    vix: float
    or_high: float
    or_low: float
    reasons: list
    strike: float = 0.0
    premium: float = 0.0
    iv: float = 0.0
    contract: str = ""
    target_pct: float = 0.80
    stop_pct: float = 0.50
    time_stop: str = "15:30"


def _calc_opening_range(df: pd.DataFrame, minutes: int = 15):
    """Calculate opening range high/low"""
    try:
        now = datetime.now(ET)
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        range_end = market_open.replace(minute=45)
        
        if df.index.tz is None:
            df.index = df.index.tz_localize(ET)
        else:
            df.index = df.index.tz_convert(ET)
        
        or_data = df[(df.index >= market_open) & (df.index < range_end)]
        
        if or_data.empty:
            return df['High'].max(), df['Low'].min()
        
        return or_data['High'].max(), or_data['Low'].min()
        
    except Exception as e:
        logger.error(f"Error calculating opening range: {e}")
        return df['High'].max(), df['Low'].min()


def _calc_vwap(df: pd.DataFrame):
    """Calculate VWAP"""
    try:
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['TPV'] = df['TP'] * df['Volume']
        return df['TPV'].sum() / df['Volume'].sum()
    except Exception as e:
        logger.error(f"Error calculating VWAP: {e}")
        return df['Close'].iloc[-1]


def _calc_rsi(series: pd.Series, period: int = 5):
    """Calculate RSI"""
    try:
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    except Exception as e:
        logger.error(f"Error calculating RSI: {e}")
        return 50.0


def run_scanner(ticker: str, df: pd.DataFrame, vix: float) -> Optional[Signal]:
    """
    Main 4/5 signal scanner
    
    Returns Signal object if 4/5 conditions met, None otherwise
    """
    try:
        if df is None or df.empty or len(df) < 20:
            return None
        
        spot = df['Close'].iloc[-1]
        or_high, or_low = _calc_opening_range(df)
        vwap = _calc_vwap(df)
        rsi = _calc_rsi(df['Close'])
        
        # Calculate volume surge
        avg_volume = df['Volume'].rolling(window=20).mean().iloc[-1]
        current_volume = df['Volume'].iloc[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
        
        # Score bullish conditions
        bull_score = 0
        bull_reasons = []
        
        # Check ORB breakout with 0.5% minimum movement
        if spot > or_high:
            breakout_pct = (spot - or_high) / or_high
            
        if breakout_pct >= 0.005:  # 0.5% minimum
            bull_score += 1
            bull_reasons.append(f"ORB up ${spot:.2f}>${or_high:.2f} (+{breakout_pct*100:.1f}%)")        
        if spot > vwap:
            bull_score += 1
            bull_reasons.append(f"VWAP up ${spot:.2f}>${vwap:.2f}")
        
        if rsi > 55:
            bull_score += 1
            bull_reasons.append(f"RSI={rsi:.1f}>55")
        
        if volume_ratio > 1.5:
            bull_score += 1
            bull_reasons.append(f"Vol {volume_ratio:.1f}x")
        
        # Score bearish conditions
        bear_score = 0
        bear_reasons = []
        
       # Check ORB breakout with 0.5% minimum movement
        if spot < or_low:
            breakout_pct = (or_low - spot) / or_low
        if breakout_pct >= 0.005:  # 0.5% minimum
            bear_score += 1
        bear_reasons.append(f"ORB down ${spot:.2f}<${or_low:.2f} (-{breakout_pct*100:.1f}%)")
        
        if spot < vwap:
            bear_score += 1
            bear_reasons.append(f"VWAP down ${spot:.2f}<${vwap:.2f}")
        
        if rsi < 45:
            bear_score += 1
            bear_reasons.append(f"RSI={rsi:.1f}<45")
        
        if volume_ratio > 1.5:
            bear_score += 1
            bear_reasons.append(f"Vol {volume_ratio:.1f}x")
        
        # VIX filter
        if vix < 25.0:
            bull_score += 1
            bear_score += 1
            vix_reason = f"VIX={vix:.1f}<25.0"
            bull_reasons.append(vix_reason)
            bear_reasons.append(vix_reason)
        
        # Determine direction
        direction = None
        total_score = 0
        reasons = []
        
        if bull_score >= SIGNAL_SCORE_THRESHOLD:
            direction = "CALL"
            total_score = bull_score
            reasons = bull_reasons
        elif bear_score >= SIGNAL_SCORE_THRESHOLD:
            direction = "PUT"
            total_score = bear_score
            reasons = bear_reasons
        
        # Return None if no 4/5 signal (pre-signals disabled)
        if not direction:
            return None
        
    # Check cooldown to prevent duplicate signals
        signal_key = f"{ticker}_{direction}"
        now = datetime.now(ET)
        
        if signal_key in _recent_signals:
            last_signal_time = _recent_signals[signal_key]
            minutes_since = (now - last_signal_time).total_seconds() / 60
            
            if minutes_since < SIGNAL_COOLDOWN_MINUTES:
                logger.info(f"[{ticker}] {direction} signal on cooldown ({minutes_since:.1f}m ago)")
                return None
        
        # Create signal
        signal = Signal(
            ticker=ticker,
            direction=direction,
            spot_price=spot,
            score=total_score,
            timestamp=datetime.now(ET),
            vwap=vwap,
            rsi=rsi,
            vix=vix,
            or_high=or_high,
            or_low=or_low,
            reasons=reasons
        )
        
        # Record this signal
        _recent_signals[signal_key] = now
        
        return signal
        
    except Exception as e:
        logger.error(f"Error in scanner for {ticker}: {e}", exc_info=True)
        return None
