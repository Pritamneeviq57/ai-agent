"""
Configuration settings for Teams Transcript Fetcher
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings:
    """Application settings"""
    
    # Azure AD Configuration
    TENANT_ID = os.getenv("TENANT_ID")
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    
    # Graph API Configuration
    GRAPH_API_BASE_URL = "https://graph.microsoft.com/v1.0"
    
    # Database Configuration
    DB_NAME = os.getenv("DB_NAME", "teams_transcripts.db")
    DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", DB_NAME)
    
    # Logging Configuration
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "logs", "app.log")
    
    # Optional: For delegated auth (single user caching)
    CACHE_DIR = os.path.expanduser("~/.teams_transcript_cache")
    CACHE_FILE = os.path.join(CACHE_DIR, "token_cache.json")
    
    # Langfuse Configuration (for LLM observability)
    LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    LANGFUSE_ENABLED = os.getenv("LANGFUSE_ENABLED", "true").lower() == "true"
    
    # OPIK Configuration (for LLM observability)
    OPIK_ENABLED = os.getenv("OPIK_ENABLED", "true").lower() == "true"
    OPIK_HOST = os.getenv("OPIK_HOST", "http://localhost:5173")
    OPIK_API_KEY = os.getenv("OPIK_API_KEY", "")
    
    # Email Testing Configuration
    EMAIL_TEST_MODE = os.getenv("EMAIL_TEST_MODE", "true").lower() == "true"  # Default to test mode
    EMAIL_TEST_RECIPIENT = os.getenv("EMAIL_TEST_RECIPIENT", "pritam.jagadale@neeviq.com")
    
    @staticmethod
    def get_email_test_recipients():
        """
        Get list of test email recipients from EMAIL_TEST_RECIPIENT environment variable.
        Supports comma-separated values for multiple recipients.
        Returns a list of email addresses (stripped of whitespace).
        """
        recipients_str = Settings.EMAIL_TEST_RECIPIENT
        if not recipients_str:
            return []
        # Split by comma and strip whitespace from each email
        recipients = [email.strip() for email in recipients_str.split(',') if email.strip()]
        return recipients
    
    @staticmethod
    def validate():
        """Validate that all required settings are configured"""
        required = ["TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"]
        missing = [s for s in required if not getattr(Settings, s)]
        
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")
        
        return True