# Testing Guide

## Prerequisites

1. **Get your Railway URL** - Your deployed app URL (e.g., `https://ai-agent-production-c956.up.railway.app`)
2. **Check Environment Variables** - Ensure these are set in Railway:
   - `ANTHROPIC_API_KEY` (for Claude API)
   - `REFRESH_TOKEN` (for Microsoft Graph API)
   - `EMAIL_TEST_RECIPIENT` (for test emails)
   - `DATABASE_URL` (for PostgreSQL)
   - `CRON_API_KEY` (optional - for API authentication. If not set, endpoints work without auth)

---

## Step 1: Verify Deployment

### Health Check
```bash
curl https://ai-agent-production-c956.up.railway.app/health
```

**Expected Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "timestamp": "..."
}
```

### Check Available Endpoints
```bash
curl https://ai-agent-production-c956.up.railway.app/
```

**Expected Response:** Should list all endpoints including `/migrate-tables`

---

## Step 2: Run Database Migration

**⚠️ IMPORTANT: Run this first to migrate existing data from old `meeting_summaries` table to new tables**

### Migration Endpoint
```bash
# Without API key (if CRON_API_KEY is not set in Railway)
curl -X POST https://ai-agent-production-c956.up.railway.app/migrate-tables \
  -H "Content-Type: application/json"

# With API key (if CRON_API_KEY is set in Railway)
curl -X POST https://ai-agent-production-c956.up.railway.app/migrate-tables \
  -H "X-API-Key: YOUR_CRON_API_KEY_HERE" \
  -H "Content-Type: application/json"
```

**Note:** The API key is **optional**. If `CRON_API_KEY` is not set in Railway environment variables, you can call the endpoint without the `X-API-Key` header. If it is set, you must include it.

**Expected Response:**
```json
{
  "status": "success",
  "message": "Migration complete: X structured summaries, Y client pulse reports",
  "migrated_structured": 5,
  "migrated_pulse": 3,
  "total_migrated": 8
}
```

**What to Check:**
- ✅ Migration completed successfully
- ✅ Count of migrated records matches your expectations
- ✅ If no records to migrate, you'll get: `"No records to migrate from meeting_summaries"`

**Verify in Railway Database:**
1. Go to Railway → Postgres → Database → Data
2. Check these tables exist:
   - `structured_summaries` (should have your structured summaries)
   - `client_pulse_reports` (should have your client pulse reports with `client_name` populated)
   - `aggregated_pulse_reports` (may be empty initially)

---

## Step 3: Test `/process` Endpoint

This endpoint processes meetings and generates both structured summaries and client pulse reports.

### Run Process Endpoint
```bash
# Without API key (if CRON_API_KEY is not set)
curl -X POST https://ai-agent-production-c956.up.railway.app/process \
  -H "Content-Type: application/json"

# With API key (if CRON_API_KEY is set)
curl -X POST https://ai-agent-production-c956.up.railway.app/process \
  -H "X-API-Key: YOUR_CRON_API_KEY_HERE" \
  -H "Content-Type: application/json"
```

**Expected Response:**
```json
{
  "status": "success",
  "meetings_found": 10,
  "transcripts_saved": 2,
  "structured_summaries_generated": 1,
  "pulse_reports_generated": 1,
  "emails_sent": 1,
  "skipped": 8,
  "no_transcript": 0,
  "message": "Found 10 meetings, processed 10 meetings (8 skipped with existing summaries, 0 with no transcript available)"
}
```

**What to Check:**
1. ✅ **Structured Summaries Generated**: Check `structured_summaries` table in Railway
2. ✅ **Client Pulse Reports Generated**: Check `client_pulse_reports` table in Railway
   - Verify `client_name` field is populated (not NULL)
3. ✅ **Emails Sent**: Check your email inbox for structured summary emails
4. ✅ **No Errors**: Check Railway logs for any errors

**Verify in Railway Database:**
```sql
-- Check structured summaries
SELECT COUNT(*) FROM structured_summaries;

-- Check client pulse reports (with client_name)
SELECT client_name, COUNT(*) 
FROM client_pulse_reports 
GROUP BY client_name;

-- Check recent entries
SELECT meeting_id, start_time, client_name 
FROM client_pulse_reports 
ORDER BY created_at DESC 
LIMIT 5;
```

---

## Step 4: Test `/generate-pulse-report` Endpoint

This endpoint aggregates client pulse reports from the last 15 days.

### Run Generate Pulse Report Endpoint
```bash
# Without API key (if CRON_API_KEY is not set)
curl -X POST https://ai-agent-production-c956.up.railway.app/generate-pulse-report \
  -H "Content-Type: application/json"

# With API key (if CRON_API_KEY is set)
curl -X POST https://ai-agent-production-c956.up.railway.app/generate-pulse-report \
  -H "X-API-Key: YOUR_CRON_API_KEY_HERE" \
  -H "Content-Type: application/json"
```

**Expected Response:**
```json
{
  "status": "success",
  "message": "Generated aggregated pulse reports for 2 clients",
  "clients_processed": 2,
  "reports_generated": 2,
  "emails_sent": 2,
  "total_pulse_reports": 5
}
```

**What to Check:**
1. ✅ **Aggregated Reports Generated**: Check `aggregated_pulse_reports` table in Railway
2. ✅ **Emails Sent**: Check `EMAIL_TEST_RECIPIENT` inbox for aggregated report emails
3. ✅ **Client Grouping**: Verify reports are grouped by client correctly
4. ✅ **No Errors**: Check Railway logs for any errors

**Verify in Railway Database:**
```sql
-- Check aggregated reports
SELECT client_name, date_range_start, date_range_end, individual_reports_count
FROM aggregated_pulse_reports
ORDER BY created_at DESC;

