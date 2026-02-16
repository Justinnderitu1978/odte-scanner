import json
import os
import logging
from datetime import datetime, time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
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
    stop_pct:    float = 0.50
    status:      str   = "OPEN"
    exit_price:  Optional[float] = None
    exit_time:   Optional[str]   = None
    pnl_pct:     Optional[float] = None

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
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


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
    if signal.premium is None or signal.premium <= 0:
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
        target_pct  = signal.target_pct,
        stop_pct    = signal.stop_pct,
    )

    trades = _load_trades()
    trades.append(trade)
    _save_trades(trades)
    logger.info(f"Trade opened: {trade.trade_id}")
    return trade


def _get_current_premium(trade: Trade) -> Optional[float]:
    try:
        from src.market_data import get_options_chain

        chain = get_options_chain(trade.ticker)
        if not chain:
            return None

        df = chain.get("calls") if trade.direction == "CALL" else chain.get("puts")
        if df is None or df.empty:
            return None

        row = df[df["strike"] == trade.strike]
        if row.empty:
            df["dist"] = (df["strike"] - trade.strike).abs()
            row = df.nsmallest(1, "dist")
        if row.empty:
            return None

        bid  = float(row.iloc[0].get("bid",       0) or 0)
        ask  = float(row.iloc[0].get("ask",       0) or 0)
        last = float(row.iloc[0].get("lastPrice", 0) or 0)

        if ask > 0:
            return (bid + ask) / 2
        return last if last > 0 else None

    except Exception as e:
        logger.error(f"Error fetching premium for {trade.trade_id}: {e}")
        return None


def _send_exit_email(subject: str, body: str):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

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
        logger.info(f"Exit alert sent to {recipient}")
    except Exception as e:
        logger.error(f"Exit email failed: {e}")


def check_exits():
    trades  = _load_trades()
    now     = datetime.now(ET)
    changed = False

    open_trades = [t for t in trades if t.status == "OPEN"]
    if not open_trades:
        logger.debug("No open trades to monitor")
        return

    for trade in open_trades:
        reason     = None
        exit_price = None
        current    = _get_current_premium(trade)

        if current is not None:
            if current >= trade.target_price:
                reason     = f"Profit target hit (+{trade.target_pct*100:.0f}%)"
                exit_price = current
            elif current <= trade.stop_price:
                reason     = f"Stop loss hit (-{trade.stop_pct*100:.0f}%)"
                exit_price = current

        if now.time() >= HARD_EXIT:
            reason     = "Hard close 3:45 PM ET"
            exit_price = current or trade.stop_price
        elif now.time() >= EARLY_EXIT and not reason:
            reason     = "Time stop 3:30 PM ET"
            exit_price = current or trade.entry_price

        if reason:
            trade.exit_price = exit_price
            trade.exit_time  = now.isoformat()
            trade.pnl_pct    = (
                (exit_price - trade.entry_price) / trade.entry_price
                if exit_price else None
            )
            trade.status = (
                "CLOSED_TARGET" if "target" in reason else
                "CLOSED_STOP"   if "Stop"   in reason else
                "CLOSED_TIME"
            )
            changed = True

            pnl     = trade.pnl_pct or 0
            emoji   = "✅" if pnl > 0 else "🛑"
            subject = (
                f"{emoji} EXIT {trade.direction} {trade.ticker} "
                f"| {reason} | P&L: {pnl*100:.1f}%"
            )
            body = f"""
            <html><body style="font-family:Arial,sans-serif">
            <h2>{emoji} Trade Exit - {trade.ticker} {trade.direction}</h2>
            <table>
              <tr><td><b>Trade ID</b></td><td>{trade.trade_id}</td></tr>
              <tr><td><b>Contract</b></td><td>{trade.contract}</td></tr>
              <tr><td><b>Reason</b></td><td>{reason}</td></tr>
              <tr><td><b>Entry</b></td><td>${trade.entry_price:.2f}</td></tr>
              <tr><td><b>Exit</b></td><td>${exit_price:.2f}</td></tr>
              <tr><td><b>P&L %</b></td>
                  <td style="color:{'green' if pnl>0 else 'red'}">
                  <b>{pnl*100:+.1f}%</b></td></tr>
              <tr><td><b>P&L $</b></td>
                  <td>${(trade.pnl_dollars or 0):+.2f}/contract</td></tr>
            </table>
            </body></html>
            """
            _send_exit_email(subject, body)
            logger.info(
                f"Trade closed: {trade.trade_id} | "
                f"{reason} | P&L: {pnl:.2%}"
            )

    if changed:
        _save_trades(trades)


def print_trade_summary():
    trades   = _load_trades()
    open_t   = [t for t in trades if t.status == "OPEN"]
    closed_t = [t for t in trades if t.status != "OPEN"]

    print(f"\n{'='*55}")
    print(f"  TRADE SUMMARY - {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'='*55}")
    print(f"  Open trades   : {len(open_t)}")
    print(f"  Closed trades : {len(closed_t)}")

    if closed_t:
        pnls = [t.pnl_pct for t in closed_t if t.pnl_pct is not None]
        if pnls:
            wins = sum(1 for p in pnls if p > 0)
            print(f"  Win rate      : {wins}/{len(pnls)} ({wins/len(pnls)*100:.0f}%)")
            print(f"  Avg P&L       : {sum(pnls)/len(pnls)*100:+.1f}%")

    for t in open_t:
        print(f"  OPEN | {t.trade_id} | {t.direction} | Entry ${t.entry_price:.2f}")
    print(f"{'='*55}\n")
