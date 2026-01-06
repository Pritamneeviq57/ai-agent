# Endpoint Documentation

## Overview

This application has three main endpoints for processing meeting summaries, generating client pulse reports, and migrating data between database tables.

---

## `/process` Endpoint

**Method:** POST  
**Description:** Fetches Teams meetings from the last 15 days and processes them to generate summaries and pulse reports.

### What It Does:

1. **Fetches Meetings**
   - Scans calendar for Teams meetings from last 15 days
   - Only processes meetings that have transcripts available

2. **For Each Meeting:**
   - **Downloads Transcript** (if not already saved) → Saved to `meeting_transcripts` table
   - **Generates Structured Summary** (if missing):
     - Creates a structured meeting summary using Claude Opus 4.5
     - **Sends email** to meeting participants (or EMAIL_TEST_RECIPIENT in test mode)
     - **Saves to `structured_summaries` table** (dedicated table)
   - **Generates Client Pulse Report** (if missing):
     - Creates a client-specific pulse report using Claude Opus 4.5
     - Extracts and stores `client_name` from meeting subject or `meetings_raw.client_name`
     - **Does NOT send email** (only saves to database)
     - **Saves to `client_pulse_reports` table** (dedicated table with `client_name` field)

3. **Returns:**
   - Number of meetings found
   - Number of transcripts saved
   - Number of structured summaries generated
   - Number of pulse reports generated
   - Number of emails sent (only for structured summaries)
   - Number of meetings skipped (already have both summaries)

### Email Behavior:
- ✅ **Structured summaries**: Emailed to participants
- ❌ **Client pulse reports**: Saved to database but NOT emailed

### Database Tables Used:
- `meeting_transcripts` - Stores raw transcripts
- `structured_summaries` - Stores structured summaries (NEW dedicated table)
- `client_pulse_reports` - Stores individual client pulse reports (NEW dedicated table with `client_name`)

---

## `/generate-pulse-report` Endpoint

**Method:** GET or POST  
**Description:** Aggregates individual **client pulse reports** (NOT structured summaries) from the last 15 days into combined reports per client.

### Important Clarification:
- ❌ **NOT based on structured summaries** - These are separate
- ✅ **Based on `client_pulse_reports` table** - Only aggregates the individual client pulse reports

### What It Does:

1. **Queries Database**
   - Finds all records from `client_pulse_reports` table from last 15 days
   - Uses `client_name` field from `client_pulse_reports` table (with fallback to `meetings_raw.client_name` or subject parsing)
   - **Does NOT use structured summaries** or `meeting_summaries` table
   - Groups them by client name

2. **For Each Client:**
   - **Aggregates Reports**: Uses LLM (Claude Opus 4.5) to combine all individual `client_pulse` reports into one comprehensive aggregated report
   - **Sends Email**: Sends aggregated report to `EMAIL_TEST_RECIPIENT` only
   - **Saves to Database**: Aggregated reports are saved to `aggregated_pulse_reports` table

3. **Returns:**
   - Number of clients processed
   - Number of aggregated reports generated
   - Number of emails sent
   - Total individual pulse reports found

### Email Behavior:
- ✅ **Aggregated reports**: Emailed to EMAIL_TEST_RECIPIENT only
- ✅ **Database storage**: Aggregated reports are saved to `aggregated_pulse_reports` table

### Database Tables Used:
- `client_pulse_reports` - Source table (reads individual pulse reports)
- `meetings_raw` - Joins to get additional client name info if needed
- `aggregated_pulse_reports` - Destination table (saves aggregated reports)

---

## `/migrate-tables` Endpoint

**Method:** POST  
**Description:** One-time migration endpoint to move existing data from old `meeting_summaries` table to new separate tables.

### What It Does:

1. **Checks for Old Table**
   - Verifies if `meeting_summaries` table exists
   - Counts records with `summary_type='structured'` or `summary_type='client_pulse'`

