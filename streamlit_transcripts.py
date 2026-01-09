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
from datetime import datetime
from src.analytics.satisfaction_analyzer import SatisfactionAnalyzer
from src.summarizer.ollama_mistral_summarizer import OllamaMistralSummarizer
from src.utils.logger import setup_logger

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
    ["📈 Satisfaction Monitor", "📝 Meeting Transcripts", "🔍 Analytics Dashboard", "🗄️ Database Viewer"],
    key="main_nav"
)

# ====================================================================
# FETCH DATA (thread-safe, creates fresh connections)
# ====================================================================
def fetch_all_meetings():
    """Fetch all meetings from last 15 days (with or without transcripts) and summaries
    
    Returns ALL meetings from meetings_raw, regardless of whether they have transcripts.
    Uses LEFT JOIN to include transcript and summary data when available.
    """
    db = DatabaseManager()
    db.connect()
    db.create_tables()
    
    cursor = db.connection.cursor()
    # Calculate date 15 days ago - use datetime object for comparison
    from datetime import datetime, timedelta, timezone
    # Use timezone-aware datetime (UTC)
    fifteen_days_ago_dt = datetime.now(timezone.utc) - timedelta(days=15)
    # Format for SQL comparison (handle both with and without timezone)
    fifteen_days_ago_str = fifteen_days_ago_dt.strftime("%Y-%m-%dT%H:%M:%S")
    
    # Use PostgreSQL parameter style (%s) instead of SQLite (?)
    param_style = "%s" if USE_POSTGRES else "?"
    
    # Start from meetings_raw to get ALL meetings from last 15 days, then LEFT JOIN to get transcripts if available
    # LEFT JOIN ensures we get ALL meetings, even if they don't have transcripts
    # Join on both meeting_id and start_time for proper matching
    cursor.execute(f"""
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
        WHERE mr.start_time >= {param_style}
        ORDER BY 
            CASE WHEN ms.summary_text IS NOT NULL THEN 0 ELSE 1 END,  -- Prioritize meetings with summaries
            mr.start_time DESC, 
            mr.created_at DESC
    """, (fifteen_days_ago_str,))
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
    """Fetch all meetings that have transcripts available (ONLY meetings with transcripts)"""
    db = DatabaseManager()
    db.connect()
    db.create_tables()
    
    cursor = db.connection.cursor()
    
    # Get all meetings with transcripts (no date filter)
    # INNER JOIN ensures we only get meetings that have transcripts
    # Additional check for non-empty transcripts
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
    for row in rows:
        row_dict = dict(row)
        transcript = row_dict.get("raw_transcript", "")
        # Double-check that transcript is not empty
        if transcript and str(transcript).strip():
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
    st.caption("💡 **This tab shows ALL meetings (with or without transcripts) from the last 15 days.**")
    
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
    
    # Fetch meetings with transcripts ONLY (using INNER JOIN)
    meetings_with_transcripts = fetch_meetings_with_transcripts()
    
    if not meetings_with_transcripts:
        st.warning("⚠️ No meetings with transcriptions found in the database.")
        st.info("💡 **Tip:** Run `python main_phase_2_3_delegated.py` to fetch meeting transcriptions first.")
        st.stop()
    
    # Display count of available transcriptions
    st.info(f"📊 **Total Meetings with Transcripts:** {len(meetings_with_transcripts)}")
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
    
    # Local network Ollama server configuration
    remote_ollama_url = "http://192.168.2.180:11434"
    
    # Fetch available models from Ollama server
    @st.cache_data(ttl=300)  # Cache for 5 minutes
    def fetch_available_models(server_url):
        """Fetch available models from Ollama server"""
        try:
            import requests
            response = requests.get(
                f"{server_url}/api/tags",
                timeout=5
            )
            if response.status_code == 200:
                models = response.json().get('models', [])
                return [model['name'] for model in models]
            else:
                return []
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return []
    
    # Fetch models
    available_models = fetch_available_models(remote_ollama_url)
    
    if not available_models:
        st.warning(f"⚠️ Could not fetch models from {remote_ollama_url}. Using default model.")
        available_models = ["gpt-oss-safeguard:20b"]  # Fallback
    
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
    
    # Summary function options
    summary_functions = {
        "📝 Standard Concise Summary": {
            "function": "summarize",
            "description": "Standard concise summary with all sections (Purpose, Decisions, Action Items, Risks, etc.)",
            "summary_type": "concise"
        },
        "⚡ Ultra Concise (Executive Summary)": {
            "function": "summarize_ultra_concise",
            "description": "One-page maximum executive summary (250 words max)",
            "summary_type": "ultra_concise"
        },
        "📧 One-Liner (Subject Line)": {
            "function": "summarize_one_liner",
            "description": "One sentence summary for Slack/email subject lines (< 100 chars)",
            "summary_type": "one_liner"
        },
        "✅ Checklist Only (Action Items)": {
            "function": "summarize_checklist_only",
            "description": "Extract only action items in checkbox format",
            "summary_type": "checklist"
        },
        "🎯 Project-Based Summary": {
            "function": "summarize_by_project",
            "description": "Summary organized by project (not chronological)",
            "summary_type": "project_based"
        },
        "📊 Client Pulse Report": {
            "function": "generate_client_pulse_report",
            "description": "Full client pulse report with sentiment, themes, priorities",
            "summary_type": "client_pulse"
        },
        "🔄 Multiple Variants (3-in-1)": {
            "function": "generate_summary_variants",
            "description": "Generate one-liner, checklist, and executive summary all at once",
            "summary_type": "variants"
        },
        "📈 Summary + Client Pulse": {
            "function": "summarize_with_client_pulse",
            "description": "Combined standard summary + client pulse report",
            "summary_type": "summary_with_pulse"
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
    
    # Model selection dropdown (moved after Meeting)
    st.subheader("🤖 Select Model")
    default_model = "gpt-oss-safeguard:20b" if "gpt-oss-safeguard:20b" in available_models else available_models[0] if available_models else "gpt-oss-safeguard:20b"
    
    selected_model = st.selectbox(
        "Choose a model for summary generation:",
        available_models,
        index=available_models.index(default_model) if default_model in available_models else 0,
        key="model_selector",
        help=f"Select a model from the remote Ollama server at {remote_ollama_url}"
    )
    
    st.info(f"🌐 **Remote Ollama Server:** `{remote_ollama_url}` | 🤖 **Selected Model:** `{selected_model}`")
    
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
                # Initialize summarizer with Ollama server and selected model
                try:
                    summarizer = OllamaMistralSummarizer(
                        base_url=remote_ollama_url,
                        model=selected_model
                    )
                    
                    if not summarizer.is_ollama_running():
                        st.error(f"❌ Ollama is not running at {remote_ollama_url}. Please check the Ollama server.")
                        st.info(f"💡 **Tip:** Make sure the Ollama service is running at {remote_ollama_url}")
                    else:
                        # Generate summary with progress
                        function_name = selected_function_info["function"]
                        summary_type = selected_function_info["summary_type"]
                        
                        with st.spinner(f"🔄 Generating {selected_function_name}... This may take a few minutes."):
                            try:
                                # Call the selected function
                                if function_name == "summarize":
                                    summary_result = summarizer.summarize(
                                        transcript,
                                        summary_type="concise",
                                        temperature=0.3
                                    )
                                elif function_name == "summarize_ultra_concise":
                                    summary_result = summarizer.summarize_ultra_concise(
                                        transcript,
                                        temperature=0.3
                                    )
                                elif function_name == "summarize_one_liner":
                                    summary_result = summarizer.summarize_one_liner(
                                        transcript,
                                        temperature=0.3
                                    )
                                elif function_name == "summarize_checklist_only":
                                    summary_result = summarizer.summarize_checklist_only(
                                        transcript,
                                        temperature=0.3
                                    )
                                elif function_name == "summarize_by_project":
                                    summary_result = summarizer.summarize_by_project(
                                        transcript,
                                        temperature=0.3
                                    )
                                elif function_name == "generate_client_pulse_report":
                                    # Get client name from meeting data
                                    client_name = selected_meeting.get("client_name") or "Client"
                                    summary_result = summarizer.generate_client_pulse_report(
                                        transcript,
                                        client_name=client_name,
                                        month="Current"
                                    )
                                elif function_name == "generate_summary_variants":
                                    summary_result = summarizer.generate_summary_variants(transcript)
                                elif function_name == "summarize_with_client_pulse":
                                    client_name = selected_meeting.get("client_name") or "Client"
                                    summary_result = summarizer.summarize_with_client_pulse(
                                        transcript,
                                        client_name=client_name,
                                        month="Current"
                                    )
                                else:
                                    st.error(f"❌ Unknown function: {function_name}")
                                    summary_result = None
                                
                                # Handle different return types
                                if summary_result is None:
                                    st.error("❌ Summary generation returned empty result.")
                                elif isinstance(summary_result, dict):
                                    # Multiple variants or combined results
                                    summary_text = f"# {selected_function_name}\n\n"
                                    for key, value in summary_result.items():
                                        summary_text += f"## {key.replace('_', ' ').title()}\n\n{value}\n\n---\n\n"
                                else:
                                    # Single string result
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
                    st.error(f"❌ Error initializing summarizer: {str(e)}")
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
    
    # Table selection
    st.subheader("📊 Select Table to View")
    table_options = [
        "meetings_raw",
        "meeting_transcripts",
        "meeting_summaries",
        "meeting_satisfaction"
    ]
    
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
            cursor.execute(f"SELECT COUNT(*) FROM {selected_table}")
            row_count = cursor.fetchone()[0]
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
    st.subheader("📈 Database Statistics")
    
    try:
        cursor = db.connection.cursor()
        
        stats = {}
        for table in table_options:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            stats[table] = cursor.fetchone()[0]
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Meetings Raw", stats.get("meetings_raw", 0))
        with col2:
            st.metric("Transcripts", stats.get("meeting_transcripts", 0))
        with col3:
            st.metric("Summaries", stats.get("meeting_summaries", 0))
        with col4:
            st.metric("Satisfaction", stats.get("meeting_satisfaction", 0))
        
    except Exception as e:
        st.error(f"❌ Error getting statistics: {str(e)}")
    
    # Database file location
    st.markdown("---")
    st.subheader("ℹ️ Database Information")
    st.info(f"**Database Location:** `{db.db_path}`")
    st.caption("💡 This is a SQLite database file. You can also open it with DB Browser for SQLite or other SQLite tools.")
    
    db.close()
