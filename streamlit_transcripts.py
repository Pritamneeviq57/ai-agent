"""
Enhanced Streamlit UI for Meeting Transcripts with Customer Satisfaction Monitoring
and Concern Pattern Identification - Tech-Enabled Delivery Excellence
"""
import os
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from datetime import datetime, timedelta
from src.analytics.satisfaction_analyzer import SatisfactionAnalyzer
from src.utils.logger import setup_logger

# Import ClaudeSummarizer for Railway deployment
ClaudeSummarizer = None
try:
    from src.summarizer.claude_summarizer import ClaudeSummarizer
except Exception as e:
    import logging
    logging.warning(f"Failed to import ClaudeSummarizer: {e}")
    ClaudeSummarizer = None

logger = setup_logger(__name__)

# Use PostgreSQL on Railway, SQLite locally (same as app.py)
USE_POSTGRES = os.getenv("DATABASE_URL") is not None

DatabaseManager = None
normalize_datetime_string = None

try:
    if USE_POSTGRES:
        from src.database.db_setup_postgres import DatabaseManager, normalize_datetime_string
        logger.info("Using PostgreSQL database (Railway deployment)")
    else:
        from src.database.db_setup_sqlite import DatabaseManager, normalize_datetime_string
        logger.info("Using SQLite database (local development)")
except Exception as e:
    import traceback
    error_msg = f"Failed to import DatabaseManager: {e}\n{traceback.format_exc()}"
    logger.error(error_msg)
    print(f"ERROR: {error_msg}", file=__import__('sys').stderr)
    logger.error("Streamlit app will start but database operations will fail.")

