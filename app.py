"""
Minimal Flask App for Railway/AWS Deployment
Uses App-Only auth + Claude for summarization
Updated: Added /migrate-tables endpoint for database migration
"""
import os
import sys
import json
import threading
from flask import Flask, jsonify, request
from datetime import datetime, timedelta
from functools import wraps

# Create Flask app FIRST - this must succeed
app = Flask(__name__)

# API Key for cron job protection (optional)
CRON_API_KEY = os.getenv("CRON_API_KEY")

# Add a simple health check that works even if everything else fails
@app.route("/health")
def health_simple():
    """Simple health check that doesn't depend on any imports"""
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat() + "Z"}), 200

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
GraphAPIClientDelegatedRefresh = None
TranscriptFetcherDelegated = None
send_summary_email = None
GraphAPIClientAppOnly = None
TranscriptFetcherAppOnly = None
send_summary_email_apponly = None

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
    import traceback
    error_msg = f"Failed to import auth modules: {e}\n{traceback.format_exc()}"
    logger.error(error_msg)
    print(f"ERROR: {error_msg}", file=sys.stderr)
    logger.error("App will start but /run endpoint will fail. Check your imports.")

# Use PostgreSQL on Railway, SQLite locally
USE_POSTGRES = os.getenv("DATABASE_URL") is not None

DatabaseManager = None
normalize_datetime_string = None

try:
    if USE_POSTGRES:
        from src.database.db_setup_postgres import DatabaseManager, normalize_datetime_string
    else:
        from src.database.db_setup_sqlite import DatabaseManager, normalize_datetime_string
except Exception as e:
    import traceback
    error_msg = f"Failed to import DatabaseManager: {e}\n{traceback.format_exc()}"
    logger.error(error_msg)
    print(f"ERROR: {error_msg}", file=sys.stderr)
    logger.error("App will start but database operations will fail.")

SKIP_SUMMARIES = os.getenv("SKIP_SUMMARIES", "false").lower() == "true"
SEND_EMAILS = os.getenv("SEND_EMAILS", "false").lower() == "true"
EMAIL_SENDER_USER_ID = os.getenv("EMAIL_SENDER_USER_ID", "")
TARGET_USER_ID = os.getenv("TARGET_USER_ID", "")  # User ID or email to fetch transcripts for


