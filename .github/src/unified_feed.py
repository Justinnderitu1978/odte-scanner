import asyncio
import logging
import os
import time
from typing import Optional, Callable
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

STALE_THRESHOLD_SECONDS = 30


class FeedSource:
    SCHWAB   = "SCHWAB"
    ALPACA   = "ALPACA"
    YFINANCE = "YFINANCE"


class UnifiedFeed:
    def __init__(
        self,
        tickers: list,
        on_bar_callback:    Optional[Callable] = None,
        on_option_callback: Optional[Callable] = None,
    ):
        self.tickers            = tickers
        self.on_bar_callback    = on_bar_callback
        self.on_option_callback = on_option_callback
        self._active_source     = FeedSource.YFINANCE
        self._schwab_stream     = None
        self._last_quote_time: dict = {}
        self._running           = False

    def _has_schwab_creds(self) -> bool:
        return bool(
            os.environ.get("SCHWAB_APP_KEY") and
            os.environ.get("SCHWAB_APP_SECRET") and
            os.environ.get("SCHWAB_REFRESH_TOKEN")
        )

    def _has_alpaca_creds(self) -> bool:
        return bool(
            os.environ.get("APCA_API_KEY_ID") and
            os.environ.get("APCA_API_SECRET_KEY")
        )

    async def start(self):
        self._running = True
        tasks = []

        if self._has_schwab_creds():
            logger.info("Schwab credentials found - starting SIP stream")
            tasks.append(asyncio.ensure_future(self._run_schwab()))
        else:
            logger.warning("Schwab credentials not set - skipping SIP stream")

        if self._has_alpaca_creds():
            logger.info("Alpaca credentials found - starting IEX stream")
            tasks.append(asyncio.ensure_future(self._run_alpaca()))
        else:
            logger.warning("Alpaca credentials not set - using yfinance fallback")

        if not tasks:
            logger.warning("No streaming credentials - using yfinance polling")
            self._active_source = FeedSource.YFINANCE
            return

        tasks.append(asyncio.ensure_future(self._health_monitor()))
        await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        self._running = False
        if self._schwab_stream:
            self._schwab_stream.stop()

    async def _run_schwab(self):
        try:
            from src.schwab_stream import SchwabStreamer
            self._schwab_stream = SchwabStreamer(
                equity_tickers     = self.tickers,
                on_bar_callback    = self._on_schwab_bar,
                on_option_callback = self._on_schwab_option,
            )
            self._active_source = FeedSource.SCHWAB
            await self._schwab_stream.start()
        except Exception as e:
            logger.error(f"Schwab stream failed: {e}")
            if self._active_source == FeedSource.SCHWAB:
                logger.warning("Schwab down - promoting Alpaca")
                self._active_source = FeedSource.ALPACA

    async def _run_alpaca(self):
        try:
            from alpaca.data.live import StockDataStream
            from alpaca.data.enums import DataFeed

            stream = StockDataStream(
                os.environ["APCA_API_KEY_ID"],
                os.environ["APCA_API_SECRET_KEY"],
                feed=DataFeed.IEX,
            )
            self._alpaca_stream = stream
            stream.subscribe_bars(self._on_alpaca_bar, *self.tickers)
            stream.subscribe_quotes(self._on_alpaca_quote, *self.tickers)

            if not self._has_schwab_creds():
                self._active_source = FeedSource.ALPACA

            logger.info("Alpaca IEX stream connected")
            await stream._run_forever()

        except ImportError:
            logger.warning("alpaca-py not installed - Alpaca unavailable")
        except Exception as e:
            logger.error(f"Alpaca stream failed: {e}")

    async def _on_schwab_bar(self, ticker: str, bar: dict):
        self._last_quote_time[ticker] = time.monotonic()
        if self._active_source != FeedSource.SCHWAB:
            logger.info("Schwab feed restored")
            self._active_source = FeedSource.SCHWAB
        if self.on_bar_callback:
            await self.on_bar_callback(ticker, bar, FeedSource.SCHWAB)

    async def _on_schwab_option(self, symbol: str, quote):
        if self.on_option_callback:
            await self.on_option_callback(symbol, quote, FeedSource.SCHWAB)

    async def _on_alpaca_bar(self, bar):
        ticker = bar.symbol
        self._last_quote_time[ticker] = time.monotonic()
        if self._active_source == FeedSource.YFINANCE:
            logger.info("Alpaca feed active")
            self._active_source = FeedSource.ALPACA
        if self.on_bar_callback:
            bar_dict = {
                "timestamp": bar.timestamp,
                "Open":   float(bar.open),
                "High":   float(bar.high),
                "Low":    float(bar.low),
                "Close":  float(bar.close),
                "Volume": float(bar.volume),
            }
            await self.on_bar_callback(ticker, bar_dict, FeedSource.ALPACA)

    async def _on_alpaca_quote(self, quote):
        from src.schwab_stream import equity_bars
        ticker = quote.symbol
        self._last_quote_time[ticker] = time.monotonic()
        if ticker in equity_bars and equity_bars[ticker]:
            mid  = (float(quote.bid_price) + float(quote.ask_price)) / 2
            last = dict(equity_bars[ticker][-1])
            last["Close"] = mid
            last["High"]  = max(last["High"], mid)
            last["Low"]   = min(last["Low"],  mid)
            equity_bars[ticker][-1] = last

    async def _health_monitor(self):
        await asyncio.sleep(60)
        while self._running:
            await asyncio.sleep(10)
            if self._active_source != FeedSource.SCHWAB:
                continue
            stale = []
            for ticker in self.tickers:
                last = self._last_quote_time.get(ticker, 0)
                if time.monotonic() - last > STALE_THRESHOLD_SECONDS:
                    stale.append(ticker)
            if stale:
                logger.warning(f"Schwab stale for {stale} - promoting Alpaca")
                self._active_source = FeedSource.ALPACA

    @property
    def active_source(self) -> str:
        return self._active_source

    def get_price(self, ticker: str) -> Optional[float]:
        from src.schwab_stream import get_latest_price, equity_bars

        if self._active_source == FeedSource.SCHWAB:
            return get_latest_price(ticker)

        if self._active_source == FeedSource.ALPACA:
            bars = equity_bars.get(ticker)
            if bars:
                return float(bars[-1]["Close"])
            return None

        return self._yfinance_price(ticker)

    def get_bars(self, ticker: str) -> Optional[pd.DataFrame]:
        from src.schwab_stream import get_bars_dataframe

        if self._active_source in (FeedSource.SCHWAB, FeedSource.ALPACA):
            return get_bars_dataframe(ticker)

        from src.market_data import get_intraday
        return get_intraday(ticker, interval="1m")

    def get_option_price(self, occ_symbol: str) -> Optional[float]:
        from src.schwab_stream import get_option_mid
        mid = get_option_mid(occ_symbol)
        if mid is not None:
            return mid
        return None

    async def subscribe_option(self, occ_symbol: str):
        if self._schwab_stream:
            await self._schwab_stream.subscribe_options([occ_symbol])

    async def unsubscribe_option(self, occ_symbol: str):
        if self._schwab_stream:
            await self._schwab_stream.unsubscribe_options([occ_symbol])

    @staticmethod
    def _yfinance_price(ticker: str) -> Optional[float]:
        try:
            import yfinance as yf
            t    = yf.Ticker(ticker)
            hist = t.history(period="1d", interval="1m")
            if hist.empty:
                return None
            return float(hist["Close"].iloc[-1])
        except Exception:
            return None


_feed: Optional[UnifiedFeed] = None


def get_feed() -> Optional[UnifiedFeed]:
    return _feed


def init_feed(tickers: list, **kwargs) -> UnifiedFeed:
    global _feed
    _feed = UnifiedFeed(tickers, **kwargs)
    return _feed
