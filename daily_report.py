"""
daily_report.py
===============
Sends an end-of-day email with all trade results, P&L summary,
and win/loss statistics. Run automatically at 4:15 PM ET by
the daily_report.yml GitHub Actions workflow.
"""

import json
import os
import smtplib
from datetime import datetime
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pytz

ET          = pytz.timezone("America/New_York")
TRADES_FILE = Path("logs/active_trades.json")
today_str   = datetime.now(ET).strftime("%A, %B %d, %Y")


def load_todays_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    raw = json.loads(TRADES_FILE.read_text())
    today_date = datetime.now(ET).strftime("%Y-%m-%d")
    return [t for t in raw if t.get("entry_time", "").startswith(today_date)]


def build_report_html(trades: list[dict]) -> str:
    if not trades:
        return f"""
        <html><body>
        <h2>📊 0DTE Daily Report — {today_str}</h2>
        <p>No trades were taken today.</p>
        </body></html>
        """

    closed = [t for t in trades if t["status"] != "OPEN"]
    open_t = [t for t in trades if t["status"] == "OPEN"]

    pnls = [float(t["pnl_pct"]) for t in closed if t.get("pnl_pct") is not None]
    wins  = sum(1 for p in pnls if p > 0)
    total = len(pnls)
    avg   = sum(pnls) / len(pnls) if pnls else 0
    total_dollar = sum(
        (float(t.get("exit_price", 0)) - float(t.get("entry_price", 0))) * 100
        for t in closed if t.get("exit_price")
    )

    def status_emoji(s):
        return {"CLOSED_TARGET": "✅", "CLOSED_STOP": "🛑", "CLOSED_TIME": "⏰", "OPEN": "🔄"}.get(s, "❓")

    rows = ""
    for t in trades:
        pnl_pct = float(t.get("pnl_pct") or 0)
        pnl_color = "green" if pnl_pct > 0 else "red"
        rows += f"""
        <tr>
          <td>{status_emoji(t['status'])}</td>
          <td>{t.get('ticker')}</td>
          <td><b>{t.get('direction')}</b></td>
          <td>{t.get('contract', 'N/A')}</td>
          <td>${float(t.get('entry_price', 0)):.2f}</td>
          <td>${float(t.get('exit_price') or 0):.2f}</td>
          <td style="color:{pnl_color}"><b>{pnl_pct*100:+.1f}%</b></td>
          <td>{t.get('status', '').replace('CLOSED_', '').replace('_', ' ')}</td>
        </tr>
        """

    summary_color = "green" if avg > 0 else "red"

    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;max-width:700px;margin:auto">
    <div style="background:#1e293b;color:white;padding:20px;border-radius:8px 8px 0 0">
      <h2 style="margin:0">📊 0DTE Daily Report</h2>
      <p style="margin:4px 0;opacity:0.8">{today_str}</p>
    </div>
    <div style="border:1px solid #e5e7eb;padding:20px;border-radius:0 0 8px 8px">

      <!-- Summary Stats -->
      <div style="display:flex;gap:16px;margin-bottom:20px">
        <div style="flex:1;background:#f8fafc;padding:12px;border-radius:8px;text-align:center">
          <div style="font-size:24px;font-weight:bold">{total}</div>
          <div style="color:#64748b">Total Trades</div>
        </div>
        <div style="flex:1;background:#f8fafc;padding:12px;border-radius:8px;text-align:center">
          <div style="font-size:24px;font-weight:bold">{wins}/{total}</div>
          <div style="color:#64748b">Win / Total</div>
        </div>
        <div style="flex:1;background:#f8fafc;padding:12px;border-radius:8px;text-align:center">
          <div style="font-size:24px;font-weight:bold;color:{summary_color}">{avg*100:+.1f}%</div>
          <div style="color:#64748b">Avg P&L</div>
        </div>
        <div style="flex:1;background:#f8fafc;padding:12px;border-radius:8px;text-align:center">
          <div style="font-size:24px;font-weight:bold;color:{'green' if total_dollar>0 else 'red'}">${total_dollar:+.0f}</div>
          <div style="color:#64748b">Total $ P&L</div>
        </div>
      </div>

      <!-- Trade Table -->
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <thead>
          <tr style="background:#f1f5f9">
            <th style="padding:8px;text-align:left"></th>
            <th style="padding:8px;text-align:left">Ticker</th>
            <th style="padding:8px;text-align:left">Dir</th>
            <th style="padding:8px;text-align:left">Contract</th>
            <th style="padding:8px;text-align:left">Entry</th>
            <th style="padding:8px;text-align:left">Exit</th>
            <th style="padding:8px;text-align:left">P&L %</th>
            <th style="padding:8px;text-align:left">Reason</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      {"<p style='color:#f59e0b'><b>⚠️ " + str(len(open_t)) + " trade(s) still OPEN — check your broker!</b></p>" if open_t else ""}

      <p style="color:#94a3b8;font-size:12px;margin-top:24px">
        0DTE options carry extreme risk. This report is for informational purposes only.
      </p>
    </div>
    </body></html>
    """


def send_report():
    trades = load_todays_trades()
    html   = build_report_html(trades)

    sender    = os.environ.get("EMAIL_ADDRESS", "")
    password  = os.environ.get("EMAIL_APP_PASSWORD", "")
    recipient = os.environ.get("RECIPIENT_EMAIL") or sender

    if not sender or not password:
        print("Email credentials not set — cannot send daily report")
        return

    closed = [t for t in trades if t["status"] != "OPEN"]
    pnls   = [float(t.get("pnl_pct") or 0) for t in closed]
    avg    = sum(pnls) / len(pnls) if pnls else 0
    emoji  = "✅" if avg > 0 else ("🛑" if avg < 0 else "➡️")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{emoji} 0DTE Daily Report — {today_str} | {len(trades)} Trades | Avg {avg*100:+.1f}%"
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.sendmail(sender, recipient, msg.as_string())
        print(f"Daily report sent to {recipient}")
    except Exception as e:
        print(f"Failed to send daily report: {e}")

    # Also reset the trades file for tomorrow
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    if open_trades:
        print(f"WARNING: {len(open_trades)} trade(s) still marked OPEN — review manually")

    # Archive today's trades, clear for next day
    if TRADES_FILE.exists():
        archive = TRADES_FILE.parent / f"trades_{datetime.now(ET).strftime('%Y%m%d')}.json"
        TRADES_FILE.rename(archive)
        print(f"Trades archived to {archive}")


if __name__ == "__main__":
    send_report()
