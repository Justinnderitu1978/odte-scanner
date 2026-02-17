"""
src/fast_entry_signal.py
========================
V-Bottom Reversal Fast Entry Signal (3/5 Early Entry System)

Catches sharp selloffs that reverse quickly - fires at 3/5 score when
specific reversal patterns are detected. More aggressive than main 4/5 system.

Entry criteria:
- Time window: 10 AM - 1 PM ET only
- Sharp selloff detected (>1.5% below OR low or >2% below VWAP)
- Reversal candle pattern forming
- RSI bounce from oversold (<30 → >35)
- Volume surge 2.0× average
- VIX < 30
- Main system showing exactly 3/5 score

Exit rules:
- Target: +60% (vs main system's +80%)
- Stop: -40% (vs main system's -50%)
- Time stop: 3:15 PM ET (vs main system's 3:30 PM)
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime, time
from dataclasses import dataclass
from typing import Optional
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

# Fast entry specific thresholds
SELLOFF_VS_OR_MIN    = -0.015    # 1.5% below opening range low
SELLOFF_VS_VWAP_MIN  = -0.020    # 2.0% below VWAP
RSI_OVERSOLD         = 30        # Must have been below 30
RSI_BOUNCE_MIN       = 35        # Must bounce above 35
VOLUME_SURGE_MULT    = 2.0       # 2× average volume (stronger than main 1.5×)
VIX_MAX_FAST         = 30.0      # Slightly higher than main system's 25
FAST_ENTRY_START     = time(10, 0)   # 10:00 AM ET
FAST_ENTRY_END       = time(13, 0)   # 1:00 PM ET
FAST_EXIT_TIME       = time(15, 15)  # 3:15 PM ET (earlier than main)


@dataclass
class FastEntrySignal:
    """Fast entry signal dataclass - mirrors main Signal but with type marker"""
    ticker:      str
    direction:   str
    score:       int          # Will be 3
    timestamp:   datetime
    spot_price:  float
    or_high:     float
    or_low:      float
    vwap:        float
    rsi:         float
    vix:         float
    signal_type: str = "V_BOTTOM_REVERSAL"
    reasons:     list = None
    
    # Exit rules - different from main system
    target_pct:  float = 0.60
    stop_pct:    float = 0.40
    time_stop:   str   = "15:15"
    
    # Options fields (filled by options_analyzer)
    strike:      Optional[float] = None
    premium:     Optional[float] = None
    iv:          Optional[float] = None
    contract:    Optional[str]   = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []

    def __str__(self):
        lines = [
            f"{'='*55}",
            f"  ⚡ FAST ENTRY — V-BOTTOM REVERSAL",
            f"{'='*55}",
            f"  Ticker    : {self.ticker} {self.direction}",
            f"  Time      : {self.timestamp.strftime('%Y-%m-%d %H:%M:%S ET')}",
            f"  Spot      : ${self.spot_price:.2f}",
            f"  Score     : {self.score}/5 (AGGRESSIVE EARLY ENTRY)",
            f"  OR Range  : ${self.or_low:.2f} - ${self.or_high:.2f}",
            f"  VWAP      : ${self.vwap:.2f}",
            f"  RSI(5)    : {self.rsi:.1f}",
            f"  VIX       : {self.vix:.1f}",
        ]
        if self.strike:
            lines += [
                f"  Strike    : ${self.strike:.0f}",
                f"  Premium   : ${self.premium:.2f}",
                f"  Contract  : {self.contract}",
            ]
        lines += [
            f"  Target    : +{self.target_pct*100:.0f}% (faster exit)",
            f"  Stop      : -{self.stop_pct*100:.0f}% (tighter stop)",
            f"  Time Stop : {self.time_stop} ET (earlier close)",
            f"  Reasons   : {' | '.join(self.reasons)}",
            f"{'='*55}",
            f"  ⚠️  HIGHER RISK - Entering before full confirmation",
            f"{'='*55}",
        ]
        return "\n".join(lines)

    def to_dict(self):
        return {k: str(v) for k, v in self.__dict__.items()}


def _is_reversal_candle(df: pd.DataFrame) -> bool:
    """
    Detect reversal candle pattern:
    - Current bar closes higher than previous 3 bars
    - Current bar's low is lower than previous bar (tried to go lower but reversed)
    """
    if len(df) < 4:
        return False
    
    current = df.iloc[-1]
    prev_3  = df.iloc[-4:-1]
    
    # Close must be higher than all previous 3 closes
    if current["Close"] <= prev_3["Close"].max():
        return False
    
    # Low should show capitulation (lower than previous bar)
    if current["Low"] >= df.iloc[-2]["Low"]:
        return False
    
    return True


def _calc_rsi_bounce(df: pd.DataFrame, period: int = 5) -> tuple:
    """
    Check if RSI has bounced from oversold.
    Returns: (min_rsi_recent, current_rsi)
    """
    if len(df) < period + 5:
        return (50.0, 50.0)
    
    # Calculate RSI
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    
    # Check last 5 bars for oversold condition
    min_rsi_recent = float(rsi.tail(5).min())
    current_rsi    = float(rsi.iloc[-1])
    
    return (min_rsi_recent, current_rsi)


def _calc_selloff_severity(current_price: float, or_low: float, vwap: float) -> dict:
    """Calculate how far price has fallen from key levels"""
    return {
        "vs_or_low":  (current_price / or_low - 1) if or_low > 0 else 0,
        "vs_vwap":    (current_price / vwap - 1) if vwap > 0 else 0,
    }


def _avg_volume_session(df: pd.DataFrame) -> float:
    """Get average volume for current session"""
    session = df[df.index.time >= time(9, 30)]
    if session.empty:
        return 0.0
    return float(session["Volume"].mean())


def evaluate_fast_entry(
    ticker:      str,
    df:          pd.DataFrame,
    vix:         float,
    main_score:  int,
    or_high:     float,
    or_low:      float,
    vwap:        float,
) -> Optional[FastEntrySignal]:
    """
    Evaluate if conditions are met for fast entry V-bottom reversal signal.
    
    This only fires when:
    - Main system shows exactly 3/5 (setup developing but not confirmed)
    - Time is between 10 AM - 1 PM ET
    - Sharp selloff detected
    - Reversal pattern confirmed
    - RSI bounce from oversold
    - Strong volume surge
    """
    
    if df is None or df.empty or len(df) < 20:
        return None
    
    now = datetime.now(ET)
    
    # ── Time window check ────────────────────────────────────────────────
    if not (FAST_ENTRY_START <= now.time() <= FAST_ENTRY_END):
        return None
    
    # ── Main system must show exactly 3/5 ────────────────────────────────
    if main_score != 3:
        return None
    
    # ── Get current bar data ─────────────────────────────────────────────
    current      = df.iloc[-1]
    spot         = float(current["Close"])
    current_vol  = float(current["Volume"])
    avg_vol      = _avg_volume_session(df)
    
    # ── Check 1: Sharp selloff detected ──────────────────────────────────
    selloff = _calc_selloff_severity(spot, or_low, vwap)
    
    if selloff["vs_or_low"] > SELLOFF_VS_OR_MIN and selloff["vs_vwap"] > SELLOFF_VS_VWAP_MIN:
        # Not oversold enough
        return None
    
    selloff_pct = min(selloff["vs_or_low"], selloff["vs_vwap"]) * 100
    
    # ── Check 2: Reversal candle pattern ─────────────────────────────────
    if not _is_reversal_candle(df):
        return None
    
    # ── Check 3: RSI bounce from oversold ────────────────────────────────
    min_rsi, current_rsi = _calc_rsi_bounce(df)
    
    if min_rsi >= RSI_OVERSOLD:
        # Wasn't oversold enough
        return None
    
    if current_rsi <= RSI_BOUNCE_MIN:
        # Hasn't bounced yet
        return None
    
    # ── Check 4: Strong volume surge ─────────────────────────────────────
    if avg_vol == 0:
        return None
    
    vol_ratio = current_vol / avg_vol
    if vol_ratio < VOLUME_SURGE_MULT:
        return None
    
    # ── Check 5: VIX filter ──────────────────────────────────────────────
    if vix >= VIX_MAX_FAST:
        return None
    
    # ── All conditions met — fire fast entry signal ──────────────────────
    reasons = [
        f"V-Bottom reversal @ ${spot:.2f}",
        f"Selloff {selloff_pct:.1f}% from highs",
        f"RSI bounce {min_rsi:.0f}→{current_rsi:.0f}",
        f"Volume surge {vol_ratio:.1f}×",
        f"Reversal candle confirmed",
        f"VIX={vix:.1f}<{VIX_MAX_FAST}",
    ]
    
    logger.info(
        f"[{ticker}] ⚡ FAST ENTRY V-BOTTOM signal fired | "
        f"Score 3/5 | Selloff {selloff_pct:.1f}% | "
        f"RSI {min_rsi:.0f}→{current_rsi:.0f}"
    )
    
    return FastEntrySignal(
        ticker     = ticker,
        direction  = "CALL",  # V-bottom is always bullish reversal
        score      = 3,
        timestamp  = now,
        spot_price = spot,
        or_high    = or_high,
        or_low     = or_low,
        vwap       = vwap,
        rsi        = current_rsi,
        vix        = vix,
        reasons    = reasons,
    )


def should_exit_fast_entry(entry_price: float, current_price: float, entry_time: datetime) -> Optional[str]:
    """
    Check if fast entry position should exit.
    Uses tighter rules than main system.
    """
    if current_price <= 0 or entry_price <= 0:
        return None
    
    pnl_pct = (current_price - entry_price) / entry_price
    now     = datetime.now(ET)
    
    # Target: +60%
    if pnl_pct >= 0.60:
        return f"TARGET HIT +{pnl_pct*100:.1f}%"
    
    # Stop: -40%
    if pnl_pct <= -0.40:
        return f"STOP HIT {pnl_pct*100:.1f}%"
    
    # Time stop: 3:15 PM
    if now.time() >= FAST_EXIT_TIME:
        return f"TIME STOP {FAST_EXIT_TIME.strftime('%H:%M')} ET"
    
    # Hard close: 3:45 PM (same as main system)
    if now.time() >= time(15, 45):
        return f"HARD CLOSE 3:45 PM"
    
    return None
