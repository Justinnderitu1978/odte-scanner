import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from dataclasses import dataclass, field
from typing import Optional
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

_pre_alerts_sent: dict = {}


@dataclass
class PreSignal:
    ticker:      str
    direction:   str
    score:       int
    needed:      int
    missing:     list
    timestamp:   datetime
    spot_price:  float
    vwap:        float
    rsi:         float
    or_high:     float
    or_low:      float

    @property
    def missing_str(self) -> str:
        return " | ".join(self.missing) if self.missing else "Unknown"


def _sms_body(ps: PreSignal) -> str:
    arrow = "up" if ps.direction == "CALL" else "down"
    return (
        f"SETUP {ps.ticker} {ps.direction} {arrow} "
        f"Score:{ps.score}/{ps.needed} "
        f"Need: {ps.missing_str[:60]} "
        f"${ps.spot_price:.2f} {ps.timestamp.strftime('%H:%M ET')}"
    )[:320]


def _send_pre_alert_sms(ps: PreSignal):
    sender   = os.environ.get("EMAIL_ADDRESS", "")
    password = os.environ.get("EMAIL_APP_PASSWORD", "")
    phone    = os.environ.get("RECIPIENT_PHONE", "")
    carrier  = os.environ.get("CARRIER", "").lower()

    if not all([sender, password, phone, carrier]):
        return

    from src.alert_system import CARRIER_GATEWAYS
    domain  = CARRIER_GATEWAYS.get(carrier)
    if not domain:
        return

    digits  = "".join(filter(str.isdigit, phone))[-10:]
    gateway = f"{digits}@{domain}"

    msg = MIMEText(_sms_body(ps))
    msg["Subject"] = ""
    msg["From"]    = sender
    msg["To"]      = gateway

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.sendmail(sender, gateway, msg.as_string())
        logger.info(f"Pre-signal SMS sent for {ps.ticker} {ps.direction}")
    except Exception as e:
        logger.error(f"Pre-signal SMS failed: {e}")


def check_pre_signal(
    ticker:    str,
    score:     int,
    direction: str,
    missing:   list,
    spot:      float,
    vwap:      float,
    rsi:       float,
    or_high:   float,
    or_low:    float,
    threshold: int = 4,
) -> Optional[PreSignal]:
    if score < threshold - 1:
        return None

    key = f"{ticker}_{direction}_{datetime.now(ET).strftime('%H%M')[:3]}"
    if key in _pre_alerts_sent:
        return None

    _pre_alerts_sent[key] = datetime.now(ET).isoformat()

    ps = PreSignal(
        ticker     = ticker,
        direction  = direction,
        score      = score,
        needed     = threshold,
        missing    = missing,
        timestamp  = datetime.now(ET),
        spot_price = spot,
        vwap       = vwap,
        rsi        = rsi,
        or_high    = or_high,
        or_low     = or_low,
    )

    _send_pre_alert_sms(ps)
    logger.info(
        f"[{ticker}] PRE-SIGNAL {direction} score={score}/{threshold} "
        f"missing=[{ps.missing_str}]"
    )
    return ps
