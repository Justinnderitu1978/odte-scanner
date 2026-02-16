import asyncio
import base64
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional
import pytz
import requests
import websockets

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

TOKEN_URL  = "https://api.schwabapi.com/v1/oauth/token"
PREFS_URL  = "https://api.schwabapi.com/trader/v1/userPreference"
EQ_FIELDS  = "0,1,2,3,8,12,13,15,29,48"
OPT_FIELDS = "0,2,3,4,8,9,10,20,21,23,24,29,32,33,34,35,39,41"
CHART_FIELDS = "0,1,2,3,4,5,7"
MAX_BARS   = 390

equity_quotes: dict = {}
option_quotes: dict = {}
equity_bars:   dict = {}


@dataclass
class EquityQuote:
    symbol:     str
    bid:        float = 0.0
    ask:        float = 0.0
    last:       float = 0.0
    mark:       float = 0.0
    volume:     float = 0.0
    high:       float = 0.0
    low:        float = 0.0
    open:       float = 0.0
    close:      float = 0.0
    updated_at: float = field(default_factory=time.monotonic)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.ask > 0 else self.last


@dataclass
class OptionQuote:
    symbol:           str
    bid:              float = 0.0
    ask:              float = 0.0
    last:             float = 0.0
    mark:             float = 0.0
    iv:               float = 0.0
    delta:            float = 0.0
    gamma:            float = 0.0
    theta:            float = 0.0
    vega:             float = 0.0
    open_interest:    float = 0.0
    underlying_price: float = 0.0
    updated_at:       float = field(default_factory=time.monotonic)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.ask > 0 else self.last


