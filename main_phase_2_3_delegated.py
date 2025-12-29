"""
Phase 2.3 - Fetch Meeting Transcripts using Delegated Authentication (MSAL)
           + Generate Summaries using Ollama + GPT-OSS-Safeguard (LOCAL MODEL)

This script:
1. Authenticates you interactively (device code flow via MSAL)
2. Scans your Teams meetings for transcripts
3. Downloads and saves transcripts to the database
4. Generates AI summaries using local Ollama + gpt-oss-safeguard:20b (no cloud, data stays private)
5. Saves summaries back to the database
6. Sends summary emails to meeting organizers

gpt-oss-safeguard:20b model: 20B parameters, high quality summaries
No API keys needed - 100% local and private!

Configuration (via environment variables):
- SKIP_SUMMARIES: Set to 'false' to enable summary generation (default: 'true' - skipped)
- DAYS_BACK: Number of days to look back for meetings (default: 15, excluding today)

Example: SKIP_SUMMARIES=false DAYS_BACK=15 python main_phase_2_3_delegated.py
"""
import os
from datetime import datetime
from src.api.graph_client_delegated import GraphAPIClientDelegated
from src.api.transcript_fetcher_delegated import TranscriptFetcherDelegated
from src.database.db_setup_sqlite import DatabaseManager, normalize_datetime_string
from src.utils.logger import setup_logger
from src.summarizer.ollama_mistral_summarizer import OllamaMistralSummarizer  # Still works - alias support
from src.utils.email_sender import send_summary_to_organizer

logger = setup_logger(__name__)

# ====================================================================
# HELPER FUNCTIONS
# ====================================================================
def parse_graph_datetime(dt_string):
    """
    Parse datetime string from Microsoft Graph API.
    Graph API returns datetimes with 7 decimal places (.0000000),
    but Python's fromisoformat() only supports up to 6 decimal places.
    
    Args:
        dt_string: Datetime string from Graph API (e.g., '2025-12-03T14:00:00.0000000')
    
    Returns:
        datetime object, or None if parsing fails
    """
    if not dt_string:
        return None
    
    try:
        # Remove timezone 'Z' if present and replace with +00:00
        dt_string = dt_string.replace("Z", "+00:00")
        
        # Fix microseconds: Graph uses 7 digits, Python supports max 6
        # Find the decimal point in the time portion
        if "." in dt_string and "+" in dt_string:
            # Split into: time part with microseconds, and timezone part
            parts = dt_string.split("+")
            time_part = parts[0]
            tz_part = "+" + parts[1]
            
            # Split time part into before and after decimal
            time_parts = time_part.split(".")
            if len(time_parts) == 2:
                # Truncate microseconds to 6 digits
                microseconds = time_parts[1][:6]
                dt_string = f"{time_parts[0]}.{microseconds}{tz_part}"
        elif "." in dt_string:
            # No timezone, just truncate microseconds
            time_parts = dt_string.split(".")
            if len(time_parts) == 2:
                microseconds = time_parts[1][:6]
                dt_string = f"{time_parts[0]}.{microseconds}"
        
        return datetime.fromisoformat(dt_string)
    except Exception as e:
        logger.error(f"Failed to parse datetime '{dt_string}': {e}")
        return None

# ====================================================================
# CONFIGURATION
# ====================================================================
# SKIP_SUMMARIES: Set to True to skip summary generation (faster testing)
# DAYS_BACK: Number of days to look back for meetings (default: 15, excluding today)
# TEST_MEETING_FILTER: For testing - only summarize meetings with this text in subject (set to None to summarize all)
SKIP_SUMMARIES = os.getenv("SKIP_SUMMARIES", "false").lower() == "true"
DAYS_BACK = int(os.getenv("DAYS_BACK", "15"))  # Fetch meetings from last 15 days by default (excluding today)

