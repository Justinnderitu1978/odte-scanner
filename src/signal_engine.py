import pandas as pd
import numpy as np
import logging
from datetime import datetime, time, timedelta
from dataclasses import dataclass, field
from typing import Optional
import pytz

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

OPENING_RANGE_MINUTES = 15
SIGNAL_SCORE_THRESHOLD = 4
SIGNAL_COOLDOWN_MINUTES = 30
NO_NEW_SIGNAL_AFTER    = time(15,  0)
HARD_EXIT_TIME         = time(15, 45)
EARLY_EXIT_TIME        = time(15, 30)
MIN_SIGNAL_AFTER       = time( 9, 50)
RSI_PERIOD             = 5
VOLUME_SURGE_RATIO     = 1.5
VIX_MAX                = 25.0


@dataclass
class Signal:
    ticker:      str
    direction:   str
    score:       int
    timestamp:   datetime
    spot_price:  float
    or_high:     float
    or_low:      float
    vwap:        float
    rsi:         float
    vix:         float
    reasons:     list = field(default_factory=list)
    strike:      Optional[float] = None
    premium:     Optional[float] = None
    iv:          Optional[float] = None
    contract:    Optional[str]   = None
    target_pct:  float = 0.80
    stop_pct:    float = 0.50

    def __str__(self):
        lines = [
            f"{'='*55}",
            f"  0DTE SIGNAL — {self.direction}  {self.ticker}",
            f"{'='*55}",
            f"  Time      : {self.timestamp.strftime('%Y-%m-%d %H:%M:%S ET')}",
            f"  Spot      : ${self.spot_price:.2f}",
            f"  Score     : {self.score}/5",
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
            f"  Reasons   : {' | '.join(self.reasons)}",
            f"{'='*55}",
        ]
        return "\n".join(l for l in lines if l)

    def to_dict(self):
        return {k: str(v) for k, v in self.__dict__.items()}


def _calc_rsi(series: pd.Series, period: int = 5) -> float:
    if len(series) < period + 1:
        return 50.0
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def _calc_vwap(df: pd.DataFrame) -> float:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_tpv = (typical * df["Volume"]).cumsum()
    cum_vol = df["Volume"].cumsum()
    vwap_series = cum_tpv / cum_vol.replace(0, np.nan)
    return float(vwap_series.iloc[-1])


def _calc_opening_range(df: pd.DataFrame) -> tuple:
    market_open = df.index[df.index.time >= time(9, 30)]
    if len(market_open) == 0:
        return (float("nan"), float("nan"))
    open_dt  = market_open[0]
    or_end   = open_dt + timedelta(minutes=OPENING_RANGE_MINUTES)
    or_bars  = df[(df.index >= open_dt) & (df.index <= or_end)]
    if or_bars.empty:
        return (float("nan"), float("nan"))
    return (float(or_bars["High"].max()), float(or_bars["Low"].min()))


def _avg_volume_today(df: pd.DataFrame) -> float:
    session = df[df.index.time >= time(9, 30)]
    if session.empty:
        return 0.0
    return float(session["Volume"].mean())


class SignalEngine:
    def __init__(self):
        self._last_signal: dict = {}

    def _cooldown_ok(self, ticker: str, now: datetime) -> bool:
        if ticker not in self._last_signal:
            return True
        elapsed = (now - self._last_signal[ticker]).total_seconds() / 60
        return elapsed >= SIGNAL_COOLDOWN_MINUTES

    def evaluate(self, ticker: str, df: pd.DataFrame, vix: float) -> Optional[Signal]:
        if df is None or df.empty:
            return None

        now = datetime.now(ET)

        if now.time() < MIN_SIGNAL_AFTER:
            return None
        if now.time() > NO_NEW_SIGNAL_AFTER:
            return None
        if not self._cooldown_ok(ticker, now):
            return None

        or_high, or_low = _calc_opening_range(df)
        if np.isnan(or_high):
            return None

        current   = df.iloc[-1]
        spot      = float(current["Close"])
        cur_vol   = float(current["Volume"])
        avg_vol   = _avg_volume_today(df)

        vwap      = _calc_vwap(df)
        rsi       = _calc_rsi(df["Close"], RSI_PERIOD)
        vol_surge = (cur_vol >= VOLUME_SURGE_RATIO * avg_vol) if avg_vol > 0 else False

        bull_score = 0
        bear_score = 0
        bull_reasons = []
        bear_reasons = []

        if spot > or_high:
            bull_score += 1
            bull_reasons.append(f"ORB up ${spot:.2f}>${or_high:.2f}")
        elif spot < or_low:
            bear_score += 1
            bear_reasons.append(f"ORB down ${spot:.2f}<${or_low:.2f}")

        if spot > vwap:
            bull_score += 1
            bull_reasons.append(f"VWAP up ${spot:.2f}>${vwap:.2f}")
        else:
            bear_score += 1
            bear_reasons.append(f"VWAP down ${spot:.2f}<${vwap:.2f}")

        if rsi > 55:
            bull_score += 1
            bull_reasons.append(f"RSI={rsi:.1f}>55")
        elif rsi < 45:
            bear_score += 1
            bear_reasons.append(f"RSI={rsi:.1f}<45")

        if vol_surge:
            if spot > vwap:
                bull_score += 1
                bull_reasons.append(f"VolSurge {cur_vol/avg_vol:.1f}x")
            else:
                bear_score += 1
                bear_reasons.append(f"VolSurge {cur_vol/avg_vol:.1f}x")

        if vix < VIX_MAX:
            bull_score += 1
            bear_score += 1
            bull_reasons.append(f"VIX={vix:.1f}<{VIX_MAX}")
            bear_reasons.append(f"VIX={vix:.1f}<{VIX_MAX}")

        direction  = None
        score      = 0
        reasons    = []

        if bull_score >= SIGNAL_SCORE_THRESHOLD and bull_score > bear_score:
            direction = "CALL"
            score     = bull_score
            reasons   = bull_reasons
        elif bear_score >= SIGNAL_SCORE_THRESHOLD and bear_score > bull_score:
            direction = "PUT"
            score     = bear_score
            reasons   = bear_reasons

# ... some code above ...
    
    if not direction:
        # Pre-signal alerts disabled - only alert on full 4/5 signals
        return None
    
    # ... rest of code below ...
        self._last_signal[ticker] = now

        return Signal(
            ticker     = ticker,
            direction  = direction,
            score      = score,
            timestamp  = now,
            spot_price = spot,
            or_high    = or_high,
            or_low     = or_low,
            vwap       = vwap,
            rsi        = rsi,
            vix        = vix,
            reasons    = reasons,
        )


_engine = SignalEngine()


def run_scanner(ticker: str, df: pd.DataFrame, vix: float) -> Optional[Signal]:
    return _engine.evaluate(ticker, df, vix)
