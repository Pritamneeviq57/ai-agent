"""
PostgreSQL Database Manager for Railway Deployment
Replaces SQLite for persistent storage on Railway
Uses psycopg3 for Python 3.13 compatibility
"""
import psycopg
from psycopg.rows import dict_row
from src.utils.logger import setup_logger
from datetime import datetime
import json
import os
import re

logger = setup_logger(__name__)


def normalize_datetime_string(dt_string):
    """
    Normalize datetime string to a consistent ISO format for database storage.
    Handles various formats from Graph API and converts them to ISO format.
    """
    if dt_string is None:
        return None
    
    if isinstance(dt_string, datetime):
        return dt_string.isoformat()
    
    if not isinstance(dt_string, str):
        return str(dt_string)
    
    dt_string = dt_string.strip()
    dt_string = re.sub(r'[Zz](\+|-)\d{2}:\d{2}$', '', dt_string)
    dt_string = dt_string.rstrip('Zz')
    
    if '.' in dt_string:
        parts = dt_string.split('.')
        dt_string = parts[0]
    
    if 'T' in dt_string:
        date_part, time_part = dt_string.split('T', 1)
        time_part = time_part.split('.')[0].split('+')[0].split('-')[0]
        if len(time_part.split(':')) == 2:
            time_part += ':00'
        dt_string = f"{date_part}T{time_part}"
    
    return dt_string


