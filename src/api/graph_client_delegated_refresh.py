"""
Graph API Client using Delegated Authentication with Refresh Token (for Railway).
Uses refresh token stored in environment variable - no user interaction needed.
"""
import msal
import requests
import os
import json
from datetime import datetime, timedelta
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Scopes needed for transcript access and email sending
SCOPES = [
    "User.Read",
    "Calendars.Read",
    "OnlineMeetings.Read",
    "OnlineMeetingTranscript.Read.All",
    "Mail.Send",
]


class GraphAPIClientDelegatedRefresh:
    """
    Graph API Client using Delegated auth with refresh token.
    Perfect for Railway - no user interaction needed.
    Refresh token is stored in environment variable.
    """

    def __init__(self):
        self.client_id = os.getenv("AZURE_CLIENT_ID") or os.getenv("CLIENT_ID")
        self.tenant_id = os.getenv("AZURE_TENANT_ID") or os.getenv("TENANT_ID")
        self.refresh_token = os.getenv("REFRESH_TOKEN")
        self.base_url = "https://graph.microsoft.com/v1.0"
        self.access_token = None
        self.token_expires_at = None
        
        if not self.client_id or not self.tenant_id:
            logger.error("Missing CLIENT_ID or TENANT_ID")
        if not self.refresh_token:
            logger.warning("REFRESH_TOKEN not set - authentication will fail")
        
        logger.info("GraphAPIClientDelegatedRefresh initialized (refresh token auth)")

    def authenticate(self):
        """
        Authenticate using refresh token.
        Gets new access token from stored refresh token.
        """
        if not self.refresh_token:
            logger.error("✗ No REFRESH_TOKEN configured")
            return False
        
        logger.info("Starting delegated authentication (refresh token flow)...")
        
        try:
            # Use MSAL to exchange refresh token for access token
            app = msal.PublicClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}"
            )
            
            # Acquire token using refresh token
            # Note: MSAL doesn't directly support refresh token flow in public client
            # So we'll use direct OAuth2 token endpoint
            token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            
            token_data = {
                "client_id": self.client_id,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(SCOPES)
            }
            
            response = requests.post(token_url, data=token_data, timeout=30)
            
            if response.status_code == 200:
                token_response = response.json()
                self.access_token = token_response.get("access_token")
                expires_in = token_response.get("expires_in", 3600)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                # Update refresh token if a new one is provided
                if "refresh_token" in token_response:
                    logger.info("✓ New refresh token received (update REFRESH_TOKEN env var)")
                    logger.warning("⚠️  Update REFRESH_TOKEN in Railway with new token")
                
                logger.info(f"✓ Authentication successful! Token expires at: {self.token_expires_at}")
                return True
            else:
                logger.error(f"✗ Token refresh failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"✗ Authentication error: {str(e)}")
            return False

    def is_token_valid(self):
        """Check if current token is still valid"""
        if not self.access_token or not self.token_expires_at:
            return False
        if datetime.now() >= (self.token_expires_at - timedelta(minutes=5)):
            return False
        return True

    def refresh_token_if_needed(self):
        """Automatically refresh token if expired"""
        if not self.is_token_valid():
            logger.info("Token expired. Refreshing...")
            self.authenticate()

    def get_headers(self):
        """Return headers for API requests"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def make_request(self, method, endpoint, params=None, data=None):
        """Make API request with automatic token refresh"""
        self.refresh_token_if_needed()
        url = f"{self.base_url}{endpoint}"

        try:
            logger.debug(f"Making {method} request to: {url}")
            response = requests.request(
                method=method,
                url=url,
                headers=self.get_headers(),
                params=params,
                json=data,
                timeout=30
            )
            response.raise_for_status()

            if response.status_code == 204:
                return None
            return response.json()

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error {e.response.status_code}: {e.response.text}")
            return None
            
        except Exception as e:
            logger.error(f"Request error: {str(e)}")
            return None

    def download_content(self, endpoint, accept=None):
        """Download content from Graph API"""
        self.refresh_token_if_needed()
        url = f"{self.base_url}{endpoint}"
        
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if accept:
            headers["Accept"] = accept

        try:
            response = requests.get(url, headers=headers, timeout=60)
            response.raise_for_status()
            return response.content
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error {e.response.status_code}: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Download error: {str(e)}")
            return None

