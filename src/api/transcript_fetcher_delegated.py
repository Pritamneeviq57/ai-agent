"""
Transcript Fetcher using Delegated Authentication.

Uses calendar events to find meetings you ATTENDED (not just organized).
Then fetches transcripts via the organizer's onlineMeeting.
"""
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from urllib.parse import quote
from src.api.graph_client_delegated import GraphAPIClientDelegated
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Get timezone from environment variable (default: UTC)
# Examples: "UTC", "Asia/Kolkata", "America/New_York", "Europe/London"
TIMEZONE_NAME = os.getenv("TIMEZONE", "UTC")

# Try to use zoneinfo (Python 3.9+) or pytz as fallback
_zoneinfo_available = False
_pytz_available = False
_UTC_TZ = None

try:
    from zoneinfo import ZoneInfo
    _zoneinfo_available = True
    _UTC_TZ = ZoneInfo("UTC")
except ImportError:
    try:
        import pytz
        _pytz_available = True
        _UTC_TZ = pytz.UTC
    except ImportError:
        logger.warning("Neither zoneinfo nor pytz available. Using UTC. Install pytz: pip install pytz")

def get_timezone():
    """Get the configured timezone object."""
    if _zoneinfo_available:
        return ZoneInfo(TIMEZONE_NAME)
    elif _pytz_available:
        return pytz.timezone(TIMEZONE_NAME)
    else:
        return None

def get_now_in_timezone():
    """Get current time in the configured timezone."""
    tz = get_timezone()
    if tz:
        return datetime.now(tz)
    else:
        # Fallback to UTC if timezone not available
        return datetime.utcnow()

def to_utc(dt):
    """Convert datetime to UTC."""
    if dt.tzinfo is None:
        # Assume UTC if no timezone info
        return dt
    if _UTC_TZ:
        if _zoneinfo_available:
            return dt.astimezone(_UTC_TZ)
        elif _pytz_available:
            return dt.astimezone(_UTC_TZ)
    # Fallback: remove timezone and assume UTC
    return dt.replace(tzinfo=None)


