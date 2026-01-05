# Railway PostgreSQL Database Schema

This document describes all data stored in the Railway PostgreSQL database for the AI Agent application.

## Database Tables

### 1. `meetings_raw`
**Purpose:** Stores raw meeting metadata from Microsoft Teams/Graph API

**Columns:**
- `id` (SERIAL PRIMARY KEY) - Auto-incrementing ID
- `meeting_id` (TEXT NOT NULL) - Unique Teams meeting identifier
- `subject` (TEXT) - Meeting subject/title
- `client_name` (TEXT) - Client name extracted from meeting
- `organizer_email` (TEXT) - Email of meeting organizer
- `participants` (TEXT) - JSON array of participant emails/info
- `start_time` (TIMESTAMP NOT NULL) - Meeting start time
- `meeting_date` (DATE) - Extracted date for easier querying
- `end_time` (TIMESTAMP) - Meeting end time
- `duration_minutes` (INTEGER) - Meeting duration
- `join_url` (TEXT) - Teams meeting join URL
- `transcript_processed` (BOOLEAN) - Whether transcript has been processed
- `transcript_processed_at` (TIMESTAMP) - When transcript was processed
- `created_at` (TIMESTAMP) - Record creation timestamp
- `updated_at` (TIMESTAMP) - Last update timestamp

**Unique Constraint:** `(meeting_id, start_time)` - Allows multiple instances of recurring meetings

**Indexes:**
- `idx_meetings_raw_meeting_id` - Fast lookup by meeting_id
- `idx_meetings_raw_start_time` - Fast lookup by start_time
- `idx_meetings_raw_end_time` - Fast lookup by end_time
- `idx_meetings_raw_processed` - Fast lookup for processed status

---

### 2. `meeting_transcripts`
**Purpose:** Stores raw transcript and chat data from Teams meetings

**Columns:**
- `id` (SERIAL PRIMARY KEY) - Auto-incrementing ID
- `meeting_id` (TEXT NOT NULL) - Teams meeting identifier
- `start_time` (TIMESTAMP NOT NULL) - Meeting start time
- `meeting_date` (DATE) - Extracted date for easier querying
- `raw_transcript` (TEXT) - Full transcript text from meeting
- `raw_chat` (TEXT) - Chat messages from meeting
- `transcript_fetched` (BOOLEAN) - Whether transcript was successfully fetched
- `transcript_url` (TEXT) - Source URL for transcript
- `created_at` (TIMESTAMP) - Record creation timestamp

**Unique Constraint:** `(meeting_id, start_time)` - One transcript per meeting instance

**Relationships:**
- Links to `meetings_raw` via `(meeting_id, start_time)`

---

### 3. `meeting_summaries`
**Purpose:** Stores AI-generated summaries of meeting transcripts

**Columns:**
- `id` (SERIAL PRIMARY KEY) - Auto-incrementing ID
- `meeting_id` (TEXT NOT NULL) - Teams meeting identifier
- `start_time` (TIMESTAMP NOT NULL) - Meeting start time
- `meeting_date` (DATE) - Extracted date for easier querying
- `summary_text` (TEXT) - The actual summary content (can be very long)
- `summary_type` (TEXT DEFAULT 'structured') - Type of summary:
  - `'structured'` - Standard structured summary
  - `'concise'` - Concise summary
  - `'ultra_concise'` - Ultra concise summary
  - `'one_liner'` - One-line summary
  - `'checklist'` - Checklist format
  - `'project_based'` - Project-based organization
  - `'client_pulse'` - Client pulse report (sentiment, themes, priorities)
  - `'variants'` - Multiple summary variants
  - `'summary_with_pulse'` - Combined summary + pulse
- `created_at` (TIMESTAMP) - When summary was generated
- `updated_at` (TIMESTAMP) - Last update timestamp

**Unique Constraint:** `(meeting_id, start_time)` - One summary per meeting instance

**Indexes:**
- `idx_meeting_summaries_meeting_id` - Fast lookup by meeting_id
- `idx_meeting_summaries_start_time` - Fast lookup by start_time

**Relationships:**
- Links to `meetings_raw` via `(meeting_id, start_time)`
- Links to `meeting_transcripts` via `(meeting_id, start_time)`

