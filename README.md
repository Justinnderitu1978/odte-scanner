# 📈 0DTE Options Scanner — GitHub Actions Edition

A **zero-cost, cloud-based** options trading signal system for 0DTE (zero days to expiration) plays on SPY, QQQ, and IWM. Runs entirely on **GitHub Actions** (free tier). Sends trade alerts via **free email** and **free carrier SMS**.

---

## 🏗️ Architecture

```
GitHub Actions (free, cloud)
   │
   ├─ Every 10 min during market hours (9:30–4:00 PM ET)
   │    └─ main.py
   │         ├─ market_data.py       ← yfinance (free, no API key)
   │         ├─ signal_engine.py     ← ORB + VWAP + RSI + Volume
   │         ├─ options_analyzer.py  ← Chain fetch + Greeks
   │         ├─ alert_system.py      ← Email + Free SMS
   │         └─ trade_manager.py     ← Track positions + exit signals
   │
   └─ 4:15 PM ET daily
        └─ daily_report.py           ← P&L summary email
```

**Total cost: $0/month** (GitHub free tier + Gmail + carrier SMS gateway)

---

## 🎯 Strategy Overview

### Instruments
| Ticker | Name | 0DTE Days |
|--------|------|-----------|
| SPY | S&P 500 ETF | Mon–Fri |
| QQQ | Nasdaq 100 ETF | Mon–Fri |
| IWM | Russell 2000 ETF | Mon/Wed/Fri |

### Signal Logic — 5-Point Scoring System

Each scan cycle scores the market on 5 dimensions:

| # | Indicator | Bullish (+1) | Bearish (+1) |
|---|-----------|-------------|-------------|
| 1 | **ORB Breakout** | Price > 15-min opening range HIGH | Price < 15-min opening range LOW |
| 2 | **VWAP** | Price > VWAP | Price < VWAP |
| 3 | **RSI(5)** | RSI > 55 | RSI < 45 |
| 4 | **Volume Surge** | 1.5× avg volume AND above VWAP | 1.5× avg volume AND below VWAP |
| 5 | **VIX Filter** | VIX < 25 (both directions get this point) | VIX < 25 |

**Signal fires when score ≥ 4/5** → buy ATM CALL or PUT

### Exit Rules
| Condition | Action |
|-----------|--------|
| +80% gain on premium | ✅ **Take profit** (recommended: set limit order immediately on entry) |
| −50% loss on premium | 🛑 **Stop loss** (set stop immediately on entry) |
| 3:30 PM ET | ⏰ **Time stop** — close regardless of P&L |
| 3:45 PM ET | ⚠️ **Hard close** — market order if still open |

### Timing
- **No signals before 9:50 AM ET** — avoids opening chaos
- **No new entries after 3:00 PM ET** — time decay accelerates sharply
- **30-minute cooldown** between signals for same ticker — avoids whipsaws

---

## 🚀 Setup Guide (15 minutes)

### Step 1: Create Your GitHub Repository

```bash
# Option A: Fork/clone this repo
git clone https://github.com/YOUR_USERNAME/odte-scanner.git
cd odte-scanner

# Option B: New private repo
gh repo create odte-scanner --private
git init && git remote add origin https://github.com/YOUR_USERNAME/odte-scanner.git
```

**Cost note:** Private repos on GitHub Free get **2,000 Actions minutes/month**.
This scanner uses ~858 minutes/month (39 runs/day × 22 days × ~1 min each).

---

### Step 2: Set Up Gmail App Password

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Security → **2-Step Verification** (must be enabled)
3. Search for **"App passwords"** → Create → Select "Mail" → Copy the 16-char password
4. You will use this as `EMAIL_APP_PASSWORD` — **never your real Gmail password**

---

### Step 3: Find Your SMS Gateway Address (Free SMS)

| Carrier | Gateway Format |
|---------|---------------|
| Verizon | `10digitnumber@vtext.com` |
| AT&T | `10digitnumber@txt.att.net` |
| T-Mobile | `10digitnumber@tmomail.net` |
| Sprint | `10digitnumber@messaging.sprintpcs.com` |
| Boost | `10digitnumber@sms.myboostmobile.com` |
| Cricket | `10digitnumber@sms.cricketwireless.com` |

Example: If your number is 415-555-1234 on Verizon → `4155551234@vtext.com`

---

### Step 4: Add GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret Name | Value | Required |
|-------------|-------|----------|
| `EMAIL_ADDRESS` | your.email@gmail.com | ✅ |
| `EMAIL_APP_PASSWORD` | 16-char Gmail App Password | ✅ |
| `RECIPIENT_EMAIL` | Where alerts are emailed | ✅ |
| `RECIPIENT_PHONE` | 10-digit phone number | For SMS |
| `CARRIER` | verizon, att, tmobile, etc. | For SMS |
| `TWILIO_ACCOUNT_SID` | From twilio.com/console | Optional |
| `TWILIO_AUTH_TOKEN` | From twilio.com/console | Optional |
| `TWILIO_FROM_NUMBER` | +1XXXXXXXXXX | Optional |
| `TWILIO_TO_NUMBER` | +1XXXXXXXXXX | Optional |

