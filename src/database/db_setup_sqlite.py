import sqlite3
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
    
    Args:
        dt_string: datetime string in various formats (e.g., '2025-12-03T14:00:00.0000000', '2025-12-03T14:00:00Z')
    
    Returns:
        str: Normalized ISO format datetime string (e.g., '2025-12-03T14:00:00')
    """
    if dt_string is None:
        return None
    
    if isinstance(dt_string, datetime):
        return dt_string.isoformat()
    
    if not isinstance(dt_string, str):
        return str(dt_string)
    
    # Remove timezone indicators and microseconds for consistent comparison
    # Pattern: YYYY-MM-DDTHH:MM:SS[.microseconds][Z/+timezone]
    dt_string = dt_string.strip()
    
    # Remove 'Z' or timezone offset
    dt_string = re.sub(r'[Zz](\+|-)\d{2}:\d{2}$', '', dt_string)
    dt_string = dt_string.rstrip('Zz')
    
    # Remove microseconds (keep only seconds)
    if '.' in dt_string:
        parts = dt_string.split('.')
        dt_string = parts[0]  # Keep only up to seconds
    
    # Ensure format is YYYY-MM-DDTHH:MM:SS
    if 'T' in dt_string:
        date_part, time_part = dt_string.split('T', 1)
        time_part = time_part.split('.')[0].split('+')[0].split('-')[0]  # Remove microseconds and timezone
        if len(time_part.split(':')) == 2:
            time_part += ':00'  # Add seconds if missing
        dt_string = f"{date_part}T{time_part}"
    
    return dt_string


class DatabaseManager:
    """Handle all database operations with SQLite"""
    
    def __init__(self, db_path="data/meetings.db"):
        self.db_path = db_path
        self.connection = None
        
        # Create data directory if it doesn't exist
        os.makedirs("data", exist_ok=True)
    
    def connect(self):
        """Connect to SQLite database"""
        try:
            self.connection = sqlite3.connect(self.db_path)
            self.connection.row_factory = sqlite3.Row
            logger.info(f"✓ Connected to SQLite database: {self.db_path}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to connect to database: {str(e)}")
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                    transcript_processed BOOLEAN DEFAULT 0,
                    transcript_processed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(meeting_id, start_time)
                )
            """)
            
            # Create index on meeting_id for faster lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meetings_raw_meeting_id 
                ON meetings_raw(meeting_id)
            """)
            
            # Create index on start_time for range queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meetings_raw_start_time 
                ON meetings_raw(start_time)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meetings_raw_end_time 
                ON meetings_raw(end_time)
            """)
            
            # Migration: Add subject column if it doesn't exist (for existing databases)
            try:
                cursor.execute("ALTER TABLE meetings_raw ADD COLUMN subject TEXT")
                logger.debug("✓ Added 'subject' column to meetings_raw table")
            except sqlite3.OperationalError:
                # Column already exists, ignore
                pass
            
            # Migration: Add meeting_date column if it doesn't exist
            cursor.execute("PRAGMA table_info(meetings_raw)")
            columns_raw = [col[1] for col in cursor.fetchall()]
            if 'meeting_date' not in columns_raw:
                logger.info("Adding meeting_date column to meetings_raw table...")
                try:
                    cursor.execute("ALTER TABLE meetings_raw ADD COLUMN meeting_date DATE")
                    # Populate meeting_date from start_time
                    cursor.execute("""
                        UPDATE meetings_raw 
                        SET meeting_date = DATE(start_time)
                        WHERE meeting_date IS NULL AND start_time IS NOT NULL
                    """)
                    logger.info("✓ Added meeting_date column to meetings_raw")
                except Exception as e:
                    logger.warning(f"Migration warning for meeting_date in meetings_raw: {e}")
            
            # Migration: Add transcript_processed columns if they don't exist
            # Refresh columns list to check for transcript_processed
            cursor.execute("PRAGMA table_info(meetings_raw)")
            columns_raw = [col[1] for col in cursor.fetchall()]
            if 'transcript_processed' not in columns_raw:
                logger.info("Adding transcript_processed columns to meetings_raw table...")
                try:
                    cursor.execute("ALTER TABLE meetings_raw ADD COLUMN transcript_processed BOOLEAN DEFAULT 0")
                    cursor.execute("ALTER TABLE meetings_raw ADD COLUMN transcript_processed_at TIMESTAMP")
                    logger.info("✓ Added transcript_processed columns to meetings_raw")
                except Exception as e:
                    logger.warning(f"Migration warning for transcript_processed in meetings_raw: {e}")
            
            # Create index for transcript_processed (after migration to ensure column exists)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meetings_raw_processed 
                ON meetings_raw(transcript_processed, end_time)
            """)
            
            # Table for transcripts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    meeting_date DATE,
                    raw_transcript TEXT,
                    raw_chat TEXT,
                    transcript_fetched BOOLEAN DEFAULT 0,
                    transcript_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(meeting_id, start_time)
                )
            """)
            
            # Migration: Add start_time column if it doesn't exist (BEFORE creating indexes)
            cursor.execute("PRAGMA table_info(meeting_transcripts)")
            columns = [col[1] for col in cursor.fetchall()]
            
            # Migration: Add meeting_date column if it doesn't exist
            if 'meeting_date' not in columns:
                logger.info("Adding meeting_date column to meeting_transcripts table...")
                try:
                    cursor.execute("ALTER TABLE meeting_transcripts ADD COLUMN meeting_date DATE")
                    # Populate meeting_date from start_time
                    cursor.execute("""
                        UPDATE meeting_transcripts 
                        SET meeting_date = DATE(start_time)
                        WHERE meeting_date IS NULL AND start_time IS NOT NULL
                    """)
                    logger.info("✓ Added meeting_date column to meeting_transcripts")
                except Exception as e:
                    logger.warning(f"Migration warning for meeting_date: {e}")
            
            if 'start_time' not in columns:
                logger.info("Adding start_time column to meeting_transcripts table...")
                try:
                    cursor.execute("ALTER TABLE meeting_transcripts ADD COLUMN start_time TIMESTAMP")
                    # For existing transcripts, try to get start_time from meetings_raw
                    cursor.execute("""
                        UPDATE meeting_transcripts 
                        SET start_time = (
                            SELECT start_time FROM meetings_raw 
                            WHERE meetings_raw.meeting_id = meeting_transcripts.meeting_id 
                            ORDER BY meetings_raw.start_time DESC
                            LIMIT 1
                        )
                        WHERE start_time IS NULL
                    """)
                    # Set a default for any remaining NULL values
                    cursor.execute("""
                        UPDATE meeting_transcripts 
                        SET start_time = created_at 
                        WHERE start_time IS NULL
                    """)
                    # Remove the old UNIQUE constraint on meeting_id and add composite unique constraint
                    # SQLite doesn't support DROP CONSTRAINT, so we'll recreate the table
                    # But first, let's just add the new unique index
                    logger.info("✓ Migration completed: start_time column added to meeting_transcripts")
                except Exception as e:
                    logger.warning(f"Migration warning for meeting_transcripts: {e}")
            else:
                logger.debug("start_time column already exists in meeting_transcripts")
            
            # Create composite unique index for meeting_transcripts
            try:
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_meeting_transcripts_unique ON meeting_transcripts(meeting_id, start_time)")
            except:
                pass  # Index might already exist or constraint already defined
            
            # Migration: Normalize existing start_time values in meeting_transcripts
            try:
                cursor.execute("SELECT meeting_id, start_time FROM meeting_transcripts WHERE start_time IS NOT NULL")
                existing_records = cursor.fetchall()
                updated_count = 0
                skipped_count = 0
                for record in existing_records:
                    old_start_time = record[1]
                    normalized_start_time = normalize_datetime_string(old_start_time)
                    if normalized_start_time and normalized_start_time != old_start_time:
                        try:
                            # Check if normalized value already exists for this meeting_id
                            cursor.execute(
                                "SELECT COUNT(*) FROM meeting_transcripts WHERE meeting_id = ? AND start_time = ?",
                                (record[0], normalized_start_time)
                            )
                            exists = cursor.fetchone()[0] > 0
                            if exists:
                                # If normalized value already exists, this is a duplicate - skip it
                                skipped_count += 1
                                continue
                            
                            cursor.execute(
                                "UPDATE meeting_transcripts SET start_time = ? WHERE meeting_id = ? AND start_time = ?",
                                (normalized_start_time, record[0], old_start_time)
                            )
                            updated_count += 1
                        except sqlite3.IntegrityError:
                            # UNIQUE constraint violation - skip this record
                            skipped_count += 1
                            continue
                if updated_count > 0 or skipped_count > 0:
                    logger.info(f"✓ Normalized {updated_count} existing start_time values in meeting_transcripts (skipped {skipped_count} duplicates)")
            except Exception as e:
                logger.warning(f"Migration warning for normalizing start_time in meeting_transcripts: {e}")
            
            # Migration: Normalize existing start_time values in meetings_raw
            try:
                cursor.execute("SELECT meeting_id, start_time FROM meetings_raw WHERE start_time IS NOT NULL")
                existing_records = cursor.fetchall()
                updated_count = 0
                skipped_count = 0
                for record in existing_records:
                    old_start_time = record[1]
                    normalized_start_time = normalize_datetime_string(old_start_time)
                    if normalized_start_time and normalized_start_time != old_start_time:
                        try:
                            # Check if normalized value already exists for this meeting_id
                            cursor.execute(
                                "SELECT COUNT(*) FROM meetings_raw WHERE meeting_id = ? AND start_time = ?",
                                (record[0], normalized_start_time)
                            )
                            exists = cursor.fetchone()[0] > 0
                            if exists:
                                skipped_count += 1
                                continue
                            
                            cursor.execute(
                                "UPDATE meetings_raw SET start_time = ? WHERE meeting_id = ? AND start_time = ?",
                                (normalized_start_time, record[0], old_start_time)
                            )
                            updated_count += 1
                        except sqlite3.IntegrityError:
                            skipped_count += 1
                            continue
                if updated_count > 0 or skipped_count > 0:
                    logger.info(f"✓ Normalized {updated_count} existing start_time values in meetings_raw (skipped {skipped_count} duplicates)")
            except Exception as e:
                logger.warning(f"Migration warning for normalizing start_time in meetings_raw: {e}")
            
            # Migration: Normalize existing start_time values in meeting_summaries
            try:
                cursor.execute("SELECT meeting_id, start_time FROM meeting_summaries WHERE start_time IS NOT NULL")
                existing_records = cursor.fetchall()
                updated_count = 0
                skipped_count = 0
                for record in existing_records:
                    old_start_time = record[1]
                    normalized_start_time = normalize_datetime_string(old_start_time)
                    if normalized_start_time and normalized_start_time != old_start_time:
                        try:
                            # Check if normalized value already exists for this meeting_id
                            cursor.execute(
                                "SELECT COUNT(*) FROM meeting_summaries WHERE meeting_id = ? AND start_time = ?",
                                (record[0], normalized_start_time)
                            )
                            exists = cursor.fetchone()[0] > 0
                            if exists:
                                skipped_count += 1
                                continue
                            
                            cursor.execute(
                                "UPDATE meeting_summaries SET start_time = ? WHERE meeting_id = ? AND start_time = ?",
                                (normalized_start_time, record[0], old_start_time)
                            )
                            updated_count += 1
                        except sqlite3.IntegrityError:
                            skipped_count += 1
                            continue
                if updated_count > 0 or skipped_count > 0:
                    logger.info(f"✓ Normalized {updated_count} existing start_time values in meeting_summaries (skipped {skipped_count} duplicates)")
            except Exception as e:
                logger.warning(f"Migration warning for normalizing start_time in meeting_summaries: {e}")
            
            # Table for meeting summaries (NEW)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            
            # Table for satisfaction analytics
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meeting_satisfaction (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (meeting_id) REFERENCES meetings_raw(meeting_id)
                )
            """)
            
            # Migration: Add sentiment_reason column if it doesn't exist
            try:
                cursor.execute("SELECT sentiment_reason FROM meeting_satisfaction LIMIT 1")
            except:
                logger.info("Adding sentiment_reason column to meeting_satisfaction table...")
                cursor.execute("ALTER TABLE meeting_satisfaction ADD COLUMN sentiment_reason TEXT")
            
            # Migration: Check if table has old UNIQUE constraint on meeting_id alone
            # SQLite doesn't support DROP CONSTRAINT, so we need to recreate the table if it has the old schema
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='meeting_summaries'")
            old_sql = cursor.fetchone()
            if old_sql and 'meeting_id TEXT NOT NULL UNIQUE' in old_sql[0]:
                logger.info("⚠️  Found old schema with UNIQUE constraint on meeting_id alone. Recreating table...")
                # Check if table has data
                cursor.execute("SELECT COUNT(*) FROM meeting_summaries")
                row_count = cursor.fetchone()[0]
                backup_data = []
                if row_count > 0:
                    logger.warning(f"⚠️  Table has {row_count} rows. Backing up data before recreating...")
                    # Backup data - get column info first
                    cursor.execute("PRAGMA table_info(meeting_summaries)")
                    column_info = cursor.fetchall()
                    column_names = [col[1] for col in column_info]
                    # Fetch all data
                    cursor.execute("SELECT * FROM meeting_summaries")
                    backup_data = cursor.fetchall()
                    logger.info(f"✓ Backed up {len(backup_data)} rows")
                
                # Drop old table and indexes
                cursor.execute("DROP TABLE IF EXISTS meeting_summaries")
                cursor.execute("DROP INDEX IF EXISTS idx_meeting_summaries_unique")
                cursor.execute("DROP INDEX IF EXISTS idx_meeting_summaries_meeting_id")
                cursor.execute("DROP INDEX IF EXISTS idx_meeting_summaries_start_time")
                logger.info("✓ Dropped old meeting_summaries table")
                
                # Recreate table with correct schema
                cursor.execute("""
                    CREATE TABLE meeting_summaries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                logger.info("✓ Recreated meeting_summaries table with correct schema")
                
                # Restore data if any
                if backup_data:
                    logger.info(f"Restoring {len(backup_data)} rows...")
                    # Get column names from backup
                    cursor.execute("PRAGMA table_info(meeting_summaries)")
                    new_columns = {col[1]: col[0] for col in cursor.fetchall()}
                    restored = 0
                    for row in backup_data:
                        # Map old columns to new schema
                        row_dict = dict(zip(column_names, row))
                        try:
                            cursor.execute("""
                                INSERT INTO meeting_summaries 
                                (meeting_id, start_time, meeting_date, summary_text, summary_type, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (
                                row_dict.get('meeting_id'),
                                row_dict.get('start_time') or row_dict.get('created_at'),
                                row_dict.get('meeting_date'),
                                row_dict.get('summary_text'),
                                row_dict.get('summary_type', 'structured'),
                                row_dict.get('created_at'),
                                row_dict.get('updated_at')
                            ))
                            restored += 1
                        except sqlite3.IntegrityError:
                            # Skip duplicates
                            pass
                    logger.info(f"✓ Restored {restored}/{len(backup_data)} rows")
            
            # Migration: Add start_time column if it doesn't exist (BEFORE creating indexes)
            # Check if start_time column exists by querying table info
            cursor.execute("PRAGMA table_info(meeting_summaries)")
            columns = [col[1] for col in cursor.fetchall()]
            
            # Migration: Add meeting_date column if it doesn't exist
            if 'meeting_date' not in columns:
                logger.info("Adding meeting_date column to meeting_summaries table...")
                try:
                    cursor.execute("ALTER TABLE meeting_summaries ADD COLUMN meeting_date DATE")
                    # Populate meeting_date from start_time
                    cursor.execute("""
                        UPDATE meeting_summaries 
                        SET meeting_date = DATE(start_time)
                        WHERE meeting_date IS NULL AND start_time IS NOT NULL
                    """)
                    logger.info("✓ Added meeting_date column to meeting_summaries")
                except Exception as e:
                    logger.warning(f"Migration warning for meeting_date in meeting_summaries: {e}")
            
            if 'start_time' not in columns:
                logger.info("Adding start_time column to meeting_summaries table...")
                try:
                    cursor.execute("ALTER TABLE meeting_summaries ADD COLUMN start_time TIMESTAMP")
                    # For existing summaries, try to get start_time from meetings_raw
                    cursor.execute("""
                        UPDATE meeting_summaries 
                        SET start_time = (
                            SELECT start_time FROM meetings_raw 
                            WHERE meetings_raw.meeting_id = meeting_summaries.meeting_id 
                            ORDER BY meetings_raw.start_time DESC
                            LIMIT 1
                        )
                        WHERE start_time IS NULL
                    """)
                    # Set a default for any remaining NULL values
                    cursor.execute("""
                        UPDATE meeting_summaries 
                        SET start_time = created_at 
                        WHERE start_time IS NULL
                    """)
                    logger.info("✓ Migration completed: start_time column added")
                except Exception as e:
                    logger.warning(f"Migration warning: {e}")
            else:
                logger.debug("start_time column already exists in meeting_summaries")
            
            # Create index on meeting_id and start_time for summaries (AFTER migration)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_summaries_meeting_id 
                ON meeting_summaries(meeting_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_summaries_start_time 
                ON meeting_summaries(start_time)
            """)
            # Create composite unique index
            try:
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_meeting_summaries_unique ON meeting_summaries(meeting_id, start_time)")
            except:
                pass  # Index might already exist or constraint already defined
            
            # Create index on meeting_id for satisfaction
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_satisfaction_meeting_id 
                ON meeting_satisfaction(meeting_id)
            """)
            
            # Create index on satisfaction_score for filtering
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_satisfaction_score 
                ON meeting_satisfaction(satisfaction_score)
            """)
            
            # Create index on risk_score for filtering
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meeting_satisfaction_risk 
                ON meeting_satisfaction(risk_score)
            """)
            
            # Table for processing logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processing_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT,
                    status TEXT,
                    error_message TEXT,
                    processing_stage TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            self.connection.commit()
            logger.info("✓ Database tables created/verified successfully")
            return True
            
        except Exception as e:
            logger.error(f"✗ Error creating tables: {str(e)}")
            return False
    
    def insert_meeting(self, meeting_data):
        """Insert a meeting record into the database"""
        if not self.connection:
            logger.error("Not connected to database")
            return False
        
        cursor = self.connection.cursor()
        
        try:
            # Normalize start_time and end_time for consistent storage
            start_time = normalize_datetime_string(meeting_data.get('start_time'))
            end_time = normalize_datetime_string(meeting_data.get('end_time'))
            
            # Extract date from start_time for easier querying
            meeting_date = None
            if start_time and 'T' in start_time:
                meeting_date = start_time.split('T')[0]  # Extract date part (YYYY-MM-DD)
            
            cursor.execute("""
                INSERT OR REPLACE INTO meetings_raw 
                (meeting_id, subject, client_name, organizer_email, participants, 
                 start_time, meeting_date, end_time, duration_minutes, join_url, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            logger.error(f"✗ Error inserting meeting: {str(e)}")
            return False
    
    def get_meeting_count(self):
        """Get total meetings in database"""
        if not self.connection:
            return 0
        
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM meetings_raw")
            count = cursor.fetchone()[0]
            return count
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
                LIMIT ?
            """, (limit,))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings: {str(e)}")
            return []
    
    def get_meetings_without_transcripts(self, limit=50):
        """Return meetings that do not have transcript/chat stored.
        
        Returns list of dicts with meeting_id, organizer_email, and join_url.
        """
        if not self.connection:
            return []

        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                SELECT mr.meeting_id, mr.organizer_email, mr.join_url
                FROM meetings_raw mr
                LEFT JOIN meeting_transcripts mt ON mr.meeting_id = mt.meeting_id AND mr.start_time = mt.start_time
                WHERE mt.meeting_id IS NULL
                ORDER BY mr.start_time DESC
                LIMIT ?
            """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings without transcripts: {str(e)}")
            return []

    def save_meeting_transcript(self, meeting_id, transcript_text=None, chat_text=None, source_url=None, start_time=None):
        """
        Insert or update transcript/chat payload for a meeting.
        
        Args:
            meeting_id (str): Teams meeting ID
            transcript_text (str): Transcript content
            chat_text (str): Chat messages
            source_url (str): Source URL for the transcript
            start_time (str or datetime): Meeting start time (required for recurring meetings)
        
        Returns:
            bool: True if saved successfully
        """
        if not self.connection:
            logger.error("Not connected to database")
            return False

        cursor = self.connection.cursor()

        try:
            # If start_time not provided, try to get it from meetings_raw
            if start_time is None:
                cursor.execute(
                    "SELECT start_time FROM meetings_raw WHERE meeting_id = ? ORDER BY start_time DESC LIMIT 1",
                    (meeting_id,)
                )
                result = cursor.fetchone()
                if result:
                    start_time = result[0]
                else:
                    logger.warning(f"Could not find start_time for meeting {meeting_id}, using current time")
                    start_time = datetime.now()
            
            # Normalize start_time to consistent format for database storage
            # This ensures recurring meetings with same meeting_id but different start_time are saved separately
            start_time = normalize_datetime_string(start_time)
            
            if not start_time:
                logger.error(f"Could not normalize start_time for meeting {meeting_id}")
                return False
            
            # Extract date from start_time for easier querying (YYYY-MM-DD format)
            # start_time format is YYYY-MM-DDTHH:MM:SS, so we can extract date part
            meeting_date = None
            if start_time and 'T' in start_time:
                meeting_date = start_time.split('T')[0]  # Extract date part (YYYY-MM-DD)
            
            try:
                cursor.execute(
                    """
                    INSERT INTO meeting_transcripts (meeting_id, start_time, meeting_date, raw_transcript, raw_chat, transcript_fetched, transcript_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(meeting_id, start_time) DO UPDATE SET
                        raw_transcript=excluded.raw_transcript,
                        raw_chat=excluded.raw_chat,
                        transcript_fetched=excluded.transcript_fetched,
                        transcript_url=excluded.transcript_url,
                        meeting_date=excluded.meeting_date,
                        created_at=CURRENT_TIMESTAMP
                    """,
                    (
                        meeting_id,
                        start_time,
                        meeting_date,
                        transcript_text,
                        chat_text,
                        bool(transcript_text or chat_text),
                        source_url,
                    ),
                )
            except sqlite3.IntegrityError as e:
                # Log detailed error information for debugging
                logger.error(f"✗ UNIQUE constraint error for meeting {meeting_id[:50]}...")
                logger.error(f"  start_time: {start_time}")
                logger.error(f"  meeting_date: {meeting_date}")
                # Check if a record with this combination already exists
                cursor.execute(
                    "SELECT meeting_id, start_time, meeting_date FROM meeting_transcripts WHERE meeting_id = ? AND start_time = ?",
                    (meeting_id, start_time)
                )
                existing = cursor.fetchone()
                if existing:
                    logger.error(f"  Existing record found: meeting_id={existing[0][:50]}..., start_time={existing[1]}, meeting_date={existing[2]}")
                else:
                    # Check for any record with same meeting_id
                    cursor.execute(
                        "SELECT meeting_id, start_time, meeting_date FROM meeting_transcripts WHERE meeting_id = ?",
                        (meeting_id,)
                    )
                    all_records = cursor.fetchall()
                    logger.error(f"  Found {len(all_records)} existing record(s) for this meeting_id:")
                    for rec in all_records:
                        logger.error(f"    - start_time={rec[1]}, meeting_date={rec[2]}")
                raise
            self.connection.commit()
            logger.info(f"✓ Saved transcript/chat data for meeting {meeting_id} at {start_time}")
            return True
        except Exception as e:
            logger.error(f"✗ Error saving transcript for meeting {meeting_id}: {str(e)}")
            return False
    
    def save_meeting_summary(self, meeting_id, summary_text, summary_type="structured", start_time=None):
        """Save meeting summary to database.
        
        Args:
            meeting_id (str): Teams meeting ID
            summary_text (str): Summary text generated by Mistral
            summary_type (str): Type of summary ('structured', 'detailed', 'concise')
            start_time (str or datetime): Meeting start time (required for recurring meetings)
        
        Returns:
            bool: True if saved successfully
        """
        if not self.connection:
            logger.error("Not connected to database")
            return False

        cursor = self.connection.cursor()

        try:
            # If start_time not provided, try to get it from meetings_raw
            if start_time is None:
                cursor.execute(
                    "SELECT start_time FROM meetings_raw WHERE meeting_id = ? ORDER BY start_time DESC LIMIT 1",
                    (meeting_id,)
                )
                result = cursor.fetchone()
                if result:
                    start_time = result[0]
                else:
                    logger.warning(f"Could not find start_time for meeting {meeting_id}, using current time")
                    start_time = datetime.now()
            
            # Normalize start_time to consistent format for database storage
            start_time = normalize_datetime_string(start_time)
            
            if not start_time:
                logger.error(f"Could not normalize start_time for meeting {meeting_id}")
                return False
            
            # Extract date from start_time for easier querying
            meeting_date = None
            if start_time and 'T' in start_time:
                meeting_date = start_time.split('T')[0]  # Extract date part (YYYY-MM-DD)
            
            cursor.execute(
                """
                INSERT INTO meeting_summaries (meeting_id, start_time, meeting_date, summary_text, summary_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(meeting_id, start_time) DO UPDATE SET
                    summary_text=excluded.summary_text,
                    summary_type=excluded.summary_type,
                    meeting_date=excluded.meeting_date,
                    updated_at=CURRENT_TIMESTAMP
            """,
                (
                    meeting_id,
                    start_time,
                    meeting_date,
                    summary_text,
                    summary_type,
                    datetime.now(),
                    datetime.now(),
                ),
            )
            self.connection.commit()
            logger.info(f"✓ Saved summary for meeting {meeting_id} at {start_time}")
            return True
        except Exception as e:
            logger.error(f"✗ Error saving summary for meeting {meeting_id}: {str(e)}")
            return False
    
    def get_meeting_summary(self, meeting_id, start_time=None):
        """Retrieve summary for a specific meeting.
        
        Args:
            meeting_id (str): Teams meeting ID
            start_time (str or datetime, optional): Meeting start time (for recurring meetings)
        
        Returns:
            dict: Summary record or None if not found
        """
        if not self.connection:
            return None

        cursor = self.connection.cursor()

        try:
            if start_time:
                # Normalize start_time to match database format
                if isinstance(start_time, datetime):
                    start_time = start_time.isoformat()
                # Normalize to consistent format for database comparison
                normalized_start_time = normalize_datetime_string(start_time) if start_time else None
                cursor.execute(
                    """
                    SELECT meeting_id, start_time, summary_text, summary_type, created_at, updated_at
                    FROM meeting_summaries
                    WHERE meeting_id = ? AND start_time = ?
                    """,
                    (meeting_id, normalized_start_time),
                )
            else:
                # Get most recent summary for this meeting_id
                cursor.execute(
                    """
                    SELECT meeting_id, start_time, summary_text, summary_type, created_at, updated_at
                    FROM meeting_summaries
                    WHERE meeting_id = ?
                    ORDER BY start_time DESC
                    LIMIT 1
                    """,
                    (meeting_id,),
                )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"✗ Error fetching summary for meeting {meeting_id}: {str(e)}")
            return None
    
    def get_meetings_with_summaries(self, limit=20):
        """Get meetings that have both transcripts and summaries.
        
        Returns:
            list: List of dicts with meeting details and summaries
        """
        if not self.connection:
            return []

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
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
                LIMIT ?
            """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings with summaries: {str(e)}")
            return []
    
    def get_meetings_with_transcripts_no_summaries(self, limit=50):
        """Get meetings with transcripts but no summaries yet.
        
        Useful for batch processing summaries.
        
        Returns:
            list: List of dicts with meeting_id and transcript
        """
        if not self.connection:
            return []

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
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
                LIMIT ?
            """,
                (limit,),
            )
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
                WHERE client_name = ?
                ORDER BY start_time DESC
                LIMIT ?
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
                WHERE start_time >= ? AND start_time <= ?
                ORDER BY start_time DESC
            """, (start_date, end_date))
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"✗ Error fetching meetings: {str(e)}")
            return []
    
    def save_satisfaction_analysis(self, meeting_id: str, analysis_result: dict):
        """Save satisfaction analysis results to database.
        
        Args:
            meeting_id: Teams meeting ID
            analysis_result: Dictionary from SatisfactionAnalyzer.analyze_transcript()
        
        Returns:
            bool: True if saved successfully
        """
        if not self.connection:
            logger.error("Not connected to database")
            return False

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO meeting_satisfaction (
                    meeting_id, satisfaction_score, sentiment_polarity, 
                    sentiment_subjectivity, sentiment_reason, risk_score, urgency_level,
                    concerns_json, concern_categories_json, key_phrases_json,
                    analyzed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    satisfaction_score=excluded.satisfaction_score,
                    sentiment_polarity=excluded.sentiment_polarity,
                    sentiment_subjectivity=excluded.sentiment_subjectivity,
                    sentiment_reason=excluded.sentiment_reason,
                    risk_score=excluded.risk_score,
                    urgency_level=excluded.urgency_level,
                    concerns_json=excluded.concerns_json,
                    concern_categories_json=excluded.concern_categories_json,
                    key_phrases_json=excluded.key_phrases_json,
                    updated_at=CURRENT_TIMESTAMP
            """,
                (
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
                ),
            )
            self.connection.commit()
            logger.info(f"✓ Saved satisfaction analysis for meeting {meeting_id}")
            return True
        except Exception as e:
            logger.error(f"✗ Error saving satisfaction analysis for meeting {meeting_id}: {str(e)}")
            return False
    
    def get_satisfaction_analysis(self, meeting_id: str):
        """Retrieve satisfaction analysis for a specific meeting.
        
        Args:
            meeting_id: Teams meeting ID
        
        Returns:
            dict: Satisfaction analysis record or None if not found
        """
        if not self.connection:
            return None

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
                SELECT 
                    meeting_id, satisfaction_score, sentiment_polarity,
                    sentiment_subjectivity, sentiment_reason, risk_score, urgency_level,
                    concerns_json, concern_categories_json, key_phrases_json,
                    analyzed_at, updated_at
                FROM meeting_satisfaction
                WHERE meeting_id = ?
            """,
                (meeting_id,),
            )
            row = cursor.fetchone()
            if row:
                result = dict(row)
                # Parse JSON fields
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
        """Get all satisfaction analyses with meeting details.
        
        Returns:
            list: List of dicts with satisfaction data and meeting info
        """
        if not self.connection:
            return []

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
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
                LIMIT ?
            """,
                (limit,),
            )
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
        """Get meetings with transcripts but no satisfaction analysis yet.
        
        Returns:
            list: List of dicts with meeting_id and transcript
        """
        if not self.connection:
            return []

        cursor = self.connection.cursor()

        try:
            cursor.execute(
                """
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
                LIMIT ?
            """,
                (limit,),
            )
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
                    SET transcript_processed = 1,
                        transcript_processed_at = ?,
                        updated_at = ?
                    WHERE meeting_id = ? AND start_time = ?
                """, (datetime.now().isoformat(), datetime.now().isoformat(), meeting_id, start_time))
            else:
                cursor.execute("""
                    UPDATE meetings_raw
                    SET transcript_processed = 1,
                        transcript_processed_at = ?,
                        updated_at = ?
                    WHERE meeting_id = ?
                    AND (transcript_processed IS NULL OR transcript_processed = 0)
                    ORDER BY start_time DESC
                    LIMIT 1
                """, (datetime.now().isoformat(), datetime.now().isoformat(), meeting_id))
            
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
            logger.error(f"✗ Error clearing database: {str(e)}")
            return False
    
    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            logger.info("✓ Database connection closed")