2. **Migrates Data:**
   - **Structured Summaries**: Moves all `summary_type='structured'` records → `structured_summaries` table
   - **Client Pulse Reports**: Moves all `summary_type='client_pulse'` records → `client_pulse_reports` table
   - Extracts `client_name` from `meetings_raw` or meeting subject during migration

3. **Returns:**
   - Number of structured summaries migrated
   - Number of client pulse reports migrated
   - Total records migrated

### Safety Features:
- ✅ **Idempotent**: Safe to run multiple times (uses `ON CONFLICT DO NOTHING`)
- ✅ **Non-destructive**: Does not delete old `meeting_summaries` table
- ✅ **Preserves timestamps**: Keeps original `created_at` and `updated_at` values

### When to Use:
- **First time setup**: After deploying code with new tables
- **After code update**: When migrating from old `meeting_summaries` table structure
- **One-time operation**: Run once to migrate existing data

---

## Why 4 Emails?

If you received 4 emails total, it means:
- **2 emails from `/process`**: Structured summaries for 2 meetings (these are individual meeting summaries)
- **2 emails from `/generate-pulse-report`**: Aggregated pulse reports for 2 clients (these combine multiple individual `client_pulse` reports per client)

### Key Distinction:
- **Structured summaries** = Individual meeting summaries (emailed from `/process`)
- **Client pulse reports** = Individual client-focused reports (saved from `/process`, NOT emailed)
- **Aggregated pulse reports** = Combined multiple `client_pulse` reports per client (emailed from `/generate-pulse-report`)

---

## Client Name Extraction

The system extracts client names using this priority:

1. **From `client_name` field** in `client_pulse_reports` table (stored when saving)
2. **From `client_name` field** in `meetings_raw` table (if available)
3. **From meeting subject** - extracts text before first colon (e.g., "Project Sync-Up: ..." → "Project Sync-Up")
4. **Cleans up** common prefixes like "Canceled:", "Project Sync-Up"
5. **Fallback**: Uses first 2-3 words from subject if no colon found
6. **Final fallback**: "Client" (instead of "Unknown Client")

**Note**: Client name is now stored directly in `client_pulse_reports.client_name` field when the report is generated, making future queries faster.

---

## Database Storage

### What Gets Saved:

#### `structured_summaries` Table (NEW)
- **Purpose**: Stores individual meeting structured summaries
- **Fields**: 
  - `meeting_id` (TEXT, NOT NULL)
  - `start_time` (TIMESTAMP, NOT NULL)
  - `meeting_date` (DATE)
  - `summary_text` (TEXT, NOT NULL)
  - `created_at` (TIMESTAMP)
  - `updated_at` (TIMESTAMP)
- **Unique constraint**: `(meeting_id, start_time)` to prevent duplicates
- **Indexes**: `meeting_id`, `start_time`

#### `client_pulse_reports` Table (NEW)
- **Purpose**: Stores individual client pulse reports (one per meeting)
- **Fields**: 
  - `meeting_id` (TEXT, NOT NULL)
  - `start_time` (TIMESTAMP, NOT NULL)
  - `meeting_date` (DATE)
  - `client_name` (TEXT) - **Stored directly in table**
  - `summary_text` (TEXT, NOT NULL)
  - `created_at` (TIMESTAMP)
  - `updated_at` (TIMESTAMP)
- **Unique constraint**: `(meeting_id, start_time)` to prevent duplicates
- **Indexes**: `meeting_id`, `start_time`, `client_name` (for faster client-based queries)

#### `aggregated_pulse_reports` Table (NEW)
- **Purpose**: Stores aggregated pulse reports (combined multiple individual reports per client)
- **Fields**: 
  - `client_name` (TEXT, NOT NULL)
  - `date_range_start` (DATE, NOT NULL)
  - `date_range_end` (DATE, NOT NULL)
  - `aggregated_report_text` (TEXT, NOT NULL)
  - `individual_reports_count` (INTEGER) - Number of individual reports aggregated
  - `created_at` (TIMESTAMP)
  - `updated_at` (TIMESTAMP)
