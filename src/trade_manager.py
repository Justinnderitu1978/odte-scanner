"""
src/trade_manager.py
====================
Trade position tracking and exit monitoring with milestone alerts.

Monitors open positions every scan cycle and sends alerts at:
- +15% profit — consider taking profit
- +20% peak — position peaked, watch for reversal
- -10% warning — position going negative
- -20% stop — EXIT RECOMMENDED

Auto-closes positions only at:
- Full target (+80%)
- Catastrophic stop (-50%)
- Time stops (3:30 PM and 3:45 PM)
"""

import json
import os
import logging
import smtplib
from datetime import datetime, time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pytz

logger = logging.getLogger(__name__)

ET           = pytz.timezone("America/New_York")
TRADES_FILE  = Path("logs/active_trades.json")
EARLY_EXIT   = time(15, 30)
HARD_EXIT    = time(15, 45)


@dataclass
class Trade:
    trade_id:    str
    ticker:      str
    direction:   str
    contract:    str
    strike:      float
    entry_price: float
    entry_spot:  float
    entry_time:  str
    target_pct:  float = 0.80
    stop_pct:    float = 0.20
    status:      str   = "OPEN"
    exit_price:  Optional[float] = None
    exit_time:   Optional[str]   = None
    pnl_pct:     Optional[float] = None
    
    # Alert tracking - prevents duplicate alerts
    alert_warning_sent:  bool = False  # -10%
    alert_stop_sent:     bool = False  # -20%
    alert_profit15_sent: bool = False  # +15%
    alert_profit20_sent: bool = False  # +20%

    @property
    def target_price(self) -> float:
        return self.entry_price * (1 + self.target_pct)

    @property
    def stop_price(self) -> float:
        return self.entry_price * (1 - self.stop_pct)

    @property
    def pnl_dollars(self) -> Optional[float]:
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) * 100

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        # Handle both old and new trade formats
        valid_keys = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid_keys)


def _load_trades() -> list:
    if not TRADES_FILE.exists():
        return []
    try:
        raw = json.loads(TRADES_FILE.read_text())
        return [Trade.from_dict(r) for r in raw]
    except Exception as e:
        logger.error(f"Error loading trades: {e}")
        return []


def _save_trades(trades: list):
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRADES_FILE.write_text(
        json.dumps([t.to_dict() for t in trades], indent=2)
    )


def open_trade(signal) -> Optional[Trade]:
    """Register a new trade from a signal"""
    if not hasattr(signal, 'premium') or signal.premium is None or signal.premium <= 0:
        logger.warning("Signal has no premium - trade not registered")
        return None

    trade = Trade(
        trade_id    = f"{signal.ticker}_{datetime.now(ET).strftime('%Y%m%d_%H%M%S')}",
        ticker      = signal.ticker,
        direction   = signal.direction,
        contract    = signal.contract or "UNKNOWN",
        strike      = signal.strike or 0,
        entry_price = signal.premium,
        entry_spot  = signal.spot_price,
        entry_time  = signal.timestamp.isoformat(),
        target_pct  = getattr(signal, 'target_pct', 0.80),
        stop_pct    = getattr(signal, 'stop_pct', 0.20),
    )

    trades = _load_trades()
    trades.append(trade)
    _save_trades(trades)
    logger.info(f"Trade opened: {trade.trade_id}")
    return trade


def _get_current_premium(trade: Trade) -> Optional[float]:
    """Fetch current option premium for a trade"""
    try:
        import yfinance as yf
        from src.market_data import get_options_chain
        
        # Extract ticker from trade
        ticker = trade.ticker
        
        # Get available expiration dates
        ticker_obj = yf.Ticker(ticker)
        expirations = ticker_obj.options
        
        if not expirations:
            logger.warning(f"No expirations available for {ticker}")
            return None
        
        # Use first available expiration (today for 0DTE)
        expiry = expirations[0]
        
        # Get option chain
        chain = get_options_chain(ticker, expiry)
        if not chain:
            return None

        # Select calls or puts based on trade direction
        df = chain.calls if trade.direction == "CALL" else chain.puts
        if df is None or df.empty:
            return None

        # Find the row matching the trade's strike
        row = df[df["strike"] == trade.strike]
        
        # If exact strike not found, find closest
        if row.empty:
            df["dist"] = (df["strike"] - trade.strike).abs()
            row = df.nsmallest(1, "dist")
        
        if row.empty:
            return None

        # Extract pricing data
        bid = float(row.iloc[0].get("bid", 0) or 0)
        ask = float(row.iloc[0].get("ask", 0) or 0)
        last = float(row.iloc[0].get("lastPrice", 0) or 0)

        # Return mid price or last
        if ask > 0:
            return (bid + ask) / 2
        return last if last > 0 else None

    except Exception as e:
        logger.error(f"Error fetching premium for {trade.trade_id}: {e}")
        return None


def _send_exit_email(subject: str, body: str):
    """Send exit or milestone alert email"""
    
    # EXIT EMAILS DISABLED - only send entry signal alerts
    return
    
    sender    = os.environ.get("EMAIL_ADDRESS", "")
    password  = os.environ.get("EMAIL_APP_PASSWORD", "")
    recipient = os.environ.get("RECIPIENT_EMAIL") or sender

    if not sender or not password:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.sendmail(sender, recipient, msg.as_string())
        logger.info(f"Alert email sent: {subject[:50]}...")
    except Exception as e:
        logger.error(f"Email failed: {e}")


def _send_milestone_alert(trade, current_price, pnl_pct, level, emoji, title, message, color):
    """Send milestone alert via email and SMS"""
    
    # MILESTONE ALERTS DISABLED
    return
    
    pnl_dollars = (current_price - trade.entry_price) * 100
    
    subject = (
        f"{emoji} {level} {trade.ticker} {trade.direction} "
        f"| Entry ${trade.entry_price:.2f} → ${current_price:.2f}"
    )
    
    html = f"""
    <html><body style="font-family:Arial,sans-serif">
    <div style="background:{color};color:white;padding:16px;border-radius:8px 8px 0 0">
      <h2 style="margin:0">{emoji} {title}</h2>
      <h3 style="margin:4px 0">{trade.ticker} {trade.direction} {level}</h3>
      <p style="margin:4px 0">{datetime.now(ET).strftime('%H:%M:%S ET')}</p>
    </div>
    <div style="border:1px solid #e5e7eb;padding:16px;border-radius:0 0 8px 8px">
      <table>
        <tr><td><b>Contract</b></td><td>{trade.contract}</td></tr>
        <tr><td><b>Entry</b></td><td>${trade.entry_price
