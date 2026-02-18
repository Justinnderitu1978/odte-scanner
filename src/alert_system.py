import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

CARRIER_GATEWAYS = {
    "verizon":   "vtext.com",
    "att":       "mms.att.net",      # Changed from txt.att.net
    "tmobile":   "tmomail.net",
    "sprint":    "messaging.sprintpcs.com",
    "boost":     "sms.myboostmobile.com",
    "cricket":   "sms.cricketwireless.com",
    "uscellular":"email.uscc.net",
    "metro":     "mymetropcs.com",
}

def _get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _sms_gateway_address():
    phone   = _get_env("RECIPIENT_PHONE")
    carrier = _get_env("CARRIER").lower()
    if not phone or not carrier:
        return None
    domain  = CARRIER_GATEWAYS.get(carrier)
    if not domain:
        logger.warning(f"Unknown carrier: {carrier}")
        return None
    digits  = "".join(filter(str.isdigit, phone))[-10:]
    return f"{digits}@{domain}"


def _build_email_body(signal):
    emoji   = "BUY CALL" if signal.direction == "CALL" else "BUY PUT"
    subject = (
        f"0DTE {emoji} {signal.ticker} "
        f"@ ${signal.spot_price:.2f} Score {signal.score}/5"
    )

    contract_html = ""
    if signal.strike:
        prem   = signal.premium or 0
        target = prem * (1 + signal.target_pct)
        stop   = prem * (1 - signal.stop_pct)
        contract_html = f"""
        <tr><td><b>Contract</b></td><td>{signal.contract}</td></tr>
        <tr><td><b>Strike</b></td><td>${signal.strike:.0f}</td></tr>
        <tr><td><b>Premium</b></td><td>${prem:.2f} (${prem*100:.0f}/contract)</td></tr>
        <tr><td><b>Target +{signal.target_pct*100:.0f}%</b></td>
            <td style="color:green"><b>${target:.2f}</b></td></tr>
        <tr><td><b>Stop -{signal.stop_pct*100:.0f}%</b></td>
            <td style="color:red"><b>${stop:.2f}</b></td></tr>
        """

    color   = "#16a34a" if signal.direction == "CALL" else "#dc2626"
    reasons = "<br>".join(f"- {r}" for r in signal.reasons)

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
    <div style="background:{color};color:white;padding:16px;border-radius:8px 8px 0 0">
      <h2 style="margin:0">0DTE {signal.direction} Signal - {signal.ticker}</h2>
      <p style="margin:4px 0">{signal.timestamp.strftime('%A %B %d, %Y %H:%M:%S ET')}</p>
    </div>
    <div style="border:1px solid #e5e7eb;border-top:none;padding:16px;border-radius:0 0 8px 8px">
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:4px 8px"><b>Ticker</b></td><td>{signal.ticker}</td></tr>
        <tr><td style="padding:4px 8px"><b>Direction</b></td>
            <td style="color:{color}"><b>{signal.direction}</b></td></tr>
        <tr><td style="padding:4px 8px"><b>Spot Price</b></td><td>${signal.spot_price:.2f}</td></tr>
        <tr><td style="padding:4px 8px"><b>Score</b></td><td>{signal.score}/5</td></tr>
        <tr><td style="padding:4px 8px"><b>OR Range</b></td>
            <td>${signal.or_low:.2f} - ${signal.or_high:.2f}</td></tr>
        <tr><td style="padding:4px 8px"><b>VWAP</b></td><td>${signal.vwap:.2f}</td></tr>
        <tr><td style="padding:4px 8px"><b>RSI(5)</b></td><td>{signal.rsi:.1f}</td></tr>
        <tr><td style="padding:4px 8px"><b>VIX</b></td><td>{signal.vix:.1f}</td></tr>
        {contract_html}
      </table>
      <h3>Signal Reasons</h3>
      <p style="background:#f9fafb;padding:10px;border-radius:6px">{reasons}</p>
      <h3>Exit Rules</h3>
      <ul>
        <li>Profit Target: +{signal.target_pct*100:.0f}% - take it</li>
        <li>Stop Loss: -{signal.stop_pct*100:.0f}% - exit immediately</li>
        <li>Time Stop: Close by 3:30 PM ET</li>
        <li>Hard Close: Market order at 3:45 PM ET</li>
      </ul>
      <p style="color:#6b7280;font-size:12px;margin-top:24px">
        Automated signal - not financial advice.
        0DTE options carry extreme risk of total loss.
      </p>
    </div>
    </body></html>
    """
    return subject, html


def _build_sms_body(signal) -> str:
    prem_str = f" Prem:${signal.premium:.2f}" if signal.premium else ""
    return (
        f"0DTE {signal.direction} {signal.ticker} "
        f"${signal.spot_price:.2f} Score:{signal.score}/5"
        f"{prem_str} "
        f"Tgt:+{signal.target_pct*100:.0f}% Stp:-{signal.stop_pct*100:.0f}% "
        f"Exit by 3:30PM ET"
    )[:320]


def send_email(signal) -> bool:
    sender    = _get_env("EMAIL_ADDRESS")
    password  = _get_env("EMAIL_APP_PASSWORD")
    recipient = _get_env("RECIPIENT_EMAIL") or sender

    if not sender or not password:
        logger.error("EMAIL_ADDRESS or EMAIL_APP_PASSWORD not set")
        return False

    subject, html = _build_email_body(signal)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info(f"Email alert sent to {recipient}")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


def send_sms_via_gateway(signal) -> bool:
    sender   = _get_env("EMAIL_ADDRESS")
    password = _get_env("EMAIL_APP_PASSWORD")
    gateway  = _sms_gateway_address()

    if not gateway:
        logger.warning("SMS gateway address could not be resolved")
        return False
    if not sender or not password:
        logger.error("Email credentials missing for SMS gateway")
        return False

    body = _build_sms_body(signal)
    msg  = MIMEText(body)
    msg["Subject"] = ""
    msg["From"]    = sender
    msg["To"]      = gateway

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, gateway, msg.as_string())
        logger.info(f"SMS sent via gateway to {gateway}")
        return True
    except Exception as e:
        logger.error(f"SMS gateway send failed: {e}")
        return False


def dispatch_alerts(signal) -> dict:
    results = {
        "email": send_email(signal),
        "sms":   send_sms_via_gateway(signal),
    }
    logger.info(f"Alert dispatch results: {results}")
    return results
