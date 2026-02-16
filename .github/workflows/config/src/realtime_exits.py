import logging
import os
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

_active_monitors: dict = {}


class ExitMonitor:
    def __init__(
        self,
        trade_id:    str,
        ticker:      str,
        direction:   str,
        occ_symbol:  str,
        entry_price: float,
        target_pct:  float = 0.80,
        stop_pct:    float = 0.50,
    ):
        self.trade_id     = trade_id
        self.ticker       = ticker
        self.direction    = direction
        self.occ_symbol   = occ_symbol
        self.entry_price  = entry_price
        self.target_price = entry_price * (1 + target_pct)
        self.stop_price   = entry_price * (1 - stop_pct)
        self.target_pct   = target_pct
        self.stop_pct     = stop_pct
        self.fired        = False
        self.highest_seen = entry_price
        self.lowest_seen  = entry_price
        self.tick_count   = 0

        logger.info(
            f"[{ticker}] Exit monitor armed | "
            f"Entry: ${entry_price:.2f} | "
            f"Target: ${self.target_price:.2f} | "
            f"Stop: ${self.stop_price:.2f}"
        )

    def on_quote(self, occ_symbol: str, quote) -> Optional[str]:
        if self.fired:
            return None

        mid = quote.mid
        if mid <= 0:
            return None

        self.tick_count   += 1
        self.highest_seen  = max(self.highest_seen, mid)
        self.lowest_seen   = min(self.lowest_seen,  mid)

        now    = datetime.now(ET)
        reason = None

        if mid >= self.target_price:
            pnl    = (mid - self.entry_price) / self.entry_price
            reason = f"TARGET HIT +{pnl*100:.1f}% | ${self.entry_price:.2f} to ${mid:.2f}"
        elif mid <= self.stop_price:
            pnl    = (mid - self.entry_price) / self.entry_price
            reason = f"STOP HIT {pnl*100:.1f}% | ${self.entry_price:.2f} to ${mid:.2f}"
        elif now.time() >= __import__('datetime').time(15, 45):
            reason = f"HARD CLOSE 3:45 PM | ${mid:.2f}"
        elif now.time() >= __import__('datetime').time(15, 30):
            reason = f"TIME STOP 3:30 PM | ${mid:.2f}"

        if reason:
            self.fired = True
            self._send_exit_alert(mid, reason)
            return reason

        return None

    def _send_exit_alert(self, exit_price: float, reason: str):
        pnl_pct    = (exit_price - self.entry_price) / self.entry_price
        pnl_dollar = (exit_price - self.entry_price) * 100
        emoji      = "✅" if pnl_pct > 0 else "🛑"

        logger.info(
            f"{emoji} EXIT [{self.trade_id}] {reason} | "
            f"P&L: {pnl_pct*100:+.1f}% (${pnl_dollar:+.2f}/contract)"
        )

        try:
            from src.trade_manager import _load_trades, _save_trades
            trades = _load_trades()
            for t in trades:
                if t.trade_id == self.trade_id and t.status == "OPEN":
                    t.exit_price = exit_price
                    t.exit_time  = datetime.now(ET).isoformat()
                    t.pnl_pct    = pnl_pct
                    t.status     = (
                        "CLOSED_TARGET" if "TARGET" in reason else
                        "CLOSED_STOP"   if "STOP"   in reason else
                        "CLOSED_TIME"
                    )
            _save_trades(trades)
        except Exception as e:
            logger.error(f"Trade state update failed: {e}")

        try:
            sender    = os.environ.get("EMAIL_ADDRESS", "")
            password  = os.environ.get("EMAIL_APP_PASSWORD", "")
            recipient = os.environ.get("RECIPIENT_EMAIL") or sender
            phone     = os.environ.get("RECIPIENT_PHONE", "")
            carrier   = os.environ.get("CARRIER", "").lower()

            if not sender or not password:
                return

            color   = "#16a34a" if pnl_pct > 0 else "#dc2626"
            subject = (
                f"{emoji} EXIT {self.direction} {self.ticker} "
                f"| {pnl_pct*100:+.1f}% (${pnl_dollar:+.2f}) "
                f"| {reason.split('|')[0].strip()}"
            )
            html = f"""
            <html><body style="font-family:Arial,sans-serif">
            <div style="background:{color};color:white;padding:16px;
                        border-radius:8px 8px 0 0">
              <h2 style="margin:0">{emoji} EXIT - {self.ticker} {self.direction}</h2>
              <p style="margin:4px 0">{datetime.now(ET).strftime('%H:%M:%S ET')}</p>
            </div>
            <div style="border:1px solid #e5e7eb;padding:16px;
                        border-radius:0 0 8px 8px">
              <table>
                <tr><td><b>Contract</b></td><td>{self.occ_symbol}</td></tr>
                <tr><td><b>Entry</b></td><td>${self.entry_price:.2f}</td></tr>
                <tr><td><b>Exit</b></td><td>${exit_price:.2f}</td></tr>
                <tr><td><b>P&L %</b></td>
                    <td style="color:{color}"><b>{pnl_pct*100:+.1f}%</b></td></tr>
                <tr><td><b>P&L $</b></td>
                    <td style="color:{color}"><b>${pnl_dollar:+.2f}/contract</b></td></tr>
                <tr><td><b>Reason</b></td><td>{reason}</td></tr>
                <tr><td><b>High seen</b></td><td>${self.highest_seen:.2f}</td></tr>
                <tr><td><b>Low seen</b></td><td>${self.lowest_seen:.2f}</td></tr>
              </table>
            </div>
            </body></html>
            """

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = sender
            msg["To"]      = recipient
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                s.login(sender, password)
                s.sendmail(sender, recipient, msg.as_string())

            if phone and carrier:
                from src.alert_system import CARRIER_GATEWAYS
                domain = CARRIER_GATEWAYS.get(carrier)
                if domain:
                    digits  = "".join(filter(str.isdigit, phone))[-10:]
                    gateway = f"{digits}@{domain}"
                    sms     = (
                        f"{emoji} EXIT {self.ticker} {self.direction} "
                        f"{pnl_pct*100:+.1f}% ${pnl_dollar:+.0f} "
                        f"@ ${exit_price:.2f}"
                    )
                    sms_msg = MIMEText(sms)
                    sms_msg["From"] = sender
                    sms_msg["To"]   = gateway
                    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                        s.login(sender, password)
                        s.sendmail(sender, gateway, sms_msg.as_string())

        except Exception as e:
            logger.error(f"Exit alert failed: {e}")


def register_exit_monitor(
    trade_id:    str,
    ticker:      str,
    direction:   str,
    occ_symbol:  str,
    entry_price: float,
    target_pct:  float = 0.80,
    stop_pct:    float = 0.50,
) -> ExitMonitor:
    monitor = ExitMonitor(
        trade_id, ticker, direction, occ_symbol,
        entry_price, target_pct, stop_pct
    )
    _active_monitors[occ_symbol] = monitor
    return monitor


def on_option_tick(occ_symbol: str, quote, source) -> Optional[str]:
    monitor = _active_monitors.get(occ_symbol)
    if monitor and not monitor.fired:
        result = monitor.on_quote(occ_symbol, quote)
        if result:
            del _active_monitors[occ_symbol]
            return result
    return None


def get_active_monitors() -> dict:
    return {k: v for k, v in _active_monitors.items() if not v.fired}


def clear_fired_monitors():
    fired = [k for k, v in _active_monitors.items() if v.fired]
    for k in fired:
        del _active_monitors[k]
