"""
main.py
=======
0DTE Options Scanner - Entry Point

Runs dual signal systems:
1. Main 4/5 high-confidence system (conservative)
2. Fast entry 3/5 V-bottom reversal system (aggressive)

Executes every 30 seconds via self-hosted runner during market hours.
"""

import logging
import sys
from datetime import datetime, time
import pytz
import yaml
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("main")
ET = pytz.timezone("America/New_York")


def load_config() -> dict:
    """Load settings from config file"""
    config_path = Path("config/settings.yaml")
    if config_path.exists():
        with config_path.open() as f:
            return yaml.safe_load(f)
    return {
        "tickers": ["SPY", "QQQ", "IWM"],
        "strike_offset": 0,
    }


def is_market_open() -> bool:
    """Check if market is currently open"""
    now = datetime.now(ET)
    
    # Weekend check
    if now.weekday() >= 5:
        return False
    
    # Market hours: 9:30 AM - 4:00 PM ET
    market_open  = time(9, 30)
    market_close = time(16, 0)
    
    return market_open <= now.time() <= market_close


def scan_ticker(ticker: str, vix: float, config: dict):
    """
    Run both signal systems on a single ticker.
    
    Flow:
    1. Fetch market data
    2. Run main 4/5 signal engine
    3. If no 4/5 signal, check for fast entry 3/5 signal
    4. Process whichever signal fired
    """
    from src.market_data       import get_intraday
    from src.signal_engine     import run_scanner, _calc_opening_range, _calc_vwap
    from src.fast_entry_signal import evaluate_fast_entry
    from src.options_analyzer  import enrich_signal
    from src.alert_system      import dispatch_alerts
    from src.trade_manager     import open_trade
    
    logger.info(f"Scanning {ticker}...")
    
    # Fetch market data
    df = get_intraday(ticker, interval="1m")
    if df is None or df.empty:
        logger.warning(f"[{ticker}] No market data available")
        return
    
    # Run main 4/5 signal engine
    main_signal = run_scanner(ticker, df, vix)
    
    if main_signal and main_signal.score >= 4:
        # Main system fired
        logger.info(f"\n{main_signal}")
        
        signal = enrich_signal(main_signal, offset_strikes=config.get("strike_offset", 0))
        dispatch_alerts(signal)
        open_trade(signal)
        
        logger.info(f"[{ticker}] Main 4/5 signal processed")
        return
    
    # Main system didn't fire - check fast entry 3/5
    or_high, or_low = _calc_opening_range(df)
    vwap = _calc_vwap(df)
    
    main_score = main_signal.score if main_signal else 0
    
    fast_signal = evaluate_fast_entry(
        ticker     = ticker,
        df         = df,
        vix        = vix,
        main_score = main_score,
        or_high    = or_high,
        or_low     = or_low,
        vwap       = vwap,
    )
    
    if fast_signal:
        # Fast entry fired
        logger.info(f"\n{fast_signal}")
        
        signal = enrich_signal(fast_signal, offset_strikes=config.get("strike_offset", 0))
        _dispatch_fast_entry_alert(signal)
        open_trade(signal)
        
        logger.info(f"[{ticker}] Fast entry 3/5 signal processed")
        return
    
    # Neither system fired
    logger.info(f"[{ticker}] No signal this cycle")


