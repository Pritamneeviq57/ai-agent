"""
Streamlit UI to visualize meeting statistics and summaries.

Usage:
    streamlit run streamlit_phase_2_2.py
"""
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from src.database.db_setup_sqlite import DatabaseManager


def load_meeting_stats():
    """Return total meeting count and per-client aggregation."""
    db = DatabaseManager()
    if not db.connect():
        return None

    cursor = db.connection.cursor()
    try:
        total_meetings = db.get_meeting_count()
        cursor.execute(
            """
            SELECT 
                COALESCE(client_name, 'Unknown') AS client_name,
                COUNT(*) AS meeting_count
            FROM meetings_raw
            GROUP BY client_name
            ORDER BY meeting_count DESC
        """
        )
        client_rows = cursor.fetchall()
        client_counts = [
            {"client_name": row["client_name"], "meeting_count": row["meeting_count"]}
            for row in client_rows
        ]
        
        # Get summary stats
        cursor.execute("SELECT COUNT(*) FROM meeting_summaries")
        total_summaries = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM meeting_transcripts")
        total_transcripts = cursor.fetchone()[0]
        
        return {
            "total": total_meetings, 
            "clients": client_counts,
            "summaries": total_summaries,
            "transcripts": total_transcripts
        }
    finally:
        db.close()


st.set_page_config(page_title="Teams Meeting Summary Agent", layout="wide")
st.title("🤖 Teams Meeting Summary Agent")
st.caption("View your meeting transcripts and AI-generated summaries")

st.info("💡 **Tip:** Run `python main_phase_2_3_delegated.py` in terminal to fetch new meetings and generate summaries.")

stats = load_meeting_stats()

if not stats:
    st.warning("Unable to load database statistics.")
else:
    # Metrics row
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📅 Total Meetings", stats["total"])
    with col2:
        st.metric("📝 Transcripts", stats["transcripts"])
    with col3:
        st.metric("✨ Summaries", stats["summaries"])
    
    st.divider()
    
    # Meetings by Client Chart
    st.subheader("📊 Meetings by Client/Member")
    client_data = stats["clients"]
    if client_data:
        df = pd.DataFrame(client_data)

        fig, ax = plt.subplots(figsize=(10, 5))
        colors = plt.cm.Blues([0.4 + 0.1*i for i in range(len(df))])
        ax.barh(df["client_name"], df["meeting_count"], color=colors)
        ax.set_xlabel("Meeting count")
        ax.set_ylabel("Client / Member")
        ax.set_title("Meetings by client/member")
        ax.invert_yaxis()
        plt.tight_layout()

        st.pyplot(fig, clear_figure=True)
    else:
        st.info("No meeting data available yet.")
    
    # Recent meetings with summaries
    st.divider()
    st.subheader("📋 Recent Meetings with Summaries")
    
    db = DatabaseManager()
    if db.connect():
        meetings = db.get_meetings_with_summaries(limit=10)
        db.close()
        
        if meetings:
            for meeting in meetings:
                with st.expander(f"📅 {meeting.get('client_name', 'Unknown')} - {meeting.get('start_time', '')[:10]}"):
                    st.markdown(f"**Organizer:** {meeting.get('organizer_email', 'N/A')}")
                    st.markdown(f"**Duration:** {meeting.get('duration_minutes', 'N/A')} minutes")
                    st.markdown("---")
                    st.markdown("**Summary:**")
                    st.markdown(meeting.get('summary_text', 'No summary available'))
        else:
            st.info("No meetings with summaries yet. Run the main script to generate summaries.")

