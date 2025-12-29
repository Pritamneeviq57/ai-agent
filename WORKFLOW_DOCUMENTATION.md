# Complete Workflow Documentation
## Teams Meeting Transcript Fetcher & Summarizer System

**Generated:** 2025-01-XX  
**Based on:** Complete codebase review

---

## 📋 **PROJECT STRUCTURE**

### **Entry Points (Main Scripts)**
1. **`main_phase_2_3_delegated.py`** - Main local execution script
2. **`app.py`** - Flask API server (for Railway/cloud deployment)
3. **`streamlit_transcripts.py`** - Web dashboard for viewing/managing transcripts
4. **`get_refresh_token.py`** - Helper script to get refresh token for Railway

### **Core Components**
- **Authentication:** `src/api/graph_client_*.py` (3 variants)
- **Transcript Fetching:** `src/api/transcript_fetcher_*.py` (2 variants)
- **Summarization:** `src/summarizer/*.py` (2 models: Ollama & Claude)
- **Database:** `src/database/db_setup_*.py` (SQLite & PostgreSQL)
- **Email:** `src/utils/email_sender*.py` (2 variants)
- **Analytics:** `src/analytics/satisfaction_analyzer.py`
- **Logging:** `src/utils/logger.py`

---

## 🔄 **COMPLETE WORKFLOW: main_phase_2_3_delegated.py**

### **STEP 1: Initialization & Configuration**

**File:** `main_phase_2_3_delegated.py` (lines 82-113)

1. **Read Environment Variables:**
   - `SKIP_SUMMARIES` (default: `false`) - Skip summary generation if `true`
   - `DAYS_BACK` (default: `15`) - Number of days to look back for meetings
   - `TEST_MEETING_FILTER` (default: `None`) - Filter for testing specific meetings

2. **Log Configuration:**
   - Logs to: `logs/app_YYYYMMDD.log`
   - Console output: INFO level
   - File output: DEBUG level

**Code Location:** `src/utils/logger.py`

---

### **STEP 2: Authentication**

**File:** `main_phase_2_3_delegated.py` (lines 115-125)  
**Implementation:** `src/api/graph_client_delegated.py`

**Process:**
1. Initialize `GraphAPIClientDelegated()`
2. **Check Token Cache:**
   - Cache location: `~/.teams_transcript_cache/token_cache.json`
   - If valid token exists → Use cached token (no login needed)
   - If no cache or expired → Proceed to device code flow

3. **Device Code Flow (if needed):**
   - Call MSAL `initiate_device_flow()` with scopes:
     - `User.Read`
     - `Calendars.Read`
     - `OnlineMeetings.Read`
     - `OnlineMeetingTranscript.Read.All`
     - `Mail.Send`
   - Display device code to user
   - User visits `https://microsoft.com/devicelogin` and enters code
   - Wait for user authentication
   - Save token to cache file

4. **Result:**
   - Access token stored in `client.access_token`
   - Token expires in ~1 hour (auto-refreshed when needed)

