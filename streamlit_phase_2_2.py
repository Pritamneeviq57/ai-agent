"""
Streamlit UI to run Phase 2.2 workflow and visualize meeting statistics.

Usage:
    streamlit run streamlit_phase_2_2.py
"""
import logging

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from main_phase_2_2 import main
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
        return {"total": total_meetings, "clients": client_counts}
    finally:
        db.close()


st.set_page_config(page_title="Teams Meeting Phase 2.2 Runner", layout="wide")
st.title("Phase 2.2 - Meeting Fetcher")
st.caption("Run `main_phase_2_2` and review meeting insights below.")

run_button = st.button("Run Phase 2.2", type="primary")

if run_button:
    with st.spinner("Running Phase 2.2..."):
        success = main()

    if success:
        st.success("Phase 2.2 completed successfully!")
    else:
        st.error("Phase 2.2 failed. Please check backend logs for details.")

stats = load_meeting_stats()
st.subheader("Meetings by Client/Member")
if not stats:
    st.warning("Unable to load database statistics.")
else:
    client_data = stats["clients"]
    if client_data:
        df = pd.DataFrame(client_data)
        st.metric("Total meetings stored", stats["total"])

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.bar(df["client_name"], df["meeting_count"], color="#4E79A7")
        ax.set_xlabel("Client / Member")
        ax.set_ylabel("Meeting count")
        ax.set_xticklabels(df["client_name"], rotation=45, ha="right")
        ax.set_title("Meetings by client/member (static)")
        plt.tight_layout()

        st.pyplot(fig, clear_figure=True)
    else:
        st.info("No meeting data available yet.")