- **Unique constraint**: `(client_name, date_range_start, date_range_end)` to prevent duplicates
- **Indexes**: `client_name`, `(date_range_start, date_range_end)`

#### Legacy Table (Still Exists)
- **`meeting_summaries`**: Old table with `summary_type` field. Still exists for backward compatibility but new data is saved to dedicated tables above.

---

## Data Flow Summary

```
/process Endpoint:
├── Meeting Transcript
│   └──→ Saved to: meeting_transcripts table
├──→ Generates "structured" summary
│   └──→ Saved to: structured_summaries table
│   └──→ Emailed ✅
└──→ Generates "client_pulse" report
    └──→ Saved to: client_pulse_reports table (with client_name)
    └──→ NOT emailed ❌

/generate-pulse-report Endpoint:
└──→ Reads from: client_pulse_reports table
    └──→ Groups by client_name
    └──→ Aggregates using LLM
    └──→ Saved to: aggregated_pulse_reports table
    └──→ Emailed ✅ (to EMAIL_TEST_RECIPIENT only)

/migrate-tables Endpoint:
└──→ Reads from: meeting_summaries table (old)
    ├──→ summary_type='structured' → structured_summaries table
    └──→ summary_type='client_pulse' → client_pulse_reports table
```

---

## Recent Updates

1. **Separate Database Tables**: Created dedicated tables for each summary type
   - `structured_summaries` - for structured summaries
   - `client_pulse_reports` - for individual client pulse reports (includes `client_name` field)
   - `aggregated_pulse_reports` - for aggregated reports

2. **Improved Client Name Storage**: Client name is now stored directly in `client_pulse_reports.client_name` field when report is generated, eliminating need for complex joins

3. **Migration Endpoint**: Added `/migrate-tables` endpoint to migrate existing data from old `meeting_summaries` table to new tables

4. **Improved Client Name Extraction**: Better logic to avoid "Unknown Client" - extracts from subject, cleans prefixes, uses fallbacks

5. **Email Title Visibility**: Meeting subject now uses darker color (#1a1a1a) and larger font (16px, bold) for better visibility

6. **Better Subject Parsing**: Handles various subject formats better (with/without colons, prefixes, etc.)

---

## Database Migration Guide

### Step 1: Deploy Updated Code
The new tables (`structured_summaries`, `client_pulse_reports`, `aggregated_pulse_reports`) will be created automatically when the app starts.

### Step 2: Run Migration
Call the migration endpoint to move existing data:

```bash
POST https://your-railway-app.up.railway.app/migrate-tables
Headers: X-API-Key: YOUR_API_KEY
```

### Step 3: Verify Migration
Check your Railway database to confirm:
- ✅ `structured_summaries` table has your structured summaries
- ✅ `client_pulse_reports` table has your client pulse reports (with `client_name` populated)
- ✅ Old `meeting_summaries` table still exists (can be kept for backup or dropped later)

### Step 4: New Data Flow
After migration, all new data will automatically go to the new tables:
- New structured summaries → `structured_summaries`
- New client pulse reports → `client_pulse_reports` (with `client_name`)
- New aggregated reports → `aggregated_pulse_reports`

---

## API Endpoints Summary

| Endpoint | Method | Purpose | Emails Sent? | Database Tables |
|----------|--------|---------|--------------|-----------------|
| `/process` | POST | Process meetings, generate summaries & pulse reports | ✅ Structured summaries only | `structured_summaries`, `client_pulse_reports` |
| `/generate-pulse-report` | GET/POST | Aggregate client pulse reports | ✅ Aggregated reports only | `client_pulse_reports` (read), `aggregated_pulse_reports` (write) |
| `/migrate-tables` | POST | Migrate data from old table to new tables | ❌ No emails | `meeting_summaries` (read), `structured_summaries`, `client_pulse_reports` (write) |
| `/health` | GET | Health check | ❌ | None |
| `/meetings` | GET | List recent meetings | ❌ | Read-only |
| `/run` | POST | Legacy endpoint (same as `/process`) | ✅ Structured summaries only | `structured_summaries`, `client_pulse_reports` |
