"""
Script to extract and display the refresh token for Railway deployment.
This ensures the complete token is copied correctly.
"""
import json
import os
from config.settings import Settings

def get_refresh_token_for_railway():
    """Extract refresh token from cache and display it for Railway"""
    cache_file = Settings.CACHE_FILE
    
    print("=" * 70)
    print("🔄 EXTRACTING REFRESH TOKEN FOR RAILWAY")
    print("=" * 70)
    print(f"\nCache location: {cache_file}\n")
    
    if not os.path.exists(cache_file):
        print("❌ No token cache found!")
        print("   Run: python get_refresh_token.py")
        return None
    
    try:
        with open(cache_file, "r") as f:
            cache_data = json.load(f)
        
        # Extract refresh token from MSAL cache
        refresh_tokens = cache_data.get("RefreshToken", {})
        if not refresh_tokens:
            print("❌ No refresh token found in cache!")
            print("   You may need to re-authenticate with: python get_refresh_token.py")
            return None
        
        # Get the first refresh token
        refresh_token = list(refresh_tokens.values())[0].get("secret")
        
        if not refresh_token:
            print("❌ Refresh token secret not found!")
            return None
        
        print("✅ Refresh token found!\n")
        print(f"📏 Token length: {len(refresh_token)} characters")
        print(f"🔍 Starts with: {refresh_token[:50]}...")
        print(f"🔍 Ends with: ...{refresh_token[-50:]}\n")
        
        # Verify it starts with "1."
        if not refresh_token.startswith("1."):
            print("⚠️  WARNING: Refresh token should start with '1.'")
            print("   This token may be invalid!")
        else:
            print("✅ Token format verified (starts with '1.')\n")
        
        print("=" * 70)
        print("📋 COPY THIS COMPLETE TOKEN FOR RAILWAY:")
        print("=" * 70)
        print("\nREFRESH_TOKEN=" + refresh_token)
        print("\n" + "=" * 70)
        print("📝 INSTRUCTIONS:")
        print("=" * 70)
        print("1. Copy the ENTIRE line above (including REFRESH_TOKEN=)")
        print("2. Go to Railway → ai-agent service → Variables tab")
        print("3. Find REFRESH_TOKEN variable")
        print("4. Click to edit")
        print("5. Paste the COMPLETE token (make sure it starts with '1.')")
        print("6. Save and redeploy")
        print("=" * 70)
        
        # Also save to a file for easy access
        token_file = os.path.join(os.path.dirname(__file__), "refresh_token.txt")
        with open(token_file, "w") as f:
            f.write(refresh_token)
        print(f"\n💾 Token also saved to: {token_file}")
        print("   (You can copy from there if needed)\n")
        
        return refresh_token
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    get_refresh_token_for_railway()