**Note:** This is where `client_pulse` summaries are stored with `summary_type = 'client_pulse'`

---

### 4. `meeting_satisfaction`
**Purpose:** Stores satisfaction analysis and sentiment data for meetings

**Columns:**
- `id` (SERIAL PRIMARY KEY) - Auto-incrementing ID
- `meeting_id` (TEXT NOT NULL UNIQUE) - Teams meeting identifier (one analysis per meeting)
- `satisfaction_score` (REAL DEFAULT 50.0) - Satisfaction score (0-100)
- `sentiment_polarity` (REAL DEFAULT 0.0) - Sentiment polarity (-1 to 1)
- `sentiment_subjectivity` (REAL DEFAULT 0.5) - Sentiment subjectivity (0 to 1)
- `sentiment_reason` (TEXT) - Explanation of sentiment analysis
- `risk_score` (REAL DEFAULT 50.0) - Risk score (0-100)
- `urgency_level` (TEXT DEFAULT 'none') - Urgency level (none, low, medium, high)
- `concerns_json` (TEXT) - JSON array of concerns identified
- `concern_categories_json` (TEXT) - JSON object of categorized concerns
- `key_phrases_json` (TEXT) - JSON array of key phrases extracted
- `analyzed_at` (TIMESTAMP) - When analysis was performed
- `updated_at` (TIMESTAMP) - Last update timestamp

**Indexes:**
- `idx_meeting_satisfaction_meeting_id` - Fast lookup by meeting_id
- `idx_meeting_satisfaction_score` - Fast lookup by satisfaction_score
- `idx_meeting_satisfaction_risk` - Fast lookup by risk_score

**Relationships:**
- Links to `meetings_raw` via `meeting_id`

---

### 5. `processing_logs`
**Purpose:** Stores processing logs and error tracking

**Columns:**
- `id` (SERIAL PRIMARY KEY) - Auto-incrementing ID
- `meeting_id` (TEXT) - Teams meeting identifier (nullable)
- `status` (TEXT) - Processing status (success, error, etc.)
- `error_message` (TEXT) - Error message if processing failed
- `processing_stage` (TEXT) - Stage where processing occurred
- `created_at` (TIMESTAMP) - When log entry was created

**Purpose:** Used for debugging and tracking processing issues

---

## Data Flow

1. **Meeting Discovery** → `meetings_raw`
   - Meetings are discovered from Microsoft Graph API
   - Basic metadata is stored

2. **Transcript Fetching** → `meeting_transcripts`
   - Transcripts are fetched from Graph API
   - Raw transcript and chat data stored

3. **Summary Generation** → `meeting_summaries`
   - AI (Claude/Mistral) generates summaries
   - Different summary types stored with `summary_type` field
   - `client_pulse` summaries stored here

4. **Satisfaction Analysis** → `meeting_satisfaction`
   - Sentiment and satisfaction analysis performed
   - Scores and concerns stored

5. **Processing Tracking** → `processing_logs`
   - Errors and processing status logged

---

## Summary Types in `meeting_summaries`

| Type | Description | Use Case |
|------|-------------|----------|
| `structured` | Standard structured summary | Default summary format |
| `concise` | Short, concise summary | Quick overview |
| `ultra_concise` | Very brief summary | Executive summary |
| `one_liner` | Single line summary | Briefest overview |
| `checklist` | Checklist format | Action items |
| `project_based` | Organized by project | Project-focused view |
| `client_pulse` | Full client pulse report | Client sentiment & themes |
| `variants` | Multiple summary types | Comprehensive view |
| `summary_with_pulse` | Summary + pulse combined | Complete analysis |

---

## Database Connection

- **Local Development:** SQLite at `data/meetings.db`
- **Railway Production:** PostgreSQL via `DATABASE_URL` environment variable
- **Connection:** Managed by `DatabaseManager` class in `src/database/db_setup_postgres.py`

---

## Key Relationships

```
meetings_raw (meeting_id, start_time)
    ├── meeting_transcripts (meeting_id, start_time)
    ├── meeting_summaries (meeting_id, start_time)
    └── meeting_satisfaction (meeting_id)
```

All tables use `(meeting_id, start_time)` as composite keys to handle recurring meetings with the same `meeting_id` but different start times.

