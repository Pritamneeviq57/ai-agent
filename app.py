"""
Minimal Flask App for Railway/AWS Deployment
Uses App-Only auth + Claude for summarization
"""
import os
import sys
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

# Initialize logger first (before any imports that might use it)
try:
    from src.utils.logger import setup_logger
    logger = setup_logger(__name__)
except Exception as e:
    # Fallback to basic logging if logger setup fails
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.warning(f"Failed to setup custom logger: {e}, using basic logging")

# Import summarizer (optional - app can run without it)
try:
    from src.summarizer.claude_summarizer import ClaudeSummarizer
    SUMMARIZER_AVAILABLE = True
except Exception as e:
    logger.error(f"Failed to import ClaudeSummarizer: {e}")
    ClaudeSummarizer = None
    SUMMARIZER_AVAILABLE = False

# Choose auth method: delegated (refresh token) or app-only
USE_DELEGATED_AUTH = os.getenv("REFRESH_TOKEN") is not None

# Import auth modules with error handling
try:
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
except Exception as e:
    logger.error(f"Failed to import auth modules: {e}")
    logger.error("App will start but /run endpoint will fail. Check your imports.")
    # Set to None so we can check later
    if USE_DELEGATED_AUTH:
        GraphAPIClientDelegatedRefresh = None
        TranscriptFetcherDelegated = None
        send_summary_email = None
    else:
        GraphAPIClientAppOnly = None
        TranscriptFetcherAppOnly = None
        send_summary_email_apponly = None

# Use PostgreSQL on Railway, SQLite locally
USE_POSTGRES = os.getenv("DATABASE_URL") is not None

try:
    if USE_POSTGRES:
        from src.database.db_setup_postgres import DatabaseManager, normalize_datetime_string
    else:
        from src.database.db_setup_sqlite import DatabaseManager, normalize_datetime_string
except Exception as e:
    logger.error(f"Failed to import DatabaseManager: {e}")
    logger.error("App will start but database operations will fail.")
    DatabaseManager = None
    normalize_datetime_string = None

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
        # Check if required modules are available
        if USE_DELEGATED_AUTH:
            if GraphAPIClientDelegatedRefresh is None or TranscriptFetcherDelegated is None:
                return jsonify({"error": "Delegated auth modules not available. Check imports."}), 500
        else:
            if GraphAPIClientAppOnly is None or TranscriptFetcherAppOnly is None:
                return jsonify({"error": "App-only auth modules not available. Check imports."}), 500
        
        if DatabaseManager is None:
            return jsonify({"error": "DatabaseManager not available. Check imports."}), 500
        
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
            if not SKIP_SUMMARIES and SUMMARIZER_AVAILABLE and ClaudeSummarizer is not None:
                try:
                    summarizer = ClaudeSummarizer()
                    if not summarizer.is_available():
                        logger.warning("Claude not available, skipping summaries")
                        summarizer = None
                except Exception as e:
                    logger.warning(f"Failed to initialize ClaudeSummarizer: {e}")
                    summarizer = None
            
            # Fetch meetings using delegated auth (uses /me endpoints)
            fetcher = TranscriptFetcherDelegated(client)
            # Limit scope to keep request under Gunicorn timeout
            meetings = fetcher.list_all_meetings_with_transcripts(
                days_back=3,      # last 3 days to keep scan fast
                include_all=False,
                limit=5           # cap to 5 meetings per run to avoid timeouts
            )
            
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
            if not SKIP_SUMMARIES and SUMMARIZER_AVAILABLE and ClaudeSummarizer is not None:
                try:
                    summarizer = ClaudeSummarizer()
                    if not summarizer.is_available():
                        logger.warning("Claude not available, skipping summaries")
                        summarizer = None
                except Exception as e:
                    logger.warning(f"Failed to initialize ClaudeSummarizer: {e}")
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
                
                # Validate transcript exists and has meaningful content
                transcript_text = bundle.get("transcript") if bundle else None
                if not transcript_text:
                    logger.warning(f"⚠️  No transcript available for meeting: {m.get('subject', 'Unknown')}")
                elif not isinstance(transcript_text, str) or not transcript_text.strip() or len(transcript_text.strip()) <= 50:
                    logger.warning(f"⚠️  Transcript too short or empty for meeting: {m.get('subject', 'Unknown')} (length: {len(transcript_text.strip()) if transcript_text else 0})")
                
                if transcript_text and isinstance(transcript_text, str) and transcript_text.strip() and len(transcript_text.strip()) > 50:
                    db.save_meeting_transcript(
                        meeting_id=m["meeting_id"],
                        transcript_text=transcript_text,
                        start_time=m.get("start_time")
                    )
                    saved += 1
                    
                    # Generate summary if available and doesn't already exist
                    if summarizer:
                        # Check if summary already exists for this meeting
                        meeting_start_time = m.get("start_time")
                        if normalize_datetime_string:
                            normalized_start_time = normalize_datetime_string(meeting_start_time) if meeting_start_time else None
                        else:
                            normalized_start_time = meeting_start_time
                        existing_summary = db.get_meeting_summary(m["meeting_id"], start_time=normalized_start_time)
                        
                        if existing_summary and existing_summary.get("summary_text"):
                            logger.info(f"⏭️  Summary already exists for meeting: {m.get('subject', 'Unknown')} (created: {existing_summary.get('created_at', 'Unknown')})")
                            logger.info(f"   Skipping summary generation and email send")
                            # Count as already summarized
                            summarized += 1
                        else:
                            try:
                                logger.info(f"📝 Generating summary for meeting: {m.get('subject', 'Unknown')}")
                                summary = summarizer.summarize(transcript_text)
                                db.save_meeting_summary(
                                    meeting_id=m["meeting_id"],
                                    summary_text=summary,
                                    summary_type="structured",
                                    start_time=m.get("start_time")
                                )
                                summarized += 1
                                logger.info(f"✅ Summary generated and saved for meeting: {m.get('subject', 'Unknown')}")
                                
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
                                logger.warning(f"Summary generation failed: {e}")
                            
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
        if DatabaseManager is None:
            return jsonify({"error": "DatabaseManager not available"}), 500
        
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
        logger.error(f"Error in /meetings: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