class DatabaseManager:
    """Handle all database operations with PostgreSQL"""
    
    def __init__(self, database_url=None):
        """
        Initialize PostgreSQL connection.
        
        Args:
            database_url: PostgreSQL connection URL. If not provided, uses DATABASE_URL env var.
                         Format: postgresql://user:password@host:port/database
        """
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.connection = None
        
        if not self.database_url:
            logger.error("DATABASE_URL environment variable not set!")
    
    def connect(self):
        """Connect to PostgreSQL database"""
        try:
            if not self.database_url:
                logger.error("No DATABASE_URL configured")
                return False
            
            self.connection = psycopg.connect(
                self.database_url,
                row_factory=dict_row,
                autocommit=False
            )
            logger.info("✓ Connected to PostgreSQL database")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to connect to PostgreSQL: {str(e)}")
            return False
    
    def create_tables(self):
        """Create necessary database tables"""
        if not self.connection:
            logger.error("Not connected to database")
            return False
        
        cursor = self.connection.cursor()
        
        try:
            # Table for raw meetings data
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meetings_raw (
                    id SERIAL PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    subject TEXT,
                    client_name TEXT,
                    organizer_email TEXT,
                    participants TEXT,
                    start_time TIMESTAMP NOT NULL,
                    meeting_date DATE,
                    end_time TIMESTAMP,
                    duration_minutes INTEGER,
                    join_url TEXT,
                    transcript_processed BOOLEAN DEFAULT FALSE,
                    transcript_processed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(meeting_id, start_time)
                )
            """)
            
            # Create indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meetings_raw_meeting_id 
                ON meetings_raw(meeting_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meetings_raw_start_time 
                ON meetings_raw(start_time)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meetings_raw_end_time 
                ON meetings_raw(end_time)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meetings_raw_processed 
                ON meetings_raw(transcript_processed, end_time)
            """)
            
            # Table for transcripts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_transcripts (
                    id SERIAL PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    meeting_date DATE,
                    raw_transcript TEXT,
                    raw_chat TEXT,
                    transcript_fetched BOOLEAN DEFAULT FALSE,
                    transcript_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(meeting_id, start_time)
                )
            """)
            
            # Table for meeting summaries
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_summaries (
                    id SERIAL PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    meeting_date DATE,
                    summary_text TEXT,
                    summary_type TEXT DEFAULT 'structured',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(meeting_id, start_time)
                )
            """)
            
            # Create indexes for summaries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_summaries_meeting_id 
                ON meeting_summaries(meeting_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_summaries_start_time 
                ON meeting_summaries(start_time)
            """)
            
            # Table for satisfaction analytics
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_satisfaction (
                    id SERIAL PRIMARY KEY,
                    meeting_id TEXT NOT NULL UNIQUE,
                    satisfaction_score REAL DEFAULT 50.0,
                    sentiment_polarity REAL DEFAULT 0.0,
                    sentiment_subjectivity REAL DEFAULT 0.5,
                    sentiment_reason TEXT,
                    risk_score REAL DEFAULT 50.0,
                    urgency_level TEXT DEFAULT 'none',
                    concerns_json TEXT,
                    concern_categories_json TEXT,
                    key_phrases_json TEXT,
                    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for satisfaction
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_satisfaction_meeting_id 
                ON meeting_satisfaction(meeting_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_satisfaction_score 
                ON meeting_satisfaction(satisfaction_score)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_satisfaction_risk 
                ON meeting_satisfaction(risk_score)
            """)
            
            # Table for processing logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processing_logs (
                    id SERIAL PRIMARY KEY,
                    meeting_id TEXT,
                    status TEXT,
                    error_message TEXT,
                    processing_stage TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            self.connection.commit()
            logger.info("✓ PostgreSQL tables created/verified successfully")
            return True
            
        except Exception as e:
            self.connection.rollback()
            logger.error(f"✗ Error creating tables: {str(e)}")
            return False
    
    def insert_meeting(self, meeting_data):
        """Insert a meeting record into the database"""
        if not self.connection:
            logger.error("Not connected to database")
            return False
        
        cursor = self.connection.cursor()
        
        try:
            start_time = normalize_datetime_string(meeting_data.get('start_time'))
            end_time = normalize_datetime_string(meeting_data.get('end_time'))
            
            meeting_date = None
            if start_time and 'T' in start_time:
                meeting_date = start_time.split('T')[0]
            
            cursor.execute("""
                INSERT INTO meetings_raw 
                (meeting_id, subject, client_name, organizer_email, participants, 
                 start_time, meeting_date, end_time, duration_minutes, join_url, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (meeting_id, start_time) DO UPDATE SET
                    subject = EXCLUDED.subject,
                    client_name = EXCLUDED.client_name,
                    organizer_email = EXCLUDED.organizer_email,
                    participants = EXCLUDED.participants,
                    meeting_date = EXCLUDED.meeting_date,
                    end_time = EXCLUDED.end_time,
                    duration_minutes = EXCLUDED.duration_minutes,
                    join_url = EXCLUDED.join_url,
                    updated_at = EXCLUDED.updated_at
            """, (
                meeting_data.get('meeting_id'),
                meeting_data.get('subject'),
                meeting_data.get('client_name'),
                meeting_data.get('organizer_email'),
                json.dumps(meeting_data.get('participants', [])),
                start_time,
                meeting_date,
                end_time,
                meeting_data.get('duration_minutes'),
                meeting_data.get('join_url'),
                datetime.now()
            ))
            
            self.connection.commit()
            logger.debug(f"✓ Inserted/Updated meeting: {meeting_data.get('meeting_id')}")
            return True
            
        except Exception as e:
            self.connection.rollback()
            logger.error(f"✗ Error inserting meeting: {str(e)}")
            return False
    
    def get_meeting_count(self):
        """Get total meetings in database"""
        if not self.connection:
            return 0
        
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT COUNT(*) as count FROM meetings_raw")
            result = cursor.fetchone()
            return result['count'] if result else 0
        except Exception as e:
            logger.error(f"✗ Error fetching count: {str(e)}")
            return 0
    
    def get_meetings(self, limit=10):
        """Get recent meetings from database"""
        if not self.connection:
            return []
        
        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                SELECT 
                    meeting_id, 
                    client_name, 
                    start_time, 
                    end_time,
                    duration_minutes, 
                    organizer_email,
                    join_url
                FROM meetings_raw
                ORDER BY start_time DESC
                LIMIT %s
            """, (limit,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings: {str(e)}")
            return []
    
    def get_meetings_without_transcripts(self, limit=50):
        """Return meetings that do not have transcript/chat stored."""
        if not self.connection:
            return []

        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                SELECT mr.meeting_id, mr.organizer_email, mr.join_url
                FROM meetings_raw mr
                LEFT JOIN meeting_transcripts mt ON mr.meeting_id = mt.meeting_id AND mr.start_time = mt.start_time
                WHERE mt.meeting_id IS NULL
                ORDER BY mr.start_time DESC
                LIMIT %s
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings without transcripts: {str(e)}")
            return []

    def save_meeting_transcript(self, meeting_id, transcript_text=None, chat_text=None, source_url=None, start_time=None):
        """Insert or update transcript/chat payload for a meeting."""
        if not self.connection:
            logger.error("Not connected to database")
            return False

        cursor = self.connection.cursor()

        try:
            if start_time is None:
                cursor.execute(
                    "SELECT start_time FROM meetings_raw WHERE meeting_id = %s ORDER BY start_time DESC LIMIT 1",
                    (meeting_id,)
                )
                result = cursor.fetchone()
                if result:
                    start_time = result['start_time']
                else:
                    logger.warning(f"Could not find start_time for meeting {meeting_id}, using current time")
                    start_time = datetime.now()
            
            start_time = normalize_datetime_string(start_time)
            
            if not start_time:
                logger.error(f"Could not normalize start_time for meeting {meeting_id}")
                return False
            
            meeting_date = None
            if start_time and 'T' in start_time:
                meeting_date = start_time.split('T')[0]
            
            cursor.execute("""
                INSERT INTO meeting_transcripts (meeting_id, start_time, meeting_date, raw_transcript, raw_chat, transcript_fetched, transcript_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (meeting_id, start_time) DO UPDATE SET
                    raw_transcript = EXCLUDED.raw_transcript,
                    raw_chat = EXCLUDED.raw_chat,
                    transcript_fetched = EXCLUDED.transcript_fetched,
                    transcript_url = EXCLUDED.transcript_url,
                    meeting_date = EXCLUDED.meeting_date,
                    created_at = CURRENT_TIMESTAMP
            """, (
                meeting_id,
                start_time,
                meeting_date,
                transcript_text,
                chat_text,
                bool(transcript_text or chat_text),
                source_url,
            ))
            
            self.connection.commit()
            logger.info(f"✓ Saved transcript/chat data for meeting {meeting_id} at {start_time}")
            return True
        except Exception as e:
            self.connection.rollback()
            logger.error(f"✗ Error saving transcript for meeting {meeting_id}: {str(e)}")
            return False
    
    def save_meeting_summary(self, meeting_id, summary_text, summary_type="structured", start_time=None):
        """Save meeting summary to database."""
        if not self.connection:
            logger.error("Not connected to database")
            return False

        cursor = self.connection.cursor()

        try:
            if start_time is None:
                cursor.execute(
                    "SELECT start_time FROM meetings_raw WHERE meeting_id = %s ORDER BY start_time DESC LIMIT 1",
                    (meeting_id,)
                )
                result = cursor.fetchone()
                if result:
                    start_time = result['start_time']
                else:
                    logger.warning(f"Could not find start_time for meeting {meeting_id}, using current time")
                    start_time = datetime.now()
            
            start_time = normalize_datetime_string(start_time)
            
            if not start_time:
                logger.error(f"Could not normalize start_time for meeting {meeting_id}")
                return False
            
            meeting_date = None
            if start_time and 'T' in start_time:
                meeting_date = start_time.split('T')[0]
            
            cursor.execute("""
                INSERT INTO meeting_summaries (meeting_id, start_time, meeting_date, summary_text, summary_type, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (meeting_id, start_time) DO UPDATE SET
                    summary_text = EXCLUDED.summary_text,
                    summary_type = EXCLUDED.summary_type,
                    meeting_date = EXCLUDED.meeting_date,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                meeting_id,
                start_time,
                meeting_date,
                summary_text,
                summary_type,
                datetime.now(),
                datetime.now(),
            ))
            
            self.connection.commit()
            logger.info(f"✓ Saved summary for meeting {meeting_id} at {start_time}")
            return True
        except Exception as e:
            self.connection.rollback()
            logger.error(f"✗ Error saving summary for meeting {meeting_id}: {str(e)}")
            return False
    
    def get_meeting_summary(self, meeting_id, start_time=None):
        """Retrieve summary for a specific meeting."""
        if not self.connection:
            return None

        cursor = self.connection.cursor()

        try:
            if start_time:
                if isinstance(start_time, datetime):
                    start_time = start_time.isoformat()
                normalized_start_time = normalize_datetime_string(start_time) if start_time else None
                cursor.execute("""
                    SELECT meeting_id, start_time, summary_text, summary_type, created_at, updated_at
                    FROM meeting_summaries
                    WHERE meeting_id = %s AND start_time = %s
                """, (meeting_id, normalized_start_time))
            else:
                cursor.execute("""
                    SELECT meeting_id, start_time, summary_text, summary_type, created_at, updated_at
                    FROM meeting_summaries
                    WHERE meeting_id = %s
                    ORDER BY start_time DESC
                    LIMIT 1
                """, (meeting_id,))
            
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"✗ Error fetching summary for meeting {meeting_id}: {str(e)}")
            return None
    
    def get_meetings_with_summaries(self, limit=20):
        """Get meetings that have both transcripts and summaries."""
        if not self.connection:
            return []

        cursor = self.connection.cursor()

        try:
            cursor.execute("""
                SELECT 
                    mr.meeting_id,
                    mr.client_name,
                    mr.organizer_email,
                    mr.start_time,
                    mr.end_time,
                    mr.duration_minutes,
                    mt.raw_transcript,
                    ms.summary_text,
                    ms.summary_type,
                    ms.created_at as summary_created_at
                FROM meetings_raw mr
                JOIN meeting_transcripts mt ON mr.meeting_id = mt.meeting_id AND mr.start_time = mt.start_time
                JOIN meeting_summaries ms ON mr.meeting_id = ms.meeting_id AND mr.start_time = ms.start_time
                ORDER BY mr.start_time DESC
                LIMIT %s
            """, (limit,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings with summaries: {str(e)}")
            return []
    
    def get_meetings_with_transcripts_no_summaries(self, limit=50):
        """Get meetings with transcripts but no summaries yet."""
        if not self.connection:
            return []

        cursor = self.connection.cursor()

        try:
            cursor.execute("""
                SELECT 
                    mr.meeting_id,
                    mr.client_name,
                    mr.organizer_email,
                    mr.start_time,
                    mt.raw_transcript
                FROM meetings_raw mr
                JOIN meeting_transcripts mt ON mr.meeting_id = mt.meeting_id AND mr.start_time = mt.start_time
                LEFT JOIN meeting_summaries ms ON mr.meeting_id = ms.meeting_id AND mr.start_time = ms.start_time
                WHERE ms.meeting_id IS NULL
                ORDER BY mr.start_time DESC
                LIMIT %s
            """, (limit,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings with transcripts but no summaries: {str(e)}")
            return []
    
    def get_meetings_by_client(self, client_name, limit=20):
        """Get meetings for a specific client"""
        if not self.connection:
            return []
        
        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                SELECT 
                    meeting_id, 
                    client_name, 
                    start_time, 
                    end_time,
                    duration_minutes, 
                    organizer_email,
                    participants
                FROM meetings_raw
                WHERE client_name = %s
                ORDER BY start_time DESC
                LIMIT %s
            """, (client_name, limit))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings: {str(e)}")
            return []
    
    def get_meetings_in_date_range(self, start_date, end_date):
        """Get meetings within a date range"""
        if not self.connection:
            return []
        
        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                SELECT 
                    meeting_id, 
                    client_name, 
                    start_time, 
                    end_time,
                    duration_minutes, 
                    organizer_email
                FROM meetings_raw
                WHERE start_time >= %s AND start_time <= %s
                ORDER BY start_time DESC
            """, (start_date, end_date))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings: {str(e)}")
            return []
    
    def save_satisfaction_analysis(self, meeting_id: str, analysis_result: dict):
        """Save satisfaction analysis results to database."""
        if not self.connection:
            logger.error("Not connected to database")
            return False

        cursor = self.connection.cursor()

        try:
            cursor.execute("""
                INSERT INTO meeting_satisfaction (
                    meeting_id, satisfaction_score, sentiment_polarity, 
                    sentiment_subjectivity, sentiment_reason, risk_score, urgency_level,
                    concerns_json, concern_categories_json, key_phrases_json,
                    analyzed_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (meeting_id) DO UPDATE SET
                    satisfaction_score = EXCLUDED.satisfaction_score,
                    sentiment_polarity = EXCLUDED.sentiment_polarity,
                    sentiment_subjectivity = EXCLUDED.sentiment_subjectivity,
                    sentiment_reason = EXCLUDED.sentiment_reason,
                    risk_score = EXCLUDED.risk_score,
                    urgency_level = EXCLUDED.urgency_level,
                    concerns_json = EXCLUDED.concerns_json,
                    concern_categories_json = EXCLUDED.concern_categories_json,
                    key_phrases_json = EXCLUDED.key_phrases_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                meeting_id,
                analysis_result.get('satisfaction_score', 50.0),
                analysis_result.get('sentiment', {}).get('polarity', 0.0),
                analysis_result.get('sentiment', {}).get('subjectivity', 0.5),
                analysis_result.get('sentiment', {}).get('reason', ''),
                analysis_result.get('risk_score', 50.0),
                analysis_result.get('urgency_level', 'none'),
                json.dumps(analysis_result.get('concerns', [])),
                json.dumps(analysis_result.get('concern_categories', {})),
                json.dumps(analysis_result.get('key_phrases', [])),
                datetime.now(),
                datetime.now(),
            ))
            
            self.connection.commit()
            logger.info(f"✓ Saved satisfaction analysis for meeting {meeting_id}")
            return True
        except Exception as e:
            self.connection.rollback()
            logger.error(f"✗ Error saving satisfaction analysis for meeting {meeting_id}: {str(e)}")
            return False
    
    def get_satisfaction_analysis(self, meeting_id: str):
        """Retrieve satisfaction analysis for a specific meeting."""
        if not self.connection:
            return None

        cursor = self.connection.cursor()

        try:
            cursor.execute("""
                SELECT 
                    meeting_id, satisfaction_score, sentiment_polarity,
                    sentiment_subjectivity, sentiment_reason, risk_score, urgency_level,
                    concerns_json, concern_categories_json, key_phrases_json,
                    analyzed_at, updated_at
                FROM meeting_satisfaction
                WHERE meeting_id = %s
            """, (meeting_id,))
            
            row = cursor.fetchone()
            if row:
                result = dict(row)
                try:
                    result['concerns'] = json.loads(result['concerns_json']) if result['concerns_json'] else []
                    result['concern_categories'] = json.loads(result['concern_categories_json']) if result['concern_categories_json'] else {}
                    result['key_phrases'] = json.loads(result['key_phrases_json']) if result['key_phrases_json'] else []
                except:
                    result['concerns'] = []
                    result['concern_categories'] = {}
                    result['key_phrases'] = []
                return result
            return None
        except Exception as e:
            logger.error(f"✗ Error fetching satisfaction analysis for meeting {meeting_id}: {str(e)}")
            return None
    
    def get_all_satisfaction_analyses(self, limit=100):
        """Get all satisfaction analyses with meeting details."""
        if not self.connection:
            return []

        cursor = self.connection.cursor()

        try:
            cursor.execute("""
                SELECT 
                    ms.meeting_id,
                    ms.satisfaction_score,
                    ms.risk_score,
                    ms.urgency_level,
                    ms.concern_categories_json,
                    ms.analyzed_at,
                    mr.client_name,
                    mr.start_time,
                    mr.organizer_email
                FROM meeting_satisfaction ms
                JOIN meetings_raw mr ON ms.meeting_id = mr.meeting_id
                ORDER BY ms.analyzed_at DESC
                LIMIT %s
            """, (limit,))
            
            rows = cursor.fetchall()
            results = []
            for row in rows:
                result = dict(row)
                try:
                    result['concern_categories'] = json.loads(result['concern_categories_json']) if result['concern_categories_json'] else {}
                except:
                    result['concern_categories'] = {}
                results.append(result)
            return results
        except Exception as e:
            logger.error(f"✗ Error fetching all satisfaction analyses: {str(e)}")
            return []
    
    def get_meetings_without_satisfaction_analysis(self, limit=50):
        """Get meetings with transcripts but no satisfaction analysis yet."""
        if not self.connection:
            return []

        cursor = self.connection.cursor()

        try:
            cursor.execute("""
                SELECT 
                    mr.meeting_id,
                    mr.client_name,
                    mr.start_time,
                    mt.raw_transcript,
                    mt.raw_chat
                FROM meetings_raw mr
                JOIN meeting_transcripts mt ON mr.meeting_id = mt.meeting_id AND mr.start_time = mt.start_time
                LEFT JOIN meeting_satisfaction ms ON mr.meeting_id = ms.meeting_id
                WHERE ms.meeting_id IS NULL
                ORDER BY mr.start_time DESC
                LIMIT %s
            """, (limit,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings without satisfaction analysis: {str(e)}")
            return []
    
    def mark_meeting_as_processed(self, meeting_id, start_time=None):
        """
        Mark a meeting as having its transcript processed.
        
        Args:
            meeting_id: The meeting ID
            start_time: Optional start_time to match specific meeting instance
        """
        if not self.connection:
            logger.error("Not connected to database")
            return False
        
        cursor = self.connection.cursor()
        
        try:
            if start_time:
                start_time = normalize_datetime_string(start_time)
                cursor.execute("""
                    UPDATE meetings_raw
                    SET transcript_processed = TRUE,
                        transcript_processed_at = %s,
                        updated_at = %s
                    WHERE meeting_id = %s AND start_time = %s
                """, (datetime.now(), datetime.now(), meeting_id, start_time))
            else:
                cursor.execute("""
                    UPDATE meetings_raw
                    SET transcript_processed = TRUE,
                        transcript_processed_at = %s,
                        updated_at = %s
                    WHERE meeting_id = %s
                    AND transcript_processed = FALSE
                    ORDER BY start_time DESC
                    LIMIT 1
                """, (datetime.now(), datetime.now(), meeting_id))
            
            self.connection.commit()
            logger.info(f"✓ Marked meeting {meeting_id} as processed")
            return True
        except Exception as e:
            self.connection.rollback()
            logger.error(f"✗ Error marking meeting as processed: {str(e)}")
            return False
    
    def clear_all_tables(self):
        """Clears all data from all tables."""
        if not self.connection:
            logger.error("Not connected to database")
            return False
        
        cursor = self.connection.cursor()
        try:
            logger.info("🗑️  Clearing database...")
            cursor.execute("DELETE FROM meeting_summaries")
            logger.info("  ✓ Cleared meeting_summaries")
            cursor.execute("DELETE FROM meeting_satisfaction")
            logger.info("  ✓ Cleared meeting_satisfaction")
            cursor.execute("DELETE FROM meeting_transcripts")
            logger.info("  ✓ Cleared meeting_transcripts")
            cursor.execute("DELETE FROM meetings_raw")
            logger.info("  ✓ Cleared meetings_raw")
            self.connection.commit()
            logger.info("✅ Database cleared successfully!")
            return True
        except Exception as e:
            self.connection.rollback()
            logger.error(f"✗ Error clearing database: {str(e)}")
            return False
    
    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            logger.info("✓ PostgreSQL connection closed")