@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "service": "Teams Meeting Summarizer",
        "endpoints": [
            "/health",
            "/process (POST) - Main endpoint: Fetches and processes meetings for last 15 days",
            "/run (POST) - Legacy endpoint for immediate processing",
            "/meetings (GET) - List recent meetings",
            "/generate-pulse-report (GET/POST) - Generate aggregated client pulse reports for last 15 days",
            "/migrate-tables (POST) - Migrate data from old meeting_summaries to new separate tables"
        ],
        "documentation": {
            "/process": {
                "description": "Fetches Teams meetings from last 15 days and processes them",
                "actions": [
                    "1. Fetches meetings with transcripts from last 15 days",
                    "2. For each meeting:",
                    "   - Downloads transcript if not already saved",
                    "   - Generates 'structured' summary (if missing) and sends email",
                    "   - Generates 'client_pulse' report (if missing) and saves to database (NO email)",
                    "3. Returns count of meetings processed, summaries generated, etc."
                ],
                "email_behavior": "Only structured summaries are emailed. Client pulse reports are saved but NOT emailed."
            },
            "/generate-pulse-report": {
                "description": "Aggregates individual client_pulse reports from last 15 days by client",
                "actions": [
                    "1. Queries all 'client_pulse' summaries from last 15 days",
                    "2. Groups them by client name",
                    "3. For each client:",
                    "   - Aggregates all individual pulse reports into one combined report using LLM",
                    "   - Sends aggregated report via email to EMAIL_TEST_RECIPIENT only",
                    "   - Does NOT save aggregated report to database (per user request)"
                ],
                "email_behavior": "Sends aggregated reports to EMAIL_TEST_RECIPIENT only. Does not save to database."
            }
        }
    })




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
                days_back=4,      # last 4 days to keep scan fast
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
                    "organizer_email": m.get("organizer_email"),
                    "participants": m.get("participants", []),  # Include all participants
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
                        existing_summary = db.get_structured_summary(m["meeting_id"], start_time=normalized_start_time)
                        
                        if existing_summary and existing_summary.get("summary_text"):
                            logger.info(f"⏭️  Summary already exists for meeting: {m.get('subject', 'Unknown')} (created: {existing_summary.get('created_at', 'Unknown')})")
                            logger.info(f"   Skipping summary generation and email send")
                            # Count as already summarized
                            summarized += 1
                        else:
                            try:
                                logger.info(f"📝 Generating summary for meeting: {m.get('subject', 'Unknown')}")
                                summary = summarizer.summarize(transcript_text)
                                db.save_structured_summary(
                                    meeting_id=m["meeting_id"],
                                    summary_text=summary,
                                    start_time=m.get("start_time")
                                )
                                summarized += 1
                                logger.info(f"✅ Summary generated and saved for meeting: {m.get('subject', 'Unknown')}")
                                
                                # Send email with summary
                                if SEND_EMAILS:
                                    try:
                                        # Extract all participant emails from meeting data
                                        participants_data = m.get("participants", [])
                                        # Handle participants if stored as JSON string
                                        if isinstance(participants_data, str):
                                            try:
                                                participants = json.loads(participants_data)
                                            except:
                                                participants = []
                                        else:
                                            participants = participants_data if participants_data else []
                                        
                                        organizer_email = m.get("user_email", "") or m.get("organizer_email", "")
                                        meeting_date = str(m.get("start_time", "Unknown"))
                                        
                                        if USE_DELEGATED_AUTH:
                                            # Use delegated auth email sender
                                            # Pass all participants to send to everyone
                                            if send_summary_email and send_summary_email(
                                                graph_client=client,
                                                recipient_email=organizer_email,  # Fallback if no participants
                                                meeting_subject=m.get("subject", "Teams Meeting"),
                                                meeting_date=meeting_date,
                                                summary_text=summary,
                                                model_name="Claude Opus 4.5",
                                                organizer_participants=participants  # All meeting participants
                                            ):
                                                emails_sent += 1
                                                logger.info(f"📧 Email sent for meeting: {m.get('subject')}")
                                        else:
                                            # Use app-only email sender
                                            if EMAIL_SENDER_USER_ID and send_summary_email_apponly and send_summary_email_apponly(
                                                graph_client=client,
                                                sender_user_id=EMAIL_SENDER_USER_ID,
                                                recipient_email=organizer_email,  # Fallback if no participants
                                                meeting_subject=m.get("subject", "Teams Meeting"),
                                                meeting_date=meeting_date,
                                                summary_text=summary,
                                                model_name="Claude Opus 4.5",
                                                participants=participants  # All meeting participants
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


def run_meeting_processing():
    """
    Standalone function to process meetings - can be called directly or via HTTP endpoint.
    Fetches all Teams meeting transcriptions from the last 15 days from Graph API.
    If transcriptions are available, checks if summary exists - if yes, skips; if not, generates summary and sends email.
    Returns a dict with processing results (not a Flask response).
    """
    try:
        if USE_DELEGATED_AUTH:
            if GraphAPIClientDelegatedRefresh is None or TranscriptFetcherDelegated is None:
                return {"error": "Delegated auth modules not available"}
        else:
            if GraphAPIClientAppOnly is None or TranscriptFetcherAppOnly is None:
                return {"error": "App-only auth modules not available"}
        
        if DatabaseManager is None:
            return {"error": "DatabaseManager not available"}
        
        # Authenticate
        if USE_DELEGATED_AUTH:
            client = GraphAPIClientDelegatedRefresh()
            if not client.authenticate():
                return {"error": "Auth failed - check REFRESH_TOKEN"}
            fetcher = TranscriptFetcherDelegated(client)
        else:
            client = GraphAPIClientAppOnly()
            if not client.authenticate():
                return {"error": "Auth failed"}
            fetcher = TranscriptFetcherAppOnly(client)
        
        # Connect to database
        db = DatabaseManager()
        if not db.connect() or not db.create_tables():
            return {"error": "Database failed"}
        
        # Fetch meetings from Graph API (last 15 days to now, using user ID)
        logger.info("📅 Fetching Teams meetings from Graph API (last 15 days to now)...")
        if USE_DELEGATED_AUTH:
            # For delegated auth, use TARGET_USER_ID if provided, otherwise use authenticated user
            if TARGET_USER_ID:
                logger.info(f"🎯 Using specific user ID: {TARGET_USER_ID}")
                all_meetings = fetcher.list_all_meetings_with_transcripts(days_back=15, limit=50, user_id=TARGET_USER_ID)
            else:
                all_meetings = fetcher.list_all_meetings_with_transcripts(days_back=15, limit=50)
        else:
            # For app-only, fetch for specific user
            if not TARGET_USER_ID:
                db.close()
                return {"error": "TARGET_USER_ID not configured for app-only auth"}
            logger.info(f"🎯 Using specific user ID: {TARGET_USER_ID}")
            # App-only fetcher uses different method name
            all_meetings_raw = fetcher.list_meetings_with_transcripts_for_user(TARGET_USER_ID)
            # Transform to match expected format and filter by date (last 15 days)
            all_meetings = []
            cutoff_date = datetime.now() - timedelta(days=15)
            for m in all_meetings_raw:
                start_time_str = m.get("start_time")
                # Parse start_time to check if it's within the last 15 days
                try:
                    if start_time_str:
                        # Try to parse the datetime string
                        if isinstance(start_time_str, str):
                            # Handle ISO format with timezone
                            if start_time_str.endswith("Z"):
                                start_time_str = start_time_str.replace("Z", "+00:00")
                            start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                        else:
                            start_dt = start_time_str
                        
                        # Only include meetings from last 15 days
                        if start_dt >= cutoff_date:
                            all_meetings.append({
                                "meeting_id": m.get("meeting_id"),
                                "subject": m.get("subject"),
                                "user_email": m.get("user_email"),
                                "organizer_email": m.get("user_email"),
                                "participants": [],
                                "start_time": m.get("start_time"),
                                "user_id": m.get("user_id")
                            })
                except Exception as e:
                    # If date parsing fails, include the meeting anyway (better to process than skip)
                    logger.debug(f"Could not parse date for meeting {m.get('meeting_id')}: {e}, including anyway")
                    all_meetings.append({
                        "meeting_id": m.get("meeting_id"),
                        "subject": m.get("subject"),
                        "user_email": m.get("user_email"),
                        "organizer_email": m.get("user_email"),
                        "participants": [],
                        "start_time": m.get("start_time"),
                        "user_id": m.get("user_id")
                    })
        
        if not all_meetings:
            db.close()
            return {
                "status": "success",
                "meetings_found": 0,
                "meetings_processed": 0,
                "message": "No meetings found"
            }
        
        logger.info(f"📋 Found {len(all_meetings)} meetings from Graph API")
        
        # Process all meetings - fetch transcriptions and check if summary exists
        logger.info(f"🔄 Processing {len(all_meetings)} meetings (fetching transcriptions and checking summaries)...")
        
        # Initialize summarizer
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
        
        saved = 0
        summarized = 0
        pulse_reports_generated = 0
        emails_sent = 0
        processed = 0
        skipped = 0
        no_transcript = 0
        
        for meeting in all_meetings:
            try:
                meeting_id = meeting["meeting_id"]
                start_time = meeting.get("start_time")
                subject = meeting.get("subject", "Unknown")
                
                logger.info(f"🔄 Processing meeting: {subject} (ID: {meeting_id})")
                
                # Check if both structured summary and client_pulse already exist - skip if both exist
                normalized_start_time = normalize_datetime_string(start_time) if start_time and normalize_datetime_string else start_time
                existing_summary = db.get_structured_summary(meeting_id, start_time=normalized_start_time)
                existing_pulse = db.get_client_pulse_report(meeting_id, start_time=normalized_start_time)
                
                if existing_summary and existing_summary.get("summary_text") and existing_pulse and existing_pulse.get("summary_text"):
                    logger.info(f"⏭️  Both structured summary and client_pulse already exist for meeting: {subject} - skipping")
                    skipped += 1
                    processed += 1
                    continue
                
                # Fetch transcript for this meeting (using user ID)
                if USE_DELEGATED_AUTH:
                    bundle = fetcher.fetch_transcript_for_meeting(meeting_id, start_time=start_time)
                else:
                    # For app-only, use TARGET_USER_ID
                    user_id = TARGET_USER_ID if TARGET_USER_ID else "me"
                    bundle = fetcher.fetch_transcript_for_meeting(user_id, meeting_id)
                
                # Validate transcript - only process if transcript is available
                transcript_text = bundle.get("transcript") if bundle else None
                if not transcript_text:
                    logger.warning(f"⚠️  No transcript available for meeting: {subject} - skipping")
                    no_transcript += 1
                    processed += 1
                    continue
                
                if not isinstance(transcript_text, str) or not transcript_text.strip() or len(transcript_text.strip()) <= 50:
                    logger.warning(f"⚠️  Transcript too short for meeting: {subject} - skipping")
                    no_transcript += 1
                    processed += 1
                    continue
                
                # Save transcript
                db.save_meeting_transcript(
                    meeting_id=meeting_id,
                    transcript_text=transcript_text,
                    start_time=start_time
                )
                saved += 1
                
                # Generate structured summary if available and doesn't exist
                if summarizer:
                    try:
                        # Generate structured summary if it doesn't exist
                        if not existing_summary or not existing_summary.get("summary_text"):
                            logger.info(f"📝 Generating structured summary for meeting: {subject}")
                            try:
                                summary = summarizer.summarize(transcript_text)
                                
                                # Validate summary before saving
                                if not summary or not isinstance(summary, str) or len(summary.strip()) < 50:
                                    logger.error(f"❌ Generated summary is invalid or too short ({len(summary) if summary else 0} chars) - not saving")
                                    raise Exception(f"Summary generation failed: returned invalid or empty result")
                                
                                db.save_structured_summary(
                                    meeting_id=meeting_id,
                                    summary_text=summary,
                                    start_time=start_time
                                )
                                summarized += 1
                                logger.info(f"✅ Structured summary generated for meeting: {subject}")
                                
                                # Send email with structured summary only (only if summary is valid)
                                if SEND_EMAILS and summary and len(summary.strip()) >= 50:
                                try:
                                    # Extract all participant emails from meeting data
                                    participants_data = meeting.get("participants", [])
                                    # Handle participants if stored as JSON string
                                    if isinstance(participants_data, str):
                                        try:
                                            participants = json.loads(participants_data)
                                        except:
                                            participants = []
                                    else:
                                        participants = participants_data if participants_data else []
                                    
                                    organizer_email = meeting.get("organizer_email", "")
                                    meeting_date = str(start_time) if start_time else "Unknown"
                                    
                                    if USE_DELEGATED_AUTH:
                                        # Use delegated auth email sender
                                        # Pass all participants to send to everyone
                                        if send_summary_email and send_summary_email(
                                            graph_client=client,
                                            recipient_email=organizer_email,  # Fallback if no participants
                                            meeting_subject=subject,
                                            meeting_date=meeting_date,
                                            summary_text=summary,
                                            model_name="Claude Opus 4.5",
                                            organizer_participants=participants  # All meeting participants
                                        ):
                                            emails_sent += 1
                                            logger.info(f"📧 Email sent for meeting: {subject}")
                                    else:
                                        # Use app-only email sender
                                        if EMAIL_SENDER_USER_ID and send_summary_email_apponly and send_summary_email_apponly(
                                            graph_client=client,
                                            sender_user_id=EMAIL_SENDER_USER_ID,
                                            recipient_email=organizer_email,  # Fallback if no participants
                                            meeting_subject=subject,
                                            meeting_date=meeting_date,
                                            summary_text=summary,
                                            model_name="Claude Opus 4.5",
                                            participants=participants  # All meeting participants
                                        ):
                                            emails_sent += 1
                                            logger.info(f"📧 Email sent for meeting: {subject}")
                                    except Exception as e:
                                        logger.warning(f"📧 Email failed: {e}")
                                else:
                                    if SEND_EMAILS:
                                        logger.warning(f"⚠️  Skipping email send - summary is empty or invalid")
                            except Exception as e:
                                logger.error(f"❌ Failed to generate or save summary for meeting {subject}: {e}")
                                # Continue processing other meetings
                        else:
                            logger.info(f"⏭️  Structured summary already exists for meeting: {subject}")
                        
                        # Generate client_pulse report if it doesn't exist
                        if not existing_pulse or not existing_pulse.get("summary_text"):
                            logger.info(f"📊 Generating client_pulse report for meeting: {subject}")
                            # Improved client name extraction
                            client_name = meeting.get("client_name") or ""
                            if not client_name or client_name.strip() == "":
                                clean_subject = subject.replace("Canceled:", "").strip()
                                
                                # Pattern 1: "Project Sync-Up: Britt Rice Ele // Neev" → "Britt Rice Ele"
                                if "Project Sync-Up:" in clean_subject and "Britt Rice Ele" in clean_subject:
                                    if "//" in clean_subject:
                                        parts = clean_subject.split("//")
                                        if len(parts) > 0:
                                            client_part = parts[0].split(":")[-1].strip()
                                            if "Britt Rice Ele" in client_part:
                                                client_name = "Britt Rice Ele"
                                
                                # Pattern 2: "Neev//BLOX FED-IRF Check-in" → "BLOX FED-IRF"
                                elif "BLOX FED-IRF" in clean_subject:
                                    if "//" in clean_subject:
                                        parts = clean_subject.split("//")
                                        if len(parts) > 1:
                                            client_name = parts[1].split("Check-in")[0].strip()
                                            if not client_name:
                                                client_name = "BLOX FED-IRF"
                                    else:
                                        client_name = "BLOX FED-IRF"
                                
                                # Pattern 3: General colon-separated subjects
                                elif ":" in clean_subject:
                                    potential_client = clean_subject.split(":")[0].strip()
                                    if potential_client and potential_client.lower() not in ["project sync-up", "canceled"]:
                                        client_name = potential_client
                                
                                # Pattern 4: Extract meaningful part from subject
                                if not client_name or client_name.strip() == "":
                                    parts = clean_subject.split()
                                    if parts:
                                        client_name = " ".join(parts[:2]) if len(parts) > 1 else parts[0]
                            
                            # Final fallback
                            if not client_name or client_name.strip() == "":
                                client_name = "Client"
                            try:
                                pulse_report = summarizer.generate_client_pulse_report(
                                    transcript_text,
                                    client_name=client_name,
                                    month="Current"
                                )
                                
                                # Validate pulse report before saving
                                if not pulse_report or not isinstance(pulse_report, str) or len(pulse_report.strip()) < 100:
                                    logger.error(f"❌ Generated pulse report is invalid or too short ({len(pulse_report) if pulse_report else 0} chars) - not saving")
                                    raise Exception(f"Pulse report generation failed: returned invalid or empty result")
                                
                                db.save_client_pulse_report(
                                    meeting_id=meeting_id,
                                    summary_text=pulse_report,
                                    client_name=client_name,
                                    start_time=start_time
                                )
                                pulse_reports_generated += 1
                                logger.info(f"✅ Client pulse report generated for meeting: {subject}")
                            except Exception as e:
                                logger.error(f"❌ Failed to generate or save pulse report for meeting {subject}: {e}")
                                # Continue processing other meetings
                        else:
                            logger.info(f"⏭️  Client pulse report already exists for meeting: {subject}")
                            
                    except Exception as e:
                        logger.warning(f"Summary/pulse generation failed: {e}")
                
                # Mark meeting as processed
                db.mark_meeting_as_processed(meeting_id, start_time)
                processed += 1
                
            except Exception as e:
                logger.error(f"Error processing meeting {meeting.get('meeting_id')}: {e}")
                continue
        
        db.close()
        
        logger.info(f"✅ Processing complete: {processed} meetings processed, {saved} transcripts saved, {summarized} structured summaries generated, {pulse_reports_generated} pulse reports generated, {emails_sent} emails sent, {skipped} skipped (both summaries exist), {no_transcript} with no transcript")
        return {
            "status": "success",
            "meetings_found": len(all_meetings),
            "meetings_processed": processed,
            "transcripts_saved": saved,
            "summaries_generated": summarized,
            "pulse_reports_generated": pulse_reports_generated,
            "emails_sent": emails_sent,
            "skipped": skipped,
            "no_transcript": no_transcript,
            "message": f"Found {len(all_meetings)} meetings, processed {processed} meetings ({skipped} skipped with existing summaries, {no_transcript} with no transcript available)"
        }
        
    except Exception as e:
        logger.error(f"Error in meeting processing: {e}")
        return {"error": str(e)}


@app.route("/process", methods=["GET", "POST"])
@require_api_key
def process_meetings():
    """
    HTTP endpoint wrapper for meeting processing.
    Main processing endpoint (runs every 6 hours).
    Fetches all Teams meeting transcriptions from the last 15 days from Graph API (using user ID).
    If transcriptions are available, checks if summary exists - if yes, skips; if not, generates summary and sends email.
    """
    result = run_meeting_processing()
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route("/migrate-tables", methods=["POST"])
@require_api_key
def migrate_tables():
    """
    Migrate existing data from meeting_summaries table to new separate tables:
    - structured_summaries
    - client_pulse_reports
    
    This is a one-time migration. Safe to run multiple times (uses ON CONFLICT DO NOTHING).
    """
    try:
        if DatabaseManager is None:
            return jsonify({"error": "DatabaseManager not available"}), 500
        
        db = DatabaseManager()
        if not db.connect() or not db.create_tables():
            return jsonify({"error": "Database connection failed"}), 500
        
        cursor = db.connection.cursor()
        
        try:
            # Check if meeting_summaries table exists
            if USE_POSTGRES:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM information_schema.tables 
                    WHERE table_name = 'meeting_summaries'
                """)
                table_exists = cursor.fetchone()['count'] > 0
            else:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM sqlite_master 
                    WHERE type='table' AND name='meeting_summaries'
                """)
                table_exists = cursor.fetchone()[0] > 0
            
            if not table_exists:
                return jsonify({
                    "status": "success",
                    "message": "meeting_summaries table doesn't exist - nothing to migrate",
                    "migrated_structured": 0,
                    "migrated_pulse": 0
                })
            
            # Count records to migrate
            if USE_POSTGRES:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM meeting_summaries
                    WHERE summary_type IN ('structured', 'client_pulse')
                """)
                total_count = cursor.fetchone()['count']
            else:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM meeting_summaries
                    WHERE summary_type IN ('structured', 'client_pulse')
                """)
                total_count = cursor.fetchone()[0]
            
            if total_count == 0:
                return jsonify({
                    "status": "success",
                    "message": "No records to migrate from meeting_summaries",
                    "migrated_structured": 0,
                    "migrated_pulse": 0
                })
            
            # Migrate structured summaries
            if USE_POSTGRES:
                cursor.execute("""
                    SELECT meeting_id, start_time, meeting_date, summary_text, created_at, updated_at
                    FROM meeting_summaries
                    WHERE summary_type = 'structured'
                """)
            else:
                cursor.execute("""
                    SELECT meeting_id, start_time, meeting_date, summary_text, created_at, updated_at
                    FROM meeting_summaries
                    WHERE summary_type = 'structured'
                """)
            
            structured_records = cursor.fetchall()
            migrated_structured = 0
            
            for record in structured_records:
                try:
                    meeting_id = record['meeting_id'] if USE_POSTGRES else record[0]
                    start_time = record['start_time'] if USE_POSTGRES else record[1]
                    meeting_date = record['meeting_date'] if USE_POSTGRES else record[2]
                    summary_text = record['summary_text'] if USE_POSTGRES else record[3]
                    created_at = record['created_at'] if USE_POSTGRES else record[4]
                    updated_at = record['updated_at'] if USE_POSTGRES else record[5]
                    
                    if USE_POSTGRES:
                        cursor.execute("""
                            INSERT INTO structured_summaries 
                            (meeting_id, start_time, meeting_date, summary_text, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (meeting_id, start_time) DO NOTHING
                        """, (meeting_id, start_time, meeting_date, summary_text, created_at, updated_at))
                    else:
                        cursor.execute("""
                            INSERT INTO structured_summaries 
                            (meeting_id, start_time, meeting_date, summary_text, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(meeting_id, start_time) DO NOTHING
                        """, (meeting_id, start_time, meeting_date, summary_text, created_at, updated_at))
                    
                    migrated_structured += 1
                except Exception as e:
                    logger.warning(f"Error migrating structured summary {meeting_id}: {e}")
            
            # Migrate client pulse reports
            if USE_POSTGRES:
                cursor.execute("""
                    SELECT 
                        ms.meeting_id,
                        ms.start_time,
                        ms.meeting_date,
                        ms.summary_text,
                        ms.created_at,
                        ms.updated_at,
                        COALESCE(mr.client_name, '') as client_name,
                        mr.subject
                    FROM meeting_summaries ms
                    LEFT JOIN meetings_raw mr ON ms.meeting_id = mr.meeting_id AND ms.start_time = mr.start_time
                    WHERE ms.summary_type = 'client_pulse'
                """)
            else:
                cursor.execute("""
                    SELECT 
                        ms.meeting_id,
                        ms.start_time,
                        ms.meeting_date,
                        ms.summary_text,
                        ms.created_at,
                        ms.updated_at,
                        COALESCE(mr.client_name, '') as client_name,
                        mr.subject
                    FROM meeting_summaries ms
                    LEFT JOIN meetings_raw mr ON ms.meeting_id = mr.meeting_id AND ms.start_time = mr.start_time
                    WHERE ms.summary_type = 'client_pulse'
                """)
            
            pulse_records = cursor.fetchall()
            migrated_pulse = 0
            
            for record in pulse_records:
                try:
                    meeting_id = record['meeting_id'] if USE_POSTGRES else record[0]
                    start_time = record['start_time'] if USE_POSTGRES else record[1]
                    meeting_date = record['meeting_date'] if USE_POSTGRES else record[2]
                    summary_text = record['summary_text'] if USE_POSTGRES else record[3]
                    created_at = record['created_at'] if USE_POSTGRES else record[4]
                    updated_at = record['updated_at'] if USE_POSTGRES else record[5]
                    client_name = record['client_name'] if USE_POSTGRES else record[6]
                    subject = record['subject'] if USE_POSTGRES else record[7]
                    
                    # Extract client_name from subject if not available
                    if not client_name or client_name.strip() == '':
                        if subject and ':' in subject:
                            client_name = subject.split(':')[0].strip()
                            if client_name.lower() in ["project sync-up", "canceled"]:
                                client_name = ''
                    
                    if USE_POSTGRES:
                        cursor.execute("""
                            INSERT INTO client_pulse_reports 
                            (meeting_id, start_time, meeting_date, client_name, summary_text, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (meeting_id, start_time) DO NOTHING
                        """, (meeting_id, start_time, meeting_date, client_name if client_name else None, summary_text, created_at, updated_at))
                    else:
                        cursor.execute("""
                            INSERT INTO client_pulse_reports 
                            (meeting_id, start_time, meeting_date, client_name, summary_text, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(meeting_id, start_time) DO NOTHING
                        """, (meeting_id, start_time, meeting_date, client_name if client_name else None, summary_text, created_at, updated_at))
                    
                    migrated_pulse += 1
                except Exception as e:
                    logger.warning(f"Error migrating client pulse report {meeting_id}: {e}")
            
            db.connection.commit()
            db.close()
            
            return jsonify({
                "status": "success",
                "message": f"Migration complete: {migrated_structured} structured summaries, {migrated_pulse} client pulse reports",
                "migrated_structured": migrated_structured,
                "migrated_pulse": migrated_pulse,
                "total_migrated": migrated_structured + migrated_pulse
            })
            
        except Exception as e:
            db.connection.rollback()
            db.close()
            logger.error(f"Error during migration: {e}")
            return jsonify({"error": str(e)}), 500
            
    except Exception as e:
        logger.error(f"Error in /migrate-tables: {e}")
        return jsonify({"error": str(e)}), 500


