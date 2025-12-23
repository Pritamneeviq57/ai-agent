"""
Graph API Client using App-Only Authentication (No User Login).

Uses client credentials flow (app secret).
Requires Application Access Policy to be set up.
Can access any user's meeting transcripts (configured in policy).

File: src/api/graph_client_apponly.py
"""
import requests
from datetime import datetime, timedelta
from src.utils.logger import setup_logger
from config.settings import Settings

logger = setup_logger(__name__)


class GraphAPIClientAppOnly:
    """
    Graph API Client using App-Only (application) authentication.
    No user interaction needed - app authenticates as itself.
    Requires Application Access Policy for accessing user data.
    """

    def __init__(self):
        self.client_id = Settings.CLIENT_ID
        self.client_secret = Settings.CLIENT_SECRET
        self.tenant_id = Settings.TENANT_ID
        self.base_url = Settings.GRAPH_API_BASE_URL
        self.access_token = None
        self.token_expires_at = None
        logger.info("GraphAPIClientAppOnly initialized (app-only auth)")

    def authenticate(self):
        """
        Authenticate using client credentials flow (app secret).
        No user login needed - app authenticates as itself.
        """
        logger.info("Starting app-only authentication (client credentials)...")
        
        try:
            token_resp = requests.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://graph.microsoft.com/.default"
                },
                timeout=10
            )

            if token_resp.status_code != 200:
                error = token_resp.json().get("error_description", "Unknown error")
                logger.error(f"✗ Authentication failed: {error}")
                return False

            data = token_resp.json()
            self.access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info(f"✓ App-only authentication successful!")
            logger.info(f"  Token expires at: {self.token_expires_at}")
            return True

        except Exception as e:
            logger.error(f"✗ Authentication error: {str(e)}")
            return False

    def is_token_valid(self):
        """Check if current token is still valid"""
        if not self.access_token or not self.token_expires_at:
            return False
        # Refresh if within 5 minutes of expiry
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

    def make_request(self, method, endpoint, params=None, data=None, retry_count=0):
        """
        Make API request with automatic token refresh.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: Graph API endpoint (e.g., /users/user-id/onlineMeetings)
            params: Query parameters
            data: Request body data
            retry_count: Internal retry counter
            
        Returns:
            JSON response or None if failed
        """
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
            status_code = e.response.status_code
            error_text = e.response.text
            logger.error(f"HTTP Error {status_code}: {error_text}")
            
            # Retry on server errors (5xx)
            if status_code >= 500 and retry_count < 2:
                logger.info(f"Retrying request (attempt {retry_count + 1})...")
                return self.make_request(method, endpoint, params, data, retry_count + 1)
            return None

        except Exception as e:
            logger.error(f"Request error: {str(e)}")
            return None

    def download_content(self, endpoint, accept=None):
        """
        Download content from Graph API (e.g., transcript file).
        
        Args:
            endpoint: API endpoint
            accept: Accept header value (e.g., "text/plain")
            
        Returns:
            Raw bytes content or None if failed
        """
        self.refresh_token_if_needed()
        url = f"{self.base_url}{endpoint}"
        
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if accept:
            headers["Accept"] = accept

        try:
            logger.debug(f"Downloading from: {url}")
            response = requests.get(url, headers=headers, timeout=60)
            response.raise_for_status()
            return response.content

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error {e.response.status_code}: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Download error: {str(e)}")
            return None