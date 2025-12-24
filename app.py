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

from src.api.graph_client_apponly import GraphAPIClientAppOnly
from src.api.transcript_fetcher_apponly import TranscriptFetcherAppOnly
from src.summarizer.claude_summarizer import ClaudeSummarizer
from src.utils.logger import setup_logger
from src.utils.email_sender_apponly import send_summary_email_apponly

# Use PostgreSQL on Railway, SQLite locally
USE_POSTGRES = os.getenv("DATABASE_URL") is not None

if USE_POSTGRES:
    from src.database.db_setup_postgres import DatabaseManager
else:
    from src.database.db_setup_sqlite import DatabaseManager

logger = setup_logger(__name__)

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
        # Auth
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
                            
                            # Send email with summary (test mode sends to test user only)
                            if SEND_EMAILS and EMAIL_SENDER_USER_ID:
                                try:
                                    recipient = m.get("user_email", "")
                                    meeting_date = str(m.get("start_time", "Unknown"))
                                    
                                    if send_summary_email_apponly(
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