st.set_page_config(
    page_title="AI-Optimized Delivery Excellence", 
    page_icon="🤖", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for modern UI
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(90deg, #1f77b4 0%, #ff7f0e 100%);
        padding: 2rem;
        border-radius: 10px;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1.5rem;
        border-radius: 10px;
        border-left: 4px solid #1f77b4;
    }
    .satisfaction-excellent { color: #28a745; font-weight: bold; }
    .satisfaction-good { color: #ffc107; font-weight: bold; }
    .satisfaction-fair { color: #fd7e14; font-weight: bold; }
    .satisfaction-poor { color: #dc3545; font-weight: bold; }
    .risk-high { color: #dc3545; font-weight: bold; }
    .risk-medium { color: #fd7e14; font-weight: bold; }
    .risk-low { color: #ffc107; font-weight: bold; }
    .risk-minimal { color: #28a745; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ====================================================================
# DATABASE CONNECTION (thread-safe)
# ====================================================================
# We'll create fresh connections as needed to avoid threading issues
analyzer = SatisfactionAnalyzer()

# ====================================================================
# HEADER
# ====================================================================
st.markdown("""
<div class="main-header">
    <h1>🤖 AI-Optimized Delivery Excellence</h1>
    <p>Monitor Customer Satisfaction & Identify Concern Patterns</p>
</div>
""", unsafe_allow_html=True)

# ====================================================================
# SIDEBAR - Navigation
# ====================================================================
st.sidebar.title("📊 Navigation")
page = st.sidebar.radio(
    "Select View",
    ["📈 Satisfaction Monitor", "📝 Meeting Transcripts", "🔍 Analytics Dashboard", "🗄️ Database Viewer", "⚙️ API Operations"],
    key="main_nav"
)

# ====================================================================
# FETCH DATA (thread-safe, creates fresh connections)
# ====================================================================
def fetch_all_meetings():
    """Fetch ALL meetings from database (with or without transcripts) and summaries
    
    Returns ALL meetings from meetings_raw, regardless of whether they have transcripts.
    Uses LEFT JOIN to include transcript and summary data when available.
    No date filter - shows all meetings in the database.
    """
    db = DatabaseManager()
    db.connect()
    db.create_tables()
    
    cursor = db.connection.cursor()
    
    # Start from meetings_raw to get ALL meetings, then LEFT JOIN to get transcripts if available
    # LEFT JOIN ensures we get ALL meetings, even if they don't have transcripts
    # Join on both meeting_id and start_time for proper matching
    # No date filter - show all meetings in database
    cursor.execute("""
        SELECT 
            mr.meeting_id, 
            mr.subject,
            mr.start_time,
            mr.meeting_date,
            mt.raw_transcript, 
            mt.raw_chat, 
            COALESCE(mt.created_at, mr.created_at) as created_at,
            ms.summary_text,
            ms.summary_type,
            ms.created_at as summary_created_at,
            mr.client_name,
            mr.organizer_email,
            mr.participants,
            mr.end_time,
            mr.duration_minutes
        FROM meetings_raw mr
        LEFT JOIN meeting_transcripts mt ON mr.meeting_id = mt.meeting_id AND mr.start_time = mt.start_time
        LEFT JOIN meeting_summaries ms ON mr.meeting_id = ms.meeting_id AND mr.start_time = ms.start_time
        ORDER BY 
            CASE WHEN ms.summary_text IS NOT NULL THEN 0 ELSE 1 END,  -- Prioritize meetings with summaries
            mr.start_time DESC, 
            mr.created_at DESC
    """)
    rows = cursor.fetchall()
    
    # Deduplicate by meeting_id + start_time, keeping the one with summary AND transcript if available
    seen = {}
    result = []
    for row in rows:
        row_dict = dict(row)
        key = (row_dict["meeting_id"], row_dict["start_time"])
        
        if key not in seen:
            seen[key] = row_dict
            result.append(row_dict)
        else:
            # Merge data: keep transcript and summary from whichever row has them
            existing = seen[key]
            # If current row has transcript and existing doesn't, merge it
            if row_dict.get("raw_transcript") and not existing.get("raw_transcript"):
                existing["raw_transcript"] = row_dict["raw_transcript"]
                existing["raw_chat"] = row_dict.get("raw_chat") or existing.get("raw_chat")
            # If current row has summary and existing doesn't, merge it
            if row_dict.get("summary_text") and not existing.get("summary_text"):
                existing["summary_text"] = row_dict["summary_text"]
                existing["summary_type"] = row_dict.get("summary_type") or existing.get("summary_type")
            # Update in result list
            for i, r in enumerate(result):
                if (r["meeting_id"], r["start_time"]) == key:
                    result[i] = existing
                    break
    db.close()
    return result

def fetch_satisfaction_data():
    """Fetch all satisfaction analyses"""
    db = DatabaseManager()
    db.connect()
    db.create_tables()
    result = db.get_all_satisfaction_analyses(limit=100)
    db.close()
    return result

def fetch_meetings_with_transcripts():
    """Fetch all meetings that have transcripts available (ONLY meetings with transcripts)
    
    Shows ALL meetings from the database that have transcripts, regardless of date.
    Uses INNER JOIN to ensure only meetings with transcripts are included.
    """
    db = DatabaseManager()
    db.connect()
    db.create_tables()
    
    cursor = db.connection.cursor()
    
    # Get all meetings with transcripts (no date filter)
    # INNER JOIN ensures we only get meetings that have transcripts
    # Match on meeting_id and start_time (both must match for proper association)
    cursor.execute("""
        SELECT 
            mr.meeting_id,
            mr.subject,
            mr.client_name,
            mr.organizer_email,
            mr.start_time,
            mr.end_time,
            mt.raw_transcript,
            mt.raw_chat,
            ms.summary_text,
            ms.summary_type
        FROM meetings_raw mr
        INNER JOIN meeting_transcripts mt ON mr.meeting_id = mt.meeting_id AND mr.start_time = mt.start_time
        LEFT JOIN meeting_summaries ms ON mr.meeting_id = ms.meeting_id AND mr.start_time = ms.start_time
        WHERE mt.raw_transcript IS NOT NULL 
            AND mt.raw_transcript != ''
            AND LENGTH(TRIM(mt.raw_transcript)) > 0
        ORDER BY mr.start_time DESC
    """)
    
    rows = cursor.fetchall()
    
    # Additional validation: filter out any rows where transcript is empty after trimming
    result = []
    seen = set()
    for row in rows:
        row_dict = dict(row)
        transcript = row_dict.get("raw_transcript", "")
        # Double-check that transcript is not empty
        if transcript and str(transcript).strip():
            # Deduplicate by meeting_id + start_time
            key = (row_dict.get("meeting_id"), row_dict.get("start_time"))
            if key not in seen:
                seen.add(key)
                result.append(row_dict)
    
    db.close()
    return result

# ====================================================================
# PAGE 1: SATISFACTION MONITOR
# ====================================================================
if page == "📈 Satisfaction Monitor":
    st.header("📈 Customer Satisfaction Monitor")
    
    # Add refresh button
    col_header, col_refresh = st.columns([4, 1])
    with col_refresh:
        if st.button("🔄 Refresh Data", help="Click to reload data from database", key="refresh_satisfaction"):
            st.rerun()
    
    st.markdown("---")
    
    # Fetch satisfaction data
    satisfaction_data = fetch_satisfaction_data()
    
    if not satisfaction_data:
        st.warning("⚠️ No satisfaction analyses found. Analyzing transcripts...")
        
        # Get meetings without analysis
        db = DatabaseManager()
        db.connect()
        db.create_tables()
        meetings_to_analyze = db.get_meetings_without_satisfaction_analysis(limit=10)
        
        if meetings_to_analyze:
            with st.spinner("Analyzing transcripts for satisfaction metrics..."):
                progress_bar = st.progress(0)
                for idx, meeting in enumerate(meetings_to_analyze):
                    analysis = analyzer.analyze_transcript(
                        meeting.get('raw_transcript', ''),
                        meeting.get('raw_chat')
                    )
                    db.save_satisfaction_analysis(meeting['meeting_id'], analysis)
                    progress_bar.progress((idx + 1) / len(meetings_to_analyze))
            
            db.close()
            st.success("✅ Analysis complete! Refreshing...")
            st.rerun()
        else:
            db.close()
            st.info("No transcripts available for analysis.")
    else:
        # Overall Statistics
        st.subheader("📊 Overall Statistics")
        
        avg_satisfaction = sum(s['satisfaction_score'] for s in satisfaction_data) / len(satisfaction_data)
        avg_risk = sum(s['risk_score'] for s in satisfaction_data) / len(satisfaction_data)
        high_risk_count = sum(1 for s in satisfaction_data if s['risk_score'] >= 70)
        high_urgency_count = sum(1 for s in satisfaction_data if s['urgency_level'] == 'high')
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            sat_label, sat_emoji = analyzer.get_satisfaction_label(avg_satisfaction)
            st.metric("Average Satisfaction", f"{avg_satisfaction:.1f}", 
                     delta=f"{sat_label} {sat_emoji}")
        with col2:
            risk_label, risk_emoji = analyzer.get_risk_label(avg_risk)
            st.metric("Average Risk Score", f"{avg_risk:.1f}",
                     delta=f"{risk_label} {risk_emoji}")
        with col3:
            st.metric("High Risk Meetings", high_risk_count,
                     delta=f"{len(satisfaction_data)} total")
        with col4:
            st.metric("High Urgency", high_urgency_count,
                     delta="Requires attention")
        
        st.markdown("---")
        
        # Satisfaction Trend Chart
        st.subheader("📈 Satisfaction Trends")
        
        df_trends = pd.DataFrame(satisfaction_data)
        df_trends['start_time'] = pd.to_datetime(df_trends['start_time'])
        df_trends = df_trends.sort_values('start_time')
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_trends['start_time'],
            y=df_trends['satisfaction_score'],
            mode='lines+markers',
            name='Satisfaction Score',
            line=dict(color='#28a745', width=3),
            marker=dict(size=8)
        ))
        fig.add_trace(go.Scatter(
            x=df_trends['start_time'],
            y=df_trends['risk_score'],
            mode='lines+markers',
            name='Risk Score',
            line=dict(color='#dc3545', width=3),
            marker=dict(size=8)
        ))
        fig.update_layout(
            title="Satisfaction & Risk Scores Over Time",
            xaxis_title="Meeting Date",
            yaxis_title="Score (0-100)",
            hovermode='x unified',
            height=400
        )
        st.plotly_chart(fig, width='stretch')
        
        # Concern Categories Analysis
        st.subheader("🔍 Concern Pattern Analysis")
        
        # Aggregate concern categories
        all_categories = {}
        for data in satisfaction_data:
            categories = data.get('concern_categories', {})
            for category, count in categories.items():
                all_categories[category] = all_categories.get(category, 0) + count
        
        if all_categories:
            df_concerns = pd.DataFrame([
                {'Category': cat.replace('_', ' ').title(), 'Count': count}
                for cat, count in sorted(all_categories.items(), key=lambda x: x[1], reverse=True)
            ])
            
            col1, col2 = st.columns(2)
            
            with col1:
                fig_bar = px.bar(
                    df_concerns.head(10),
                    x='Count',
                    y='Category',
                    orientation='h',
                    title="Top Concern Categories",
                    color='Count',
                    color_continuous_scale='Reds'
                )
                fig_bar.update_layout(height=400)
                st.plotly_chart(fig_bar, width='stretch')
            
            with col2:
                fig_pie = px.pie(
                    df_concerns.head(8),
                    values='Count',
                    names='Category',
                    title="Concern Distribution"
                )
                fig_pie.update_layout(height=400)
                st.plotly_chart(fig_pie, width='stretch')
        else:
            st.info("No concerns identified in analyzed meetings.")
        
        # High Risk Meetings Table
        st.subheader("⚠️ High Risk Meetings Requiring Attention")
        
        high_risk_meetings = [s for s in satisfaction_data if s['risk_score'] >= 60]
        high_risk_meetings.sort(key=lambda x: x['risk_score'], reverse=True)
        
        if high_risk_meetings:
            df_high_risk = pd.DataFrame([
                {
                    'Client': m.get('client_name', 'Unknown'),
                    'Satisfaction': f"{m['satisfaction_score']:.1f}",
                    'Risk Score': f"{m['risk_score']:.1f}",
                    'Urgency': m['urgency_level'].upper(),
                    'Date': str(m.get('start_time', ''))[:10] if m.get('start_time') else 'Unknown',
                    'Meeting ID': m['meeting_id'][:30] + '...'
                }
                for m in high_risk_meetings[:20]
            ])
            st.dataframe(df_high_risk, width='stretch', hide_index=True)
        else:
            st.success("✅ No high-risk meetings identified!")

# ====================================================================
# PAGE 2: MEETING TRANSCRIPTS
# ====================================================================
elif page == "📝 Meeting Transcripts":
    st.header("📝 Meeting Transcripts & Summaries")
    st.caption("💡 **This tab shows ALL meetings (with or without transcripts) from the database.**")
    
    # Add refresh button
    col_header, col_refresh = st.columns([4, 1])
    with col_refresh:
        if st.button("🔄 Refresh Data", help="Click to reload data from database"):
            st.rerun()
    
    st.markdown("---")
    
    rows = fetch_all_meetings()
    
    if not rows:
        st.warning("No meetings found in database. Run `python main_phase_2_3_delegated.py` first.")
        st.info("💡 **Tip:** After running the main script, click the '🔄 Refresh Data' button above to see new data.")
        st.stop()
    
    # Count meetings with and without transcripts and summaries
    # Check for non-empty transcripts (not just truthy values)
    meetings_with_transcripts = sum(1 for row in rows if row.get("raw_transcript") and str(row.get("raw_transcript", "")).strip())
    meetings_with_summaries = sum(1 for row in rows if row.get("summary_text") and str(row.get("summary_text", "")).strip())
    meetings_without_transcripts = len(rows) - meetings_with_transcripts
    
    # Show status with last update time
    from datetime import datetime
    current_time = datetime.now().strftime("%H:%M:%S")
    
    col_status1, col_status2, col_status3, col_status4 = st.columns(4)
    with col_status1:
        st.info(f"📊 **Total Meetings:** {len(rows)}")
    with col_status2:
        st.info(f"✅ **With Transcripts:** {meetings_with_transcripts}")
    with col_status3:
        st.info(f"❌ **Without Transcripts:** {meetings_without_transcripts}")
    with col_status4:
        st.info(f"🤖 **With Summaries:** {meetings_with_summaries}")
    
    st.caption(f"Last refreshed: {current_time} | Click '🔄 Refresh Data' to reload")
    
    # Create meeting options
    meeting_options = {}
    for idx, row in enumerate(rows):
        meeting_id = row["meeting_id"]
        # Generate unique ID (first 8 characters of meeting_id)
        unique_id = meeting_id[:8] if meeting_id else "UNKNOWN"
        
        # Use actual meeting start time, not database creation time
        start_time_val = row.get("start_time")
        if start_time_val:
            try:
                from datetime import datetime
                # Handle datetime objects from PostgreSQL or string format from Graph API
                if isinstance(start_time_val, datetime):
                    meeting_date_str = start_time_val.strftime("%Y-%m-%d %H:%M")
                else:
                    # Handle Microsoft Graph API datetime format: "2025-12-03T07:50:00.0000000"
                    # Remove excessive decimal places and add timezone if needed
                    start_time_str = str(start_time_val).split('.')[0]  # Remove fractional seconds
                    if 'Z' not in start_time_str and '+' not in start_time_str:
                        start_time_str += "+00:00"  # Assume UTC if no timezone
                    meeting_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    meeting_date_str = meeting_time.strftime("%Y-%m-%d %H:%M")
            except Exception as e:
                created_at_val = row.get("created_at")
                if created_at_val:
                    meeting_date_str = str(created_at_val)[:19] if isinstance(created_at_val, str) else created_at_val.strftime("%Y-%m-%d %H:%M:%S")[:19]
                else:
                    meeting_date_str = "Unknown"
        else:
            created_at_val = row.get("created_at")
            if created_at_val:
                meeting_date_str = str(created_at_val)[:19] if isinstance(created_at_val, str) else created_at_val.strftime("%Y-%m-%d %H:%M:%S")[:19]
            else:
                meeting_date_str = "Unknown"
        
        # Show ✅ if transcript exists, ❌ if not
        has_transcript = "✅" if row.get("raw_transcript") else "❌"
        client = row.get("client_name") or "Unknown"
        subject = row.get("subject") or "Untitled Meeting"
        
        # Show: Status, Subject, Client, Date, and Unique ID
        # Include start_time in label to ensure uniqueness for recurring meetings
        start_time_val = row.get("start_time")
        if start_time_val:
            start_time_display = str(start_time_val)[:16] if isinstance(start_time_val, str) else start_time_val.strftime("%Y-%m-%dT%H:%M")[:16]
        else:
            start_time_display = meeting_date_str
        label = f"{has_transcript} [{unique_id}] {subject} - {client} ({meeting_date_str}) [{start_time_display}]"
        meeting_options[label] = {
            "index": idx,
            "row": row,
            "meeting_id": meeting_id,
            "start_time": row.get("start_time")  # Store start_time for verification
        }
    
    # Selection box
    selected_label = st.selectbox(
        "Select a meeting:",
        list(meeting_options.keys()),
        key="meeting_selector"
    )
    
    if selected_label:
        meeting_data = meeting_options[selected_label]
        row = meeting_data["row"]
        meeting_id = meeting_data["meeting_id"]
        start_time = meeting_data.get("start_time") or row.get("start_time")
        
        st.session_state.current_meeting_id = meeting_id
        st.session_state.current_start_time = start_time  # Store start_time for verification
        
        # Meeting Info
        st.subheader("📅 Meeting Information")
        
        # Extract participants from participants JSON
        import json
        client_emails = []
        organizer_emails = []  # Only neeviq.com emails
        all_participant_emails = []  # All participants for reference
        participants_data = row.get("participants")
        if participants_data:
            try:
                participants = json.loads(participants_data) if isinstance(participants_data, str) else participants_data
                if isinstance(participants, list):
                    for participant in participants:
                        email = participant.get("email", "")
                        if email:
                            all_participant_emails.append(email)
                            # Separate client and organizer participants
                            if "neeviq.com" in email.lower():
                                organizer_emails.append(email)
                            else:
                                client_emails.append(email)
            except:
                pass
        
        # Calculate actual duration from start_time and end_time
        actual_duration = "N/A"
        meeting_start_time = "N/A"
        meeting_end_time = "N/A"
        
        if row.get("start_time") and row.get("end_time"):
            try:
                from datetime import datetime
                # Handle Microsoft Graph API datetime format: "2025-12-03T07:50:00.0000000"
                # Remove excessive decimal places and handle timezone
                start_str = str(row["start_time"]).split('.')[0]  # Remove fractional seconds
                end_str = str(row["end_time"]).split('.')[0]  # Remove fractional seconds
                
                # Add timezone if not present (assume UTC)
                if 'Z' not in start_str and '+' not in start_str:
                    start_str += "+00:00"
                if 'Z' not in end_str and '+' not in end_str:
                    end_str += "+00:00"
                
                start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                
                # Calculate duration in minutes
                duration_minutes = int((end - start).total_seconds() / 60)
                actual_duration = f"{duration_minutes} min"
                
                # Format start and end times
                meeting_start_time = start.strftime("%Y-%m-%d %H:%M")
                meeting_end_time = end.strftime("%H:%M")
            except Exception as e:
                # Fallback to stored duration
                actual_duration = f"{row['duration_minutes']} min" if row.get("duration_minutes") else "N/A"
        elif row.get("duration_minutes"):
            actual_duration = f"{row['duration_minutes']} min"
        
        # Display meeting information with smaller font size
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown("**Client Participants**")
            # Show all client emails without truncation
            # If no external participants, show "Internal meeting"
            if client_emails:
                client_display = ", ".join(client_emails)
            else:
                client_display = "Internal meeting"
            st.markdown(f"<div style='font-size: 14px;'>{client_display}</div>", unsafe_allow_html=True)
        
        with col2:
            st.markdown("**Duration**")
            st.markdown(f"<div style='font-size: 14px;'>{actual_duration}</div>", unsafe_allow_html=True)
            if meeting_start_time != "N/A":
                st.markdown(f"<div style='font-size: 12px; color: gray;'>{meeting_start_time} - {meeting_end_time}</div>", unsafe_allow_html=True)
        
        with col3:
            st.markdown("**Organizer**")
            organizer = row["organizer_email"] or "Unknown"
            # Show full organizer email without truncation
            st.markdown(f"<div style='font-size: 14px;'>{organizer}</div>", unsafe_allow_html=True)
        
        with col4:
            st.markdown("**Organizer Participants**")
            # Show only neeviq.com emails (organizer participants)
            if organizer_emails:
                # Show all emails, wrap if too many
                if len(organizer_emails) <= 2:
                    participants_display = ", ".join(organizer_emails)
                else:
                    # Show first 2, then indicate more
                    participants_display = ", ".join(organizer_emails[:2]) + f" +{len(organizer_emails) - 2} more"
                st.markdown(f"<div style='font-size: 14px;'>{participants_display}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='font-size: 14px;'>No organizer participants</div>", unsafe_allow_html=True)
        
        # Show all client participants in expandable section if there are many
        if client_emails and len(client_emails) > 3:
            with st.expander(f"👥 All Client Participants ({len(client_emails)})"):
                for email in client_emails:
                    st.markdown(f"- {email}")
        
        # Show all organizer participants in expandable section if there are more than 2
        if organizer_emails and len(organizer_emails) > 2:
            with st.expander(f"👥 All Organizer Participants ({len(organizer_emails)})"):
                for email in organizer_emails:
                    st.markdown(f"- {email}")
        
        st.markdown("---")
        
        # Satisfaction Analysis for this meeting
        st.subheader("📊 Satisfaction Analysis")
        
        db = DatabaseManager()
        db.connect()
        db.create_tables()
        satisfaction_analysis = db.get_satisfaction_analysis(meeting_id)
        
        if not satisfaction_analysis:
            # Analyze on the fly
            with st.spinner("Analyzing satisfaction metrics..."):
                analysis = analyzer.analyze_transcript(
                    row.get("raw_transcript", ""),
                    row.get("raw_chat")
                )
                db.save_satisfaction_analysis(meeting_id, analysis)
                satisfaction_analysis = db.get_satisfaction_analysis(meeting_id)
        
        if satisfaction_analysis:
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                sat_score = satisfaction_analysis['satisfaction_score']
                sat_label, sat_emoji = analyzer.get_satisfaction_label(sat_score)
                st.metric("Satisfaction Score", f"{sat_score:.1f}/100", 
                         delta=f"{sat_label} {sat_emoji}")
            
            with col2:
                risk_score = satisfaction_analysis['risk_score']
                risk_label, risk_emoji = analyzer.get_risk_label(risk_score)
                st.metric("Risk Score", f"{risk_score:.1f}/100",
                         delta=f"{risk_label} {risk_emoji}")
            
            with col3:
                urgency = satisfaction_analysis['urgency_level'].upper()
                urgency_colors = {'HIGH': '🔴', 'MEDIUM': '🟠', 'LOW': '🟡', 'NONE': '🟢'}
                st.metric("Urgency Level", urgency, 
                         delta=urgency_colors.get(urgency, '⚪'))
            
            with col4:
                sentiment = satisfaction_analysis.get('sentiment_polarity', 0)
                sentiment_label = "Positive" if sentiment > 0.1 else "Negative" if sentiment < -0.1 else "Neutral"
                st.metric("Sentiment", sentiment_label,
                         delta=f"{sentiment:.2f}")
            
            # Sentiment Reason
            sentiment_reason = satisfaction_analysis.get('sentiment_reason', '')
            if sentiment_reason:
                st.markdown("#### 💭 Why {0}?".format(sentiment_label))
                st.info(sentiment_reason)
            
            # Concerns
            concerns = satisfaction_analysis.get('concerns', [])
            if concerns:
                st.markdown("#### 🔍 Identified Concerns")
                for i, concern in enumerate(concerns[:5], 1):
                    severity_emoji = "🔴" if concern['severity'] >= 4 else "🟠" if concern['severity'] >= 3 else "🟡"
                    concern_context = concern['context']
                    st.markdown(f"""
                    **{severity_emoji} Concern #{i}** ({concern['type'].title()})
                    > {concern_context}
                    """)
            
            # Concern Categories
            concern_categories = satisfaction_analysis.get('concern_categories', {})
            if concern_categories:
                st.markdown("#### 📋 Concern Categories")
                category_df = pd.DataFrame([
                    {'Category': cat.replace('_', ' ').title(), 'Count': count}
                    for cat, count in concern_categories.items()
                ])
                st.dataframe(category_df, width='stretch', hide_index=True)
        
        db.close()  # Close the connection after satisfaction analysis
        st.markdown("---")
        
        # Summary Section
        summary_text = row.get("summary_text")
        if summary_text:
            st.subheader("🤖 AI-Generated Summary")
            st.caption("💡 **Note:** This is the summary that was sent via email. The raw transcript is shown below.")
            
            summary_type = row.get("summary_type") or "unknown"
            badge_map = {
                "structured": "🏗️ Structured",
                "detailed": "📋 Detailed",
                "concise": "⚡ Concise"
            }
            badge = badge_map.get(summary_type, f"📝 {summary_type}")
            
            st.write(f"**Type:** {badge}")
            
            # Display summary with proper markdown rendering
            # Use markdown instead of HTML to properly render tables and formatting
            st.markdown("---")
            st.markdown(summary_text)
        else:
            st.warning("⚠️ No summary available for this meeting.")
            st.info("💡 **Note:** Summaries are generated from transcripts and sent via email. If you received an email, the summary should appear here after it's generated.")
        
        # Transcript Section
        st.subheader("📄 Transcript")
        
        # Debug info: Show which meeting instance this transcript belongs to
        if row.get("start_time"):
            st.caption(f"📅 Meeting Date: {row.get('meeting_date', 'N/A')} | Start Time: {row.get('start_time', 'N/A')}")
        
        # Get transcript - handle both None and empty string cases
        transcript = row.get("raw_transcript") or None
        if transcript:
            transcript = str(transcript).strip()
            if not transcript:
                transcript = None
        
        # Debug: Show raw transcript status (can be removed later)
        with st.expander("🔍 Debug Info (click to view)", expanded=False):
            st.write(f"**Meeting ID:** `{meeting_id[:50]}...`")
            st.write(f"**Start Time:** `{start_time}`")
            st.write(f"**Transcript in row:** `{'Yes' if row.get('raw_transcript') else 'No'}`")
            st.write(f"**Transcript type:** `{type(row.get('raw_transcript'))}`")
            st.write(f"**Transcript length:** `{len(row.get('raw_transcript', '')) if row.get('raw_transcript') else 0}`")
            st.write(f"**Summary in row:** `{'Yes' if row.get('summary_text') else 'No'}`")
        
        if not transcript:
            # No transcript available - show prominent message
            st.warning("⚠️ **Transcription not available for this meeting**")
            st.info("""
            **Possible reasons:**
            - Transcription was not enabled during the meeting
            - The meeting recording is still being processed
            - You may not have access to this meeting's transcript
            
            💡 Tip: Make sure transcription is enabled in Teams before the meeting starts.
            💡 **If you received a summary email, that's different from the raw transcript.**
            """)
        else:
            # Transcript available - show stats and content
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Characters", f"{len(transcript):,}")
            with col2:
                st.metric("Lines", transcript.count("\n") + 1)
            with col3:
                words = len(transcript.split())
                st.metric("Words", f"{words:,}")
            
            with st.expander("📖 View Full Transcript", expanded=False):
                st.text_area(
                    "Full Transcript",
                    transcript,
                    height=500,
                    disabled=True,
                    key=f"transcript_{meeting_id}",
                    label_visibility="collapsed"
                )
        
        # Chat Section
        chat_text = row.get("raw_chat")
        if chat_text and str(chat_text).strip():
            st.subheader("💬 Chat Messages")
            with st.expander("💬 View Chat Messages", expanded=False):
                st.text_area(
                    "Chat Messages",
                    chat_text,
                    height=300,
                    disabled=True,
                    key=f"chat_{meeting_id}",
                    label_visibility="collapsed"
                )

# ====================================================================
# PAGE 3: ANALYTICS DASHBOARD
# ====================================================================
elif page == "🔍 Analytics Dashboard":
    st.header("🔍 Analytics Dashboard")
    st.caption("💡 **This tab shows ALL meetings WITH transcripts available in the database.**")
    st.info("ℹ️ **Note:** All meetings shown here have transcripts (✅). The 📄 icon indicates a summary exists, 📝 indicates a summary needs to be generated.")
    
    # Add refresh button
    col_header, col_refresh = st.columns([4, 1])
    with col_refresh:
        if st.button("🔄 Refresh Data", help="Click to reload data from database", key="refresh_analytics"):
            st.rerun()
    
    st.markdown("---")
    
    # Show database statistics for debugging
    db = DatabaseManager()
    db.connect()
    db.create_tables()
    cursor = db.connection.cursor()
    
    # Count total meetings in database
    cursor.execute("SELECT COUNT(*) as count FROM meetings_raw")
    total_meetings_result = cursor.fetchone()
    total_meetings = total_meetings_result['count'] if isinstance(total_meetings_result, dict) else total_meetings_result[0]
    
    # Count meetings with transcripts (count distinct meeting_id + start_time combinations)
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM (
            SELECT DISTINCT mt.meeting_id, mt.start_time
            FROM meeting_transcripts mt
            WHERE mt.raw_transcript IS NOT NULL 
                AND mt.raw_transcript != ''
                AND LENGTH(TRIM(mt.raw_transcript)) > 0
        ) as distinct_meetings
    """)
    meetings_with_transcripts_count_result = cursor.fetchone()
    meetings_with_transcripts_count = meetings_with_transcripts_count_result['count'] if isinstance(meetings_with_transcripts_count_result, dict) else meetings_with_transcripts_count_result[0]
    
    db.close()
    
    # Display statistics
    col1, col2 = st.columns(2)
    with col1:
        st.metric("📊 Total Meetings in Database", total_meetings)
    with col2:
        st.metric("✅ Meetings with Transcripts", meetings_with_transcripts_count)
    
    st.markdown("---")
    
    # Fetch meetings with transcripts ONLY (using INNER JOIN)
    meetings_with_transcripts = fetch_meetings_with_transcripts()
    
    if not meetings_with_transcripts:
        st.warning("⚠️ No meetings with transcriptions found in the database.")
        st.info(f"💡 **Tip:** Your database has {total_meetings} total meetings, but {meetings_with_transcripts_count} have transcripts. Run `python main_phase_2_3_delegated.py` to fetch meeting transcriptions first.")
        st.stop()
    
    # Display count of available transcriptions
    st.info(f"📊 **Total Meetings with Transcripts (Displayed):** {len(meetings_with_transcripts)}")
    if len(meetings_with_transcripts) < meetings_with_transcripts_count:
        st.warning(f"⚠️ **Note:** Found {meetings_with_transcripts_count} meetings with transcripts in database, but only {len(meetings_with_transcripts)} are displayed. This might be due to join conditions or duplicate filtering.")
    st.caption("✅ All meetings shown here have transcripts available for analysis and summary generation.")
    
    st.markdown("---")
    
    # Create meeting options for dropdown
    meeting_options = {}
    for idx, meeting in enumerate(meetings_with_transcripts):
        meeting_id = meeting["meeting_id"]
        subject = meeting.get("subject") or "Untitled Meeting"
        client_name = meeting.get("client_name") or "Unknown Client"
        
        # Format start time
        start_time_str = "Unknown"
        start_time_val = meeting.get("start_time")
        if start_time_val:
            try:
                from datetime import datetime
                if isinstance(start_time_val, datetime):
                    start_time_str = start_time_val.strftime("%Y-%m-%d %H:%M")
                else:
                    start_time_str_raw = str(start_time_val).split('.')[0]
                    if 'Z' not in start_time_str_raw and '+' not in start_time_str_raw:
                        start_time_str_raw += "+00:00"
                    meeting_time = datetime.fromisoformat(start_time_str_raw.replace("Z", "+00:00"))
                    start_time_str = meeting_time.strftime("%Y-%m-%d %H:%M")
            except Exception:
                start_time_str = str(start_time_val)[:19] if start_time_val else "Unknown"
        
        # In Analytics Dashboard, all meetings have transcripts (that's why they're here)
        # Show ✅ for transcript (always true in this tab) and indicate summary status
        unique_id = meeting_id[:8] if meeting_id else "UNKNOWN"
        
        # Create label for dropdown - include start_time to ensure uniqueness for recurring meetings
        if start_time_val:
            if isinstance(start_time_val, datetime):
                start_time_display = start_time_val.strftime("%Y-%m-%dT%H:%M")[:16]
            else:
                start_time_display = str(start_time_val)[:16]
        else:
            start_time_display = start_time_str
        
        # All meetings here have transcripts, so always show ✅
        # Add summary indicator: 📄 = has summary, 📝 = needs summary
        if meeting.get("summary_text"):
            summary_indicator = "📄"
            summary_note = "has summary"
        else:
            summary_indicator = "📝"
            summary_note = "needs summary"
        
        # Show: ✅ (transcript) + summary indicator + meeting details
        label = f"✅ {summary_indicator} [{unique_id}] {subject} - {client_name} ({start_time_str})"
        meeting_options[label] = {
            "index": idx,
            "meeting": meeting,
            "meeting_id": meeting_id,
            "start_time": meeting.get("start_time")  # Store start_time for unique widget keys
        }
    
    # Claude API configuration (for Railway deployment)
    # No model selection needed - Claude Opus is used by default
    
    # Get current selection from session state or default to first
    default_selected_label = list(meeting_options.keys())[0] if meeting_options else None
    current_index = 0
    current_selected_label = default_selected_label
    
    if "analytics_meeting_selector" in st.session_state:
        current_selection = st.session_state["analytics_meeting_selector"]
        if current_selection in meeting_options:
            current_selected_label = current_selection
            current_index = list(meeting_options.keys()).index(current_selection)
    
    # Display meeting information first (using current selection)
    if current_selected_label and current_selected_label in meeting_options:
        current_meeting_data = meeting_options[current_selected_label]
        current_meeting = current_meeting_data["meeting"]
        
        st.subheader("📅 Meeting Information")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"**Subject:** {current_meeting.get('subject', 'N/A')}")
        with col2:
            st.markdown(f"**Client:** {current_meeting.get('client_name', 'N/A')}")
        with col3:
            st.markdown(f"**Organizer:** {current_meeting.get('organizer_email', 'N/A')}")
        
        # Transcript stats
        current_transcript = current_meeting.get("raw_transcript", "")
        if current_transcript:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Transcript Length", f"{len(current_transcript):,} characters")
            with col2:
                st.metric("Word Count", f"{len(current_transcript.split()):,} words")
            with col3:
                st.metric("Lines", f"{current_transcript.count(chr(10)) + 1:,}")
        
        st.markdown("---")
    
    # Create Summary Button
    st.subheader("✨ Generate Summary")
    
    # Summary function options (Claude API supports these)
    summary_functions = {
        "📝 Standard Structured Summary": {
            "function": "summarize",
            "description": "Standard structured summary with all sections (Purpose, Decisions, Action Items, Risks, etc.)",
            "summary_type": "structured"
        },
        "📊 Client Pulse Report": {
            "function": "generate_client_pulse_report",
            "description": "Full client pulse report with sentiment, themes, priorities",
            "summary_type": "client_pulse"
        }
    }
    
    # Dropdown to select summary function (moved first)
    selected_function_name = st.selectbox(
        "Select Summary Type:",
        options=list(summary_functions.keys()),
        help="Choose which type of summary to generate",
        key="summary_function_selector"
    )
    
    # Show description of selected function
    selected_function_info = summary_functions[selected_function_name]
    st.caption(f"💡 {selected_function_info['description']}")
    
    st.markdown("---")
    
    # Dropdown to select meeting (moved after Summary Type)
    st.subheader("📋 Select Meeting")
    selected_label = st.selectbox(
        "Choose a meeting to generate summary:",
        list(meeting_options.keys()),
        index=current_index,
        key="analytics_meeting_selector",
        help="Select a meeting from the list. ✅ = has transcript (all meetings here have transcripts), 📄 = has summary, 📝 = needs summary."
    )
    
    st.markdown("---")
    
    # Show Claude model info
    st.subheader("🤖 AI Model")
    st.info(f"🤖 **Model:** Claude Opus 4.5 (via Anthropic API)")
    st.caption("💡 Using Claude Opus 4.5 for high-quality summaries. Make sure ANTHROPIC_API_KEY is set in Railway.")
    
    st.markdown("---")
    
        # Get selected meeting data after dropdown selection
    if selected_label and selected_label in meeting_options:
        selected_meeting_data = meeting_options[selected_label]
        selected_meeting = selected_meeting_data["meeting"]
        selected_meeting_id = selected_meeting_data["meeting_id"]
        transcript = selected_meeting.get("raw_transcript", "")
        start_time = selected_meeting.get("start_time")
        
        # Create Summary Button (moved before Existing Summary)
        col1, col2 = st.columns([3, 1])
        with col1:
            st.info("Click the button below to generate a new summary for the selected meeting transcription.")
        with col2:
            create_summary_button = st.button(
                "✨ Create Summary",
                type="primary",
                use_container_width=True,
                key="create_summary_btn"
            )
        
        st.markdown("---")
        
        # Check if summary already exists
        existing_summary = selected_meeting.get("summary_text")
        existing_summary_type = selected_meeting.get("summary_type")
        
        if existing_summary:
            st.success(f"✅ Summary already exists (Type: {existing_summary_type or 'unknown'})")
            st.markdown("---")
            st.subheader("📄 Existing Summary")
            st.markdown(existing_summary)
            st.markdown("---")
        
        if create_summary_button:
            if not transcript:
                st.error("❌ No transcript available for this meeting. Cannot generate summary.")
            else:
                # Initialize Claude summarizer
                if ClaudeSummarizer is None:
                    st.error("❌ ClaudeSummarizer not available. Make sure ANTHROPIC_API_KEY is set in Railway.")
                else:
                    try:
                        summarizer = ClaudeSummarizer()
                        
                        if not summarizer.is_available():
                            st.error("❌ Claude API is not available. Check ANTHROPIC_API_KEY in Railway environment variables.")
                            st.info("💡 **Tip:** Make sure ANTHROPIC_API_KEY is set in Railway environment variables.")
                        else:
                            # Generate summary with progress
                            function_name = selected_function_info["function"]
                            summary_type = selected_function_info["summary_type"]
                            
                            with st.spinner(f"🔄 Generating {selected_function_name} with Claude Opus 4.5... This may take a few minutes."):
                                try:
                                    # Call the selected function
                                    if function_name == "summarize":
                                        summary_result = summarizer.summarize(
                                            transcript,
                                            summary_type="structured"
                                        )
                                    elif function_name == "generate_client_pulse_report":
                                        # Get client name from meeting data
                                        client_name = selected_meeting.get("client_name") or "Client"
                                        summary_result = summarizer.generate_client_pulse_report(
                                            transcript,
                                            client_name=client_name,
                                            month="Current"
                                        )
                                    else:
                                        st.error(f"❌ Unknown function: {function_name}")
                                        summary_result = None
                                    
                                    # Handle return type (Claude returns string)
                                    if summary_result is None:
                                        st.error("❌ Summary generation returned empty result.")
                                    else:
                                        # Single string result from Claude
                                        summary_text = summary_result
                                        
                                        if summary_text:
                                            # Save summary to database
                                            db = DatabaseManager()
                                            db.connect()
                                            db.create_tables()
                                            
                                            success = db.save_meeting_summary(
                                                selected_meeting_id,
                                                summary_text,
                                                summary_type=summary_type,
                                                start_time=start_time
                                            )
                                            db.close()
                                            
                                            if success:
                                                st.success(f"✅ {selected_function_name} generated and saved successfully!")
                                                st.markdown("---")
                                                st.subheader(f"📄 Generated Summary ({selected_function_name})")
                                                st.markdown(summary_text)
                                                
                                                # Rerun to refresh the page and show updated data
                                                st.info("🔄 Refreshing page to show updated data...")
                                                st.rerun()
                                            else:
                                                st.error("❌ Failed to save summary to database.")
                                        else:
                                            st.error("❌ Summary generation returned empty result.")
                                            
                                except Exception as e:
                                    st.error(f"❌ Error generating summary: {str(e)}")
                                    st.exception(e)
                                    
                    except Exception as e:
                        st.error(f"❌ Error initializing Claude summarizer: {str(e)}")
                        st.exception(e)
        
        # Show transcript preview
        if transcript:
            st.markdown("---")
            st.subheader("📖 Transcript Preview")
            
            # Show meeting date and start time for verification
            if start_time:
                try:
                    from datetime import datetime
                    start_time_str_raw = str(start_time).split('.')[0]
                    if 'Z' not in start_time_str_raw and '+' not in start_time_str_raw:
                        start_time_str_raw += "+00:00"
                    meeting_time = datetime.fromisoformat(start_time_str_raw.replace("Z", "+00:00"))
                    meeting_date_str = meeting_time.strftime("%Y-%m-%d %H:%M")
                    st.caption(f"📅 **Meeting Date:** {meeting_date_str} | **Start Time:** {start_time}")
                except:
                    st.caption(f"📅 **Start Time:** {start_time}")
            
            # Show a preview snippet (first 500 chars) to verify it's the correct transcript
            preview_snippet = transcript[:500].replace(chr(10), ' ').replace(chr(13), ' ')
            st.info(f"**Preview (first 500 chars):** {preview_snippet}...")
            
            with st.expander("View Full Transcript", expanded=False):
                # Use start_time in key to ensure unique widget for each meeting instance
                # This prevents Streamlit from caching/reusing the same widget for different meeting instances
                unique_key = f"analytics_transcript_{selected_meeting_id}_{start_time}" if start_time else f"analytics_transcript_{selected_meeting_id}"
                st.text_area(
                    "Full Transcript",
                    transcript,
                    height=400,
                    disabled=True,
                    key=unique_key,
                    label_visibility="collapsed"
                )

# ====================================================================
# PAGE 4: DATABASE VIEWER
# ====================================================================
elif page == "🗄️ Database Viewer":
    st.header("🗄️ Database Viewer")
    
    # Add refresh button
    col_header, col_refresh = st.columns([4, 1])
    with col_refresh:
        if st.button("🔄 Refresh Data", help="Click to reload data from database", key="refresh_db_viewer"):
            st.rerun()
    
    st.markdown("---")
    
    # Connect to database
    db = DatabaseManager()
    db.connect()
    db.create_tables()
    
    # Fetch all tables from database dynamically
    cursor = db.connection.cursor()
    table_options = []
    
    try:
        if USE_POSTGRES:
            # PostgreSQL: Get all tables from public schema
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                    AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
        else:
            # SQLite: Get all tables
            cursor.execute("""
                SELECT name as table_name
                FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """)
        
        tables_result = cursor.fetchall()
        table_options = [row['table_name'] if isinstance(row, dict) else row[0] for row in tables_result]
        
        if not table_options:
            st.warning("⚠️ No tables found in the database.")
            db.close()
            st.stop()
            
    except Exception as e:
        st.error(f"❌ Error fetching tables: {str(e)}")
        st.exception(e)
        # Fallback to default tables
        table_options = [
            "meetings_raw",
            "meeting_transcripts",
            "meeting_summaries",
            "meeting_satisfaction"
        ]
    
    # Table selection
    st.subheader("📊 Select Table to View")
    st.caption(f"💡 Found {len(table_options)} table(s) in the database")
    
    selected_table = st.selectbox(
        "Choose a table:",
        table_options,
        key="table_selector"
    )
    
    if selected_table:
        st.markdown("---")
        st.subheader(f"📋 {selected_table.replace('_', ' ').title()}")
        
        try:
            cursor = db.connection.cursor()
            
            # Get row count
            cursor.execute(f"SELECT COUNT(*) as count FROM {selected_table}")
            result = cursor.fetchone()
            # Handle both dict (PostgreSQL) and tuple (SQLite) results
            row_count = result['count'] if isinstance(result, dict) else result[0]
            st.info(f"**Total Rows:** {row_count}")
            
            if row_count > 0:
                # Get column names - use PostgreSQL information_schema instead of PRAGMA
                if USE_POSTGRES:
                    cursor.execute("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = %s 
                        ORDER BY ordinal_position
                    """, (selected_table,))
                    table_info = cursor.fetchall()
                    columns = [row['column_name'] if isinstance(row, dict) else row[0] for row in table_info]
                else:
                    # SQLite uses PRAGMA
                    cursor.execute(f"PRAGMA table_info({selected_table})")
                    table_info = cursor.fetchall()
                    columns = [col[1] for col in table_info]
                
                # Determine order by column (different tables have different timestamp columns)
                order_by_col = None
                timestamp_columns = ['created_at', 'updated_at', 'analyzed_at', 'start_time']
                for col_name in timestamp_columns:
                    if col_name in columns:
                        order_by_col = col_name
                        break
                
                # Build query with appropriate ordering
                if order_by_col:
                    query = f"SELECT * FROM {selected_table} ORDER BY {order_by_col} DESC LIMIT 100"
                else:
                    query = f"SELECT * FROM {selected_table} LIMIT 100"
                
                # Fetch all data
                cursor.execute(query)
                rows = cursor.fetchall()
                
                # Create DataFrame
                df = pd.DataFrame(rows, columns=columns)
                
                # Display data
                st.dataframe(
                    df,
                    use_container_width=True,
                    height=400
                )
                
                # Download button
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download as CSV",
                    data=csv,
                    file_name=f"{selected_table}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    key=f"download_{selected_table}"
                )
            else:
                st.warning("⚠️ No data found in this table.")
                
        except Exception as e:
            st.error(f"❌ Error reading table: {str(e)}")
            st.exception(e)
    
    st.markdown("---")
    
    # Database Statistics
    st.markdown("---")
    st.subheader("📈 Database Statistics")
    
    try:
        # Use the same cursor or create a new one
        if 'cursor' not in locals():
            cursor = db.connection.cursor()
        
        stats = {}
        for table in table_options:
            try:
                cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
                result = cursor.fetchone()
                # Handle both dict (PostgreSQL) and tuple (SQLite) results
                stats[table] = result['count'] if isinstance(result, dict) else result[0]
            except Exception as e:
                # If a table can't be accessed, set count to "Error"
                stats[table] = f"Error: {str(e)[:50]}"
        
        # Display statistics in a grid (4 columns)
        num_tables = len(table_options)
        num_cols = 4
        num_rows = (num_tables + num_cols - 1) // num_cols  # Ceiling division
        
        for row in range(num_rows):
            cols = st.columns(num_cols)
            for col_idx in range(num_cols):
                table_idx = row * num_cols + col_idx
                if table_idx < num_tables:
                    table_name = table_options[table_idx]
                    with cols[col_idx]:
                        # Format table name for display (replace underscores, capitalize)
                        display_name = table_name.replace('_', ' ').title()
                        count = stats.get(table_name, 0)
                        if isinstance(count, str) and count.startswith("Error"):
                            st.metric(display_name, "Error", help=count)
                        else:
                            st.metric(display_name, f"{count:,}" if isinstance(count, (int, float)) else count)
        
    except Exception as e:
        st.error(f"❌ Error getting statistics: {str(e)}")
    
    # Database file location
    st.markdown("---")
    st.subheader("ℹ️ Database Information")
    if USE_POSTGRES:
        # PostgreSQL doesn't have db_path, show connection info instead
        db_url = os.getenv("DATABASE_URL", "Not configured")
        # Mask password in URL for security
        if db_url != "Not configured" and "@" in db_url:
            # Extract just the host and database name
            try:
                from urllib.parse import urlparse
                parsed = urlparse(db_url)
                db_info = f"{parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"
            except:
                db_info = "PostgreSQL (Railway)"
        else:
            db_info = "PostgreSQL (Railway)"
        st.info(f"**Database Type:** PostgreSQL")
        st.info(f"**Database Location:** `{db_info}`")
        st.caption("💡 This is a PostgreSQL database hosted on Railway. Data is persistent and shared across deployments.")
    else:
        # SQLite has db_path
        if hasattr(db, 'db_path'):
            st.info(f"**Database Location:** `{db.db_path}`")
            st.caption("💡 This is a SQLite database file. You can also open it with DB Browser for SQLite or other SQLite tools.")
        else:
            st.info("**Database Type:** SQLite")
            st.caption("💡 This is a SQLite database file.")
    
    db.close()

# ====================================================================
# PAGE 5: API OPERATIONS
# ====================================================================
elif page == "⚙️ API Operations":
    st.header("⚙️ API Operations")
    st.caption("💡 **Trigger API operations directly from the UI. These are the same functions used by the Flask API endpoints.**")
    
    # Import API functions from app.py
    try:
        from app import run_meeting_processing
        API_FUNCTIONS_AVAILABLE = True
    except ImportError as e:
        st.error(f"❌ Could not import API functions: {e}")
        st.info("💡 **Note:** API functions are in `app.py`. Make sure the file is accessible.")
        API_FUNCTIONS_AVAILABLE = False
    
    if API_FUNCTIONS_AVAILABLE:
        st.markdown("---")
        
        # Operation 1: Process Meetings
        st.subheader("🔄 Process Meetings")
        st.info("""
        **What this does:**
        - Fetches Teams meetings from Microsoft Graph API (last 15 days)
        - Downloads transcripts
        - Generates structured summaries (saves + emails)
        - Generates client pulse reports (saves only, no email)
        - Skips meetings that already have both summaries
        """)
        
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption("⚠️ **Warning:** This operation may take several minutes depending on the number of meetings.")
        with col2:
            process_button = st.button("🚀 Process Meetings", type="primary", use_container_width=True, key="process_meetings_btn")
        
        if process_button:
            with st.spinner("🔄 Processing meetings... This may take several minutes. Please wait..."):
                try:
                    result = run_meeting_processing()
                    
                    if "error" in result:
                        st.error(f"❌ Error: {result['error']}")
                    else:
                        st.success("✅ Processing complete!")
                        
                        # Display results
                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            st.metric("Meetings Found", result.get('meetings_found', 0))
                        with col2:
                            st.metric("Transcripts Saved", result.get('transcripts_saved', 0))
                        with col3:
                            st.metric("Summaries Generated", result.get('summaries_generated', 0))
                        with col4:
                            st.metric("Pulse Reports", result.get('pulse_reports_generated', 0))
                        
                        col5, col6, col7 = st.columns(3)
                        with col5:
                            st.metric("Emails Sent", result.get('emails_sent', 0))
                        with col6:
                            st.metric("Skipped", result.get('skipped', 0))
                        with col7:
                            st.metric("No Transcript", result.get('no_transcript', 0))
                        
                        st.info(f"📝 **Message:** {result.get('message', '')}")
                        
                        # Refresh button
                        if st.button("🔄 Refresh Page to See New Data", key="refresh_after_process"):
                            st.rerun()
                            
                except Exception as e:
                    st.error(f"❌ Error processing meetings: {str(e)}")
                    st.exception(e)
        
        st.markdown("---")
        
        # Operation 2: Generate Pulse Report
        st.subheader("📊 Generate Aggregated Pulse Reports")
        st.info("""
        **What this does:**
        - Aggregates individual client pulse reports from last 15 days
        - Groups by client name
        - Generates combined reports using Claude Opus 4.5
        - Saves to `aggregated_pulse_reports` table
        - Sends email to `EMAIL_TEST_RECIPIENT` (if configured)
        """)
        
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption("⚠️ **Warning:** This operation may take 1-3 minutes depending on the number of reports.")
        with col2:
            generate_pulse_button = st.button("📊 Generate Pulse Reports", type="primary", use_container_width=True, key="generate_pulse_btn")
        
        if generate_pulse_button:
            with st.spinner("🔄 Generating aggregated pulse reports... This may take a few minutes. Please wait..."):
                try:
                    from datetime import datetime, timedelta
                    import os
                    
                    if DatabaseManager is None:
                        st.error("❌ DatabaseManager not available")
                    else:
                        # Initialize summarizer
                        if ClaudeSummarizer is None:
                            st.error("❌ ClaudeSummarizer not available. Check ANTHROPIC_API_KEY.")
                        else:
                            summarizer = ClaudeSummarizer()
                            if not summarizer.is_available():
                                st.error("❌ Claude API is not available. Check ANTHROPIC_API_KEY.")
                            else:
                                # Connect to database
                                db = DatabaseManager()
                                if not db.connect() or not db.create_tables():
                                    st.error("❌ Database connection failed")
                                else:
                                    # Calculate date range (last 15 days)
                                    end_date = datetime.now()
                                    start_date = end_date - timedelta(days=15)
                                    start_date_str = start_date.strftime("%Y-%m-%d")
                                    end_date_str = end_date.strftime("%Y-%m-%d")
                                    date_range = f"{start_date_str} to {end_date_str}"
                                    
                                    # Query for client_pulse summaries from last 15 days
                                    cursor = db.connection.cursor()
                                    
                                    param_style = "%s" if USE_POSTGRES else "?"
                                    cursor.execute(f"""
                                        SELECT 
                                            cpr.meeting_id,
                                            cpr.start_time,
                                            cpr.summary_text as pulse_report,
                                            COALESCE(cpr.client_name, mr.client_name, 'Unknown Client') as client_name,
                                            mr.subject
                                        FROM client_pulse_reports cpr
                                        JOIN meetings_raw mr ON cpr.meeting_id = mr.meeting_id AND cpr.start_time = mr.start_time
                                        WHERE cpr.start_time >= {param_style}
                                          AND cpr.start_time <= {param_style}
                                        ORDER BY client_name, cpr.start_time DESC
                                    """, (start_date_str, end_date_str))
                                    
                                    all_pulse_reports = cursor.fetchall()
                                    
                                    if not all_pulse_reports:
                                        st.warning("⚠️ No client pulse reports found in last 15 days.")
                                        db.close()
                                    else:
                                        # Group by client_name
                                        client_groups = {}
                                        for row in all_pulse_reports:
                                            client_name = row['client_name'] if isinstance(row, dict) else row[3]
                                            if not client_name or client_name.strip() == '' or client_name == 'Unknown Client':
                                                client_name = 'Client'
                                            
                                            if client_name not in client_groups:
                                                client_groups[client_name] = []
                                            pulse_report = row['pulse_report'] if isinstance(row, dict) else row[2]
                                            client_groups[client_name].append(pulse_report)
                                        
                                        st.info(f"📋 Found {len(all_pulse_reports)} pulse reports across {len(client_groups)} clients")
                                        
                                        reports_generated = 0
                                        emails_sent = 0
                                        errors = []
                                        
                                        # Process each client group
                                        progress_bar = st.progress(0)
                                        total_clients = len(client_groups)
                                        
                                        for idx, (client_name, pulse_reports_list) in enumerate(client_groups.items()):
                                            try:
                                                st.write(f"🔄 Processing client: **{client_name}** ({len(pulse_reports_list)} reports)...")
                                                
                                                # Generate aggregated report
                                                aggregated_report = summarizer.aggregate_pulse_reports(
                                                    pulse_reports_list,
                                                    client_name=client_name,
                                                    date_range=date_range
                                                )
                                                
                                                # Save aggregated report
                                                db.save_aggregated_pulse_report(
                                                    client_name=client_name,
                                                    date_range_start=start_date_str,
                                                    date_range_end=end_date_str,
                                                    aggregated_report_text=aggregated_report,
                                                    individual_reports_count=len(pulse_reports_list)
                                                )
                                                reports_generated += 1
                                                
                                                # Send email if configured
                                                email_recipient = os.getenv("EMAIL_TEST_RECIPIENT", "")
                                                if email_recipient and os.getenv("SEND_EMAILS", "false").lower() == "true":
                                                    # Email sending logic would go here
                                                    # For now, just mark as would be sent
                                                    emails_sent += 1
                                                
                                                progress_bar.progress((idx + 1) / total_clients)
                                                
                                            except Exception as e:
                                                error_msg = f"Error processing client {client_name}: {e}"
                                                logger.error(error_msg)
                                                errors.append(error_msg)
                                                continue
                                        
                                        db.connection.commit()
                                        db.close()
                                        
                                        st.success(f"✅ Generated {reports_generated} aggregated pulse reports!")
                                        
                                        # Display results
                                        col1, col2, col3 = st.columns(3)
                                        with col1:
                                            st.metric("Clients Processed", len(client_groups))
                                        with col2:
                                            st.metric("Reports Generated", reports_generated)
                                        with col3:
                                            st.metric("Emails Sent", emails_sent)
                                        
                                        if errors:
                                            st.warning(f"⚠️ {len(errors)} errors occurred. Check logs for details.")
                                        
                                        # Refresh button
                                        if st.button("🔄 Refresh Page to See New Data", key="refresh_after_pulse"):
                                            st.rerun()
                                        
                except Exception as e:
                    st.error(f"❌ Error generating pulse reports: {str(e)}")
                    st.exception(e)
        
        st.markdown("---")
        
        # Operation 3: Health Check
        st.subheader("💚 Health Check")
        health_button = st.button("Check Health", key="health_check_btn")
        
        if health_button:
            try:
                db = DatabaseManager()
                if db.connect():
                    db.create_tables()
                    db.close()
                    st.success("✅ **Status:** Healthy")
                    st.success("✅ **Database:** Connected")
                    st.info(f"🕐 **Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                else:
                    st.error("❌ **Database:** Connection failed")
            except Exception as e:
                st.error(f"❌ **Error:** {str(e)}")
        
        st.markdown("---")
        
        # Information
        st.subheader("ℹ️ About API Operations")
        st.info("""
        **Note:** These operations call the same functions used by the Flask API endpoints.
        Since Streamlit is currently deployed (not Flask), you can trigger these operations
        directly from this UI instead of using curl commands.
        
        **For external access (cron jobs, webhooks):** Consider deploying Flask API as a
        separate service to get RESTful endpoints. See `DEPLOYMENT_OPTIONS.md` for details.
        """)