def _process_pulse_reports_background():
    """
    Background function to process pulse reports.
    This runs in a separate thread to avoid HTTP timeout.
    Returns None - all results are logged.
    """
    try:
        if DatabaseManager is None:
            logger.error("DatabaseManager not available for pulse report generation")
            return
        
        # Initialize summarizer
        summarizer = None
        if not SKIP_SUMMARIES and SUMMARIZER_AVAILABLE and ClaudeSummarizer is not None:
            try:
                summarizer = ClaudeSummarizer()
                if not summarizer.is_available():
                    logger.error("Claude summarizer not available for pulse report generation")
                    return
            except Exception as e:
                logger.error(f"Failed to initialize ClaudeSummarizer: {e}")
                return
        else:
            logger.error("Summarizer not available for pulse report generation")
            return
        
        # Connect to database
        db = DatabaseManager()
        if not db.connect() or not db.create_tables():
            logger.error("Database connection failed for pulse report generation")
            return
        
        # Calculate date range (last 15 days)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=15)
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")
        date_range = f"{start_date_str} to {end_date_str}"
        
        logger.info(f"📊 Generating aggregated pulse reports for last 15 days ({date_range})...")
        
        # Query for client_pulse summaries from last 15 days, grouped by client_name
        cursor = db.connection.cursor()
        
        if USE_POSTGRES:
            query = """
                SELECT 
                    cpr.meeting_id,
                    cpr.start_time,
                    cpr.summary_text as pulse_report,
                    COALESCE(cpr.client_name, mr.client_name,
                        CASE 
                            WHEN mr.subject LIKE '%%:%%' THEN SPLIT_PART(mr.subject, ':', 1)
                            ELSE 'Unknown Client'
                        END
                    ) as client_name,
                    mr.subject
                FROM client_pulse_reports cpr
                JOIN meetings_raw mr ON cpr.meeting_id = mr.meeting_id AND cpr.start_time = mr.start_time
                WHERE cpr.start_time >= %s
                  AND cpr.start_time <= %s
                ORDER BY client_name, cpr.start_time DESC
            """
            cursor.execute(query, (start_date_str, end_date_str))
        else:
            query = """
                SELECT 
                    cpr.meeting_id,
                    cpr.start_time,
                    cpr.summary_text as pulse_report,
                    CASE 
                        WHEN cpr.client_name IS NOT NULL AND cpr.client_name != '' THEN cpr.client_name
                        WHEN mr.client_name IS NOT NULL AND mr.client_name != '' THEN mr.client_name
                        WHEN mr.subject LIKE '%:%' THEN TRIM(SUBSTR(mr.subject, 1, INSTR(mr.subject, ':') - 1))
                        ELSE 'Unknown Client'
                    END as client_name,
                    mr.subject
                FROM client_pulse_reports cpr
                JOIN meetings_raw mr ON cpr.meeting_id = mr.meeting_id AND cpr.start_time = mr.start_time
                WHERE cpr.start_time >= ?
                  AND cpr.start_time <= ?
                ORDER BY client_name, cpr.start_time DESC
            """
            cursor.execute(query, (start_date_str, end_date_str))
        
        all_pulse_reports = cursor.fetchall()
        
        if not all_pulse_reports:
            # Debug: Let's check what's actually in the database (before closing connection)
            if USE_POSTGRES:
                debug_query = """
                    SELECT COUNT(*) as count, 
                           COUNT(CASE WHEN cpr.client_name IS NOT NULL AND cpr.client_name != '' THEN 1 END) as with_client_name
                    FROM client_pulse_reports cpr
                    JOIN meetings_raw mr ON cpr.meeting_id = mr.meeting_id AND cpr.start_time = mr.start_time
                    WHERE cpr.start_time >= %s
                      AND cpr.start_time <= %s
                """
                cursor.execute(debug_query, (start_date_str, end_date_str))
            else:
                debug_query = """
                    SELECT COUNT(*) as count,
                           SUM(CASE WHEN cpr.client_name IS NOT NULL AND cpr.client_name != '' THEN 1 ELSE 0 END) as with_client_name
                    FROM client_pulse_reports cpr
                    JOIN meetings_raw mr ON cpr.meeting_id = mr.meeting_id AND cpr.start_time = mr.start_time
                    WHERE cpr.start_time >= ?
                      AND cpr.start_time <= ?
                """
                cursor.execute(debug_query, (start_date_str, end_date_str))
            debug_result = cursor.fetchone()
            total_count = debug_result['count'] if USE_POSTGRES else debug_result[0]
            with_client_name = debug_result['with_client_name'] if USE_POSTGRES else debug_result[1]
            db.close()
            logger.info(f"No client_pulse reports found in last 15 days (Total pulse reports: {total_count}, With client_name: {with_client_name})")
            return
        
        # Group by client_name (extracted from query or subject)
        client_groups = {}
        for row in all_pulse_reports:
            client_name = row['client_name'] if USE_POSTGRES else row[3]
            # Fallback: extract from subject if client_name is still empty
            if not client_name or client_name.strip() == '' or client_name == 'Unknown Client':
                subject = row['subject'] if USE_POSTGRES else row[4]
                if subject:
                    # Handle specific recurring meeting patterns
                    clean_subject = subject.replace("Canceled:", "").strip()
                    
                    # Pattern 1: "Project Sync-Up: Britt Rice Ele // Neev" → "Britt Rice Ele"
                    if "Project Sync-Up:" in clean_subject and "Britt Rice Ele" in clean_subject:
                        # Extract "Britt Rice Ele" part
                        if "//" in clean_subject:
                            parts = clean_subject.split("//")
                            if len(parts) > 0:
                                client_part = parts[0].split(":")[-1].strip()
                                if "Britt Rice Ele" in client_part:
                                    client_name = "Britt Rice Ele"
                    
                    # Pattern 2: "Neev//BLOX FED-IRF Check-in" → "BLOX FED-IRF"
                    elif "BLOX FED-IRF" in clean_subject:
                        if "//" in clean_subject:
                            parts = clean_subject.split("//")
                            if len(parts) > 1:
                                client_name = parts[1].split("Check-in")[0].strip()
                                if not client_name:
                                    client_name = "BLOX FED-IRF"
                            else:
                                client_name = "BLOX FED-IRF"
                        else:
                            client_name = "BLOX FED-IRF"
                    
                    # Pattern 3: General colon-separated subjects
                    elif ':' in clean_subject:
                        potential_client = clean_subject.split(':')[0].strip()
                        # Clean up common prefixes
                        if potential_client and potential_client.lower() not in ["project sync-up", "canceled"]:
                            client_name = potential_client
                    
                    # Pattern 4: Extract meaningful part from subject
                    if not client_name or client_name.strip() == '' or client_name == 'Unknown Client':
                        # For subjects like "Neev//BLOX FED-IRF Check-in", use the first meaningful part
                        parts = clean_subject.split()
                        if parts:
                            # Take first 2-3 words as client name
                            client_name = " ".join(parts[:2]) if len(parts) > 1 else parts[0]
                
                # Final fallback
                if not client_name or client_name.strip() == '' or client_name == 'Unknown Client':
                    client_name = 'Client'
            
            if client_name not in client_groups:
                client_groups[client_name] = []
            pulse_report = row['pulse_report'] if USE_POSTGRES else row[2]
            client_groups[client_name].append(pulse_report)
        
        logger.info(f"📋 Found {len(all_pulse_reports)} pulse reports across {len(client_groups)} clients")
        
        reports_generated = 0
        emails_sent = 0
        errors = []
        
        # Process each client group
        for client_name, pulse_reports_list in client_groups.items():
            try:
                logger.info(f"🔄 Aggregating {len(pulse_reports_list)} pulse reports for client: {client_name}")
                
                # Generate aggregated report using LLM
                aggregated_report = summarizer.aggregate_pulse_reports(
                    pulse_reports_list,
                    client_name=client_name,
                    date_range=date_range
                )
                
                # Save aggregated report to new aggregated_pulse_reports table
                db.save_aggregated_pulse_report(
                    client_name=client_name,
                    date_range_start=start_date_str,
                    date_range_end=end_date_str,
                    aggregated_report_text=aggregated_report,
                    individual_reports_count=len(pulse_reports_list)
                )
                reports_generated += 1
                logger.info(f"✅ Aggregated pulse report saved for client: {client_name}")
                
                # Send email to EMAIL_TEST_RECIPIENT only
                email_recipient = os.getenv("EMAIL_TEST_RECIPIENT", "")
                if email_recipient and SEND_EMAILS:
                    try:
                        # Authenticate if needed for email
                        if USE_DELEGATED_AUTH:
                            client = GraphAPIClientDelegatedRefresh()
                            if not client.authenticate():
                                logger.warning("Failed to authenticate for email sending")
                                continue
                        else:
                            client = GraphAPIClientAppOnly()
                            if not client.authenticate():
                                logger.warning("Failed to authenticate for email sending")
                                continue
                        
                        # Prepare email
                        email_subject = f"15-Day Client Pulse Report: {client_name} ({date_range})"
                        
                        if USE_DELEGATED_AUTH:
                            if send_summary_email and send_summary_email(
                                graph_client=client,
                                recipient_email=email_recipient,
                                meeting_subject=email_subject,
                                meeting_date=date_range,
                                summary_text=aggregated_report,
                                model_name="Claude Opus 4.5",
                                organizer_participants=[]
                            ):
                                emails_sent += 1
                                logger.info(f"📧 Aggregated pulse report email sent to {email_recipient} for client: {client_name}")
                        else:
                            if EMAIL_SENDER_USER_ID and send_summary_email_apponly and send_summary_email_apponly(
                                graph_client=client,
                                sender_user_id=EMAIL_SENDER_USER_ID,
                                recipient_email=email_recipient,
                                meeting_subject=email_subject,
                                meeting_date=date_range,
                                summary_text=aggregated_report,
                                model_name="Claude Opus 4.5",
                                participants=[]
                            ):
                                emails_sent += 1
                                logger.info(f"📧 Aggregated pulse report email sent to {email_recipient} for client: {client_name}")
                    except Exception as e:
                        error_msg = f"Email failed for {client_name}: {e}"
                        logger.warning(f"📧 {error_msg}")
                        errors.append(error_msg)
                
            except Exception as e:
                error_msg = f"Error processing client {client_name}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
                continue
        
        db.close()
        
        logger.info(f"✅ Pulse report generation complete: {reports_generated} aggregated reports generated, {emails_sent} emails sent for {len(client_groups)} clients")
        if errors:
            logger.warning(f"Errors during processing: {errors}")
        
    except Exception as e:
        logger.error(f"Error in background pulse report generation: {e}")
        import traceback
        logger.error(traceback.format_exc())


