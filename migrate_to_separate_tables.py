"""
Migration script to move data from meeting_summaries to separate tables:
- structured_summaries
- client_pulse_reports

Run this script once to migrate existing data.
"""
import os
import sys
from src.database.db_setup_postgres import DatabaseManager, normalize_datetime_string
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

def migrate_data():
    """Migrate data from meeting_summaries to separate tables"""
    db = DatabaseManager()
    
    if not db.connect():
        logger.error("Failed to connect to database")
        return False
    
    if not db.create_tables():
        logger.error("Failed to create tables")
        return False
    
    cursor = db.connection.cursor()
    
    try:
        # Check if meeting_summaries table exists and has data
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.tables 
            WHERE table_name = 'meeting_summaries'
        """)
        table_exists = cursor.fetchone()['count'] > 0
        
        if not table_exists:
            logger.info("meeting_summaries table doesn't exist - nothing to migrate")
            return True
        
        # Count records to migrate
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM meeting_summaries
            WHERE summary_type IN ('structured', 'client_pulse')
        """)
        total_count = cursor.fetchone()['count']
        
        if total_count == 0:
            logger.info("No records to migrate from meeting_summaries")
            return True
        
        logger.info(f"Found {total_count} records to migrate")
        
        # Migrate structured summaries
        cursor.execute("""
            SELECT meeting_id, start_time, meeting_date, summary_text, created_at, updated_at
            FROM meeting_summaries
            WHERE summary_type = 'structured'
        """)
        structured_records = cursor.fetchall()
        
        migrated_structured = 0
        for record in structured_records:
            try:
                cursor.execute("""
                    INSERT INTO structured_summaries 
                    (meeting_id, start_time, meeting_date, summary_text, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (meeting_id, start_time) DO NOTHING
                """, (
                    record['meeting_id'],
                    record['start_time'],
                    record['meeting_date'],
                    record['summary_text'],
                    record['created_at'],
                    record['updated_at']
                ))
                migrated_structured += 1
            except Exception as e:
                logger.warning(f"Error migrating structured summary {record['meeting_id']}: {e}")
        
        # Migrate client pulse reports
        # First, get client_name from meetings_raw if available
        cursor.execute("""
            SELECT 
                ms.meeting_id,
                ms.start_time,
                ms.meeting_date,
                ms.summary_text,
                ms.created_at,
                ms.updated_at,
                COALESCE(mr.client_name, '') as client_name
            FROM meeting_summaries ms
            LEFT JOIN meetings_raw mr ON ms.meeting_id = mr.meeting_id AND ms.start_time = mr.start_time
            WHERE ms.summary_type = 'client_pulse'
        """)
        pulse_records = cursor.fetchall()
        
        migrated_pulse = 0
        for record in pulse_records:
            try:
                # Try to extract client_name from subject if not in meetings_raw
                client_name = record['client_name'] or ''
                if not client_name or client_name.strip() == '':
                    cursor.execute("""
                        SELECT subject FROM meetings_raw 
                        WHERE meeting_id = %s AND start_time = %s
                    """, (record['meeting_id'], record['start_time']))
                    subject_result = cursor.fetchone()
                    if subject_result and subject_result['subject']:
                        subject = subject_result['subject']
                        if ':' in subject:
                            client_name = subject.split(':')[0].strip()
                
                cursor.execute("""
                    INSERT INTO client_pulse_reports 
                    (meeting_id, start_time, meeting_date, client_name, summary_text, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (meeting_id, start_time) DO NOTHING
                """, (
                    record['meeting_id'],
                    record['start_time'],
                    record['meeting_date'],
                    client_name if client_name else None,
                    record['summary_text'],
                    record['created_at'],
                    record['updated_at']
                ))
                migrated_pulse += 1
            except Exception as e:
                logger.warning(f"Error migrating client pulse report {record['meeting_id']}: {e}")
        
        db.connection.commit()
        
        logger.info(f"✅ Migration complete:")
        logger.info(f"   - Migrated {migrated_structured} structured summaries")
        logger.info(f"   - Migrated {migrated_pulse} client pulse reports")
        logger.info(f"   - Total: {migrated_structured + migrated_pulse} records")
        
        return True
        
    except Exception as e:
        db.connection.rollback()
        logger.error(f"Error during migration: {e}")
        return False
    finally:
        db.close()

if __name__ == "__main__":
    print("Starting migration from meeting_summaries to separate tables...")
    success = migrate_data()
    if success:
        print("✅ Migration completed successfully!")
        print("\nNote: The old 'meeting_summaries' table still exists with old data.")
        print("You can drop it later if you want, but it's safe to keep it for backup.")
    else:
        print("❌ Migration failed. Check logs for details.")
        sys.exit(1)

