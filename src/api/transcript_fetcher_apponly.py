"""
Transcript Fetcher using App-Only Authentication - Organization-Wide.

Fetches transcripts from ALL users in your organization.
Uses /users endpoint to iterate through all users and their meetings.
Works with Application Access Policy.

File: src/api/transcript_fetcher_apponly.py
"""
from typing import Dict, Optional, List
from src.api.graph_client_apponly import GraphAPIClientAppOnly
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class TranscriptFetcherAppOnly:
    """Fetch transcripts organization-wide using app-only auth."""

    def __init__(self, graph_client: GraphAPIClientAppOnly):
        self.client = graph_client

    def get_all_users(self, filter_licenseAssignment: bool = True) -> List[Dict]:
        """
        Get all users in the organization.
        
        Args:
            filter_licenseAssignment: If True, only returns licensed users 
                                     (likely to have Teams/meeting access)
        
        Returns:
            List of user dicts with id, displayName, userPrincipalName
        """
        logger.info("👥 Fetching all users from organization...")
        
        try:
            all_users = []
            next_link = None
            page = 1

            while True:
                if next_link:
                    # Use the @odata.nextLink for pagination
                    # Remove base URL since make_request adds it
                    endpoint = next_link.replace(self.client.base_url, "")
                    response = self.client.make_request("GET", endpoint)
                else:
                    # First page - optionally filter by license assignment
                    params = {
                        "$select": "id,displayName,userPrincipalName,assignedLicenses",
                        "$top": 999  # Max per page
                    }
                    response = self.client.make_request("GET", "/users", params=params)
                
                if not response or "value" not in response:
                    logger.warning("Could not fetch users")
                    break

                users = response.get("value", [])
                
                # Filter for licensed users if requested
                if filter_licenseAssignment:
                    users = [u for u in users if u.get("assignedLicenses")]
                
                all_users.extend(users)
                logger.debug(f"  Page {page}: {len(users)} users")

                # Check for next page
                next_link = response.get("@odata.nextLink")
                if not next_link:
                    break
                
                page += 1

            logger.info(f"✅ Found {len(all_users)} users in organization")
            return all_users

        except Exception as e:
            logger.error(f"Error fetching users: {str(e)}")
            return []

    def list_all_meetings_with_transcripts_org_wide(self) -> List[Dict]:
        """
        Scan ALL users' meetings for transcripts.
        
        Returns:
            List of dicts with user_id, user_name, user_email, meeting_id, subject, start_time, transcript_count
        """
        logger.info("=" * 70)
        logger.info("SCANNING ORGANIZATION FOR MEETINGS WITH TRANSCRIPTS")
        logger.info("=" * 70)

        # Get all users
        users = self.get_all_users(filter_licenseAssignment=True)
        
        if not users:
            logger.warning("No users found in organization")
            return []

        meetings_with_transcripts = []
        total_meetings_scanned = 0
        users_with_meetings = 0

        # Scan each user's meetings
        for idx, user in enumerate(users, 1):
            user_id = user.get("id")
            user_name = user.get("displayName", "Unknown")
            user_email = user.get("userPrincipalName", "Unknown")
            
            logger.info(f"\n[{idx}/{len(users)}] Scanning {user_name} ({user_email})...")

            try:
                # Get this user's online meetings
                response = self.client.make_request(
                    "GET",
                    f"/users/{user_id}/onlineMeetings",
                    params={"$top": 999}
                )
                
                if not response or not response.get("value"):
                    logger.debug(f"    No meetings found")
                    continue

                user_meetings = response["value"]
                total_meetings_scanned += len(user_meetings)
                logger.debug(f"    Found {len(user_meetings)} meetings")

                # Check each meeting for transcripts
                for meeting in user_meetings:
                    meeting_id = meeting.get("id")
                    subject = meeting.get("subject", "Unknown")
                    start_time = meeting.get("startDateTime", "Unknown")

                    try:
                        # Check if this meeting has transcripts
                        transcript_resp = self.client.make_request(
                            "GET",
                            f"/users/{user_id}/onlineMeetings/{meeting_id}/transcripts"
                        )

                        if transcript_resp and transcript_resp.get("value"):
                            transcript_count = len(transcript_resp["value"])
                            logger.info(f"    ✓ '{subject}' - {transcript_count} transcript(s)")
                            
                            meetings_with_transcripts.append({
                                "user_id": user_id,
                                "user_name": user_name,
                                "user_email": user_email,
                                "meeting_id": meeting_id,
                                "subject": subject,
                                "start_time": start_time,
                                "transcript_count": transcript_count
                            })
                            
                    except Exception as e:
                        logger.debug(f"    Could not check transcripts for '{subject}': {str(e)}")
                        continue

                if meetings_with_transcripts:
                    users_with_meetings += 1

            except Exception as e:
                logger.debug(f"  Error scanning user {user_name}: {str(e)}")
                continue

        # Summary
        logger.info("\n" + "=" * 70)
        logger.info(f"SCAN SUMMARY")
        logger.info("=" * 70)
        logger.info(f"  Total users scanned: {len(users)}")
        logger.info(f"  Users with transcripts: {users_with_meetings}")
        logger.info(f"  Total meetings with transcripts: {len(meetings_with_transcripts)}")
        logger.info("=" * 70)

        return meetings_with_transcripts

    def fetch_transcript_for_meeting(self, user_id: str, meeting_id: str) -> Optional[Dict[str, Optional[str]]]:
        """
        Fetch transcript bundle for a specific meeting.
        
        Args:
            user_id: The user ID who organized/attended the meeting
            meeting_id: The online meeting ID
        
        Returns:
            Dict with {transcript, chat, source} or None if not found
        """
        logger.debug(f"Fetching transcript for user {user_id}, meeting {meeting_id}")

        try:
            # Get transcripts for this meeting
            transcript_resp = self.client.make_request(
                "GET",
                f"/users/{user_id}/onlineMeetings/{meeting_id}/transcripts"
            )

            if not transcript_resp or not transcript_resp.get("value"):
                logger.debug(f"No transcripts found for meeting {meeting_id}")
                return None

            transcripts = transcript_resp["value"]
            logger.debug(f"Found {len(transcripts)} transcript(s)")

            if not transcripts:
                return None

            # Get the first transcript
            transcript = transcripts[0]
            transcript_id = transcript.get("id")
            logger.debug(f"Using transcript ID: {transcript_id}")

            # Download transcript content
            logger.debug(f"Downloading transcript content...")
            content = self.client.download_content(
                f"/users/{user_id}/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content"
            )

            if content:
                transcript_text = self._decode_content(content)
                logger.debug(f"Downloaded transcript ({len(transcript_text)} characters)")
                
                return {
                    "transcript": transcript_text,
                    "chat": None,
                    "source": f"users/{user_id}/onlineMeetings/{meeting_id}/transcripts/{transcript_id}"
                }
            else:
                logger.warning(f"Could not download transcript content")
                return None

        except Exception as e:
            logger.error(f"Error fetching transcript: {str(e)}")
            return None

    @staticmethod
    def _decode_content(raw_bytes: bytes) -> str:
        """
        Decode content from bytes to string.
        Tries multiple encodings.
        """
        if not raw_bytes:
            return ""
        
        encodings = ["utf-8", "utf-16", "utf-16-le", "latin-1", "cp1252"]
        
        for encoding in encodings:
            try:
                decoded = raw_bytes.decode(encoding, errors="ignore")
                if decoded.strip():
                    logger.debug(f"Successfully decoded using {encoding}")
                    return decoded
            except Exception:
                continue
        
        logger.warning("Could not decode with standard encodings, falling back to utf-8")
        return raw_bytes.decode("utf-8", errors="ignore")