-- Check if reports have content
SELECT client_name, LENGTH(aggregated_report_text) as report_length
FROM aggregated_pulse_reports;
```

---

## Step 5: Verify Data Flow

### Check All Tables Have Data

**In Railway Database, run these queries:**

```sql
-- 1. Check structured summaries
SELECT COUNT(*) as structured_count FROM structured_summaries;

-- 2. Check client pulse reports
SELECT 
  COUNT(*) as total_pulse_reports,
  COUNT(DISTINCT client_name) as unique_clients,
  COUNT(CASE WHEN client_name IS NOT NULL AND client_name != '' THEN 1 END) as with_client_name
FROM client_pulse_reports;

-- 3. Check aggregated reports
SELECT 
  client_name,
  date_range_start,
  date_range_end,
  individual_reports_count
FROM aggregated_pulse_reports
ORDER BY created_at DESC;

-- 4. Verify data consistency (check if meetings have both summaries)
SELECT 
  ss.meeting_id,
  ss.start_time,
  CASE WHEN cpr.meeting_id IS NOT NULL THEN 'Yes' ELSE 'No' END as has_pulse_report
FROM structured_summaries ss
LEFT JOIN client_pulse_reports cpr 
  ON ss.meeting_id = cpr.meeting_id 
  AND ss.start_time = cpr.start_time
LIMIT 10;
```

---

## Step 6: Test Edge Cases

### Test with No Data
If you want to test with fresh data, you can check what happens when there's no data:

```bash
# Should return appropriate message
curl -X POST https://ai-agent-production-c956.up.railway.app/generate-pulse-report \
  -H "X-API-Key: YOUR_CRON_API_KEY_HERE"
```

**Expected if no data:**
```json
{
  "status": "success",
  "message": "No client_pulse reports found in last 15 days",
  "clients_processed": 0,
  "reports_generated": 0,
  "emails_sent": 0
}
```

### Test Migration Multiple Times
The migration is idempotent - safe to run multiple times:

```bash
# Run migration again - should not create duplicates
curl -X POST https://ai-agent-production-c956.up.railway.app/migrate-tables \
  -H "X-API-Key: YOUR_CRON_API_KEY_HERE"
```

**Expected:** Same count or "already migrated" message

---

## Troubleshooting

### Issue: Migration returns 0 records
**Possible Causes:**
- Old `meeting_summaries` table doesn't exist
- No records with `summary_type='structured'` or `summary_type='client_pulse'`
- Table exists but is empty

**Solution:** Check Railway database directly:
```sql
SELECT COUNT(*), summary_type 
FROM meeting_summaries 
GROUP BY summary_type;
```

### Issue: `/process` generates summaries but not pulse reports
**Possible Causes:**
- Claude API errors (check logs)
- Rate limiting (check logs for 429 errors)
- Missing client_name extraction

**Solution:** Check Railway logs for errors, verify `ANTHROPIC_API_KEY` is set

### Issue: `/generate-pulse-report` returns "No client_pulse reports found"
**Possible Causes:**
- No `client_pulse_reports` in last 15 days
- Date range issue
- Client name extraction failed

**Solution:** 
```sql
-- Check if reports exist
SELECT COUNT(*), MIN(start_time), MAX(start_time)
FROM client_pulse_reports;

-- Check client names
SELECT client_name, COUNT(*) 
FROM client_pulse_reports 
GROUP BY client_name;
```

### Issue: Emails not being sent
**Possible Causes:**
- `SEND_EMAILS` environment variable not set or false
- `EMAIL_TEST_RECIPIENT` not set
- Email authentication failed

**Solution:** Check Railway environment variables and logs

---

## Quick Test Checklist

- [ ] Health check works
- [ ] Migration endpoint runs successfully
- [ ] Migration migrated expected number of records
- [ ] New tables exist in Railway database
- [ ] `/process` endpoint generates structured summaries
- [ ] `/process` endpoint generates client pulse reports
- [ ] Client pulse reports have `client_name` populated
- [ ] Structured summary emails are sent
- [ ] `/generate-pulse-report` aggregates reports correctly
- [ ] Aggregated reports are saved to database
- [ ] Aggregated report emails are sent to `EMAIL_TEST_RECIPIENT`
- [ ] No errors in Railway logs

---

## Monitoring

### Check Railway Logs
1. Go to Railway → Your Service → Logs
2. Look for:
   - ✅ Success messages: "✅ Structured summary generated", "✅ Client pulse report generated"
   - ❌ Error messages: "Claude API error", "Database connection failed"
   - ⚠️ Warnings: "Rate limit hit", "Email failed"

### Check Database Tables
1. Go to Railway → Postgres → Database → Data
2. Verify tables exist and have data:
   - `structured_summaries`
   - `client_pulse_reports` (check `client_name` column)
   - `aggregated_pulse_reports`

---

## Example Full Test Flow

```bash
# 1. Health check
curl https://ai-agent-production-c956.up.railway.app/health

# 2. Migrate data
curl -X POST https://ai-agent-production-c956.up.railway.app/migrate-tables \
  -H "X-API-Key: YOUR_CRON_API_KEY"

# 3. Process meetings
curl -X POST https://ai-agent-production-c956.up.railway.app/process \
  -H "X-API-Key: YOUR_CRON_API_KEY"

# 4. Generate aggregated reports
curl -X POST https://ai-agent-production-c956.up.railway.app/generate-pulse-report \
  -H "X-API-Key: YOUR_CRON_API_KEY"

# 5. Check results in Railway database
```

---

## Need Help?

If you encounter issues:
1. Check Railway logs for error messages
2. Verify all environment variables are set correctly
3. Check database tables directly in Railway
4. Verify API keys are valid (Anthropic, Microsoft Graph)
5. Check email recipient address is correct

