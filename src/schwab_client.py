"""
Schwab API Client
Handles authentication and data fetching from Schwab API
"""

import os
import requests
import base64
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class SchwabClient:
    """Client for Schwab Market Data API"""
    
    def __init__(self):
        self.app_key = os.environ.get("SCHWAB_APP_KEY", "")
        self.app_secret = os.environ.get("SCHWAB_APP_SECRET", "")
        self.refresh_token = os.environ.get("SCHWAB_REFRESH_TOKEN", "")
        
        self.access_token = None
        self.token_expires = None
        
        if not all([self.app_key, self.app_secret, self.refresh_token]):
            logger.warning("Schwab credentials not configured - falling back to yfinance")
            self.enabled = False
        else:
            self.enabled = True
            self._refresh_access_token()
    
    def _refresh_access_token(self):
        """Get a new access token using refresh token"""
        auth_string = f"{self.app_key}:{self.app_secret}"
        auth_b64 = base64.b64encode(auth_string.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token
        }
        
        try:
            response = requests.post(
                "https://api.schwabapi.com/v1/oauth/token",
                headers=headers,
                data=data,
                timeout=10
            )
            
            if response.status_code == 200:
                tokens = response.json()
                self.access_token = tokens["access_token"]
                # Token expires in ~30 minutes, refresh after 25
                self.token_expires = datetime.now() + timedelta(minutes=25)
                logger.info("Schwab access token refreshed")
            else:
                logger.error(f"Failed to refresh token: {response.status_code}")
                self.enabled = False
                
        except Exception as e:
            logger.error(f"Error refreshing Schwab token: {e}")
            self.enabled = False
    
    def _ensure_token_valid(self):
        """Check if token needs refresh"""
        if not self.enabled:
            return False
            
        if not self.access_token or datetime.now() >= self.token_expires:
            self._refresh_access_token()
        
        return self.enabled
    
    def get_quote(self, symbol):
        """Get real-time quote for a symbol"""
        if not self._ensure_token_valid():
            return None
        
        headers = {
            "Authorization": f"Bearer {self.access_token}"
        }
        
        try:
            response = requests.get(
                f"https://api.schwabapi.com/marketdata/v1/quotes/{symbol}",
                headers=headers,
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Quote fetch failed for {symbol}: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching quote for {symbol}: {e}")
            return None
    
    def get_option_chain(self, symbol, strike=None, contract_type=None):
        """Get option chain for a symbol"""
        if not self._ensure_token_valid():
            return None
        
        headers = {
            "Authorization": f"Bearer {self.access_token}"
        }
        
        params = {
            "symbol": symbol,
            "contractType": contract_type or "ALL",
            "includeUnderlyingQuote": "true"
        }
        
        if strike:
            params["strike"] = strike
        
        try:
            response = requests.get(
                "https://api.schwabapi.com/marketdata/v1/chains",
                headers=headers,
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Option chain fetch failed: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching option chain: {e}")
            return None


# Global client instance
_schwab_client = None

def get_schwab_client():
    """Get or create Schwab client singleton"""
    global _schwab_client
    if _schwab_client is None:
        _schwab_client = SchwabClient()
    return _schwab_client