---

### Step 5: Push to GitHub

```bash
git add .
git commit -m "Initial 0DTE scanner setup"
git push origin main
```

The workflows will automatically start running on the next scheduled time during market hours.

---

### Step 6: Test Manually

1. Go to **Actions** tab in your GitHub repo
2. Click **"0DTE Options Scanner"**
3. Click **"Run workflow"**
4. Set `force_signal` to `true` for a test alert (sends email/SMS with dummy data)
5. Click **"Run workflow"**

---

## ⚙️ Configuration

Edit `config/settings.yaml` to customize:

```yaml
tickers: [SPY, QQQ]    # Tickers to scan
strike_offset: 0        # 0=ATM, 1=1-strike OTM
score_threshold: 4      # Signal sensitivity (3=more signals, 4=conservative)
vix_max: 25.0           # Skip when VIX > this
profit_target_pct: 0.80 # Close at +80% gain
stop_loss_pct: 0.50     # Close at -50% loss
```

---

## 📊 Monitoring

### View Logs
Go to **Actions** → click any workflow run → click **"scan"** job to see real-time logs:
```
2024-01-15 10:23:45 [INFO] main — 0DTE Scanner starting — 2024-01-15 10:23 ET
2024-01-15 10:23:46 [INFO] main — VIX: 14.2
2024-01-15 10:23:47 [INFO] main — Scanning SPY...
2024-01-15 10:23:49 [INFO] signal_engine — [SPY] CALL signal fired! score=4/5
2024-01-15 10:23:51 [INFO] alert_system — Email alert sent to you@gmail.com
2024-01-15 10:23:52 [INFO] alert_system — SMS sent via gateway to 4155551234@vtext.com
```

### Download Trade Logs
Every run uploads logs as **artifacts** — go to Actions → any run → **Artifacts** section at the bottom.

---

## 📱 Sample Alerts

**Email alert (HTML):**
```
🟢 0DTE CALL Signal — SPY
Monday January 15, 2024 10:23:45 ET

Ticker:    SPY          Score:     4/5
Direction: CALL         OR Range:  $474.50 – $476.20
Spot:      $477.85      VWAP:      $476.10
RSI(5):    61.2         VIX:       14.2

Contract:  SPY_240115C478
Strike:    $478
Premium:   $1.45 ($145/contract)
IV:        42%
Target +80%: → $2.61
Stop  −50%:  → $0.73

Reasons:
• ORB↑ $477.85>$476.20
• VWAP↑ $477.85>$476.10
• RSI=61.2>55
• VolSurge 2.1x
• VIX=14.2<25.0
```

**SMS alert (free, ~160 chars):**
```
0DTE CALL SPY $477.85 Score:4/5 Prem:$1.45 Tgt:+80% Stp:-50% Exit by 3:30PM ET
```

---

## ⚠️ Risk Disclaimer

0DTE options are **extremely high-risk**:
- Options can expire **worthless within hours**
- Losses can reach **100% of premium paid** very quickly  
- This system generates signals — it **does not execute trades**
- You must manually execute at your broker
- **Always set your stop loss and take profit immediately upon entry**
- This is not financial advice — trade at your own risk
- Past signal performance does not guarantee future results

### Recommended Position Sizing
- Risk no more than **1–2% of your account** per trade
- For $10,000 account → max $100–$200 per trade (1–2 contracts at typical premiums)
- Never go all-in on a single 0DTE position

---

## 🔧 Advanced Options

### Increase Scan Frequency (Self-Hosted Runner)
GitHub-hosted runners have ~1–5 min cron delays. For faster signals:
1. Set up a free Oracle Cloud or AWS Free Tier VM
2. Install [GitHub Actions self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners)
3. Run every 5 minutes: `*/5 13-21 * * 1-5`

### Add Twilio for Reliable SMS (~$0.008/msg)
1. Sign up at [twilio.com](https://twilio.com) (free trial: $15 credit)
2. Add the 4 Twilio secrets (see Step 4)
3. Uncomment `twilio` in `requirements.txt`

### Run on DST Boundaries
The cron covers `13:00–21:00 UTC` to handle both EDT (UTC-4) and EST (UTC-5).
No changes needed when clocks change.

---

## 📁 File Structure

```
odte-scanner/
├── .github/
│   └── workflows/
│       ├── market_scanner.yml    ← Runs every 10 min during market hours
│       └── daily_report.yml      ← 4:15 PM daily P&L email
├── src/
│   ├── market_data.py           ← yfinance data fetching
│   ├── signal_engine.py         ← ORB + VWAP + RSI + Volume logic
│   ├── options_analyzer.py      ← Options chain + Greeks
│   ├── trade_manager.py         ← Position tracking + exits
│   └── alert_system.py          ← Email + SMS alerts
├── config/
│   └── settings.yaml            ← Your configuration
├── logs/
│   └── .gitkeep
├── main.py                      ← Main orchestrator
├── daily_report.py              ← Daily P&L report
├── requirements.txt
└── README.md
```

---

*Built for GitHub Actions free tier. Zero API keys required. Zero monthly cost.*
