"""
Helper script to get a refresh token for delegated authentication.
Run this ONCE locally to get a refresh token, then add it to Railway.
"""
import msal
import os
from dotenv import load_dotenv

load_dotenv()

# Azure AD Configuration
CLIENT_ID = os.getenv("CLIENT_ID") or os.getenv("AZURE_CLIENT_ID")
TENANT_ID = os.getenv("TENANT_ID") or os.getenv("AZURE_TENANT_ID")

# Scopes needed
SCOPES = [
    "User.Read",
    "Calendars.Read",
    "OnlineMeetings.Read",
    "OnlineMeetingTranscript.Read.All",
    "Mail.Send",
    "offline_access"  # Required to get refresh token
]

def get_refresh_token():
    """Get refresh token using device code flow"""
    if not CLIENT_ID or not TENANT_ID:
        print("❌ Error: CLIENT_ID and TENANT_ID must be set in .env file")
        return None
    
    print("=" * 60)
    print("🔐 GETTING REFRESH TOKEN FOR RAILWAY")
    print("=" * 60)
    print(f"\nClient ID: {CLIENT_ID}")
    print(f"Tenant ID: {TENANT_ID}\n")
    
    # Create MSAL app
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}"
    )
    
    # Initiate device code flow
    print("Initiating device code flow...\n")
    flow = app.initiate_device_flow(scopes=SCOPES)
    
    if "user_code" not in flow:
        print(f"❌ Failed to create device flow: {flow.get('error_description', 'Unknown error')}")
        return None
    
    # Display instructions
    print("=" * 60)
    print("📱 AUTHENTICATION REQUIRED")
    print("=" * 60)
    print(f"\n{flow['message']}\n")
    print("=" * 60)
    print("\n⏳ Waiting for you to complete authentication...\n")
    
    # Wait for user to complete
    result = app.acquire_token_by_device_flow(flow)
    
    if "access_token" in result:
        refresh_token = result.get("refresh_token")
        
        if refresh_token:
            print("\n" + "=" * 60)
            print("✅ SUCCESS! REFRESH TOKEN OBTAINED")
            print("=" * 60)
            print("\n📋 Add this to Railway Variables:")
            print("-" * 60)
            print(f"REFRESH_TOKEN={refresh_token}")
            print("-" * 60)
            print("\n⚠️  IMPORTANT:")
            print("1. Copy the REFRESH_TOKEN value above")
            print("2. Go to Railway → Your Service → Variables")
            print("3. Add new variable: REFRESH_TOKEN")
            print("4. Paste the token value")
            print("5. Save and redeploy")
            print("\n✅ After adding REFRESH_TOKEN, the app will use delegated auth!")
            print("=" * 60)
            return refresh_token
        else:
            print("❌ No refresh token in response. Make sure 'offline_access' scope is included.")
            return None
    else:
        print(f"❌ Authentication failed: {result.get('error_description', 'Unknown error')}")
        return None

if __name__ == "__main__":
    get_refresh_token()