def _dispatch_fast_entry_alert(signal):
    """Send fast entry alert with modified format"""
    import os
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    
    sender    = os.environ.get("EMAIL_ADDRESS", "")
    password  = os.environ.get("EMAIL_APP_PASSWORD", "")
    recipient = os.environ.get("RECIPIENT_EMAIL") or sender
    phone     = os.environ.get("RECIPIENT_PHONE", "")
    carrier   = os.environ.get("CARRIER", "").lower()
    
    if not sender or not password:
        logger.warning("Email credentials not set")
        return
    
    subject = (
        f"⚡ FAST ENTRY {signal.direction} {signal.ticker} "
        f"@ ${signal.spot_price:.2f} Score 3/5 V-BOTTOM"
    )
    
    color = "#f59e0b"
    
    contract_html = ""
    if signal.premium:
        target = signal.premium * (1 + signal.target_pct)
        stop   = signal.premium * (1 - signal.stop_pct)
        contract_html = f"""
        <tr><td><b>Contract</b></td><td>{signal.contract}</td></tr>
        <tr><td><b>Premium</b></td><td>${signal.premium:.2f}</td></tr>
        <tr><td><b>Target +{signal.target_pct*100:.0f}%</b></td>
            <td style="color:green"><b>${target:.2f}</b></td></tr>
        <tr><td><b>Stop -{signal.stop_pct*100:.0f}%</b></td>
            <td style="color:red"><b>${stop:.2f}</b></td></tr>
        """
    
    reasons = "<br>".join(f"- {r}" for r in signal.reasons)
    
    html = f"""
    <html><body style="font-family:Arial,sans-serif">
    <div style="background:{color};color:white;padding:16px;border-radius:8px 8px 0 0">
      <h2 style="margin:0">⚡ FAST ENTRY — V-BOTTOM REVERSAL</h2>
      <h3 style="margin:4px 0">{signal.ticker} {signal.direction}</h3>
      <p style="margin:4px 0">{signal.timestamp.strftime('%H:%M:%S ET')}</p>
    </div>
    <div style="border:1px solid #e5e7eb;padding:16px;border-radius:0 0 8px 8px">
      <div style="background:#fef3c7;padding:12px;border-radius:6px;margin-bottom:16px">
        <b>⚠️ AGGRESSIVE EARLY ENTRY</b><br>
        Entering at 3/5 score before full confirmation.<br>
        Higher risk — Tighter stop & faster exit.
      </div>
      <table>
        <tr><td><b>Spot Price</b></td><td>${signal.spot_price:.2f}</td></tr>
        <tr><td><b>Score</b></td><td>3/5 (Fast Entry)</td></tr>
        <tr><td><b>RSI</b></td><td>{signal.rsi:.1f}</td></tr>
        <tr><td><b>VIX</b></td><td>{signal.vix:.1f}</td></tr>
        {contract_html}
      </table>
      <h3>Signal Reasons</h3>
      <p style="background:#f9fafb;padding:10px;border-radius:6px">{reasons}</p>
    </div>
    </body></html>
    """
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html"))
    
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.sendmail(sender, recipient, msg.as_string())
        logger.info(f"Fast entry email sent")
    except Exception as e:
        logger.error(f"Fast entry email failed: {e}")
    
    if phone and carrier:
        from src.alert_system import CARRIER_GATEWAYS
        domain = CARRIER_GATEWAYS.get(carrier)
        if domain:
            digits  = "".join(filter(str.isdigit, phone))[-10:]
            gateway = f"{digits}@{domain}"
            
            prem_str = f" ${signal.premium:.2f}" if signal.premium else ""
            sms_body = (
                f"⚡ FAST ENTRY {signal.ticker} {signal.direction} "
                f"${signal.spot_price:.2f}{prem_str} "
                f"Score:3/5 V-Bottom "
                f"Tgt:+{signal.target_pct*100:.0f}% Stp:-{signal.stop_pct*100:.0f}%"
            )[:320]
            
            sms_msg = MIMEText(sms_body)
            sms_msg["From"] = sender
            sms_msg["To"]   = gateway
            
            try:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                    s.login(sender, password)
                    s.sendmail(sender, gateway, sms_msg.as_string())
                logger.info(f"Fast entry SMS sent")
            except Exception as e:
                logger.error(f"Fast entry SMS failed: {e}")


def main():
    """Main scanner entry point"""
    from src.market_data   import get_vix
    from src.trade_manager import check_exits, print_trade_summary
    
    now = datetime.now(ET)
    logger.info("="*55)
    logger.info(f"0DTE Scanner starting — {now.strftime('%Y-%m-%d %H:%M ET')}")
    logger.info("="*55)
    
    if not is_market_open():
        logger.info("Market is closed — nothing to do")
        return
    
    config = load_config()
    ticker_list = config.get("tickers", ["SPY", "QQQ", "IWM"])
    
    logger.info(f"Tickers configured: {ticker_list}")
    
    vix = get_vix()
    logger.info(f"VIX: {vix:.1f}")
    
    check_exits()
    
    for ticker in ticker_list:
        try:
            scan_ticker(ticker, vix, config)
        except Exception as e:
            logger.error(f"[{ticker}] Scan error: {e}", exc_info=True)
    
    print_trade_summary()
    
    logger.info("="*55)
    logger.info("Scan cycle complete")
    logger.info("="*55)


if __name__ == "__main__":
    main()