@app.route("/generate-pulse-report", methods=["GET", "POST"])
@require_api_key
def generate_pulse_report():
    """
    Generate aggregated client pulse reports for the last 15 days.
    Groups by client_name, aggregates all client_pulse summaries, and sends email to EMAIL_TEST_RECIPIENT.
    
    This endpoint now processes in the background to avoid HTTP timeout errors.
    Returns immediately with a status message.
    """
    try:
        # Validate dependencies before starting background thread
        if DatabaseManager is None:
            return jsonify({"error": "DatabaseManager not available"}), 500
        
        if not SUMMARIZER_AVAILABLE or ClaudeSummarizer is None:
            return jsonify({"error": "Summarizer not available"}), 500
        
        # Start background processing
        thread = threading.Thread(target=_process_pulse_reports_background, daemon=True)
        thread.start()
        
        # Return immediately
        return jsonify({
            "status": "processing",
            "message": "Pulse report generation started in background. Check Railway logs for completion status.",
            "note": "Processing may take 1-3 minutes. Check logs, database, and email for results."
        }), 202  # 202 Accepted - request accepted for processing
        
    except Exception as e:
        logger.error(f"Error starting pulse report generation: {e}")
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
    import sys
    
    # Check if we should run processing directly
    run_process = os.getenv("RUN_PROCESS", "false").lower() == "true"
    
    # Also check command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--process" or sys.argv[1] == "-p":
            run_process = True
    
    if run_process:
        # Run meeting processing directly
        print("=" * 70)
        print("Running meeting processing locally...")
        print("=" * 70)
        result = run_meeting_processing()
        if "error" in result:
            print(f"\n❌ Error: {result['error']}")
            sys.exit(1)
        else:
            print("\n✅ Processing complete!")
            print(f"   Meetings found: {result.get('meetings_found', 0)}")
            print(f"   Meetings processed: {result.get('meetings_processed', 0)}")
            print(f"   Transcripts saved: {result.get('transcripts_saved', 0)}")
            print(f"   Summaries generated: {result.get('summaries_generated', 0)}")
            print(f"   Pulse reports generated: {result.get('pulse_reports_generated', 0)}")
            print(f"   Emails sent: {result.get('emails_sent', 0)}")
            print(f"   Skipped: {result.get('skipped', 0)}")
            print(f"   No transcript: {result.get('no_transcript', 0)}")
            print(f"\n{result.get('message', '')}")
            sys.exit(0)
    elif os.getenv("RUN_SERVER", "false").lower() == "true":
        # Start the web server
        port = int(os.getenv("PORT", 8080))
        app.run(host="127.0.0.1", port=port)
    else:
        print("Flask app loaded. Options:")
        print("  - Set RUN_PROCESS=true or run with --process to process meetings")
        print("  - Set RUN_SERVER=true to start the web server")
        print("  - App is ready for testing or importing without starting a server.")

