"""
Script to check the latest MFA session and retrieve the access token.
Reads from the cached token file used by GraphAPIClientDelegated.
"""
import msal
import os
import json
from datetime import datetime, timedelta
from config.settings import Settings

def check_mfa_token():
    """Check latest MFA session and display token"""
    cache_file = Settings.CACHE_FILE
    cache_dir = Settings.CACHE_DIR
    
    print("=" * 60)
    print("🔐 CHECKING LATEST MFA SESSION TOKEN")
    print("=" * 60)
    print(f"\nCache location: {cache_file}\n")
    
    # Check if cache file exists
    if not os.path.exists(cache_file):
        print("❌ No token cache found!")
        print(f"   Cache file does not exist: {cache_file}")
        print("\n💡 To create a new session, run:")
        print("   python -c 'from src.api.graph_client_delegated import GraphAPIClientDelegated; client = GraphAPIClientDelegated(); client.authenticate()'")
        return None
    
    # Load cache
    cache = msal.SerializableTokenCache()
    try:
        with open(cache_file, "r") as f:
            cache.deserialize(f.read())
        print("✓ Token cache loaded successfully\n")
    except Exception as e:
        print(f"❌ Error loading cache: {e}")
        return None
    
    # Get accounts from cache
    app = msal.PublicClientApplication(
        Settings.CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{Settings.TENANT_ID}",
        token_cache=cache
    )
    
    accounts = app.get_accounts()
    
    if not accounts:
        print("❌ No accounts found in cache!")
        print("   You need to authenticate first.")
        return None
    
    print(f"✓ Found {len(accounts)} account(s) in cache")
    for i, account in enumerate(accounts, 1):
        print(f"   {i}. {account.get('username', 'Unknown')}")
    
    # Get token silently (from cache)
    SCOPES = [
        "User.Read",
        "Calendars.Read",
        "OnlineMeetings.Read",
        "OnlineMeetingTranscript.Read.All",
        "Mail.Send",
    ]
    
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    
    if result and "access_token" in result:
        access_token = result["access_token"]
        expires_in = result.get("expires_in", 3600)
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        print("\n" + "=" * 60)
        print("✅ TOKEN FOUND!")
        print("=" * 60)
        print(f"\n📧 Account: {accounts[0].get('username', 'Unknown')}")
        print(f"⏰ Expires in: {expires_in} seconds ({expires_in // 60} minutes)")
        print(f"📅 Expires at: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Check if token is still valid
        if datetime.now() < expires_at - timedelta(minutes=5):
            print("✅ Token is VALID (not expired)")
        else:
            print("⚠️  Token is EXPIRED or will expire soon")
        
        print("\n" + "-" * 60)
        print("🔑 ACCESS TOKEN:")
        print("-" * 60)
        print(access_token)
        print("-" * 60)
        
        # Try to get refresh token from cache
        refresh_token = None
        try:
            # Read the cache file and look for refresh token
            with open(cache_file, "r") as f:
                cache_data = json.load(f)
                # MSAL stores refresh tokens in the cache
                # Look for refresh tokens in the cache structure
                if "RefreshToken" in cache_data:
                    refresh_tokens = cache_data["RefreshToken"]
                    if refresh_tokens:
                        # Get the first refresh token (usually there's only one)
                        refresh_token = list(refresh_tokens.values())[0].get("secret")
        except Exception as e:
            logger.debug(f"Could not extract refresh token from cache: {e}")
        
        # Also check if refresh token is in the result
        if not refresh_token and "refresh_token" in result:
            refresh_token = result["refresh_token"]
        
        if refresh_token:
            print("\n" + "=" * 60)
            print("🔄 REFRESH TOKEN (for Railway):")
            print("=" * 60)
            print(refresh_token)
            print("=" * 60)
            print("\n⚠️  IMPORTANT: This is the REFRESH TOKEN, not the access token!")
            print("   Use this in Railway's REFRESH_TOKEN environment variable.")
        else:
            print("\n⚠️  No refresh token found in cache.")
            print("   You may need to re-authenticate to get a refresh token.")
            print("   Run: python get_refresh_token.py")
        
        # Show token details (first/last 20 chars for security)
        token_preview = f"{access_token[:20]}...{access_token[-20:]}"
        print(f"\n📋 Access token preview: {token_preview}")
        print(f"📏 Access token length: {len(access_token)} characters")
        
        return {"access_token": access_token, "refresh_token": refresh_token}
    else:
        error = result.get("error_description", "Unknown error") if result else "No token in cache"
        print(f"\n❌ Could not retrieve token: {error}")
        print("\n💡 You may need to re-authenticate. Run:")
        print("   python -c 'from src.api.graph_client_delegated import GraphAPIClientDelegated; client = GraphAPIClientDelegated(); client.authenticate()'")
        return None

if __name__ == "__main__":
    check_mfa_token()

