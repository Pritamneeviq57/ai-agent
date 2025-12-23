"""
Graph API Client using Delegated Authentication (Interactive User Login).

This allows using /me endpoints and doesn't require Application Access Policy.
Uses MSAL for device code flow authentication with TOKEN CACHING.
"""
import msal
import requests
import os
import json
from datetime import datetime, timedelta
from src.utils.logger import setup_logger
from config.settings import Settings

logger = setup_logger(__name__)

# Scopes needed for transcript access and email sending
SCOPES = [
    "User.Read",
    "Calendars.Read",
    "OnlineMeetings.Read",
    "OnlineMeetingTranscript.Read.All",
    "Mail.Send",  # Required for sending emails
]


class GraphAPIClientDelegated:
    """
    Graph API Client using Delegated (user) authentication.
    Uses device code flow - user logs in via browser.
    Caches token for automatic authentication on subsequent runs.
    """

    def __init__(self):
        self.client_id = Settings.CLIENT_ID
        self.tenant_id = Settings.TENANT_ID
        self.base_url = Settings.GRAPH_API_BASE_URL
        self.access_token = None
        self.token_expires_at = None
        
        # Setup token cache
        self.cache_dir = Settings.CACHE_DIR
        self.cache_file = Settings.CACHE_FILE
        
        # Create cache directory if it doesn't exist
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Setup MSAL cache
        self.cache = msal.SerializableTokenCache()
        
        # Load existing cache if it exists
        if os.path.exists(self.cache_file):
            logger.debug(f"Loading token cache from {self.cache_file}")
            try:
                with open(self.cache_file, "r") as f:
                    self.cache.deserialize(f.read())
                logger.debug("✓ Token cache loaded successfully")
            except Exception as e:
                logger.debug(f"Could not load cache: {str(e)}")
                self.cache = msal.SerializableTokenCache()
        
        # MSAL public client app with cache
        self.app = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=self.cache
        )
        logger.info("GraphAPIClientDelegated initialized (delegated auth with caching)")

    def _save_cache(self):
        """Save token cache to file"""
        try:
            if self.cache.has_state_changed:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(self.cache_file, "w") as f:
                    f.write(self.cache.serialize())
                logger.debug(f"✓ Token cache saved to {self.cache_file}")
        except Exception as e:
            logger.error(f"Error saving cache: {str(e)}")

    def authenticate(self):
        """
        Authenticate using device code flow.
        Uses cached token if available, otherwise prompts for login.
        """
        logger.info("Starting delegated authentication (device code flow)...")
        
        # Try to get token from cache first
        logger.debug("Checking for cached token...")
        accounts = self.app.get_accounts()
        
        if accounts:
            logger.debug(f"Found {len(accounts)} account(s) in cache")
            result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
            
            if result and "access_token" in result:
                self.access_token = result["access_token"]
                self.token_expires_at = datetime.now() + timedelta(seconds=result.get("expires_in", 3600))
                logger.info("✓ Token acquired from cache (no login needed)")
                return True
            else:
                logger.debug("Could not get silent token, will need to login")
        else:
            logger.debug("No cached accounts found")

        # Initiate device code flow
        logger.info("Initiating device code flow...")
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        
        if "user_code" not in flow:
            logger.error(f"✗ Failed to create device flow: {flow.get('error_description', 'Unknown error')}")
            return False

        # Display instructions to user
        print("\n" + "=" * 60)
        print("🔐 AUTHENTICATION REQUIRED")
        print("=" * 60)
        print(f"\n{flow['message']}\n")
        print("=" * 60 + "\n")

        # Wait for user to complete authentication
        result = self.app.acquire_token_by_device_flow(flow)
        
        if "access_token" in result:
            self.access_token = result["access_token"]
            self.token_expires_at = datetime.now() + timedelta(seconds=result.get("expires_in", 3600))
            
            # Save the token cache
            self._save_cache()
            
            logger.info(f"✓ Authentication successful! Token expires at: {self.token_expires_at}")
            logger.info(f"✓ Token cached to: {self.cache_file}")
            return True
        else:
            logger.error(f"✗ Authentication failed: {result.get('error_description', 'Unknown error')}")
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
            logger.info("Token expired. Re-authenticating...")
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