class TranscriptFetcherDelegated:
    """Fetch transcripts for meetings you attended using calendar events."""

    def __init__(self, graph_client: GraphAPIClientDelegated):
        self.client = graph_client

    def list_all_meetings_with_transcripts(self, days_back: int = 2, include_all: bool = False, limit: int = None, user_id: str = None) -> List[Dict]:
        """
        Find meetings with transcripts by:
        1. Get calendar events with Teams meetings (past N days)
        2. For each event, get the onlineMeeting via joinWebUrl
        3. Check if transcripts exist
        
        Args:
            days_back: Number of days to look back (default: 2)
            include_all: If True, returns all meetings even without transcripts
            limit: Optional maximum number of meetings to return (None = all in date range)
            user_id: Optional user ID or email to fetch calendar for. If None, uses /me endpoint.
        """
        mode = "all meetings" if include_all else "meetings with transcripts"
        user_info = f" for user {user_id}" if user_id else ""
        logger.info(f"Scanning calendar{user_info} for {mode} from last {days_back} days (including today up to now){f' (limit: {limit})' if limit else ''}...")
        
        meetings_list = []
        meetings_count = 0
        
        # Get calendar events from the past N days (including today up to now)
        # Use configured timezone instead of UTC
        now = get_now_in_timezone()
        start_time = now - timedelta(days=days_back)
        end_time = now  # Include today up to current time
        
        # Convert to UTC for Graph API (which expects UTC)
        start_time_utc = to_utc(start_time)
        end_time_utc = to_utc(end_time)
        
        # Format times for Graph API (always in UTC)
        start_str = start_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        logger.info(f"📅 Using timezone: {TIMEZONE_NAME}")
        if now.tzinfo:
            logger.info(f"📅 Local time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            logger.info(f"📅 Local time: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC)")
        logger.info(f"📅 Fetching meetings from {start_str} (UTC) to {end_str} (UTC)")
        
        # Get calendar events (filter in code since isOnlineMeeting doesn't support filtering)
        # Include attendees to get participant information
        # Handle pagination if there are more than 100 events
        all_events = []
        # Use /users/{user_id}/calendarView if user_id is provided, otherwise use /me/calendarView
        if user_id:
            endpoint = f"/users/{user_id}/calendarView?startDateTime={start_str}&endDateTime={end_str}&$select=id,subject,start,end,isOnlineMeeting,onlineMeeting,organizer,attendees&$top=100"
        else:
            endpoint = f"/me/calendarView?startDateTime={start_str}&endDateTime={end_str}&$select=id,subject,start,end,isOnlineMeeting,onlineMeeting,organizer,attendees&$top=100"
        
        logger.info(f"Fetching calendar events from {start_str} to {end_str}...")
        endpoint_base = endpoint.split('?')[0]
        logger.info(f"Using endpoint: {endpoint_base}")
        
        # Handle pagination
        while endpoint:
            try:
                response = self.client.make_request("GET", endpoint)
                
                if not response:
                    logger.warning("No calendar events found or error occurred - response was None")
                    if user_id:
                        logger.warning(f"⚠️  Note: Accessing another user's calendar ({user_id}) may require additional permissions.")
                        logger.warning(f"    With delegated auth, you need 'Calendars.Read' permission for that user.")
                        logger.warning(f"    Consider using /me endpoint if this is your own calendar.")
                    break
                
                # Check if response contains an error
                if isinstance(response, dict) and "error" in response:
                    error_code = response.get("error", {}).get("code", "Unknown")
                    error_message = response.get("error", {}).get("message", "Unknown error")
                    logger.error(f"Graph API error: {error_code} - {error_message}")
                    logger.error(f"Full error response: {response}")
                    if user_id and "Forbidden" in error_message or "403" in str(error_code):
                        logger.error(f"⚠️  Permission denied accessing user {user_id}'s calendar.")
                        logger.error(f"    With delegated auth, you can only access calendars you have permission to read.")
                    break
                
                events_batch = response.get("value", [])
                if events_batch:
                    logger.info(f"Received {len(events_batch)} events in this batch")
                all_events.extend(events_batch)
                
                # Check for next page
                endpoint = response.get("@odata.nextLink")
                if endpoint:
                    logger.debug(f"Fetching next page of calendar events...")
            except Exception as e:
                logger.error(f"Exception while fetching calendar events: {e}")
                logger.exception(e)  # Log full traceback
                break
        
        # Filter to only online meetings
        events = [e for e in all_events if e.get("isOnlineMeeting")]
        logger.info(f"Found {len(events)} Teams meetings in calendar (out of {len(all_events)} total events)")
        
        for event in events:
            subject = event.get("subject", "Untitled")
            online_meeting = event.get("onlineMeeting", {})
            join_url = online_meeting.get("joinUrl") if online_meeting else None
            
            if not join_url:
                logger.debug(f"Skipping '{subject}' - no join URL")
                continue
            
            logger.debug(f"Checking '{subject}' for transcripts...")
            
            # Get the onlineMeeting ID from the joinUrl
            meeting_info = self._get_meeting_from_join_url(join_url)
            
            if not meeting_info:
                continue
            
            meeting_id = meeting_info.get("id")
            
            # Check for transcripts
            transcripts = self._list_transcripts(meeting_id)
            has_transcript = len(transcripts) > 0
            
            # Extract attendees/participants
            attendees = event.get("attendees", [])
            participants = []
            for attendee in attendees:
                email_address = attendee.get("emailAddress", {})
                participants.append({
                    "name": email_address.get("name", ""),
                    "email": email_address.get("address", ""),
                    "type": attendee.get("type", ""),
                    "response": attendee.get("status", {}).get("response", "")
                })
            
            # Extract organizer
            organizer = event.get("organizer", {})
            organizer_email = organizer.get("emailAddress", {}).get("address", "")
            organizer_name = organizer.get("emailAddress", {}).get("name", "")
            
            # Extract start and end times from Graph API
            # Graph API returns: {"dateTime": "2025-12-03T07:50:00.0000000", "timeZone": "UTC"}
            start_info = event.get("start", {})
            end_info = event.get("end", {})
            start_time = start_info.get("dateTime") if start_info else None
            end_time = end_info.get("dateTime") if end_info else None
            
            # Log transcript status
            if has_transcript:
                logger.info(f"✓ Found {len(transcripts)} transcript(s) for '{subject}'")
            else:
                logger.debug(f"No transcripts for '{subject}'")
            
            # Include meeting if: has transcripts OR include_all mode
            if has_transcript or include_all:
                meetings_list.append({
                    "meeting_id": meeting_id,
                    "subject": subject,
                    "join_url": join_url,
                    "transcript_count": len(transcripts),
                    "transcripts": transcripts,
                    "has_transcript": has_transcript,
                    "participants": participants,
                    "organizer_email": organizer_email,
                    "organizer_name": organizer_name,
                    "client_name": organizer_name,  # Use organizer name as client name for now
                    "start_time": start_time,
                    "end_time": end_time
                })
                
                meetings_count += 1
                
                # Check if we've reached the optional limit
                if limit and meetings_count >= limit:
                    logger.info(f"✓ Reached limit of {limit} meetings, stopping scan")
                    break
        
        return meetings_list

    def _get_meeting_from_join_url(self, join_url: str) -> Optional[Dict]:
        """Get onlineMeeting details from join URL."""
        # Encode the join URL for the filter
        encoded_url = quote(join_url, safe='')
        
        # Use filter to find the meeting by joinWebUrl
        endpoint = f"/me/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'"
        
        response = self.client.make_request("GET", endpoint)
        
        if response and response.get("value"):
            return response["value"][0]
        
        # Try alternate approach - decode meeting ID from join URL
        meeting_id = self._extract_meeting_id_from_url(join_url)
        if meeting_id:
            endpoint = f"/me/onlineMeetings/{meeting_id}"
            response = self.client.make_request("GET", endpoint)
            if response and response.get("id"):
                return response
        
        return None

    def _extract_meeting_id_from_url(self, join_url: str) -> Optional[str]:
        """Extract meeting ID from Teams join URL."""
        # Teams URLs contain encoded meeting info
        # Format: https://teams.microsoft.com/l/meetup-join/...
        try:
            if "19:meeting_" in join_url or "19%3ameeting_" in join_url.lower():
                # Extract the meeting thread ID
                import re
                match = re.search(r'19[:%]3[aA]meeting_[^/&]+', join_url)
                if match:
                    return match.group(0).replace('%3a', ':').replace('%3A', ':')
        except Exception as e:
            logger.debug(f"Could not extract meeting ID: {e}")
        return None

    def _list_transcripts(self, meeting_id: str) -> List[Dict]:
        """List transcripts for a meeting."""
        endpoint = f"/me/onlineMeetings/{meeting_id}/transcripts"
        
        response = self.client.make_request("GET", endpoint)
        
        if response and response.get("value"):
            return response["value"]
        
        return []

    def fetch_transcript_for_meeting(self, meeting_id: str, start_time: str = None) -> Optional[Dict[str, Optional[str]]]:
        """
        Fetch the transcript content for a specific meeting instance.
        
        For recurring meetings, this method tries to match transcripts to the specific
        meeting instance based on start_time. If multiple transcripts exist, it will
        select the one that best matches the meeting start_time.
        
        Args:
            meeting_id: Teams meeting ID
            start_time: Meeting start time (ISO format string) - used to match the correct transcript for recurring meetings
        
        Returns:
            dict with 'transcript', 'chat', 'source' keys, or None if no transcript found
        """
        from datetime import datetime, timedelta
        
        # Get list of transcripts
        transcripts = self._list_transcripts(meeting_id)
        
        if not transcripts:
            logger.warning(f"No transcripts found for meeting {meeting_id}")
            return None
        
        logger.info(f"Found {len(transcripts)} transcript(s) for meeting {meeting_id}")
        if start_time:
            logger.info(f"  Meeting start_time: {start_time}")
        
        # Log all available transcripts with their dates for debugging
        logger.info(f"  Available transcripts:")
        for idx, t in enumerate(transcripts, 1):
            transcript_id = t.get("id", "NO_ID")[:50]  # First 50 chars of ID
            created_dt = t.get("createdDateTime", "NO_DATE")
            logger.info(f"    {idx}. ID: {transcript_id}... | Created: {created_dt}")
        
        # If start_time is provided, try to match the transcript to the specific meeting instance
        selected_transcripts = []
        
        if start_time:
            # Parse start_time to compare with transcript metadata
            try:
                # Parse the meeting start_time
                if isinstance(start_time, str):
                    # Handle Graph API datetime format (may have 7 decimal places, may or may not have timezone)
                    start_time_clean = start_time
                    
                    # Add timezone if missing
                    if not ("Z" in start_time_clean or "+" in start_time_clean or start_time_clean.count("-") > 2):
                        # No timezone, assume UTC
                        start_time_clean = start_time_clean + "Z"
                    
                    # Replace Z with +00:00 for ISO format
                    start_time_clean = start_time_clean.replace("Z", "+00:00")
                    
                    # Fix microseconds (Graph API uses 7 digits, Python supports max 6)
                    if "." in start_time_clean:
                        if "+" in start_time_clean:
                            parts = start_time_clean.split("+")
                            time_part = parts[0]
                            tz_part = "+" + parts[1]
                        else:
                            # No timezone part
                            time_part = start_time_clean
                            tz_part = ""
                        
                        time_parts = time_part.split(".")
                        if len(time_parts) == 2 and len(time_parts[1]) > 6:
                            microseconds = time_parts[1][:6]  # Truncate to 6 digits
                            start_time_clean = f"{time_parts[0]}.{microseconds}{tz_part}"
                    
                    meeting_start_dt = datetime.fromisoformat(start_time_clean)
                else:
                    meeting_start_dt = start_time
                
                # Try to find transcript(s) that match the meeting start_time
                # IMPORTANT: Match by DATE first, then by time difference
                # This prevents matching transcripts from different days for recurring meetings
                best_match = None
                best_match_diff = None
                meeting_date = meeting_start_dt.date()  # Extract date for matching
                logger.info(f"  🔍 Matching transcript for meeting date: {meeting_date} (start_time: {meeting_start_dt})")
                
                for transcript_obj in transcripts:
                    # Check if transcript has metadata about when it was created/recorded
                    transcript_dt = None
                    
                    # Try createdDateTime first
                    if "createdDateTime" in transcript_obj:
                        try:
                            created_str = transcript_obj["createdDateTime"]
                            created_str = created_str.replace("Z", "+00:00")
                            
                            # Handle microseconds (Graph API uses up to 7 digits, Python supports max 6)
                            if "." in created_str:
                                if "+" in created_str:
                                    parts = created_str.split("+")
                                    time_part = parts[0]
                                    tz_part = "+" + parts[1]
                                    time_parts = time_part.split(".")
                                    if len(time_parts) == 2:
                                        # Truncate or pad microseconds to 6 digits
                                        microseconds = time_parts[1][:6].ljust(6, '0')[:6]
                                        created_str = f"{time_parts[0]}.{microseconds}{tz_part}"
                                else:
                                    # No timezone, but has microseconds
                                    time_parts = created_str.split(".")
                                    if len(time_parts) == 2:
                                        if len(time_parts[1]) > 6:
                                            microseconds = time_parts[1][:6]
                                        else:
                                            # Pad to 6 digits if needed
                                            microseconds = time_parts[1].ljust(6, '0')[:6]
                                        created_str = f"{time_parts[0]}.{microseconds}"
                            
                            transcript_dt = datetime.fromisoformat(created_str)
                        except Exception as e:
                            logger.debug(f"    Failed to parse createdDateTime '{transcript_obj.get('createdDateTime')}': {e}")
                            pass
                    
                    # If we have a transcript datetime, check if it matches the meeting date
                    if transcript_dt:
                        transcript_date = transcript_dt.date()
                        
                        # CRITICAL: Only consider transcripts from the SAME DATE as the meeting
                        if transcript_date == meeting_date:
                            # Same date - calculate time difference
                            diff = abs((transcript_dt - meeting_start_dt).total_seconds())
                            if best_match_diff is None or diff < best_match_diff:
                                best_match = transcript_obj
                                best_match_diff = diff
                                logger.debug(f"    Found date match: {transcript_date} (time diff: {diff:.0f}s)")
                        else:
                            # Different date - skip this transcript
                            logger.debug(f"    Skipping transcript from different date: {transcript_date} (meeting: {meeting_date})")
                
                # If we found a good match (same date and within 1 hour of meeting start), use it
                if best_match and best_match_diff is not None and best_match_diff < 3600:  # 1 hour tolerance
                    selected_transcripts = [best_match]
                    selected_id = best_match.get("id", "NO_ID")[:50]
                    selected_date = best_match.get("createdDateTime", "NO_DATE")
                    logger.info(f"  ✅ Matched transcript to meeting instance (same date, time diff: {best_match_diff:.0f}s)")
                    logger.info(f"     Selected transcript ID: {selected_id}... | Created: {selected_date}")
                else:
                    # No good match found (either no same-date transcript or time diff > 1 hour)
                    # Try to find best match by date first, even if time diff is large
                    if best_match and best_match_diff is not None:
                        # We have a same-date match, but time diff is > 1 hour
                        # Still use it as it's the correct date
                        selected_transcripts = [best_match]
                        selected_id = best_match.get("id", "NO_ID")[:50]
                        selected_date = best_match.get("createdDateTime", "NO_DATE")
                        logger.warning(f"  ⚠️  Found same-date transcript but time diff is large ({best_match_diff:.0f}s = {best_match_diff/3600:.2f} hours), using it anyway")
                        logger.info(f"     Selected transcript ID: {selected_id}... | Created: {selected_date}")
                    else:
                        # No same-date match found - this is problematic
                        # Try to find transcripts with same date but failed parsing
                        same_date_transcripts = []
                        for t in transcripts:
                            if "createdDateTime" in t:
                                try:
                                    created_str = t["createdDateTime"].replace("Z", "+00:00")
                                    if "." in created_str:
                                        if "+" in created_str:
                                            parts = created_str.split("+")
                                            time_part = parts[0]
                                            tz_part = "+" + parts[1]
                                            time_parts = time_part.split(".")
                                            if len(time_parts) == 2:
                                                microseconds = time_parts[1][:6]
                                                created_str = f"{time_parts[0]}.{microseconds}{tz_part}"
                                        else:
                                            time_parts = created_str.split(".")
                                            if len(time_parts) == 2 and len(time_parts[1]) > 6:
                                                microseconds = time_parts[1][:6]
                                                created_str = f"{time_parts[0]}.{microseconds}"
                                    
                                    t_dt = datetime.fromisoformat(created_str)
                                    if t_dt.date() == meeting_date:
                                        same_date_transcripts.append((t, abs((t_dt - meeting_start_dt).total_seconds())))
                                except:
                                    pass
                        
                        if same_date_transcripts:
                            # Sort by time difference and use the closest one
                            same_date_transcripts.sort(key=lambda x: x[1])
                            selected_transcripts = [same_date_transcripts[0][0]]
                            selected_id = same_date_transcripts[0][0].get("id", "NO_ID")[:50]
                            selected_date = same_date_transcripts[0][0].get("createdDateTime", "NO_DATE")
                            logger.warning(f"  ⚠️  Using same-date transcript with time diff: {same_date_transcripts[0][1]:.0f}s")
                            logger.info(f"     Selected transcript ID: {selected_id}... | Created: {selected_date}")
                        else:
                            # No same-date transcript found - try fallback: use most recent transcript if within 7 days
                            # This handles cases where meeting runs past midnight or timezone issues
                            logger.warning(f"  ⚠️  No same-date transcript found for meeting date {meeting_date}")
                            logger.warning(f"     Available transcript dates:")
                            
                            # Collect all transcripts with their dates and time differences
                            all_transcript_candidates = []
                            for t in transcripts:
                                if "createdDateTime" in t:
                                    try:
                                        created_str = t["createdDateTime"].replace("Z", "+00:00")
                                        if "." in created_str:
                                            if "+" in created_str:
                                                parts = created_str.split("+")
                                                time_part = parts[0]
                                                tz_part = "+" + parts[1]
                                                time_parts = time_part.split(".")
                                                if len(time_parts) == 2:
                                                    microseconds = time_parts[1][:6]
                                                    created_str = f"{time_parts[0]}.{microseconds}{tz_part}"
                                            else:
                                                time_parts = created_str.split(".")
                                                if len(time_parts) == 2 and len(time_parts[1]) > 6:
                                                    microseconds = time_parts[1][:6]
                                                    created_str = f"{time_parts[0]}.{microseconds}"
                                        t_dt = datetime.fromisoformat(created_str)
                                        time_diff = abs((t_dt - meeting_start_dt).total_seconds())
                                        date_diff = abs((t_dt.date() - meeting_date).days)
                                        all_transcript_candidates.append((t, t_dt, time_diff, date_diff))
                                        logger.warning(f"       - {t_dt.date()} (created: {t.get('createdDateTime')}, "
                                                    f"time diff: {time_diff/3600:.2f}h, date diff: {date_diff}d)")
                                    except Exception as e:
                                        logger.warning(f"       - Unknown date (created: {t.get('createdDateTime')}, error: {e})")
                            
                            # Fallback: Use most recent transcript if within 7 days and within 24 hours of meeting time
                            if all_transcript_candidates:
                                # Sort by date difference first (prefer closer dates), then by time difference
                                all_transcript_candidates.sort(key=lambda x: (x[3], x[2]))
                                best_fallback = all_transcript_candidates[0]
                                
                                # Only use fallback if date is within 7 days and time is within 24 hours
                                if best_fallback[3] <= 7 and best_fallback[2] <= 86400:  # 7 days, 24 hours
                                    selected_transcripts = [best_fallback[0]]
                                    selected_id = best_fallback[0].get("id", "NO_ID")[:50]
                                    selected_date = best_fallback[0].get("createdDateTime", "NO_DATE")
                                    logger.warning(f"  ⚠️  Using fallback transcript (date diff: {best_fallback[3]}d, "
                                                 f"time diff: {best_fallback[2]/3600:.2f}h)")
                                    logger.info(f"     Selected transcript ID: {selected_id}... | Created: {selected_date}")
                                else:
                                    logger.error(f"  ❌ REJECTING: No suitable transcript found!")
                                    logger.error(f"     Best candidate: date diff {best_fallback[3]}d, time diff {best_fallback[2]/3600:.2f}h (outside tolerance)")
                                    return None
                            else:
                                logger.error(f"  ❌ REJECTING: No transcripts with parseable dates!")
                                return None
            except Exception as e:
                logger.error(f"  ❌ Error matching transcript to meeting instance: {e}")
                logger.error(f"     Cannot safely match transcript without date information - returning None")
                # Do NOT use fallback transcript - it might be from wrong date
                return None
        else:
            # No start_time provided - use most recent transcript if multiple exist
            if len(transcripts) == 1:
                selected_transcripts = transcripts
                logger.info(f"  Using single transcript (no start_time provided for matching)")
            else:
                # Multiple transcripts but no start_time - use most recent
                transcripts_with_time = [t for t in transcripts if "createdDateTime" in t]
                if transcripts_with_time:
                    transcripts_with_time.sort(
                        key=lambda x: x.get("createdDateTime", ""),
                        reverse=True
                    )
                    selected_transcripts = [transcripts_with_time[0]]
                    logger.info(f"  Using most recent transcript (no start_time provided for matching)")
                else:
                    selected_transcripts = transcripts
                    logger.warning(f"  ⚠️  No createdDateTime in transcripts, using ALL {len(transcripts)} transcripts")
        
        # Download selected transcript(s)
        transcript_parts = []
        source_urls = []
        
        for idx, transcript_obj in enumerate(selected_transcripts, 1):
            transcript_id = transcript_obj.get("id")
            
            if not transcript_id:
                logger.debug(f"Skipping transcript {idx} - no ID")
                continue
            
            logger.debug(f"Downloading transcript {idx}/{len(selected_transcripts)}: {transcript_id}")
            
            # Download transcript content (VTT format)
            endpoint = f"/me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content"
            
            # Try to get text/vtt format
            content = self.client.download_content(endpoint, accept="text/vtt")
            
            if not content:
                # Try without accept header
                content = self.client.download_content(endpoint)
            
            if content:
                transcript_text = content.decode("utf-8", errors="ignore")
                if transcript_text.strip():
                    transcript_parts.append(transcript_text)
                    source_urls.append(f"onlineMeetings/{meeting_id}/transcripts/{transcript_id}")
                    logger.debug(f"✓ Successfully downloaded transcript {idx}/{len(selected_transcripts)} ({len(transcript_text)} chars)")
                else:
                    logger.debug(f"Transcript {idx} is empty, skipping")
            else:
                logger.warning(f"Could not download transcript {idx}/{len(selected_transcripts)}: {transcript_id}")
        
        if not transcript_parts:
            logger.warning(f"No transcript content could be downloaded for meeting {meeting_id}")
            return None
        
        # If multiple transcripts were selected, combine them (should be rare now)
        if len(transcript_parts) > 1:
            separator = "\n\n========== Transcript Part {part_num} ==========\n\n"
            combined_parts = []
            for i, part in enumerate(transcript_parts, 1):
                if i > 1:
                    combined_parts.append(separator.format(part_num=i))
                combined_parts.append(part)
            combined_transcript = "".join(combined_parts)
            logger.info(f"✓ Combined {len(transcript_parts)} transcript(s) into one ({len(combined_transcript)} total chars)")
        else:
            combined_transcript = transcript_parts[0]
            logger.info(f"✓ Downloaded transcript ({len(combined_transcript)} chars)")
        
        return {
            "transcript": combined_transcript,
            "chat": None,  # Chat messages are separate
            "source": source_urls[0] if source_urls else None  # Return first source URL
        }
