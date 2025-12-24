"""
Minimal Flask App for Railway/AWS Deployment
Uses App-Only auth + Claude for summarization
"""
import os
from flask import Flask, jsonify, request
from datetime import datetime
from functools import wraps

app = Flask(__name__)

# API Key for cron job protection (optional)
CRON_API_KEY = os.getenv("CRON_API_KEY")

def require_api_key(f):
    """Decorator to require API key for cron endpoints"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if CRON_API_KEY:
            # Check header or query param
            api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
            if api_key != CRON_API_KEY:
                return jsonify({"error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated

from src.summarizer.claude_summarizer import ClaudeSummarizer
from src.utils.logger import setup_logger

# Initialize logger first (before any imports that might use it)
logger = setup_logger(__name__)

# Choose auth method: delegated (refresh token) or app-only
USE_DELEGATED_AUTH = os.getenv("REFRESH_TOKEN") is not None

if USE_DELEGATED_AUTH:
    from src.api.graph_client_delegated_refresh import GraphAPIClientDelegatedRefresh
    from src.api.transcript_fetcher_delegated import TranscriptFetcherDelegated
    from src.utils.email_sender import send_summary_email
    logger.info("Using delegated authentication (refresh token)")
else:
    from src.api.graph_client_apponly import GraphAPIClientAppOnly
    from src.api.transcript_fetcher_apponly import TranscriptFetcherAppOnly
    from src.utils.email_sender_apponly import send_summary_email_apponly
    logger.info("Using app-only authentication")

# Use PostgreSQL on Railway, SQLite locally
USE_POSTGRES = os.getenv("DATABASE_URL") is not None

if USE_POSTGRES:
    from src.database.db_setup_postgres import DatabaseManager
else:
    from src.database.db_setup_sqlite import DatabaseManager

SKIP_SUMMARIES = os.getenv("SKIP_SUMMARIES", "false").lower() == "true"
SEND_EMAILS = os.getenv("SEND_EMAILS", "false").lower() == "true"
EMAIL_SENDER_USER_ID = os.getenv("EMAIL_SENDER_USER_ID", "")
TARGET_USER_ID = os.getenv("TARGET_USER_ID", "")  # User ID or email to fetch transcripts for


@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "service": "Teams Meeting Summarizer",
        "endpoints": ["/health", "/run (POST)", "/meetings"]
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat() + "Z"})


@app.route("/run", methods=["GET", "POST"])
@require_api_key
def run_fetch():
    """Trigger transcript fetch and summarization (protected by API key if set)"""
    try:
        # Auth - use delegated if refresh token available, otherwise app-only
        if USE_DELEGATED_AUTH:
            client = GraphAPIClientDelegatedRefresh()
            if not client.authenticate():
                return jsonify({"error": "Auth failed - check REFRESH_TOKEN"}), 500
            
            # Database
            db = DatabaseManager()
            if not db.connect() or not db.create_tables():
                return jsonify({"error": "Database failed"}), 500
            
            # Summarizer (optional)
            summarizer = None
            if not SKIP_SUMMARIES:
                summarizer = ClaudeSummarizer()
                if not summarizer.is_available():
                    logger.warning("Claude not available, skipping summaries")
                    summarizer = None
            
            # Fetch meetings using delegated auth (uses /me endpoints)
            fetcher = TranscriptFetcherDelegated(client)
            meetings = fetcher.list_all_meetings_with_transcripts(days_back=30, include_all=False)
            
            # Transform to match expected format
            meetings_list = []
            for m in meetings:
                meetings_list.append({
                    "meeting_id": m.get("meeting_id"),
                    "subject": m.get("subject"),
                    "user_email": m.get("organizer_email"),
                    "start_time": m.get("start_time"),
                    "user_id": "me"  # Delegated auth uses /me endpoints
                })
            meetings = meetings_list
            
        else:
            # App-only auth (original method)
            client = GraphAPIClientAppOnly()
            if not client.authenticate():
                return jsonify({"error": "Auth failed"}), 500
            
            # Database
            db = DatabaseManager()
            if not db.connect() or not db.create_tables():
                return jsonify({"error": "Database failed"}), 500
            
            # Summarizer (optional)
            summarizer = None
            if not SKIP_SUMMARIES:
                summarizer = ClaudeSummarizer()
                if not summarizer.is_available():
                    logger.warning("Claude not available, skipping summaries")
                    summarizer = None
            
            # Fetch meetings
            fetcher = TranscriptFetcherAppOnly(client)
            
            # Use specific user if TARGET_USER_ID is set, otherwise scan all users
            if TARGET_USER_ID:
                logger.info(f"🎯 Using specific user: {TARGET_USER_ID}")
                meetings = fetcher.list_meetings_with_transcripts_for_user(TARGET_USER_ID)
            else:
                logger.info("🌐 Scanning all users in organization (requires User.Read.All)")
                meetings = fetcher.list_all_meetings_with_transcripts_org_wide()
        
        saved = 0
        summarized = 0
        emails_sent = 0
        
        for m in meetings:
            try:
                db.insert_meeting({
                    "meeting_id": m["meeting_id"],
                    "subject": m.get("subject"),
                    "organizer_email": m.get("user_email"),
                    "start_time": m.get("start_time")
                })
                
                # Fetch transcript - method depends on auth type
                if USE_DELEGATED_AUTH:
                    bundle = fetcher.fetch_transcript_for_meeting(m["meeting_id"], start_time=m.get("start_time"))
                else:
                    bundle = fetcher.fetch_transcript_for_meeting(m["user_id"], m["meeting_id"])
                if bundle and bundle.get("transcript"):
                    db.save_meeting_transcript(
                        meeting_id=m["meeting_id"],
                        transcript_text=bundle["transcript"],
                        start_time=m.get("start_time")
                    )
                    saved += 1
                    
                    # Generate summary if available
                    if summarizer:
                        try:
                            summary = summarizer.summarize(bundle["transcript"])
                            db.save_meeting_summary(
                                meeting_id=m["meeting_id"],
                                summary_text=summary,
                                summary_type="structured",
                                start_time=m.get("start_time")
                            )
                            summarized += 1
                            
                            # Send email with summary
                            if SEND_EMAILS:
                                try:
                                    recipient = m.get("user_email", "")
                                    meeting_date = str(m.get("start_time", "Unknown"))
                                    
                                    if USE_DELEGATED_AUTH:
                                        # Use delegated auth email sender
                                        if send_summary_email(
                                            graph_client=client,
                                            recipient_email=recipient,
                                            meeting_subject=m.get("subject", "Teams Meeting"),
                                            meeting_date=meeting_date,
                                            summary_text=summary,
                                            model_name="Claude"
                                        ):
                                            emails_sent += 1
                                            logger.info(f"📧 Email sent for meeting: {m.get('subject')}")
                                    else:
                                        # Use app-only email sender
                                        if EMAIL_SENDER_USER_ID and send_summary_email_apponly(
                                            graph_client=client,
                                            sender_user_id=EMAIL_SENDER_USER_ID,
                                            recipient_email=recipient,
                                            meeting_subject=m.get("subject", "Teams Meeting"),
                                            meeting_date=meeting_date,
                                            summary_text=summary,
                                            model_name="Claude"
                                        ):
                                            emails_sent += 1
                                            logger.info(f"📧 Email sent for meeting: {m.get('subject')}")
                                except Exception as e:
                                    logger.warning(f"📧 Email failed: {e}")
                                    
                        except Exception as e:
                            logger.warning(f"Summary failed: {e}")
                            
            except Exception as e:
                logger.warning(f"Error: {e}")
        
        db.close()
        return jsonify({
            "status": "success",
            "meetings_found": len(meetings),
            "transcripts_saved": saved,
            "summaries_generated": summarized,
            "emails_sent": emails_sent
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/meetings")
def list_meetings():
    try:
        db = DatabaseManager()
        if not db.connect():
            return jsonify({"error": "DB failed"}), 500
        
        count = db.get_meeting_count()
        cursor = db.connection.cursor()
        cursor.execute("SELECT meeting_id, subject, start_time FROM meetings_raw ORDER BY created_at DESC LIMIT 10")
        meetings = [dict(row) for row in cursor.fetchall()]
        db.close()
        
        return jsonify({"total": count, "recent": meetings})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