**Authentication Method:** Delegated (User Context)  
**User ID:** Authenticated user (whoever logs in)  
**Endpoints Used:** `/me/*` (user's own data)

---

### **STEP 3: Database Connection**

**File:** `main_phase_2_3_delegated.py` (lines 127-142)  
**Implementation:** `src/database/db_setup_sqlite.py`

**Process:**
1. Initialize `DatabaseManager()` with default path: `data/meetings.db`
2. **Connect to SQLite:**
   - Create `data/` directory if doesn't exist
   - Connect to `data/meetings.db`
   - Set row factory to `sqlite3.Row` (dict-like access)

3. **Create Tables:**
   - `meetings_raw` - Meeting metadata
     - Columns: `id`, `meeting_id`, `subject`, `client_name`, `organizer_email`, `participants` (JSON), `start_time`, `end_time`, `duration_minutes`, `join_url`, `created_at`, `updated_at`
     - Unique constraint: `(meeting_id, start_time)` - handles recurring meetings
   - `meeting_transcripts` - Raw transcript content
     - Columns: `id`, `meeting_id`, `start_time`, `raw_transcript`, `raw_chat`, `source_url`, `created_at`
     - Unique constraint: `(meeting_id, start_time)`
   - `meeting_summaries` - Generated summaries
     - Columns: `id`, `meeting_id`, `start_time`, `summary_text`, `summary_type`, `created_at`
     - Unique constraint: `(meeting_id, start_time)`
   - `meeting_satisfaction` - Satisfaction analysis
     - Columns: `id`, `meeting_id`, `start_time`, `satisfaction_score`, `risk_score`, `urgency_level`, `sentiment_polarity`, `concerns` (JSON), `concern_categories` (JSON), `analyzed_at`

4. **Run Migrations:**
   - Normalize existing `start_time` values to consistent format
   - Handle duplicate records from recurring meetings

**Database Type:** SQLite (default)  
**Database Path:** `data/meetings.db`  
**Alternative:** PostgreSQL (if `DATABASE_URL` env var is set)

---

### **STEP 4: Summarizer Initialization**

**File:** `main_phase_2_3_delegated.py` (lines 144-174)  
**Implementation:** `src/summarizer/ollama_mistral_summarizer.py`

**Process:**
1. **Check if Summaries Enabled:**
   - If `SKIP_SUMMARIES=true` → Skip initialization, set `summarizer = None`

2. **Initialize Ollama Summarizer:**
   - Base URL: `http://192.168.2.180:11434` (local network Ollama server)
   - Model: `gpt-oss-safeguard:20b` (20B parameters)
   - Initialize `OllamaMistralSummarizer(base_url, model)`

3. **Health Check:**
   - Call `GET http://192.168.2.180:11434/api/tags`
   - Verify model `gpt-oss-safeguard:20b` is available
   - If not available → Set `summarizer = None`, log warning

4. **Configuration:**
   - Timeout: 900 seconds (15 minutes) for long transcripts
   - Max direct size: 30,000 characters (before chunking)
   - Chunk size: 2000 tokens with 200 token overlap
   - Temperature: 0.2-0.3 (concise summaries)

**Model:** `gpt-oss-safeguard:20b`  
**Server:** `http://192.168.2.180:11434` (local network)  
**Privacy:** 100% local, no cloud, data stays private

---

### **STEP 5: Meeting Discovery**

**File:** `main_phase_2_3_delegated.py` (lines 176-195)  
**Implementation:** `src/api/transcript_fetcher_delegated.py`

**Process:**
1. **Initialize Fetcher:**
   - Create `TranscriptFetcherDelegated(client)`
   - Pass authenticated Graph API client

2. **Scan Calendar:**
   - Calculate date range:
     - End: Start of today (00:00:00 UTC)
     - Start: `DAYS_BACK` days ago (default: 15 days)
   - Call Graph API: `GET /me/calendarView?startDateTime={start}&endDateTime={end}`
   - Select fields: `id`, `subject`, `start`, `end`, `isOnlineMeeting`, `onlineMeeting`, `organizer`, `attendees`
   - Handle pagination (if > 100 events)

3. **Filter Teams Meetings:**
   - Filter events where `isOnlineMeeting = true`
   - Extract meeting metadata:
     - `meeting_id` (from `onlineMeeting.id`)
     - `subject`
     - `join_url` (from `onlineMeeting.joinUrl`)
     - `organizer_email` (from `organizer.emailAddress.address`)
     - `participants` (from `attendees` array)
     - `start_time` (from `start.dateTime`)
     - `end_time` (from `end.dateTime`)

4. **Check Transcript Availability:**
   - For each meeting:
     - Call `GET /me/onlineMeetings/{meeting_id}/transcripts`
     - Check if transcripts exist
     - Count available transcripts

5. **Build Meetings List:**
   - Include ALL meetings if `include_all=True`
   - Include only meetings with transcripts if `include_all=False`
   - Return list with metadata + transcript availability

**User ID:** Authenticated user (via `/me` endpoints)  
**Date Range:** Last `DAYS_BACK` days (excluding today)  
**Filter:** Only Teams online meetings

---

### **STEP 6: Transcript Fetching & Storage**

**File:** `main_phase_2_3_delegated.py` (lines 198-408)  
**Implementation:** `src/api/transcript_fetcher_delegated.py` (lines 206-507)

**For Each Meeting:**

#### **6.1 Save Meeting Metadata**
- Extract meeting data:
  - Calculate `duration_minutes` from `start_time` and `end_time`
  - Parse `participants` JSON
  - Extract `client_name` (from organizer name)
- Call `db.insert_meeting(meeting_data)`
- Save to `meetings_raw` table
- Handle duplicates: `UNIQUE(meeting_id, start_time)` constraint

#### **6.2 Check Transcript Availability**
- If `has_transcript = False` → Skip transcript fetching, continue to next meeting

#### **6.3 Fetch Transcript**
- **Get Meeting Start Time:** `meeting.get("start_time")`
- **List Transcripts:** `GET /me/onlineMeetings/{meeting_id}/transcripts`
- **Match Transcript to Meeting Instance:**
  - For recurring meetings, match by date first, then time
  - Parse `transcript.createdDateTime` and compare with `meeting.start_time`
  - Select transcript from same date as meeting
  - Validate time difference < 1 hour (tolerance)
  - If no matching transcript → Return `None`, skip

#### **6.4 Download Transcript Content**
- For matched transcript(s):
  - Call `GET /me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content`
  - Accept header: `text/vtt` (WebVTT format)
  - Decode response as UTF-8
  - Validate: Not empty, minimum 50 characters, meaningful content

#### **6.5 Save Transcript to Database**
- Call `db.save_meeting_transcript()`:
  - `meeting_id` + `start_time` (unique key)
  - `transcript_text` (raw VTT content)
  - `chat_text` (if available, separate from transcript)
  - `source_url` (for reference)
- Save to `meeting_transcripts` table
- Handle duplicates: Skip if already exists

**Transcript Format:** WebVTT (VTT)  
**Storage:** SQLite `meeting_transcripts` table  
**Validation:** Minimum 50 characters, not empty

---

### **STEP 7: Summary Generation**

**File:** `main_phase_2_3_delegated.py` (lines 281-402)  
**Implementation:** `src/summarizer/ollama_mistral_summarizer.py`

**Process (if `summarizer` is available):**

#### **7.1 Check if Summary Exists**
- Query database: `db.get_meeting_summary(meeting_id, start_time)`
- If summary exists → Skip generation, log "Summary already exists"
- If no summary → Proceed to generation

#### **7.2 Prepare Transcript**
- Get transcript text from database
- Check length:
  - If > 30,000 chars → Use chunking approach
  - If ≤ 30,000 chars → Process directly

#### **7.3 Generate Summary**
- **Build Prompt:**
  - Include transcript text
  - Request structured format:
    - Meeting Summary (date, attendees, duration)
    - Purpose
    - Key Decisions (with owners)
    - Action Items (table format: Owner | Task | Deadline | Status)
    - Technical Context
    - Outstanding Questions
    - Risks & Blockers
    - Documents Required
    - Next Meeting
    - Critical Numbers/Dates
    - Sentiment Progression

- **Call Ollama API:**
  - Endpoint: `POST http://192.168.2.180:11434/api/generate`
  - Model: `gpt-oss-safeguard:20b`
  - Parameters:
    - `temperature`: 0.3
    - `num_predict`: 8000 (max output tokens)
    - `num_ctx`: 16384 (context window)
  - Timeout: 900 seconds (15 minutes)
  - Retry logic: 3 attempts with exponential backoff

- **Process Response:**
  - Stream response chunks
  - Combine into full summary text
  - Validate: Minimum 100 chars, maximum 20,000 chars

#### **7.4 Save Summary**
- Call `db.save_meeting_summary()`:
  - `meeting_id` + `start_time`
  - `summary_text` (markdown format)
  - `summary_type`: `"structured"`
- Save to `meeting_summaries` table

#### **7.5 Satisfaction Analysis (Optional)**
- If `include_satisfaction=True`:
  - Initialize `SatisfactionAnalyzer()`
  - Call `analyzer.analyze_transcript(transcript_text, chat_text)`
  - Extract:
    - Satisfaction score (0-100)
    - Risk score (0-100)
    - Urgency level (high/medium/low/none)
    - Sentiment polarity (-1 to +1)
    - Identified concerns (list)
    - Concern categories (dict)
  - Save to `meeting_satisfaction` table

**Model:** `gpt-oss-safeguard:20b`  
**Server:** `http://192.168.2.180:11434`  
**Summary Type:** Structured (default)  
**Format:** Markdown

---

### **STEP 8: Email Sending**

**File:** `main_phase_2_3_delegated.py` (lines 360-395)  
**Implementation:** `src/utils/email_sender.py`

**Process (if summary generated successfully):**

#### **8.1 Extract Recipients**
- Get `organizer_email` from meeting data
- Get `participants` list from meeting data
- Filter participants: Only emails with `neeviq.com` domain
- If no organizer participants found → Use `organizer_email` as fallback
- Remove duplicates

#### **8.2 Format Email**
- **Subject:** `"Meeting Summary (GPT-OSS-Safeguard:20b): {meeting_subject}"`
- **Body (HTML):**
  - Convert markdown summary to HTML
  - Format headers, tables, lists, bold/italic
  - Include meeting info: Subject, Date, AI Model
  - Include footer: Generation timestamp, automation notice

#### **8.3 Send Email**
- Call Microsoft Graph API: `POST /me/sendMail`
- Payload:
  ```json
  {
    "message": {
      "subject": "...",
      "body": {
        "contentType": "HTML",
        "content": "..."
      },
      "toRecipients": [...]
    },
    "saveToSentItems": true
  }
  ```
- Headers: `Authorization: Bearer {access_token}`
- Timeout: 30 seconds

#### **8.4 Test Mode**
- If `EMAIL_TEST_MODE=true` (from `config/settings.py`):
  - Send only to test recipient: `EMAIL_TEST_RECIPIENT` (default: `pritam.jagadale@neeviq.com`)
  - Ignore all participant emails

**Recipients:** All organizer participants (neeviq.com emails)  
**Email Format:** HTML  
**Save to Sent Items:** Yes

---

### **STEP 9: Logging & Summary**

**File:** `main_phase_2_3_delegated.py` (lines 414-448)

**Final Statistics:**
- Meetings scanned: Total count
- Meetings with transcripts: Count
- Meetings without transcripts: Count
- Transcripts saved: Count
- Transcripts failed: Count
- Summaries generated: Count
- Summary failures: Count
- Emails sent: Count
- Email failures: Count
- Model used: `gpt-oss-safeguard:20b`
- Privacy: 100% local

**Close Database:** `db.close()`

---

## 🌐 **WORKFLOW: Railway.app Deployment**

### **Purpose:** Cloud deployment on Railway.app platform

### **Deployment Configuration:**

**Files:**
- `railway.toml` - Railway-specific configuration
- `Procfile` - Process definition
- `app.py` - Flask API server

**Railway Configuration (`railway.toml`):**
```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "gunicorn app:app --bind 0.0.0.0:$PORT --timeout 180 --graceful-timeout 180"
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

**Server:** Gunicorn WSGI server  
**Port:** `$PORT` (Railway provides automatically)  
**Timeout:** 180 seconds (3 minutes)  
**Health Check:** `/health` endpoint

---

## 🌐 **WORKFLOW: app.py (Flask API)**

### **STEP-BY-STEP RAILWAY WORKFLOW:**

#### **STEP 1: Initial Setup (One-Time)**

**1.1 Get Refresh Token (Local):**
- Run: `python get_refresh_token.py`
- Authenticate via device code flow
- Copy the `REFRESH_TOKEN` value displayed
- Add to Railway Variables: `REFRESH_TOKEN=<token>`

**1.2 Railway Environment Variables:**
Required variables in Railway:
- `REFRESH_TOKEN` - Refresh token for delegated auth (if using delegated)
- `CLIENT_ID` or `AZURE_CLIENT_ID` - Azure App Client ID
- `TENANT_ID` or `AZURE_TENANT_ID` - Azure AD Tenant ID
- `CLIENT_SECRET` - Azure App Secret (if using app-only auth)
- `DATABASE_URL` - PostgreSQL connection string (Railway provides automatically)
- `ANTHROPIC_API_KEY` - Claude API key (for summarization)
- `SKIP_SUMMARIES` - Set to `"false"` to enable summaries (default: `"false"`)
- `SEND_EMAILS` - Set to `"true"` to enable email sending (default: `"false"`)
- `CRON_API_KEY` - Optional API key to protect `/run` endpoint
- `TARGET_USER_ID` - Optional: Specific user ID for app-only auth
- `EMAIL_SENDER_USER_ID` - Optional: User ID for sending emails (app-only)

**1.3 Deploy to Railway:**
- Connect GitHub repository to Railway
- Railway auto-detects Python project
- Uses `nixpacks` builder
- Installs dependencies from `requirements.txt`
- Starts Gunicorn server

---

#### **STEP 2: Server Startup**

**File:** `app.py` (lines 1-59)

**Process:**
1. **Initialize Flask App:**
   - Create Flask application instance
   - Read `CRON_API_KEY` for endpoint protection

2. **Choose Authentication Method:**
   - Check if `REFRESH_TOKEN` env var exists
   - If exists → Use **Delegated Auth** (refresh token)
   - If not → Use **App-Only Auth** (client credentials)

3. **Choose Database:**
   - Check if `DATABASE_URL` env var exists
   - If exists → Use **PostgreSQL** (Railway provides)
   - If not → Use **SQLite** (fallback)

4. **Initialize Summarizer:**
   - Import `ClaudeSummarizer` (not Ollama - cloud deployment)
   - Check if `ANTHROPIC_API_KEY` is available

5. **Server Starts:**
   - Gunicorn binds to `0.0.0.0:$PORT`
   - Health check available at `/health`
   - Ready to accept requests

**Authentication Method Selection:**
```python
USE_DELEGATED_AUTH = os.getenv("REFRESH_TOKEN") is not None
```

**Database Selection:**
```python
USE_POSTGRES = os.getenv("DATABASE_URL") is not None
```

---

#### **STEP 3: Authentication (When `/run` is Called)**

**File:** `app.py` (lines 81-84 or 122-124)  
**Implementation:** `src/api/graph_client_delegated_refresh.py` OR `src/api/graph_client_apponly.py`

**Method 1: Delegated with Refresh Token (Recommended for Railway)**

**Process:**
1. **Initialize Client:**
   - Read `REFRESH_TOKEN` from environment
   - Read `CLIENT_ID` (or `AZURE_CLIENT_ID`)
   - Read `TENANT_ID` (or `AZURE_TENANT_ID`)

2. **Exchange Refresh Token:**
   - Endpoint: `https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token`
   - Method: `POST`
   - Payload:
     ```json
     {
       "client_id": "...",
       "refresh_token": "...",
       "grant_type": "refresh_token",
       "scope": "User.Read Calendars.Read OnlineMeetings.Read OnlineMeetingTranscript.Read.All Mail.Send"
     }
     ```

3. **Get Access Token:**
   - Response contains `access_token` and `expires_in`
   - Store access token in `client.access_token`
   - Calculate expiration time
   - If new refresh token provided → Log warning to update Railway variable

4. **Auto-Refresh:**
   - Token expires in ~1 hour
   - Automatically refreshes when expired (before API calls)

**User ID:** Authenticated user (whoever the refresh token belongs to)  
**Endpoints:** `/me/*` (user's own data)  
**No User Interaction:** Perfect for Railway (no device code flow needed)

**Method 2: App-Only Authentication (Alternative)**

**Process:**
1. **Initialize Client:**
   - Read `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID`

2. **Get App-Only Token:**
   - Endpoint: `https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token`
   - Method: `POST`
   - Payload:
     ```json
     {
       "client_id": "...",
       "client_secret": "...",
       "scope": "https://graph.microsoft.com/.default",
       "grant_type": "client_credentials"
     }
     ```

3. **Access All Users:**
   - Can access any user's data (requires `User.Read.All` permission)
   - Uses `/users/{user_id}` endpoints instead of `/me`

**User ID:** `TARGET_USER_ID` env var OR all users in organization  
**Endpoints:** `/users/{id}/*` (any user's data)

---

#### **STEP 4: Database Connection**

**File:** `app.py` (lines 87-89 or 127-129)  
**Implementation:** `src/database/db_setup_postgres.py` (if `DATABASE_URL` set)

**Process:**
1. **Initialize DatabaseManager:**
   - Read `DATABASE_URL` from environment (Railway provides automatically)
   - Format: `postgresql://user:password@host:port/database`

2. **Connect to PostgreSQL:**
   - Use `psycopg` library (Python 3.13 compatible)
   - Set row factory to `dict_row` (dict-like access)
   - Enable autocommit: `False` (use transactions)

3. **Create Tables:**
   - Same schema as SQLite:
     - `meetings_raw` (SERIAL PRIMARY KEY instead of AUTOINCREMENT)
     - `meeting_transcripts`
     - `meeting_summaries`
     - `meeting_satisfaction`
   - Use `CREATE TABLE IF NOT EXISTS` (idempotent)
   - Create indexes for performance

**Database Type:** PostgreSQL (provided by Railway)  
**Connection:** Managed by Railway (automatic provisioning)  
**Persistence:** Data persists across deployments

---

#### **STEP 5: Meeting Discovery**

**File:** `app.py` (lines 99-118 or 139-148)

**Delegated Auth (Refresh Token):**
1. **Initialize Fetcher:**
   - Create `TranscriptFetcherDelegated(client)`
   - Uses authenticated Graph API client

2. **Scan Calendar:**
   - Call: `list_all_meetings_with_transcripts()`
   - Parameters:
     - `days_back=3` (last 3 days - limited for Railway timeout)
     - `include_all=False` (only meetings with transcripts)
     - `limit=5` (cap to 5 meetings per run - avoid Gunicorn timeout)
   - Endpoint: `/me/calendarView`
   - Filter: Only Teams online meetings

3. **User ID:** Authenticated user (from refresh token)

**App-Only Auth:**
1. **Initialize Fetcher:**
   - Create `TranscriptFetcherAppOnly(client)`

2. **Scan Users:**
   - If `TARGET_USER_ID` set:
     - Call: `list_meetings_with_transcripts_for_user(TARGET_USER_ID)`
     - Fetch for specific user only
   - If `TARGET_USER_ID` not set:
     - Call: `list_all_meetings_with_transcripts_org_wide()`
     - Scan ALL users in organization (requires `User.Read.All`)

**Limitations for Railway:**
- Limited to 3 days back (vs 15 days locally)
- Limited to 5 meetings per run (vs unlimited locally)
- Reason: Gunicorn timeout (180 seconds)

---

#### **STEP 6: Transcript Fetching**

**File:** `app.py` (lines 163-181)

**Process:**
1. **Save Meeting Metadata:**
   - Call `db.insert_meeting()` with meeting data
   - Save to `meetings_raw` table

2. **Fetch Transcript:**
   - **Delegated:** `fetcher.fetch_transcript_for_meeting(meeting_id, start_time)`
   - **App-Only:** `fetcher.fetch_transcript_for_meeting(user_id, meeting_id)`
   - Endpoint: `/me/onlineMeetings/{id}/transcripts/{tid}/content` (delegated)
   - OR: `/users/{user_id}/onlineMeetings/{id}/transcripts/{tid}/content` (app-only)

3. **Validate Transcript:**
   - Check: Not empty
   - Check: Minimum 50 characters
   - Check: Meaningful content

4. **Save to Database:**
   - Call `db.save_meeting_transcript()`
   - Save to PostgreSQL `meeting_transcripts` table

**Format:** WebVTT (VTT)  
**Storage:** PostgreSQL (persistent)

---

#### **STEP 7: Summary Generation**

**File:** `app.py` (lines 184-244)  
**Implementation:** `src/summarizer/claude_summarizer.py`

**Process:**
1. **Check if Summary Exists:**
   - Query: `db.get_meeting_summary(meeting_id, start_time)`
   - If exists → Skip generation, count as already summarized

2. **Initialize Claude Summarizer:**
   - Model: `claude-3-haiku-20240307` (default)
   - Requires: `ANTHROPIC_API_KEY` environment variable
   - API: Anthropic Cloud API (not local)

3. **Generate Summary:**
   - Call: `summarizer.summarize(transcript_text)`
   - Sends transcript to Claude API
   - Returns structured markdown summary
   - Timeout: Handled by Anthropic API

4. **Save Summary:**
   - Call: `db.save_meeting_summary()`
   - Save to PostgreSQL `meeting_summaries` table
   - Summary type: `"structured"`

**Model:** Claude (`claude-3-haiku-20240307`)  
**Provider:** Anthropic Cloud API  
**API Key:** `ANTHROPIC_API_KEY` env var  
**Cost:** Pay-per-use (cloud service)

---

#### **STEP 8: Email Sending**

**File:** `app.py` (lines 209-241)

**Process:**
1. **Check if Enabled:**
   - If `SEND_EMAILS != "true"` → Skip email sending

2. **Extract Recipient:**
   - Get `user_email` from meeting data (organizer email)

3. **Send Email:**
   - **Delegated:** `send_summary_email()` - Uses `/me/sendMail`
   - **App-Only:** `send_summary_email_apponly()` - Uses `/users/{EMAIL_SENDER_USER_ID}/sendMail`
   - Requires: `EMAIL_SENDER_USER_ID` for app-only auth

4. **Email Format:**
   - Subject: `"Meeting Summary (Claude): {meeting_subject}"`
   - Body: HTML formatted summary
   - Recipient: Organizer email

**Recipients:** Organizer email (single recipient)  
**Model Name in Email:** "Claude"  
**Format:** HTML

---

#### **STEP 9: API Response**

**File:** `app.py` (lines 249-256)

**Return JSON:**
```json
{
  "status": "success",
  "meetings_found": 5,
  "transcripts_saved": 3,
  "summaries_generated": 2,
  "emails_sent": 2
}
```

**Error Handling:**
- Returns `500` status code on error
- Error message in JSON: `{"error": "..."}`

---

### **API Endpoints:**

#### **`GET /` - Home**
- Returns service status and available endpoints
- No authentication required

#### **`GET /health` - Health Check**
- Returns: `{"status": "healthy", "timestamp": "..."}`
- Used by Railway for health monitoring
- No authentication required

#### **`POST /run` - Trigger Transcript Fetch**
- **Protected by:** `CRON_API_KEY` (if set)
- **Authentication:** API key in header (`X-API-Key`) or query param (`api_key`)
- **Process:** Full workflow (auth → fetch → summarize → email)
- **Returns:** JSON with statistics

#### **`GET /meetings` - List Meetings**
- Returns recent meetings from database
- No authentication required (read-only)

---

### **Railway-Specific Features:**

1. **Automatic PostgreSQL:**
   - Railway automatically provisions PostgreSQL database
   - `DATABASE_URL` provided automatically
   - Data persists across deployments

2. **Health Checks:**
   - Railway monitors `/health` endpoint
   - Auto-restarts on failure (up to 3 retries)

3. **Logs:**
   - View logs in Railway dashboard
   - Also logged to files: `logs/app_YYYYMMDD.log`

4. **Environment Variables:**
   - Set in Railway dashboard → Variables tab
   - Secure storage (encrypted)

5. **Cron Jobs:**
   - Use Railway Cron or external cron service
   - Call `POST /run?api_key=<CRON_API_KEY>`
   - Recommended: Every 6 hours

---

### **Key Differences: Local vs Railway**

| Feature | Local (`main_phase_2_3_delegated.py`) | Railway (`app.py`) |
|---------|--------------------------------------|-------------------|
| **Authentication** | Device code flow (interactive) | Refresh token (automatic) |
| **Database** | SQLite (`data/meetings.db`) | PostgreSQL (Railway managed) |
| **Summarizer** | Ollama `gpt-oss-safeguard:20b` (local) | Claude (cloud API) |
| **Days Back** | 15 days | 3 days (limited) |
| **Meetings/Run** | Unlimited | 5 meetings max |
| **Timeout** | No limit | 180 seconds (Gunicorn) |
| **User ID** | Authenticated user | Authenticated user OR `TARGET_USER_ID` |
| **Email** | All organizer participants | Single organizer email |
| **Deployment** | Manual run | Automatic (GitHub → Railway) |

---

### **Railway Deployment Checklist:**

✅ **Before Deployment:**
1. Get refresh token: `python get_refresh_token.py`
2. Add `REFRESH_TOKEN` to Railway variables
3. Add `CLIENT_ID` / `TENANT_ID` to Railway variables
4. Add `ANTHROPIC_API_KEY` to Railway variables (if using summaries)
5. Set `SKIP_SUMMARIES=false` (if you want summaries)
6. Set `SEND_EMAILS=true` (if you want emails)
7. Add `CRON_API_KEY` (optional, for endpoint protection)

✅ **Deployment:**
1. Connect GitHub repo to Railway
2. Railway auto-detects Python project
3. Auto-installs dependencies
4. Auto-starts Gunicorn server

✅ **After Deployment:**
1. Test: `GET https://your-app.railway.app/health`
2. Test: `POST https://your-app.railway.app/run?api_key=<key>`
3. Set up cron job (external service or Railway Cron)
4. Monitor logs in Railway dashboard

---

### **Exact Railway Workflow Steps:**

1. **Deploy:** Railway auto-deploys from GitHub
2. **Start:** Gunicorn starts Flask app on `$PORT`
3. **Health Check:** Railway monitors `/health` endpoint
4. **Cron Trigger:** External service calls `POST /run?api_key=<key>`
5. **Authenticate:** Exchange refresh token for access token
6. **Connect DB:** Connect to Railway PostgreSQL
7. **Scan Calendar:** Last 3 days, max 5 meetings
8. **Fetch Transcripts:** Download from Graph API
9. **Save to DB:** PostgreSQL `meeting_transcripts` table
10. **Generate Summary:** Claude API call
11. **Save Summary:** PostgreSQL `meeting_summaries` table
12. **Send Email:** Graph API `/me/sendMail`
13. **Return JSON:** Statistics to caller
14. **Complete:** Close database connection

---

## 🌐 **WORKFLOW: app.py (Flask API) - DETAILED**

### **Purpose:** Cloud deployment (Railway/AWS)

### **Authentication Methods:**

#### **Method 1: Delegated with Refresh Token (Railway Recommended)**
- **File:** `src/api/graph_client_delegated_refresh.py`
- **Trigger:** If `REFRESH_TOKEN` env var is set
- **Process:**
  1. Read `REFRESH_TOKEN` from environment
  2. Exchange refresh token for access token via OAuth2 endpoint
  3. No user interaction needed (perfect for Railway)
  4. Auto-refresh when token expires

#### **Method 2: App-Only Authentication**
- **File:** `src/api/graph_client_apponly.py`
- **Trigger:** If `REFRESH_TOKEN` is NOT set
- **Process:**
  1. Use `CLIENT_ID` + `CLIENT_SECRET` + `TENANT_ID`
  2. Get app-only token (service principal)
  3. Can access all users (requires `User.Read.All` permission)

### **API Endpoints:**

#### **`GET /` - Health Check**
- Returns service status and available endpoints

#### **`GET /health` - Health Check**
- Returns: `{"status": "healthy", "timestamp": "..."}`

#### **`POST /run` - Trigger Transcript Fetch**
- **Protected by:** `CRON_API_KEY` (if set)
- **Process:**
  1. Authenticate (delegated or app-only)
  2. Connect to database (PostgreSQL if `DATABASE_URL` set, else SQLite)
  3. Initialize summarizer (Claude, not Ollama)
  4. Fetch meetings:
     - Delegated: Use `/me` endpoints (authenticated user)
     - App-only: Use `TARGET_USER_ID` or scan all users
  5. Process meetings (same as main script)
  6. Return JSON with statistics

#### **`GET /meetings` - List Meetings**
- Returns recent meetings from database

### **Configuration:**
- `SKIP_SUMMARIES`: Skip summary generation
- `SEND_EMAILS`: Enable email sending
- `EMAIL_SENDER_USER_ID`: User ID for sending emails (app-only)
- `TARGET_USER_ID`: Specific user to fetch for (app-only)
- `CRON_API_KEY`: Protect `/run` endpoint
- `DATABASE_URL`: Use PostgreSQL instead of SQLite

---

## 📊 **WORKFLOW: streamlit_transcripts.py (Dashboard)**

### **Purpose:** Web UI for viewing/managing transcripts

### **Pages:**

#### **1. Satisfaction Monitor**
- Fetch satisfaction analyses from database
- Display:
  - Overall statistics (avg satisfaction, risk scores)
  - Satisfaction trends chart
  - Concern pattern analysis
  - High-risk meetings table

#### **2. Meeting Transcripts**
- Fetch ALL meetings from last 15 days (with or without transcripts)
- Display:
  - Meeting list with transcript status (✅/❌)
  - Meeting details (participants, duration, organizer)
  - Satisfaction analysis (if available)
  - Summary (if available)
  - Full transcript (if available)
  - Chat messages (if available)

#### **3. Analytics Dashboard**
- Fetch ONLY meetings WITH transcripts
- Features:
  - Select summary type (8 different types)
  - Select meeting
  - Select model (from Ollama server)
  - Generate summary on-demand
  - View existing summaries
  - View transcript preview

#### **4. Database Viewer**
- View raw database tables:
  - `meetings_raw`
  - `meeting_transcripts`
  - `meeting_summaries`
  - `meeting_satisfaction`
- Download as CSV
- View statistics

### **Database:** SQLite (`data/meetings.db`)

---

## 🔧 **HELPER SCRIPTS**

### **get_refresh_token.py**
- **Purpose:** Get refresh token for Railway deployment
- **Process:**
  1. Use device code flow to authenticate
  2. Extract refresh token from response
  3. Display token for user to add to Railway variables
- **Usage:** Run once locally, add `REFRESH_TOKEN` to Railway

---

## 📁 **DATA FLOW SUMMARY**

```
Microsoft Teams
    ↓
Microsoft Graph API
    ↓
Authentication (MSAL Device Code / Refresh Token)
    ↓
Calendar Scan (/me/calendarView)
    ↓
Transcript Fetch (/me/onlineMeetings/{id}/transcripts)
    ↓
SQLite Database (data/meetings.db)
    ├── meetings_raw (metadata)
    ├── meeting_transcripts (raw VTT)
    ├── meeting_summaries (AI-generated)
    └── meeting_satisfaction (analytics)
    ↓
Ollama Server (http://192.168.2.180:11434)
    ↓
gpt-oss-safeguard:20b Model
    ↓
Summary Generation
    ↓
Database (save summary)
    ↓
Email (Microsoft Graph API /me/sendMail)
    ↓
Organizer Participants (neeviq.com emails)
```

---

## 🔑 **KEY CONFIGURATION VARIABLES**

| Variable | Purpose | Default | Location |
|----------|---------|---------|----------|
| `SKIP_SUMMARIES` | Skip summary generation | `false` | Env var |
| `DAYS_BACK` | Days to look back | `15` | Env var |
| `SEND_EMAILS` | Enable email sending | `false` | Env var |
| `EMAIL_TEST_MODE` | Test mode (send to test email only) | `true` | `config/settings.py` |
| `EMAIL_TEST_RECIPIENT` | Test email address | `pritam.jagadale@neeviq.com` | `config/settings.py` |
| `TENANT_ID` | Azure AD Tenant ID | Required | Env var |
| `CLIENT_ID` | Azure App Client ID | Required | Env var |
| `CLIENT_SECRET` | Azure App Secret | Required (app-only) | Env var |
| `REFRESH_TOKEN` | Refresh token for delegated auth | Optional | Env var |
| `CRON_API_KEY` | API key for `/run` endpoint | Optional | Env var |
| `DATABASE_URL` | PostgreSQL connection string | Optional | Env var |
| `LOG_LEVEL` | Logging level | `INFO` | Env var |

---

## 🎯 **EXACT USER ID & MODEL INFORMATION**

### **User ID for Fetching:**
- **main_phase_2_3_delegated.py:** Authenticated user (via `/me` endpoints)
- **app.py (delegated):** Authenticated user (via `/me` endpoints)
- **app.py (app-only):** `TARGET_USER_ID` env var OR all users in organization

### **Model for Summary Generation:**
- **main_phase_2_3_delegated.py:** `gpt-oss-safeguard:20b` on `http://192.168.2.180:11434`
- **app.py:** `claude-3-haiku-20240307` (Anthropic API)
- **streamlit_transcripts.py:** User-selectable (default: `gpt-oss-safeguard:20b`)

### **Database:**
- **Default:** SQLite at `data/meetings.db`
- **Alternative:** PostgreSQL (if `DATABASE_URL` env var is set)

---

## 📝 **EXACT WORKFLOW STEPS**

1. **Start:** Run `python main_phase_2_3_delegated.py`
2. **Authenticate:** MSAL device code flow (or use cached token)
3. **Connect DB:** SQLite at `data/meetings.db`
4. **Init Summarizer:** Ollama `gpt-oss-safeguard:20b` on `http://192.168.2.180:11434`
5. **Scan Calendar:** Last 15 days, `/me/calendarView`
6. **Filter:** Only Teams online meetings
7. **Check Transcripts:** `/me/onlineMeetings/{id}/transcripts`
8. **Download:** `/me/onlineMeetings/{id}/transcripts/{tid}/content`
9. **Save:** `meeting_transcripts` table
10. **Generate Summary:** Ollama API call
11. **Save Summary:** `meeting_summaries` table
12. **Send Email:** `/me/sendMail` to organizer participants
13. **Log Statistics:** Console + `logs/app_YYYYMMDD.log`
14. **Complete:** Close database connection

---

**End of Documentation**