# ⚠️ TESTING MODE: Only summarize meetings matching this subject and date/time
# Set to None or empty string to summarize ALL meetings (normal operation)
TEST_MEETING_FILTER = None  # Set to None to summarize ALL meetings


def main():
    """Main execution"""
    
    mode_label = "SKIP SUMMARIES" if SKIP_SUMMARIES else "WITH SUMMARIES (GPT-OSS-SAFEGUARD:20B)"
    logger.info("=" * 70)
    logger.info(f"PHASE 2.3 - FETCH MEETING TRANSCRIPTS ({mode_label})")
    logger.info("=" * 70)
    logger.info(f"   Configuration: Fetching ALL meetings from last {DAYS_BACK} days (excluding today)")
    
    if SKIP_SUMMARIES:
        logger.warning("⚠️  SUMMARIES SKIPPED - Set SKIP_SUMMARIES=false to enable summary generation")
    else:
        logger.info("✓  Summary generation enabled with gpt-oss-safeguard:20b model (local, private)")
        if TEST_MEETING_FILTER:
            logger.warning(f"🧪 TESTING MODE: Only summarizing specific meeting:")
            logger.warning(f"   Subject: '{TEST_MEETING_FILTER.get('subject')}'")
            logger.warning(f"   Date: {TEST_MEETING_FILTER.get('date')}")
            logger.warning(f"   Time: {TEST_MEETING_FILTER.get('time')}")
            logger.warning(f"   Set TEST_MEETING_FILTER = None to summarize ALL meetings")

    # ====================================================================
    # Step 1: Authenticate
    # ====================================================================
    logger.info("\n[Step 1] Authenticating with Microsoft Graph...")
    client = GraphAPIClientDelegated()
    
    if not client.authenticate():
        logger.error("❌ Authentication failed. Exiting.")
        return False
    
    logger.info("✅ Authentication successful - using your user context")

    # ====================================================================
    # Step 2: Connect to Database
    # ====================================================================
    logger.info("\n[Step 2] Connecting to database...")
    db = DatabaseManager()
    
    if not db.connect():
        logger.error("❌ Database connection failed. Exiting.")
        return False
    
    if not db.create_tables():
        logger.error("❌ Failed to create database tables.")
        db.close()
        return False
    
    logger.info("✅ Connected to database")

    # ====================================================================
    # Step 3: Initialize Summarizer (if enabled)
    # ====================================================================
    if SKIP_SUMMARIES:
        logger.info("\n[Step 3] Skipping summarizer initialization (SKIP_SUMMARIES=true)")
        summarizer = None
    else:
        logger.info("\n[Step 3] Initializing Ollama + GPT-OSS-Safeguard summarizer (local network server)...")
        try:
            # Use gpt-oss-safeguard:20b model on local network server
            # Local Ollama server: http://192.168.2.180:11434
            REMOTE_OLLAMA_URL = "http://192.168.2.180:11434"
            summarizer = OllamaMistralSummarizer(
                base_url=REMOTE_OLLAMA_URL,
                model="gpt-oss-safeguard:20b"
            )
            
            if not summarizer.is_ollama_running():
                logger.warning(f"⚠️  Ollama is not running on {REMOTE_OLLAMA_URL}. Skipping summaries.")
                logger.warning("   To enable:")
                logger.warning("   1. Check if the Ollama server is accessible")
                logger.warning("   2. Ensure Ollama is running on 192.168.2.180:11434")
                summarizer = None
            else:
                logger.info(f"✅ Ollama + gpt-oss-safeguard:20b ready on local network server ({REMOTE_OLLAMA_URL})")
                logger.info("   Model: gpt-oss-safeguard:20b (20B parameters) - High quality summaries")
                logger.info("   Server: Local network (potentially faster with GPU/better CPU)")
        except Exception as e:
            logger.warning(f"⚠️  Could not initialize summarizer: {e}")
            logger.warning("   Transcripts will be fetched but NOT summarized.")
            summarizer = None

    # ====================================================================
    # Step 4: Create Fetcher and Scan for Meetings
    # ====================================================================
    logger.info("\n[Step 4] Scanning for meetings...")
    fetcher = TranscriptFetcherDelegated(client)
    
    # include_all=True means we get ALL meetings, even without transcripts
    # days_back=DAYS_BACK means we fetch from last N days (excluding today, no limit on count)
    logger.info(f"   Fetching ALL meetings from last {DAYS_BACK} days (excluding today, with or without transcripts)...")
    meetings = fetcher.list_all_meetings_with_transcripts(days_back=DAYS_BACK, include_all=True)

    if not meetings:
        logger.info("\n⚠️  No meetings with transcripts found.")
        logger.info("💡 Tip: Transcripts may take 5-20 minutes to process after a meeting ends.")
        logger.info("💡 Make sure you were in the meeting (as organizer or attendee).")
        db.close()
        return True

    meetings_with_transcripts = [m for m in meetings if m.get("has_transcript")]
    logger.info(f"\n✅ Found {len(meetings)} meeting(s) ({len(meetings_with_transcripts)} with transcripts)")

    # ====================================================================
    # Step 5: Fetch, Summarize and Save Transcripts
    # ====================================================================
    logger.info(f"\n[Step 5] Processing {len(meetings)} meetings...")
    
    saved = 0
    skipped = 0
    failed = 0
    summarized = 0
    summary_failed = 0
    emails_sent = 0
    emails_failed = 0
    
    for idx, meeting in enumerate(meetings, 1):
        meeting_id = meeting["meeting_id"]
        subject = meeting["subject"]
        transcript_count = meeting["transcript_count"]
        has_transcript = meeting.get("has_transcript", False)
        
        logger.info(f"\n--- [{idx}/{len(meetings)}] {subject} ---")
        logger.info(f"    Transcripts available: {transcript_count}")
        
        try:
            # ========================================================
            # Save Meeting Metadata (participants, times, etc.)
            # ========================================================
            # Calculate duration if start and end times are available
            duration_minutes = None
            if meeting.get("start_time") and meeting.get("end_time"):
                start = parse_graph_datetime(meeting["start_time"])
                end = parse_graph_datetime(meeting["end_time"])
                if start and end:
                    duration_minutes = int((end - start).total_seconds() / 60)
            
            meeting_data = {
                "meeting_id": meeting_id,
                "subject": meeting.get("subject", "Untitled Meeting"),
                "client_name": meeting.get("client_name"),
                "organizer_email": meeting.get("organizer_email"),
                "participants": meeting.get("participants", []),
                "start_time": meeting.get("start_time"),
                "end_time": meeting.get("end_time"),
                "duration_minutes": duration_minutes,
                "join_url": meeting.get("join_url")
            }
            db.insert_meeting(meeting_data)
            
            # ========================================================
            # Fetch Transcript (only if available)
            # ========================================================
            if not has_transcript:
                logger.info(f"    ⚠️  No transcript available for this meeting")
                skipped += 1
                continue
            
            # Get start_time for this meeting instance to fetch the correct transcript
            meeting_start_time = meeting.get("start_time")
            bundle = fetcher.fetch_transcript_for_meeting(meeting_id, start_time=meeting_start_time)
            
            # Validate transcript exists and has meaningful content
            transcript_text = bundle.get("transcript") if bundle else None
            if not transcript_text:
                logger.warning(f"    ⚠️  No transcript content returned for this meeting")
                failed += 1
            elif not isinstance(transcript_text, str) or not transcript_text.strip() or len(transcript_text.strip()) <= 50:
                logger.warning(f"    ⚠️  Transcript too short or empty (length: {len(transcript_text.strip()) if transcript_text else 0} chars, minimum required: 50)")
                failed += 1
            else:
                # Save transcript to database
                # Get start_time for this meeting instance
                meeting_start_time = meeting.get("start_time")
                success = db.save_meeting_transcript(
                    meeting_id=meeting_id,
                    transcript_text=transcript_text,
                    chat_text=bundle.get("chat"),
                    source_url=bundle.get("source"),
                    start_time=meeting_start_time
                )
                
                if success:
                    saved += 1
                    transcript_len = len(transcript_text)
                    logger.info(f"    ✅ Saved transcript ({transcript_len} chars)")
                    
                    # ================================================
                    # Generate Summary (if Ollama is available)
                    # ================================================
                    # Check if this meeting matches the test filter
                    should_summarize = True
                    if TEST_MEETING_FILTER:
                        # Check subject, date, and time
                        subject_match = TEST_MEETING_FILTER.get("subject", "") in subject
                        
                        # Parse start_time to check date and time
                        date_match = True
                        time_match = True
                        if meeting.get("start_time"):
                            start_dt = parse_graph_datetime(meeting["start_time"])
                            if start_dt:
                                if TEST_MEETING_FILTER.get("date"):
                                    date_match = start_dt.strftime("%Y-%m-%d") == TEST_MEETING_FILTER["date"]
                                if TEST_MEETING_FILTER.get("time"):
                                    time_match = start_dt.strftime("%H:%M") == TEST_MEETING_FILTER["time"]
                        
                        should_summarize = subject_match and date_match and time_match
                        
                        if not should_summarize:
                            logger.info(f"    ⏭️  Skipping summary (not matching test filter)")
                        else:
                            logger.info(f"    ✅ Match found! Subject: '{TEST_MEETING_FILTER.get('subject')}', Date: {TEST_MEETING_FILTER.get('date')}, Time: {TEST_MEETING_FILTER.get('time')}")
                    
                    if summarizer and should_summarize:
                        # Check if summary already exists for this meeting
                        meeting_start_time = meeting.get("start_time")
                        # Normalize start_time to match database format before checking
                        normalized_start_time = normalize_datetime_string(meeting_start_time) if meeting_start_time else None
                        existing_summary = db.get_meeting_summary(meeting_id, start_time=normalized_start_time)
                        
                        if existing_summary and existing_summary.get("summary_text"):
                            logger.info(f"    ⏭️  Summary already exists in database, skipping generation")
                            logger.info(f"       (Summary created: {existing_summary.get('created_at', 'Unknown')})")
                            # Still count as summarized since we have a summary
                            summarized += 1
                        else:
                            try:
                                logger.info(f"    📝 Generating summary (using gpt-oss-safeguard:20b model)...")
                                
                                # Generate structured summary (no satisfaction analysis for now)
                                result = summarizer.summarize(
                                    transcription=transcript_text,
                                    summary_type="structured",
                                    include_satisfaction=False
                                )
                            
                                # Handle tuple return (summary, satisfaction_analysis)
                                if isinstance(result, tuple):
                                    summary, satisfaction_analysis = result
                                else:
                                    summary = result
                                    satisfaction_analysis = None
                                
                                # Save summary to database
                                # Get start_time for this meeting instance
                                meeting_start_time = meeting.get("start_time")
                                summary_success = db.save_meeting_summary(
                                    meeting_id=meeting_id,
                                    summary_text=summary,
                                    summary_type="structured",
                                    start_time=meeting_start_time
                                )
                            
                                # Save satisfaction analysis if available
                                if satisfaction_analysis:
                                    try:
                                        db.save_satisfaction_analysis(meeting_id, satisfaction_analysis)
                                        logger.info(f"    ✅ Satisfaction analysis saved")
                                    except Exception as e:
                                        logger.warning(f"    ⚠️  Failed to save satisfaction analysis: {e}")
                                
                                if summary_success:
                                    summarized += 1
                                    logger.info(f"    ✅ Summary saved to database")
                                    
                                    # ================================================
                                    # Send Summary Email to Organizer
                                    # ================================================
                                    organizer_email = meeting.get("organizer_email")
                                    if organizer_email:
                                        try:
                                            # Format meeting date for email
                                            meeting_date_str = "Unknown"
                                            if meeting.get("start_time"):
                                                start_dt = parse_graph_datetime(meeting["start_time"])
                                                if start_dt:
                                                    meeting_date_str = start_dt.strftime("%Y-%m-%d %H:%M UTC")
                                                else:
                                                    meeting_date_str = str(meeting.get("start_time", "Unknown"))
                                            
                                            # Send email to organizer and all organizer participants with model name
                                            email_sent = send_summary_to_organizer(
                                                graph_client=client,
                                                organizer_email=organizer_email,
                                                meeting_subject=subject,
                                                meeting_date=meeting_date_str,
                                                summary_text=summary,
                                                meeting_id=meeting_id,
                                                model_name="GPT-OSS-Safeguard:20b",
                                                participants=meeting.get("participants", [])
                                            )
                                            
                                            if email_sent:
                                                emails_sent += 1
                                            else:
                                                emails_failed += 1
                                        except Exception as e:
                                            emails_failed += 1
                                            logger.warning(f"    ⚠️  Failed to send email: {str(e)}")
                                    else:
                                        logger.warning(f"    ⚠️  No organizer email found, skipping email send")
                                else:
                                    summary_failed += 1
                                    logger.warning(f"    ⚠️  Failed to save summary to database")
                                    
                            except Exception as e:
                                summary_failed += 1
                                logger.warning(f"    ⚠️  Summary generation failed: {str(e)}")
                else:
                    failed += 1
                    logger.warning(f"    ⚠️  Failed to save transcript to database")
            else:
                failed += 1
                logger.warning(f"    ⚠️  Could not fetch transcript content")
                
        except Exception as e:
            failed += 1
            logger.error(f"    ❌ Error: {str(e)}")

    # ====================================================================
    # Summary
    # ====================================================================
    logger.info("\n" + "=" * 70)
    logger.info("PHASE 2.3 SUMMARY")
    logger.info("=" * 70)
    logger.info(f"   Meetings scanned: {len(meetings)}")
    logger.info(f"   Meetings with transcripts: {len(meetings_with_transcripts)}")
    logger.info(f"   Meetings without transcripts: {skipped}")
    logger.info(f"   Transcripts saved: {saved}")
    logger.info(f"   Transcripts failed: {failed}")
    if summarizer:
        logger.info(f"   Summaries generated: {summarized}")
        logger.info(f"   Summary failures: {summary_failed}")
        logger.info(f"   Emails sent: {emails_sent}")
        logger.info(f"   Email failures: {emails_failed}")
        logger.info(f"   Model used: gpt-oss-safeguard:20b (20B parameters) - High quality summaries")
        logger.info(f"   Privacy: 100% local, no cloud, data stays on your machine")
    else:
        logger.info(f"   Summaries: SKIPPED (Set SKIP_SUMMARIES=false to enable)")
    logger.info(f"   Run completed at: {datetime.utcnow().isoformat()}Z")
    logger.info("=" * 70)

    db.close()
    
    if saved > 0:
        logger.info(f"\n✅ SUCCESS! Phase 2.3 complete.")
        logger.info(f"   {saved} transcript(s) saved to database.")
        if summarizer and summarized > 0:
            logger.info(f"   {summarized} summary/summaries generated and saved.")
        return True
    else:
        logger.warning(f"\n⚠️  Phase 2.3 complete but no transcripts were saved.")
        logger.warning(f"   Check the logs above for details.")
        return True


if __name__ == "__main__":
    try:
        success = main()
        if not success:
            logger.error("\n❌ Phase 2.3 failed.")
            exit(1)
    except KeyboardInterrupt:
        logger.info("\n⚠️  Process interrupted by user.")
        exit(1)
    except Exception as e:
        logger.error(f"\n❌ Unexpected error: {str(e)}")
        exit(1)