class TokenManager:
    def __init__(self):
        self._app_key       = os.environ.get("SCHWAB_APP_KEY",      "")
        self._app_secret    = os.environ.get("SCHWAB_APP_SECRET",   "")
        self._refresh_token = os.environ.get("SCHWAB_REFRESH_TOKEN","")
        self._access_token  = ""
        self._expires_at    = 0.0

    def _credentials_header(self) -> str:
        encoded = base64.b64encode(
            f"{self._app_key}:{self._app_secret}".encode()
        ).decode()
        return f"Basic {encoded}"

    def refresh(self) -> str:
        if not self._refresh_token:
            raise ValueError("SCHWAB_REFRESH_TOKEN not set")
        resp = requests.post(
            TOKEN_URL,
            headers={
                "Authorization": self._credentials_header(),
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            data={
                "grant_type":    "refresh_token",
                "refresh_token": self._refresh_token,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Token refresh failed: {resp.status_code}")
        data = resp.json()
        self._access_token  = data["access_token"]
        self._expires_at    = time.monotonic() + data.get("expires_in", 1800) - 60
        if "refresh_token" in data:
            self._refresh_token = data["refresh_token"]
        logger.info("Access token refreshed")
        return self._access_token

    def get_access_token(self) -> str:
        if time.monotonic() >= self._expires_at:
            self.refresh()
        return self._access_token

    def get_streamer_info(self) -> dict:
        token = self.get_access_token()
        resp  = requests.get(
            PREFS_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"User preferences failed: {resp.status_code}")
        prefs    = resp.json()
        streamer = prefs.get("streamerInfo", [{}])[0]
        return {
            "socket_url":                    streamer.get("streamerSocketUrl", ""),
            "schwab_client_customer_id":     streamer.get("schwabClientCustomerId", ""),
            "schwab_client_correl_id":       streamer.get("schwabClientCorrelId", ""),
            "schwab_client_channel":         streamer.get("schwabClientChannel", ""),
            "schwab_client_function_id":     streamer.get("schwabClientFunctionId", ""),
        }


class SchwabStreamer:
    def __init__(
        self,
        equity_tickers:     list,
        on_bar_callback:    Optional[Callable] = None,
        on_option_callback: Optional[Callable] = None,
    ):
        self.equity_tickers     = equity_tickers
        self.on_bar_callback    = on_bar_callback
        self.on_option_callback = on_option_callback
        self._tokens            = TokenManager()
        self._ws                = None
        self._request_id        = 0
        self._streamer_info:    dict = {}
        self._option_subs:      set  = set()
        self._running           = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _build_request(self, service: str, command: str, parameters: dict) -> str:
        return json.dumps({
            "requests": [{
                "service":    service,
                "requestid":  str(self._next_id()),
                "command":    command,
                "SchwabClientCustomerId": self._streamer_info["schwab_client_customer_id"],
                "SchwabClientCorrelId":   self._streamer_info["schwab_client_correl_id"],
                "parameters": parameters,
            }]
        })

    async def _login(self):
        msg = self._build_request(
            "ADMIN", "LOGIN",
            {
                "Authorization":          self._tokens.get_access_token(),
                "SchwabClientChannel":    self._streamer_info["schwab_client_channel"],
                "SchwabClientFunctionId": self._streamer_info["schwab_client_function_id"],
            },
        )
        await self._ws.send(msg)
        response = json.loads(await self._ws.recv())
        code = response.get("response", [{}])[0].get("content", {}).get("code", -1)
        if code != 0:
            raise RuntimeError(f"Schwab login failed: {response}")
        logger.info("Schwab WebSocket login successful")

    async def _subscribe_equities(self):
        symbols = ",".join(self.equity_tickers)
        await self._ws.send(self._build_request(
            "LEVELONE_EQUITIES", "SUBS",
            {"keys": symbols, "fields": EQ_FIELDS},
        ))
        await self._ws.send(self._build_request(
            "CHART_EQUITY", "SUBS",
            {"keys": symbols, "fields": CHART_FIELDS},
        ))
        logger.info(f"Subscribed to equity stream: {self.equity_tickers}")

    async def subscribe_options(self, option_symbols: list):
        if not self._ws or not self._running:
            return
        new_syms = [s for s in option_symbols if s not in self._option_subs]
        if not new_syms:
            return
        command = "ADD" if self._option_subs else "SUBS"
        await self._ws.send(self._build_request(
            "LEVELONE_OPTIONS", command,
            {"keys": ",".join(new_syms), "fields": OPT_FIELDS},
        ))
        self._option_subs.update(new_syms)
        logger.info(f"Subscribed to options: {new_syms}")

    async def unsubscribe_options(self, option_symbols: list):
        if not self._ws or not option_symbols:
            return
        to_remove = [s for s in option_symbols if s in self._option_subs]
        if not to_remove:
            return
        await self._ws.send(self._build_request(
            "LEVELONE_OPTIONS", "UNSUBS",
            {"keys": ",".join(to_remove)},
        ))
        self._option_subs -= set(to_remove)

    def _handle_equity_quote(self, content: list):
        for item in content:
            sym = item.get("key", "")
            if not sym:
                continue
            q = equity_quotes.get(sym) or EquityQuote(symbol=sym)
            q.bid    = float(item.get("1",  q.bid))
            q.ask    = float(item.get("2",  q.ask))
            q.last   = float(item.get("3",  q.last))
            q.volume = float(item.get("8",  q.volume))
            q.high   = float(item.get("12", q.high))
            q.low    = float(item.get("13", q.low))
            q.close  = float(item.get("15", q.close))
            q.open   = float(item.get("29", q.open))
            q.mark   = float(item.get("48", q.mark))
            q.updated_at = time.monotonic()
            equity_quotes[sym] = q

    def _handle_chart_bar(self, content: list):
        for item in content:
            sym = item.get("key", "")
            if not sym:
                continue
            if sym not in equity_bars:
                equity_bars[sym] = deque(maxlen=MAX_BARS)
            bar = {
                "timestamp": datetime.fromtimestamp(
                    item.get("7", 0) / 1000, tz=ET
                ),
                "Open":   float(item.get("1", 0)),
                "High":   float(item.get("2", 0)),
                "Low":    float(item.get("3", 0)),
                "Close":  float(item.get("4", 0)),
                "Volume": float(item.get("5", 0)),
            }
            equity_bars[sym].append(bar)
            if self.on_bar_callback:
                asyncio.ensure_future(self.on_bar_callback(sym, bar))

    def _handle_option_quote(self, content: list):
        for item in content:
            sym = item.get("key", "")
            if not sym:
                continue
            q = option_quotes.get(sym) or OptionQuote(symbol=sym)
            q.bid              = float(item.get("2",  q.bid))
            q.ask              = float(item.get("3",  q.ask))
            q.last             = float(item.get("4",  q.last))
            q.iv               = float(item.get("10", q.iv))
            q.open_interest    = float(item.get("9",  q.open_interest))
            q.delta            = float(item.get("32", q.delta))
            q.gamma            = float(item.get("33", q.gamma))
            q.theta            = float(item.get("34", q.theta))
            q.vega             = float(item.get("35", q.vega))
            q.underlying_price = float(item.get("39", q.underlying_price))
            q.mark             = float(item.get("41", q.mark))
            q.updated_at       = time.monotonic()
            option_quotes[sym] = q
            if self.on_option_callback:
                asyncio.ensure_future(self.on_option_callback(sym, q))

    def _dispatch_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        for block in msg.get("data", []):
            service = block.get("service", "")
            content = block.get("content", [])
            if service == "LEVELONE_EQUITIES":
                self._handle_equity_quote(content)
            elif service == "CHART_EQUITY":
                self._handle_chart_bar(content)
            elif service == "LEVELONE_OPTIONS":
                self._handle_option_quote(content)

    async def _token_refresh_loop(self):
        while self._running:
            await asyncio.sleep(25 * 60)
            try:
                self._tokens.refresh()
            except Exception as e:
                logger.error(f"Token refresh failed: {e}")

    async def start(self):
        self._running       = True
        self._streamer_info = self._tokens.get_streamer_info()
        socket_url          = self._streamer_info["socket_url"]

        if not socket_url:
            raise RuntimeError("No streamer socket URL from Schwab API")

        logger.info(f"Connecting to Schwab stream: {socket_url}")
        asyncio.ensure_future(self._token_refresh_loop())

        backoff = 5
        while self._running:
            try:
                async with websockets.connect(
                    socket_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    backoff  = 5
                    await self._login()
                    await self._subscribe_equities()
                    if self._option_subs:
                        await self.subscribe_options(list(self._option_subs))
                    logger.info("Schwab stream active")
                    async for message in ws:
                        self._dispatch_message(message)
            except Exception as e:
                logger.warning(f"Schwab stream error: {e} - reconnecting in {backoff}s")
            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def stop(self):
        self._running = False


def format_option_symbol(underlying, expiry, option_type, strike) -> str:
    sym        = underlying.ljust(6)
    date       = expiry.strftime("%y%m%d")
    strike_str = f"{int(strike * 1000):08d}"
    return f"{sym}{date}{option_type}{strike_str}"


def get_latest_price(ticker: str) -> Optional[float]:
    q = equity_quotes.get(ticker)
    if q is None:
        return None
    if time.monotonic() - q.updated_at > 5:
        return None
    return q.mid or q.last


def get_bars_dataframe(ticker: str):
    import pandas as pd
    bars = equity_bars.get(ticker)
    if not bars:
        return None
    df = pd.DataFrame(list(bars))
    if df.empty:
        return None
    df = df.set_index("timestamp")
    return df[["Open", "High", "Low", "Close", "Volume"]]


def get_option_mid(occ_symbol: str) -> Optional[float]:
    q = option_quotes.get(occ_symbol)
    if q is None:
        return None
    if time.monotonic() - q.updated_at > 10:
        return None
    return q.mid
