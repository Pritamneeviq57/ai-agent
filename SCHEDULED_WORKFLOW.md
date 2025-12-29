# Scheduled Meeting Processing Workflow

## Overview

This workflow uses a simple single-phase approach to process Teams meeting transcripts:
- **Processing** (Every 6 hours): Fetches all Teams meeting transcriptions from the last 1 day to now from Graph API (using user ID). If transcriptions are available, checks if summary exists - if yes, skips; if not, generates summary and sends email.

## Workflow Steps

### Main Processing (`/process` endpoint)
**Runs at: Every 6 hours**

1. Authenticates with Microsoft Graph API (using delegated auth with refresh token or app-only)
2. Fetches all Teams meetings from the **last 1 day to now** from Graph API (using user ID from `TARGET_USER_ID`)
3. For each meeting:
   - **Checks if summary already exists** - if yes, skips processing (saves API calls)
   - **Fetches transcript** from Microsoft Graph API (using user ID)
   - **If transcript is available:**
     - Validates transcript (not empty, >50 characters)
     - Saves transcript to database
     - Generates summary using Claude (if enabled and summary doesn't exist)
     - Sends email with summary (if enabled)
     - **Marks meeting as processed** (`transcript_processed = TRUE`)
   - **If transcript is not available:** Skips the meeting
4. Returns statistics:
   - Meetings found
   - Meetings processed
   - Transcripts saved
   - Summaries generated
   - Emails sent
   - Skipped (with existing summaries)
   - No transcript (meetings without available transcriptions)

**Benefits:**
- Simple: Single endpoint, no pre-scanning needed
- Efficient: Checks for existing summaries before fetching transcripts (saves API calls)
- User-specific: Uses user ID to fetch transcriptions for the specified user
- Flexible: Runs every 6 hours to catch all meetings throughout the day
- Comprehensive: Fetches transcriptions for all meetings from last 1 day to now
- Smart: Only processes meetings with available transcriptions

## Database Schema Updates

### Database Fields Used:
- `transcript_processed` (BOOLEAN): Whether transcript has been processed
- `transcript_processed_at` (TIMESTAMP): When processing completed
- `scheduled_processing_time` (TIMESTAMP): Optional field (calculated but not required for workflow)

### Indexes:
- `idx_meetings_raw_end_time`: For efficient end_time queries
- `idx_meetings_raw_processed`: For efficient processing status queries

## Cron Job Setup

### Main Processing Job (Every 6 hours)
**URL:** `https://ai-agent-production-c956.up.railway.app/process?api_key=YOUR_KEY`

**Schedule:**
- **Crontab:** `0 */6 * * *` (Every 6 hours: 12:00 AM, 6:00 AM, 12:00 PM, 6:00 PM)
- **Or use:** "Every 6 hours" in cron-job.org

**Purpose:** Fetches all Teams meeting transcriptions from the last 1 day to now from Graph API (using user ID). If transcriptions are available, checks if summary exists - if yes, skips; if not, generates summary and sends email.

**Note:** This is the only endpoint you need to schedule. It handles everything:
- Fetches meetings from Graph API (last 1 day to now) using user ID
- Fetches transcriptions for all meetings (using user ID)
- Checks database to skip meetings with existing summaries (before fetching transcript to save API calls)
- Only processes meetings with available transcriptions
- Generates summaries and sends emails for meetings without existing summaries

## Configuration

### Required Environment Variables:
- `REFRESH_TOKEN`: For delegated authentication (or use app-only auth)
- `CLIENT_ID`: Azure app client ID
- `TENANT_ID`: Azure tenant ID
- `TARGET_USER_ID`: User ID or email to fetch meetings for (for app-only auth)
- `DATABASE_URL`: PostgreSQL connection string
- `ANTHROPIC_API_KEY`: For summary generation (optional)
- `SEND_EMAILS`: Set to `true` to enable email sending
- `CRON_API_KEY`: API key for protecting endpoints (optional but recommended)

### Optional Environment Variables:
- `SKIP_SUMMARIES`: Set to `true` to skip summary generation
- `EMAIL_SENDER_USER_ID`: For app-only auth email sending

## Example Timeline

**Day 1:**
- **12:00 PM:** `/process` runs → Fetches meetings from last 1 day
  - Meeting A: 10:00 AM - 11:00 AM → ✅ Fetches transcript, generates summary, sends email
  - Meeting B: 2:00 PM - 3:00 PM (upcoming) → ✅ Fetches transcript (if available), generates summary, sends email
  - Meeting C: 4:00 PM - 5:00 PM (upcoming) → ✅ Fetches transcript (if available), generates summary, sends email

- **6:00 PM:** `/process` runs → Fetches meetings from last 1 day
  - Meeting A: 10:00 AM - 11:00 AM → ⏭️ Skipped (summary already exists)
  - Meeting B: 2:00 PM - 3:00 PM → ⏭️ Skipped (summary already exists) or ✅ Processed if transcript now available
  - Meeting C: 4:00 PM - 5:00 PM → ⏭️ Skipped (summary already exists) or ✅ Processed if transcript now available

## API Endpoints

### `GET/POST /process` (Main Endpoint)
- **Purpose:** Fetches all Teams meeting transcriptions from the last 1 day to now from Graph API (using user ID). If transcriptions are available, checks if summary exists - if yes, skips; if not, generates summary and sends email.
- **Auth:** Requires API key (if `CRON_API_KEY` is set)
- **Returns:**
  ```json
  {
    "status": "success",
    "meetings_found": 5,
    "meetings_processed": 5,
    "transcripts_saved": 3,
    "summaries_generated": 3,
    "emails_sent": 3,
    "skipped": 2,
    "no_transcript": 0,
    "message": "Found 5 meetings, processed 5 meetings (2 skipped with existing summaries, 0 with no transcript available)"
  }
  ```

### `GET/POST /run` (Legacy)
- **Purpose:** Immediate processing (original endpoint)
- **Note:** Still available for manual triggers or testing

## Benefits

1. **Simple:** Single endpoint, no pre-scanning or scheduling needed
2. **Efficient:** Checks for existing summaries before fetching transcripts (saves API calls)
3. **User-specific:** Uses user ID (`TARGET_USER_ID`) to fetch transcriptions for the specified user
4. **Comprehensive:** Fetches transcriptions for all meetings from last 1 day to now
5. **Smart:** Only processes meetings with available transcriptions
6. **Scalable:** Can handle multiple meetings per day
7. **Trackable:** Database tracks which meetings have been processed
8. **Flexible:** Runs every 6 hours to catch all meetings throughout the day

## Troubleshooting

### Meetings not being processed?
1. Check if `/process` is running every 6 hours
2. Verify `TARGET_USER_ID` is configured correctly
3. Verify meetings are from the last 1 day to now
4. Check if meetings already have summaries (they will be skipped)
5. Verify transcriptions are available in Graph API for the user
6. Check the `no_transcript` count in the response - meetings without transcriptions will be skipped

### Transcripts not available?
- Transcripts may take time to appear after meeting ends
- If transcript is not available, the meeting will be skipped and can be processed in the next run
- Check Graph API directly to verify transcript availability

### Database migration?
- New columns are automatically added on first run
- Existing meetings will have `transcript_processed = FALSE` by default
