"""
Fast Local Summarizer - Production Ready Version
Updated for llama2:13b model on MacBook Pro
Optimized for CPU-only inference with longer timeouts
No API keys, no cloud, 100% local and private
"""
import requests
import json
import time
import re
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.utils.logger import setup_logger
from src.analytics.satisfaction_analyzer import SatisfactionAnalyzer
from src.utils.langfuse_client import trace_ollama_generation, trace_summarization
from src.utils.opik_client import trace_ollama_generation_sync as trace_ollama_opik, trace_summarization as trace_summarization_opik

logger = setup_logger(__name__)


class SummarizerConfig:
    """Configuration for OllamaMistralSummarizer"""
    # Timeouts (in seconds)
    TIMEOUT_SECONDS = 900  # 15 minutes for MacBook Pro CPU inference
    API_CHECK_TIMEOUT = 5  # Quick API health check
    
    # Processing thresholds
    MAX_DIRECT_SIZE = 30000  # chars before chunking
    MAX_CHUNK_TOKENS = 2000
    CHUNK_OVERLAP = 200
    
    # Model parameters
    NUM_PREDICT = 8000  # max output tokens
    NUM_CTX = 16384  # context window
    TEMPERATURE_CONCISE = 0.2
    TEMPERATURE_DETAILED = 0.3
    
    # Validation
    MIN_SUMMARY_LENGTH = 100  # chars
    MAX_SUMMARY_LENGTH = 20000  # chars
    MAX_RETRIES = 3
    RETRY_BACKOFF = 2  # exponential backoff multiplier


class OllamaMistralSummarizer:
    """Fast summarizer using Ollama + llama2:13b with retry logic"""
    
    def __init__(self, base_url="http://localhost:11434", model="llama2:13b"):
        """
        Initialize the summarizer
        
        Args:
            base_url (str): Ollama API base URL
            model (str): Model name (default: llama2:13b)
        """
        self.base_url = base_url
        self.model = model
        self.config = SummarizerConfig()
        self.timeout = self.config.TIMEOUT_SECONDS
        self.chunker = TranscriptChunker()
        self.satisfaction_analyzer = SatisfactionAnalyzer()
        
        logger.info(f"✓ OllamaMistralSummarizer initialized with {self.model}")
        logger.info(f"  Timeout: {self.timeout}s (15 minutes)")
        logger.info(f"  Processing: Sequential (1 chunk at a time for optimal CPU performance)")
    
    def health_check(self):
        """
        Check if Ollama is running and model is available
        
        Returns:
            bool: True if healthy, False otherwise
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=self.config.API_CHECK_TIMEOUT
            )
            
            if response.status_code == 200:
                models = response.json().get('models', [])
                model_names = [m['name'] for m in models]
                
                if self.model in model_names:
                    logger.info(f"✅ Ollama health check passed")
                    logger.info(f"   Model available: {self.model}")
                    return True
                else:
                    logger.error(f"❌ Model '{self.model}' not found")
                    logger.error(f"   Available models: {model_names}")
                    return False
            else:
                logger.error(f"❌ Ollama returned status {response.status_code}")
                return False
                
        except requests.exceptions.ConnectionError:
            logger.error(f"❌ Could not connect to Ollama at {self.base_url}")
            logger.error(f"   Make sure to run: ollama serve")
            return False
        except Exception as e:
            logger.error(f"❌ Health check failed: {e}")
            return False
    
    def is_ollama_running(self):
        """Check if Ollama service is running (legacy method)"""
        return self.health_check()
    
    def summarize(self, transcription, summary_type="concise", temperature=0.3, include_satisfaction=False):
        """
        Generate concise, actionable meeting summary from transcription
        Optimized for brevity and clarity with document requirements
        """
        if not self.is_ollama_running():
            raise ConnectionError(f"Ollama is not running at {self.base_url}")
        
        transcript_len = len(transcription)
        logger.info(f"Generating concise summary using {self.model} ({transcript_len} chars)...")
        
        if transcript_len > self.config.MAX_DIRECT_SIZE:
            logger.info(f"Transcript is large ({transcript_len} chars), using chunking approach...")
            return self._summarize_chunked_with_final_pass(transcription, temperature)
        
        # Concise summary prompt - focused on essentials only
        prompt = f"""Create a CONCISE meeting summary from the transcript below. Keep it brief and actionable.

Meeting Transcript:
{transcription}

Provide summary in this exact format (be brief, 2-3 lines per section max):

## MEETING SUMMARY

**Date:** [date if mentioned]

**Attendees:** [key people only]

**Duration:** [if mentioned]

### PURPOSE

[1-2 sentences on why the meeting happened]

### KEY DECISIONS

- [Decision 1 with owner]
- [Decision 2 with owner]
- [Decision 3 with owner]

### ACTION ITEMS (PRIORITY ORDER)

| Owner | Task | Deadline | Status |
|-------|------|----------|--------|
| [Name] | [Task] | [When] | [Blocked/On-track] |

### TECHNICAL CONTEXT (if applicable)
[Explain frameworks, architectures, logic discussed. Include worked examples if any.]
[Note WHY decisions made, not just THAT they were made.]

### OUTSTANDING QUESTIONS / OPEN ITEMS
- [Unresolved item] - Impact: [High/Med/Low]
- [What needs verification?] - Owner: [name]

### RISKS & BLOCKERS

- [Risk/Blocker] - Impact: [High/Medium/Low]

### DOCUMENTS REQUIRED

- [ ] [Document Type]: [Deliverable name] - Due: [date] - Owner: [name]

### NEXT MEETING

- Date: [if scheduled]
- Focus: [key topics]

### CRITICAL NUMBERS/DATES

| Item | Value |
|------|-------|
| [Name] | [Number/Date] |

### SENTIMENT PROGRESSION
- Start: [Opening tone] → [Turning point if any] → End: [Closing tone]
- Overall: [Positive/Neutral/Negative]
- Client Concerns: [If any]
- Team Morale: [If evident]

Keep it under 500 words. Be specific with names, dates, and deliverables."""
        
        summary = self._query_llama2_with_retry(prompt, temperature)
        
        logger.info(f"✅ Concise summary generated ({len(summary)} chars)")
        return summary
    
    def summarize_ultra_concise(self, transcription, temperature=0.3):
        """
        Generate ULTRA CONCISE summary - one page maximum
        Optimized for busy executives and quick reference
        """
        if not self.is_ollama_running():
            raise ConnectionError(f"Ollama is not running at {self.base_url}")
        
        transcript_len = len(transcription)
        logger.info(f"Generating ultra-concise summary ({transcript_len} chars)...")
        
        MAX_DIRECT_SIZE = 30000
        
        if transcript_len > self.config.MAX_DIRECT_SIZE:
            logger.info(f"Using chunking for large transcript...")
            return self._summarize_chunked_with_final_pass(transcription, temperature)
        
        # ULTRA-CONCISE prompt - focus on ONLY essentials
        prompt = f"""Generate a ONE-PAGE executive summary from this transcript. Be brutally concise.

Transcript:

{transcription}

Format (EXACTLY as shown):

# MEETING SUMMARY

**Date:** [Date] | **People:** [3-4 key names] | **Duration:** [time]

## 🔴 CRITICAL (do first)

- [Item 1] - Owner: [name] - Due: [date]

- [Item 2] - Owner: [name] - Due: [date]

## 📋 ACTION ITEMS

| Owner | Task | Due |

|-------|------|-----|

| [Name] | [Task in 6-8 words] | [Date] |

| [Name] | [Task in 6-8 words] | [Date] |

## 🎯 DECISIONS

- [Decision 1 - one line]

- [Decision 2 - one line]

- [Decision 3 - one line]

## 📄 DOCUMENTS NEEDED

- [Doc type]: [Name] → Due: [date] | Owner: [name]

- [Doc type]: [Name] → Due: [date] | Owner: [name]

## ⚠️ RISKS

- [Risk 1] - Impact: [High/Med/Low]

- [Risk 2] - Impact: [High/Med/Low]

## 📌 KEY NUMBERS

| What | Value |

|------|-------|

| [Item] | [Number] |

## NEXT MEETING

[Date & focus in one line]

TOTAL LENGTH: Maximum 250 words. Be specific with names and dates. Use abbreviations. Cut all fluff."""
        
        summary = self._query_llama2_with_retry(prompt, temperature)
        
        logger.info(f"✅ Ultra-concise summary generated ({len(summary)} chars)")
        return summary
    
    def summarize_one_liner(self, transcription, temperature=0.3):
        """
        Generate EXTREME one-liner summary for Slack/email subject
        """
        if not self.is_ollama_running():
            raise ConnectionError(f"Ollama is not running at {self.base_url}")
        
        prompt = f"""Summarize this meeting in ONE sentence for a Slack/email subject line.

Include: what happened, who's responsible, critical deadline.

Keep under 100 characters.

Transcript:

{transcription}

Example format:

"CVTRC hanger layout due tomorrow (Pranil), Level-2 install critical for concrete pour"

Your summary:"""
        
        summary = self._query_llama2_with_retry(prompt, temperature)
        logger.info(f"✅ One-liner generated")
        return summary.strip()
    
    def summarize_checklist_only(self, transcription, temperature=0.3):
        """
        Generate ONLY action items in checklist format for quick task tracking
        """
        if not self.is_ollama_running():
            raise ConnectionError(f"Ollama is not running at {self.base_url}")
        
        prompt = f"""Extract ONLY action items from this transcript. Format as checkbox list.

Transcript:

{transcription}

Format EXACTLY like this (nothing else):

## ACTION ITEMS TO DO

- [ ] [Task] — Owner: [name] — Due: [date] — Status: [Blocked/On-track]

- [ ] [Task] — Owner: [name] — Due: [date] — Status: [Blocked/On-track]

- [ ] [Task] — Owner: [name] — Due: [date] — Status: [Blocked/On-track]

Only include specific, assigned tasks. No general discussion items."""
        
        summary = self._query_llama2_with_retry(prompt, temperature)
        logger.info(f"✅ Checklist generated")
        return summary
    
    def generate_summary_variants(self, transcription):
        """
        Generate 3 summary formats in one call for maximum flexibility
        """
        logger.info("Generating 3 summary variants...")
        
        return {
            "one_liner": self.summarize_one_liner(transcription),
            "checklist": self.summarize_checklist_only(transcription),
            "executive": self.summarize_ultra_concise(transcription)
        }
    
    def summarize_by_project(self, transcription, temperature=0.3):
        """
        Generate summary organized by PROJECT instead of by discussion order
        Much shorter and more actionable
        """
        if not self.is_ollama_running():
            raise ConnectionError(f"Ollama is not running at {self.base_url}")
        
        prompt = f"""Summarize by PROJECT only. Each project gets 2-3 lines MAX.

Transcript:

{transcription}

Format:

# PROJECT STATUS SUMMARY

## [PROJECT NAME 1]

**Status:** [On-track/At-risk/Blocked]

**Critical:** [One critical item] — Due: [date] — Owner: [name]

**Next:** [One next step]

## [PROJECT NAME 2]

**Status:** [On-track/At-risk/Blocked]

**Critical:** [One critical item] — Due: [date] — Owner: [name]

**Next:** [One next step]

## [PROJECT NAME 3]

**Status:** [On-track/At-risk/Blocked]

**Critical:** [One critical item] — Due: [date] — Owner: [name]

**Next:** [One next step]

Keep each project to exactly 3 lines."""
        
        summary = self._query_llama2_with_retry(prompt, temperature)
        logger.info(f"✅ Project-based summary generated")
        return summary
    
    def generate_customer_pulse_report(self, transcription, customer_name="Customer", month="Current"):
        """
        Generate CUSTOMER PULSE REPORT matching Barton Mechanical format
        
        Args:
            transcription: Full meeting transcript
            customer_name: Name of customer/client
            month: Report month (e.g., "January 2025")
        
        Returns:
            str: Formatted markdown report
        """
        if not self.is_ollama_running():
            raise ConnectionError(f"Ollama is not running at {self.base_url}")
        
        logger.info(f"🔄 Generating Customer Pulse Report for {customer_name}...")
        
        try:
            # Extract all components
            meetings = self._extract_meetings_for_report(transcription)
            sentiment_breakdown = self._extract_sentiment_breakdown(transcription)
            themes = self._extract_themes_for_report(transcription)
            priorities = self._extract_client_priorities(transcription)
            followups = self._extract_recommended_followups(transcription)
            overall_sentiment = self._determine_overall_sentiment(sentiment_breakdown)
            
            # Format report
            report = self._format_customer_pulse_report(
                customer_name=customer_name,
                month=month,
                overall_sentiment=overall_sentiment,
                meetings=meetings,
                sentiment_breakdown=sentiment_breakdown,
                themes=themes,
                priorities=priorities,
                followups=followups
            )
            
            logger.info(f"✅ Customer Pulse Report generated successfully")
            return report
            
        except Exception as e:
            logger.error(f"❌ Error generating pulse report: {str(e)}")
            raise
    
    def _extract_meetings_for_report(self, transcription):
        """Extract meetings from transcript for pulse report"""
        prompt = f"""Extract all meetings/interactions from this transcript.

For each meeting, identify:
1. Date (e.g., "Jan 3", "Dec 5", "Current", "Week of Dec 5")
2. Meeting Type (Kickoff, Weekly Sync, Coordination, Review, Leadership, Status)
3. Key Points (brief 1-2 line summary of outcomes)
4. Sentiment (Positive, Neutral, Negative)

Transcript:
{transcription}

Return ONLY valid JSON (no markdown, no code blocks, no extra text):
{{
  "meetings": [
    {{
      "date": "Jan 3",
      "meeting_type": "Kickoff",
      "key_points": "Aligned deliverables; introduced automation plugin",
      "sentiment": "Positive"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.1)
            data = self._parse_json_response(response)
            return data.get("meetings", [])
        except Exception as e:
            logger.warning(f"Meeting extraction failed: {str(e)}")
            return []
    
    def _extract_sentiment_breakdown(self, transcription):
        """Extract detailed sentiment breakdown with counts and narratives"""
        prompt = f"""Analyze sentiment in this transcript. Provide counts and detailed explanation.

Count how many mentions are positive, neutral, and negative.
Then explain WHAT caused each sentiment (not just counts).

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "positive_count": 18,
  "positive_mentions": "Description of what customer praised. Include specific things they appreciated.",
  "neutral_count": 5,
  "neutral_mentions": "Description of neutral statements. Include clarifications or operational discussions.",
  "negative_count": 4,
  "negative_mentions": "Description of concerns or complaints. Include what caused frustration or concern."
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data
        except Exception as e:
            logger.warning(f"Sentiment breakdown failed: {str(e)}")
            return {
                "positive_count": 0,
                "positive_mentions": "Unable to extract",
                "neutral_count": 0,
                "neutral_mentions": "Unable to extract",
                "negative_count": 0,
                "negative_mentions": "Unable to extract"
            }
    
    def _extract_themes_for_report(self, transcription):
        """Extract major themes with frequency"""
        prompt = f"""Identify 3-5 major themes in this transcript.

For each theme:
1. Theme name (e.g., "Timelines", "Quality", "Communication")
2. Frequency (High, Medium, Low) - based on how often discussed
3. Example or specific instance

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "themes": [
    {{
      "theme": "Timelines",
      "frequency": "High",
      "example": "Need earlier updates on delays"
    }},
    {{
      "theme": "QC Issues",
      "frequency": "Medium",
      "example": "Missing dimensions in sheets"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data.get("themes", [])
        except Exception as e:
            logger.warning(f"Theme extraction failed: {str(e)}")
            return []
    
    def _extract_client_priorities(self, transcription):
        """Extract customer priorities and requirements"""
        prompt = f"""Extract 3-5 key priorities/requirements that the customer mentioned or cares about.

Focus on:
- What they repeatedly mentioned
- What they explicitly requested
- What would improve satisfaction
- What they value most

Format as bullet points with action-oriented language.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "priorities": [
    "Faster turnaround on revision cycles",
    "Standardized deliverables (annotations, sheet layouts)",
    "Access to preview builds of automation tools"
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data.get("priorities", [])
        except Exception as e:
            logger.warning(f"Priority extraction failed: {str(e)}")
            return []
    
    def _extract_recommended_followups(self, transcription):
        """Extract recommended follow-up actions"""
        prompt = f"""Extract 3-5 recommended follow-up actions based on this transcript.

These should be:
- Actions the team should take to improve satisfaction
- Commitments that were made
- Issues to follow up on
- Improvements to implement

Format as specific action items with deliverables or outcomes.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "followups": [
    "Weekly proactive progress email",
    "Roll out QC Checklist v2",
    "Schedule automation demo on Feb 5"
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data.get("followups", [])
        except Exception as e:
            logger.warning(f"Follow-up extraction failed: {str(e)}")
            return []
    
    def _determine_overall_sentiment(self, sentiment_data):
        """Determine overall sentiment from breakdown"""
        try:
            pos = sentiment_data.get("positive_count", 0)
            neu = sentiment_data.get("neutral_count", 0)
            neg = sentiment_data.get("negative_count", 0)
            
            total = pos + neu + neg
            if total == 0:
                return "Neutral"
            
            pos_ratio = pos / total
            neg_ratio = neg / total
            
            # Positive if more than 60% positive mentions
            if pos_ratio > 0.6:
                return "Positive"
            # Negative if more than 30% negative mentions
            elif neg_ratio > 0.3:
                return "Negative"
            # Mixed/leaning positive if 40-60% positive
            elif pos_ratio >= 0.4:
                return "Positive"
            # Otherwise neutral
            else:
                return "Neutral"
        except Exception as e:
            logger.warning(f"Sentiment determination failed: {str(e)}")
            return "Neutral"
    
    def _format_customer_pulse_report(self, customer_name, month, overall_sentiment, 
                                       meetings, sentiment_breakdown, themes, 
                                       priorities, followups):
        """Format all components into customer pulse report"""
        from datetime import datetime
        
        # Build meeting table
        meeting_table = "| Date | Meeting Type | Key Points | Sentiment |\n"
        meeting_table += "|------|--------------|-----------|----------|\n"
        
        if meetings:
            for m in meetings:
                date = m.get("date", "")
                mtype = m.get("meeting_type", "")
                points = m.get("key_points", "")
                sentiment = m.get("sentiment", "")
                meeting_table += f"| {date} | {mtype} | {points} | {sentiment} |\n"
        else:
            meeting_table += "| N/A | N/A | No meetings extracted | N/A |\n"
        
        # Build themes table
        themes_table = "| Theme | Frequency | Example |\n"
        themes_table += "|-------|-----------|----------|\n"
        
        if themes:
            for t in themes:
                theme_name = t.get("theme", "")
                freq = t.get("frequency", "")
                example = t.get("example", "")
                themes_table += f"| {theme_name} | {freq} | {example} |\n"
        else:
            themes_table += "| N/A | N/A | No themes extracted |\n"
        
        # Build priorities list
        if priorities:
            priorities_list = "\n".join([f"• {p}" for p in priorities])
        else:
            priorities_list = "• No priorities extracted"
        
        # Build followups list
        if followups:
            followups_list = "\n".join([f"• {f}" for f in followups])
        else:
            followups_list = "• No follow-ups recommended"
        
        # Get sentiment breakdown
        pos_count = sentiment_breakdown.get("positive_count", 0)
        pos_mentions = sentiment_breakdown.get("positive_mentions", "")
        neu_count = sentiment_breakdown.get("neutral_count", 0)
        neu_mentions = sentiment_breakdown.get("neutral_mentions", "")
        neg_count = sentiment_breakdown.get("negative_count", 0)
        neg_mentions = sentiment_breakdown.get("negative_mentions", "")
        
        # Assemble report
        report = f"""# Customer Pulse Report

**Customer:** {customer_name}  
**Month:** {month}  
**Overall Sentiment:** {overall_sentiment}

---

## 1. Meeting Summary Consolidation

{meeting_table}

---

## 2. Sentiment Trends

**Positive Mentions ({pos_count}):** {pos_mentions}

**Neutral Mentions ({neu_count}):** {neu_mentions}

**Negative Mentions ({neg_count}):** {neg_mentions}

---

## 3. Themes Identified

{themes_table}

---

## 4. Client Priorities

{priorities_list}

---

## 5. Recommended Follow-ups

{followups_list}

---

*Report Generated: {datetime.now().strftime("%B %d, %Y")}*
"""
        
        return report
    
    def _parse_json_response(self, response):
        """Parse JSON from LLM response with error handling"""
        import json
        import re
        
        try:
            cleaned = response.strip()
            
            # Remove markdown code blocks if present
            if "```" in cleaned:
                match = re.search(r'```(?:json)?\s*(.*?)\s*```', cleaned, re.DOTALL)
                if match:
                    cleaned = match.group(1).strip()
            
            # Extract JSON object from text
            json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if json_match:
                cleaned = json_match.group(0)
            
            # Parse JSON
            return json.loads(cleaned)
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON Parse Error: {str(e)}")
            logger.debug(f"Failed to parse response: {response[:300]}")
            return {}
        except Exception as e:
            logger.error(f"Error parsing JSON: {str(e)}")
            return {}
    
    def generate_client_pulse_report(self, transcription, client_name="Client", month="Current"):
        """
        Generate CLIENT PULSE REPORT format summary
        Following the Barton Mechanical example structure
        Uses multi-step specialized extraction for better accuracy on complex transcripts
        """
        if not self.is_ollama_running():
            raise ConnectionError(f"Ollama is not running at {self.base_url}")
        
        logger.info(f"🔄 Generating Client Pulse Report using multi-step extraction...")
        
        try:
            import time
            
            # Step 1: Identify stakeholders (PRIMARY decision-maker and dependencies)
            logger.info("Step 1/11: Extracting stakeholders...")
            stakeholders_data = self._extract_stakeholders_pulse(transcription, client_name)
            time.sleep(0.5)  # Small delay to avoid overwhelming the model
            
            # Step 2: Extract sentiment with enhanced tone analysis
            logger.info("Step 2/11: Extracting sentiment with tone analysis...")
            sentiment_data = self._extract_sentiment_enhanced_pulse(transcription)
            time.sleep(0.5)
            
            # Step 3: Extract top 5 critical items and deadlines
            logger.info("Step 3/11: Extracting critical items (top 5)...")
            critical_items_data = self._extract_critical_items_comprehensive_pulse(transcription)
            time.sleep(0.3)
            
            # Step 4: Extract top 5 action items with owners
            logger.info("Step 4/11: Extracting action items (top 5)...")
            action_items_data = self._extract_action_items_comprehensive_pulse(transcription)
            time.sleep(0.3)
            
            # Step 5: Extract top 3-4 root causes
            logger.info("Step 5/11: Extracting root causes (top 3-4)...")
            root_causes_data = self._extract_root_causes_pulse(transcription)
            time.sleep(0.3)
            
            # Step 6: Extract top 3-4 risks
            logger.info("Step 6/11: Extracting risks (top 3-4)...")
            risks_data = self._extract_risks_pulse(transcription)
            time.sleep(0.3)
            
            # Step 7: Extract top 5 themes
            logger.info("Step 7/11: Extracting themes (top 5)...")
            themes_data = self._extract_themes_pulse(transcription)
            time.sleep(0.3)
            
            # Step 8: Extract strategic context
            logger.info("Step 8/11: Extracting strategic context...")
            strategic_context = self._extract_strategic_context_pulse(transcription)
            time.sleep(0.5)
            
            # Step 9: Extract client priorities with strategic context
            logger.info("Step 9/11: Extracting client priorities with strategic context...")
            priorities_data = self._extract_client_priorities_pulse(transcription)
            time.sleep(0.5)
            
            # Step 10: Extract meeting summary
            logger.info("Step 10/11: Extracting meeting summary...")
            meeting_summary = self._extract_meeting_summary_pulse(transcription)
            time.sleep(0.5)
            
            # Step 11: Extract key projects
            logger.info("Step 11/11: Extracting key projects...")
            key_projects = self._extract_key_projects_pulse(transcription)
            
            # Combine all data
            combined_data = {
                "stakeholders": stakeholders_data,
                "overall_sentiment": sentiment_data.get("overall_sentiment", "Unknown"),
                "sentiment_reasoning": sentiment_data.get("reasoning", ""),
                "sentiment_trend": sentiment_data.get("trend", ""),
                "sentiment_summary": sentiment_data.get("summary", {}),
                "meetings": meeting_summary,
                "critical_items": critical_items_data,
                "action_items": action_items_data,
                "root_causes": root_causes_data,
                "risks": risks_data,
                "themes": themes_data,
                "strategic_context": strategic_context,
                "client_priorities": priorities_data,
                "key_projects": key_projects,
                "recommended_followups": action_items_data.get("followups", []) if isinstance(action_items_data, dict) else []
            }
            
            # Format as formatted report
            report = self._format_pulse_report_from_data(combined_data, client_name, month)
            
            logger.info(f"✅ Client Pulse Report generated successfully")
            return report
            
        except Exception as e:
            logger.error(f"❌ Error generating pulse report: {str(e)}")
            # Fallback to original method if multi-step fails
            logger.info("Falling back to single-prompt method...")
            return self._generate_client_pulse_report_fallback(transcription, client_name, month)
    
    def _format_pulse_report(self, pulse_data_str, client_name, month):
        """
        Format JSON pulse data into readable Client Pulse Report
        """
        import json
        import re
        from datetime import datetime
        
        # Clean the JSON string - remove markdown code blocks if present
        cleaned_str = pulse_data_str.strip()
        
        # Remove markdown code blocks (```json ... ``` or ``` ... ```)
        if cleaned_str.startswith("```"):
            # Extract content between code blocks
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', cleaned_str, re.DOTALL)
            if match:
                cleaned_str = match.group(1).strip()
        else:
            # Try to extract JSON object from text
            # Look for content between first { and last }
            json_match = re.search(r'\{.*\}', cleaned_str, re.DOTALL)
            if json_match:
                cleaned_str = json_match.group(0)
        
        # Try to parse JSON
        try:
            data = json.loads(cleaned_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON Parse Error: {str(e)}")
            logger.debug(f"Failed to parse: {pulse_data_str[:200]}...")
            logger.info(f"Returning error report for client: {client_name}")
            
            # Return a formatted error message instead of crashing
            return f"""# CLIENT PULSE REPORT

**Customer:** {client_name}  

**Month:** {month}  

⚠️ **Error:** Could not parse pulse data from LLM response. The LLM may not have returned valid JSON.

**Raw Response (first 500 chars):**

```

{pulse_data_str[:500]}...

```

Please try generating the report again."""
        
        # Normalize the data structure - handle different JSON formats from LLM
        # Map alternative field names to expected structure
        normalized_data = {}
        
        # Handle overall_sentiment (could be sentiment_overall)
        normalized_data['overall_sentiment'] = data.get('overall_sentiment') or data.get('sentiment_overall', 'Unknown')
        
        # Handle meetings (could be in different format)
        normalized_data['meetings'] = data.get('meetings', [])
        
        # Handle sentiment_summary - normalize different structures
        sentiment_summary = data.get('sentiment_summary', {})
        sentiment_count = data.get('sentiment_count', {})
        
        # If we have sentiment_count, convert it to sentiment_summary format
        if sentiment_count and not sentiment_summary:
            sentiment_summary = {
                'positive_count': sentiment_count.get('positive', 0),
                'neutral_count': sentiment_count.get('neutral', 0),
                'negative_count': sentiment_count.get('negative', 0),
                'positive_mentions': data.get('positive_mentions', []),
                'neutral_mentions': data.get('neutral_mentions', []),
                'negative_mentions': data.get('negative_mentions', [])
            }
        else:
            # Ensure we have the lists even if counts are missing
            if 'positive_mentions' not in sentiment_summary:
                sentiment_summary['positive_mentions'] = data.get('positive_mentions', [])
            if 'neutral_mentions' not in sentiment_summary:
                sentiment_summary['neutral_mentions'] = data.get('neutral_mentions', [])
            if 'negative_mentions' not in sentiment_summary:
                sentiment_summary['negative_mentions'] = data.get('negative_mentions', [])
        
        # Handle themes (could be key_themes)
        normalized_data['themes'] = data.get('themes') or data.get('key_themes', [])
        
        # Handle other fields
        normalized_data['client_priorities'] = data.get('client_priorities', [])
        normalized_data['critical_items'] = data.get('critical_items', [])
        normalized_data['recommended_followups'] = data.get('recommended_followups', [])
        normalized_data['key_projects'] = data.get('key_projects', [])
        normalized_data['documents_required'] = data.get('documents_required', [])
        
        # Build report with normalized data
        positive_mentions = sentiment_summary.get('positive_mentions', []) or []
        neutral_mentions = sentiment_summary.get('neutral_mentions', []) or []
        negative_mentions = sentiment_summary.get('negative_mentions', []) or []
        
        report = f"""# CLIENT PULSE REPORT

**Customer:** {client_name}  

**Month:** {month}  

**Overall Sentiment:** {normalized_data.get('overall_sentiment', 'Unknown')}

---

## 1. MEETING SUMMARY CONSOLIDATION

| Date | Meeting Type | Key Points | Sentiment |

|------|--------------|-----------|-----------|

"""
        
        meetings = normalized_data.get('meetings', [])
        if not meetings:
            report += "| No meeting data available | | | |\n"
        else:
            for meeting in meetings:
                if isinstance(meeting, dict):
                    report += f"| {meeting.get('date', 'N/A')} | {meeting.get('meeting_type', 'N/A')} | {str(meeting.get('key_points', 'N/A'))[:100]} | {meeting.get('sentiment', 'N/A')} |\n"
        
        report += f"""

---

## 2. SENTIMENT TRENDS

**Positive Mentions ({sentiment_summary.get('positive_count', len(positive_mentions))}):** {', '.join(str(m) for m in positive_mentions[:3]) if positive_mentions else 'None'}

**Neutral Mentions ({sentiment_summary.get('neutral_count', len(neutral_mentions))}):** {', '.join(str(m) for m in neutral_mentions[:2]) if neutral_mentions else 'None'}

**Negative Mentions ({sentiment_summary.get('negative_count', len(negative_mentions))}):** {', '.join(str(m) for m in negative_mentions[:2]) if negative_mentions else 'None'}

---

## 3. THEMES IDENTIFIED

| Theme | Frequency | Example |

|-------|-----------|---------|

"""
        
        themes = normalized_data.get('themes', [])
        if not themes:
            report += "| No themes identified | | |\n"
        else:
            for theme in themes:
                if isinstance(theme, dict):
                    report += f"| {theme.get('theme', 'N/A')} | {theme.get('frequency', 'N/A')} | {str(theme.get('example', 'N/A'))[:100]} |\n"
        
        report += f"""

---

## 4. CLIENT PRIORITIES

"""
        priorities = normalized_data.get('client_priorities', [])
        if not priorities:
            report += "• No priorities identified\n"
        else:
            for priority in priorities:
                report += f"• {str(priority)}\n"
        
        report += f"""

---

## 5. CRITICAL ITEMS (NEXT 30 DAYS)

"""
        critical_items = normalized_data.get('critical_items', [])
        if not critical_items:
            report += "• No critical items identified\n"
        else:
            for item in critical_items:
                report += f"• {str(item)}\n"
        
        report += f"""

---

## 6. RECOMMENDED FOLLOW-UPS

"""
        followups = normalized_data.get('recommended_followups', [])
        if not followups:
            report += "• No follow-ups recommended\n"
        else:
            for followup in followups:
                report += f"• {str(followup)}\n"
        
        report += f"""

---

## 7. KEY PROJECTS IN FOCUS

"""
        projects = normalized_data.get('key_projects', [])
        if not projects:
            report += "• No projects identified\n"
        else:
            for project in projects:
                report += f"• {str(project)}\n"
        
        report += f"""

---

## 8. DOCUMENTS REQUIRED

"""
        documents = normalized_data.get('documents_required', [])
        if not documents:
            report += "• No documents required\n"
        else:
            for doc in documents:
                if isinstance(doc, dict):
                    doc_type = doc.get('type', 'Document')
                    doc_name = doc.get('name', 'N/A')
                    due_date = doc.get('due_date', 'N/A')
                    owner = doc.get('owner', 'N/A')
                    report += f"• **{doc_type}:** {doc_name} - Due: {due_date} - Owner: {owner}\n"
                else:
                    report += f"• {str(doc)}\n"
        
        return report
    
    def _extract_stakeholders_pulse(self, transcription, provided_name):
        """Extract primary decision-maker and critical dependencies"""
        prompt = f"""Identify the PRIMARY DECISION-MAKER and CRITICAL DEPENDENCIES from this meeting transcript.

CRITICAL: The PRIMARY DECISION-MAKER is the person who:
- Makes final decisions
- Sets deadlines
- Approves work
- Expresses concerns or satisfaction
- Has authority over the project
- Is the actual CLIENT (not internal team members)
- Speaks with authority and makes definitive statements

CRITICAL DEPENDENCIES are other key stakeholders who:
- Are important to the project success
- Need to be managed
- Have influence but are not the primary decision-maker

Distinguish between:
- EXTERNAL CLIENTS (the actual customer/decision-maker)
- INTERNAL TEAM MEMBERS (your team, not the client)

Look for names mentioned in the transcript. The primary decision-maker is often the one who:
- Sets expectations and deadlines
- Expresses satisfaction or concerns
- Makes final calls on priorities
- Has the authority to approve or reject work

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "primary_decision_maker": {{
    "name": "Full name of primary decision-maker (extract from transcript)",
    "role": "Their role/title if mentioned",
    "importance": "Why they are the primary decision-maker (specific examples from transcript)",
    "evidence": "Quote or example showing they make decisions"
  }},
  "critical_dependencies": [
    {{
      "name": "Other stakeholder name",
      "role": "Their role",
      "relationship": "How they relate to primary decision-maker",
      "importance": "Why they matter"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.1)
            data = self._parse_json_response(response)
            return data
        except Exception as e:
            logger.warning(f"Stakeholder extraction failed: {str(e)}")
            return {
                "primary_decision_maker": {"name": provided_name, "role": "", "importance": "", "evidence": ""},
                "critical_dependencies": []
            }
    
    def _extract_sentiment_enhanced_pulse(self, transcription):
        """Extract sentiment with enhanced tone analysis - recognize positive/grateful even with demands"""
        prompt = f"""Analyze sentiment in this transcript with NUANCED understanding. CRITICAL: Demanding or setting deadlines does NOT mean negative sentiment.

A client can be:
- POSITIVE/GRATEFUL while making demands (e.g., "I appreciate your work, but we need X by Y")
- SATISFIED while being direct (e.g., "You've earned that, now let's focus on...")
- APPRECIATIVE while setting expectations (e.g., "Thank you for stepping up, we need...")
- GRATEFUL while being firm (e.g., "I truly appreciate it, here's what we need next")
- POSITIVE with leadership tone (e.g., "I'm happy with progress, let's maintain momentum")

Look for:
- Words of appreciation, gratitude, praise ("appreciate", "thank you", "earned", "stepped up", "happy", "pleased")
- Recognition of good work
- Positive reinforcement
- Warm tone even with demands
- Expressions of satisfaction
- Leadership tone (decisive, directive, but positive)

Track how sentiment CHANGES through the meeting.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "overall_sentiment": "Positive|Neutral|Negative|Frustrated|Concerned|Satisfied|Grateful|Appreciative|Mixed",
  "reasoning": "Why this sentiment (include specific quotes showing appreciation or concern)",
  "trend": "How sentiment changed (e.g., 'Started positive, maintained appreciation throughout' or 'Frustrated → Improving → Collaborative')",
  "summary": {{
    "positive_count": 0,
    "positive_mentions": ["specific quotes showing appreciation/gratitude"],
    "neutral_count": 0,
    "neutral_mentions": ["specific quotes"],
    "negative_count": 0,
    "negative_mentions": ["specific quotes showing concern/frustration"]
  }}
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data
        except Exception as e:
            logger.warning(f"Sentiment extraction failed: {str(e)}")
            return {
                "overall_sentiment": "Unknown",
                "reasoning": "",
                "trend": "",
                "summary": {}
            }
    
    def _extract_critical_items_comprehensive_pulse(self, transcription):
        """Extract top 5 most critical items and deadlines"""
        prompt = f"""Extract the TOP 5 most critical items, deadlines, and deliverables from this transcript.

Focus on:
- Most urgent deadlines (today, tomorrow, specific dates)
- Highest priority deliverables
- Items that block other work
- Items with explicit deadlines

Return ONLY the TOP 5 most critical items. Provide COMPLETE, FULL descriptions - do not truncate or abbreviate.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "critical_items": [
    {{
      "item": "Complete, full description of the critical item with all details",
      "deadline": "Exact deadline",
      "owner": "Person responsible if mentioned",
      "priority": "High|Critical|Urgent"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.1)
            data = self._parse_json_response(response)
            return data.get("critical_items", [])
        except Exception as e:
            logger.warning(f"Critical items extraction failed: {str(e)}")
            return []
    
    def _extract_action_items_comprehensive_pulse(self, transcription):
        """Extract top 5 action items with owners"""
        prompt = f"""Extract the TOP 5 most important action items from this transcript.

Focus on:
- Tasks with assigned owners
- Items with deadlines
- High-priority commitments
- Blocking items

Return ONLY the TOP 5. Provide COMPLETE, FULL action descriptions - do not truncate or abbreviate.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "action_items": [
    {{
      "action": "Complete, full description of the action with all details",
      "owner": "Person responsible",
      "deadline": "Deadline if mentioned",
      "status": "Blocked|On-track|At-risk"
    }}
  ],
  "followups": [
    "Complete follow-up action descriptions"
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.1)
            data = self._parse_json_response(response)
            return data
        except Exception as e:
            logger.warning(f"Action items extraction failed: {str(e)}")
            return {"action_items": [], "followups": []}
    
    def _extract_root_causes_pulse(self, transcription):
        """Extract top 3-4 root causes of problems"""
        prompt = f"""Identify the TOP 3-4 ROOT CAUSES of problems mentioned in this transcript.

Focus on core underlying issues, not symptoms. Provide COMPLETE, FULL descriptions - do not truncate.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "root_causes": [
    {{
      "issue": "Complete description of the core problem with all details",
      "impact": "Complete description of how it affects client/project"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data.get("root_causes", [])
        except Exception as e:
            logger.warning(f"Root causes extraction failed: {str(e)}")
            return []
    
    def _extract_risks_pulse(self, transcription):
        """Extract top 3-4 risks"""
        prompt = f"""Identify the TOP 3-4 most significant risks mentioned in this transcript.

Focus on relationship, project, or timeline risks. Provide COMPLETE, FULL descriptions - do not truncate.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "risks": [
    {{
      "risk": "Complete description of the risk with all details",
      "impact": "Complete description of what could happen",
      "likelihood": "High|Medium|Low",
      "mitigation": "Complete description of how to address it"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data.get("risks", [])
        except Exception as e:
            logger.warning(f"Risks extraction failed: {str(e)}")
            return []
    
    def _extract_themes_pulse(self, transcription):
        """Extract top 5 themes"""
        prompt = f"""Identify the TOP 5 major themes in this transcript.

Focus on most frequently discussed topics. Provide COMPLETE examples - do not truncate.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "themes": [
    {{
      "theme": "Theme Name",
      "frequency": "High|Medium|Low",
      "example": "Complete example with full details"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data.get("themes", [])
        except Exception as e:
            logger.warning(f"Themes extraction failed: {str(e)}")
            return []
    
    def _extract_strategic_context_pulse(self, transcription):
        """Extract strategic context - why things matter, business model, relationships"""
        prompt = f"""Extract STRATEGIC CONTEXT from this transcript - the "why" behind the work.

Look for:
- Business model information (e.g., "Major income from X", "Outsourcing model", "90% capacity")
- Relationship dynamics (e.g., "GC makes changes without notice", "Client expectations")
- Strategic priorities (e.g., "This client is critical because...")
- Context that explains WHY pressure exists
- Background information that helps understand the situation
- Business relationships and dependencies

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "strategic_context": "Overall strategic context and why things matter",
  "business_model": "Business model information if mentioned",
  "relationship_dynamics": "How relationships work (e.g., GC behavior, client expectations)",
  "strategic_priorities": "What matters most strategically"
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data
        except Exception as e:
            logger.warning(f"Strategic context extraction failed: {str(e)}")
            return {
                "strategic_context": "",
                "business_model": "",
                "relationship_dynamics": "",
                "strategic_priorities": ""
            }
    
    def _extract_client_priorities_pulse(self, transcription):
        """Extract top 3-5 client priorities"""
        prompt = f"""Extract the TOP 3-5 key priorities the client mentioned.

Focus on what they repeatedly mentioned or explicitly requested. Provide COMPLETE, FULL descriptions - do not truncate.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "priorities": [
    {{
      "priority": "Complete priority description with all details",
      "strategic_context": "Complete description of why it matters (optional)"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            priorities = data.get("priorities", [])
            # Convert to simple list if structured, or keep as is
            if priorities and isinstance(priorities[0], dict):
                return priorities
            else:
                return [{"priority": p, "strategic_context": ""} if isinstance(p, str) else p for p in priorities]
        except Exception as e:
            logger.warning(f"Priorities extraction failed: {str(e)}")
            return []
    
    def _extract_meeting_summary_pulse(self, transcription):
        """Extract meeting summary"""
        prompt = f"""Extract meeting summary information.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "meetings": [
    {{
      "date": "YYYY-MM-DD or 'Current'",
      "meeting_type": "Status|Kickoff|Coordination|Review|Weekly Sync|Leadership",
      "key_points": "Brief summary (1-2 sentences, full text no truncation)",
      "sentiment": "Positive|Neutral|Negative|Frustrated|Concerned|Grateful|Appreciative"
    }}
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data.get("meetings", [])
        except Exception as e:
            logger.warning(f"Meeting summary extraction failed: {str(e)}")
            return []
    
    def _extract_key_projects_pulse(self, transcription):
        """Extract key projects mentioned"""
        prompt = f"""Extract key projects mentioned in this transcript.

Transcript:
{transcription}

Return ONLY valid JSON:
{{
  "projects": [
    "Project 1",
    "Project 2",
    "Project 3"
  ]
}}"""
        
        try:
            response = self._query_llama2_with_retry(prompt, temperature=0.2)
            data = self._parse_json_response(response)
            return data.get("projects", [])
        except Exception as e:
            logger.warning(f"Key projects extraction failed: {str(e)}")
            return []
    
    def _format_pulse_report_from_data(self, data, client_name, month):
        """Format the combined data into a pulse report"""
        from datetime import datetime
        
        # Extract stakeholders
        stakeholders = data.get("stakeholders", {})
        primary_dm = stakeholders.get("primary_decision_maker", {})
        display_client_name = primary_dm.get("name", client_name)
        client_role = primary_dm.get("role", "")
        client_importance = primary_dm.get("importance", "")
        client_evidence = primary_dm.get("evidence", "")
        critical_dependencies = stakeholders.get("critical_dependencies", [])
        
        # Extract other data
        overall_sentiment = data.get("overall_sentiment", "Unknown")
        sentiment_reasoning = data.get("sentiment_reasoning", "")
        sentiment_trend = data.get("sentiment_trend", "")
        sentiment_summary = data.get("sentiment_summary", {})
        strategic_context_data = data.get("strategic_context", {})
        
        # Build report - concise version
        report = f"""# CLIENT PULSE REPORT

**Decision-Maker:** {display_client_name}{f' ({client_role})' if client_role else ''} | **Month:** {month} | **Sentiment:** {overall_sentiment}
{f'**Trend:** {sentiment_trend[:60]}' if sentiment_trend and len(sentiment_trend) > 0 else ''}

---

## 1. MEETING SUMMARY CONSOLIDATION

| Date | Meeting Type | Key Points | Sentiment |
|------|--------------|-----------|-----------|
"""
        
        meetings = data.get("meetings", [])
        if not meetings:
            report += "| No meeting data | | | |\n"
        else:
            for meeting in meetings[:1]:  # Limit to 1 meeting
                if isinstance(meeting, dict):
                    key_points = str(meeting.get('key_points', 'N/A'))
                    report += f"| {meeting.get('date', 'N/A')} | {meeting.get('meeting_type', 'N/A')} | {key_points} | {meeting.get('sentiment', 'N/A')} |\n"
        
        # Sentiment - concise
        positive_mentions = sentiment_summary.get('positive_mentions', []) or []
        negative_mentions = sentiment_summary.get('negative_mentions', []) or []
        
        report += f"""

---

## 2. SENTIMENT

**Positive ({sentiment_summary.get('positive_count', len(positive_mentions))}):** {', '.join(str(m) for m in positive_mentions[:2]) if positive_mentions else 'None'}
**Negative ({sentiment_summary.get('negative_count', len(negative_mentions))}):** {', '.join(str(m) for m in negative_mentions[:2]) if negative_mentions else 'None'}

---

## 3. THEMES IDENTIFIED

| Theme | Frequency | Example |
|-------|-----------|---------|
"""
        
        themes = data.get("themes", [])[:5]  # Limit to top 5
        if not themes:
            report += "| No themes | | |\n"
        else:
            for theme in themes:
                if isinstance(theme, dict):
                    example = str(theme.get('example', 'N/A'))
                    report += f"| {theme.get('theme', 'N/A')} | {theme.get('frequency', 'N/A')} | {example} |\n"
        
        # Client Priorities
        report += f"""

---

## 4. CLIENT PRIORITIES

"""
        priorities = data.get("client_priorities", [])[:5]  # Limit to top 5
        if not priorities:
            report += "• None\n"
        else:
            for priority in priorities:
                if isinstance(priority, dict):
                    priority_text = priority.get('priority', str(priority))
                    report += f"• {priority_text}\n"
                else:
                    report += f"• {str(priority)}\n"
        
        # Root Causes
        report += f"""

---

## 5. ROOT CAUSES IDENTIFIED

"""
        root_causes = data.get("root_causes", [])[:4]  # Limit to top 4
        if not root_causes:
            report += "• None\n"
        else:
            for cause in root_causes:
                if isinstance(cause, dict):
                    issue = cause.get('issue', 'N/A')
                    impact = cause.get('impact', '')
                    report += f"• **{issue}**"
                    if impact:
                        report += f" - {impact}"
                    report += "\n"
                else:
                    report += f"• {str(cause)}\n"
        
        # Critical Items
        report += f"""

---

## 6. CRITICAL ITEMS & DEADLINES

"""
        critical_items = data.get("critical_items", [])[:5]  # Limit to top 5
        if not critical_items:
            report += "• None\n"
        else:
            if critical_items and isinstance(critical_items[0], dict):
                report += "| Item | Deadline | Owner | Priority |\n"
                report += "|------|----------|-------|----------|\n"
                for item in critical_items:
                    item_desc = str(item.get('item', 'N/A'))
                    report += f"| {item_desc} | {item.get('deadline', 'N/A')} | {item.get('owner', 'N/A')} | {item.get('priority', 'N/A')} |\n"
            else:
                for item in critical_items:
                    report += f"• {str(item)}\n"
        
        # Risks
        report += f"""

---

## 7. RISK ASSESSMENT

"""
        risks = data.get("risks", [])[:4]  # Limit to top 4
        if not risks:
            report += "• None\n"
        else:
            for risk in risks:
                if isinstance(risk, dict):
                    risk_desc = risk.get('risk', 'N/A')
                    impact = risk.get('impact', '')
                    mitigation = risk.get('mitigation', '')
                    report += f"• **{risk_desc}** ({risk.get('likelihood', 'N/A')}) - {impact} | Mitigate: {mitigation}\n"
                else:
                    report += f"• {str(risk)}\n"
        
        # Action Items
        report += f"""

---

## 8. ACTION ITEMS (WITH OWNERS & DEADLINES)

"""
        action_items_data = data.get("action_items", {})
        action_items = action_items_data.get("action_items", []) if isinstance(action_items_data, dict) else action_items_data
        action_items = action_items[:5]  # Limit to top 5
        if not action_items:
            report += "• None\n"
        else:
            if action_items and isinstance(action_items[0], dict):
                report += "| Action | Owner | Deadline | Status |\n"
                report += "|--------|-------|----------|--------|\n"
                for item in action_items:
                    action_desc = str(item.get('action', 'N/A'))
                    report += f"| {action_desc} | {item.get('owner', 'N/A')} | {item.get('deadline', 'N/A')} | {item.get('status', 'N/A')} |\n"
            else:
                for item in action_items:
                    report += f"• {str(item)}\n"
        
        # Follow-ups - concise
        followups = action_items_data.get("followups", []) if isinstance(action_items_data, dict) else []
        followups = followups[:3]  # Limit to top 3
        if followups:
            report += f"""

---

## 9. FOLLOW-UPS

"""
            for followup in followups:
                report += f"• {str(followup)}\n"
        
        # Key Projects - concise
        projects = data.get("key_projects", [])[:3]  # Limit to top 3
        if projects:
            report += f"""

---

## 10. KEY PROJECTS

"""
            for project in projects:
                report += f"• {str(project)}\n"
        
        return report
    
    def _generate_client_pulse_report_fallback(self, transcription, client_name, month):
        """Fallback to original single-prompt method if multi-step fails"""
        prompt = f"""Analyze this meeting transcript and extract data for a CLIENT PULSE REPORT.

IMPORTANT: Return ONLY valid JSON. Do NOT include markdown code blocks, explanations, or any other text. Start with {{ and end with }}.

Transcript:

{transcription}

Return ONLY this JSON structure (fill in actual values):

{{
  "overall_sentiment": "Positive|Neutral|Negative",
  "meetings": [
    {{
      "date": "YYYY-MM-DD or 'Current'",
      "meeting_type": "Status|Kickoff|Coordination|Review|Weekly Sync|Leadership",
      "key_points": "Brief summary (1-2 sentences)",
      "sentiment": "Positive|Neutral|Negative"
    }}
  ],
  "sentiment_summary": {{
    "positive_count": 0,
    "positive_mentions": ["mention1", "mention2", "mention3"],
    "neutral_count": 0,
    "neutral_mentions": ["mention1", "mention2"],
    "negative_count": 0,
    "negative_mentions": ["concern1", "concern2"]
  }},
  "themes": [
    {{
      "theme": "Theme Name",
      "frequency": "High|Medium|Low",
      "example": "Brief example quote or description"
    }}
  ],
  "client_priorities": [
    "Priority 1",
    "Priority 2",
    "Priority 3"
  ],
  "recommended_followups": [
    "Action 1",
    "Action 2",
    "Action 3"
  ],
  "key_projects": [
    "Project 1",
    "Project 2"
  ],
  "critical_items": [
    "Item 1",
    "Item 2"
  ]
}}"""
        
        pulse_data_str = self._query_llama2_with_retry(prompt, temperature=0.2)
        report = self._format_pulse_report(pulse_data_str, client_name, month)
        return report
    
    def generate_multiple_client_pulse_reports(self, transcriptions_dict):
        """
        Generate pulse reports for multiple clients at once
        Input: {"Client Name": transcription, "Client 2": transcription2}
        """
        logger.info(f"Generating {len(transcriptions_dict)} Client Pulse Reports...")
        
        reports = {}
        for client_name, transcription in transcriptions_dict.items():
            reports[client_name] = self.generate_client_pulse_report(
                transcription, 
                client_name=client_name,
                month="Current"
            )
        
        return reports
    
    def export_pulse_report_to_html(self, pulse_report, filename="pulse_report.html"):
        """
        Export pulse report to HTML file for easy sharing
        
        Returns:
            dict: Status information including filename, size, and status
        """
        html = f"""<!DOCTYPE html>

<html>

<head>

    <meta charset="UTF-8">

    <title>Client Pulse Report</title>

    <style>

        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}

        .container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}

        h1 {{ color: #333; border-bottom: 3px solid #0066cc; padding-bottom: 10px; }}

        h2 {{ color: #0066cc; margin-top: 30px; }}

        table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}

        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}

        th {{ background: #0066cc; color: white; font-weight: 600; }}

        tr:nth-child(even) {{ background: #f9f9f9; }}

        tr:hover {{ background: #f0f0f0; }}

        .sentiment-positive {{ color: green; font-weight: bold; }}

        .sentiment-neutral {{ color: orange; font-weight: bold; }}

        .sentiment-negative {{ color: red; font-weight: bold; }}

        ul {{ line-height: 1.8; }}

        .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; font-size: 12px; text-align: center; }}

    </style>

</head>

<body>

    <div class="container">

        {pulse_report}

        <div class="footer">

            <p>Generated by NeevIQ Summary System</p>

            <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

        </div>

    </div>

</body>

</html>"""
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(html)
            
            # Verify file was created
            file_size = os.path.getsize(filename)
            logger.info(f"✅ Report exported to {filename} ({file_size} bytes)")
            
            return {
                "filename": filename,
                "size": file_size,
                "status": "success",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return {
                "filename": filename,
                "size": 0,
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def _summarize_chunked_with_final_pass(self, transcription, temperature=0.3):
        """
        Summarize large transcripts by chunking, then creating a final unified summary
        """
        logger.info("Splitting large transcript into chunks...")
        
        # Chunk size: ~2000 tokens = ~8000 chars per chunk (safe for context window)
        chunks = self.chunker.chunk_transcript(
            transcription, 
            max_tokens=self.config.MAX_CHUNK_TOKENS,
            overlap=self.config.CHUNK_OVERLAP
        )
        logger.info(f"Created {len(chunks)} chunks. Processing sequentially...")
        
        chunk_summaries = []
        start_time = time.time()
        
        # Process chunks sequentially
        for idx, chunk in enumerate(chunks):
            try:
                chunk_prompt = f"""Summarize this portion of a meeting transcript. Focus on key points, decisions, action items, and important details:

{chunk}

Provide a concise but detailed summary of this portion:"""
                
                summary = self._query_llama2_with_retry(chunk_prompt, temperature)
                chunk_summaries.append(summary)
                
                elapsed = time.time() - start_time
                logger.info(f"  ✅ Chunk {idx + 1}/{len(chunks)} done ({elapsed:.0f}s elapsed)")
            except Exception as e:
                logger.warning(f"  ⚠️  Chunk {idx + 1} failed: {e}")
                continue
        
        if not chunk_summaries:
            raise Exception("Failed to summarize any chunks")
        
        elapsed = time.time() - start_time
        logger.info(f"Successfully processed {len(chunk_summaries)}/{len(chunks)} chunks in {elapsed:.0f}s")
        logger.info("Creating final unified summary...")
        
        # Combine chunk summaries
        combined = "\n\n".join(chunk_summaries)
        
        # Final pass to create cohesive, detailed summary
        final_prompt = f"""Create a comprehensive and detailed meeting summary from the following chunk summaries:

{combined}

Please provide a detailed summary including:

1. MEETING OVERVIEW:
   - What was the main purpose and context of this meeting?
   - Who were the key participants?

2. MAIN TOPICS DISCUSSED:
   - List and explain each major topic covered in detail
   - What specific issues, questions, or concerns were raised?

3. KEY DECISIONS MADE:
   - What decisions or conclusions were reached?
   - What was agreed upon?

4. ACTION ITEMS:
   - Who needs to do what?
   - Any deadlines or timeframes mentioned?

5. TECHNICAL DETAILS:
   - Any specific technical information, data, specifications, or numbers mentioned
   - Any system names, tools, or technologies discussed

6. CLIENT FEEDBACK & REQUESTS:
   - What feedback, concerns, or requests did the client express?
   - Any issues or problems that need attention?

7. NEXT STEPS:
   - What are the follow-up actions?
   - What will happen next?

8. IMPORTANT DETAILS:
   - Any important names, dates, numbers, or deliverables mentioned
   - Any critical information that should be noted

Be specific and include relevant details from the meeting:"""
        
        final_summary = self._query_llama2_with_retry(final_prompt, temperature)
        logger.info(f"✅ Final summary generated ({len(final_summary)} chars)")
        return final_summary
    
    def _summarize_chunked_sequential(self, transcription, summary_type="structured", temperature=0.3):
        """
        Summarize long transcripts using SEQUENTIAL processing (OPTIMIZED FOR MACBOOK PRO)
        Process one chunk at a time to avoid CPU/memory issues
        """
        logger.info("Splitting long transcript into chunks...")
        
        # Balanced chunk size for llama2:13b on CPU
        # 1200 tokens = ~4800 chars (creates manageable number of chunks)
        chunks = self.chunker.chunk_transcript(
            transcription, 
            max_tokens=1200,
            overlap=100
        )
        logger.info(f"Created {len(chunks)} chunks. Processing sequentially (1 at a time for optimal performance)...")
        
        chunk_summaries = []
        start_time = time.time()
        
        # Process chunks ONE AT A TIME (sequential for MacBook Pro CPU)
        for idx, chunk in enumerate(chunks):
            try:
                # Ultra-simple prompt - just the chunk
                prompt = f"Generate summary:\n\n{chunk}"
                summary = self._query_llama2_with_retry(prompt, temperature)
                chunk_summaries.append(summary)
                
                elapsed = time.time() - start_time
                logger.info(f"  ✅ Chunk {idx + 1}/{len(chunks)} done ({elapsed:.0f}s elapsed)")
            except Exception as e:
                logger.warning(f"  ⚠️  Chunk {idx + 1} failed: {e}")
                # Continue with other chunks
                continue
        
        if not chunk_summaries:
            raise Exception("Failed to summarize any chunks")
        
        elapsed = time.time() - start_time
        logger.info(f"Successfully processed {len(chunk_summaries)}/{len(chunks)} chunks in {elapsed:.0f}s")
        logger.info("Creating final unified summary...")
        
        # Combine chunk summaries
        combined = "\n\n".join(chunk_summaries)
        
        # Final pass to create cohesive summary
        final_prompt = f"Combine into one summary:\n\n{combined}"
        
        return self._query_llama2_with_retry(final_prompt, temperature)
    
    def _build_concise_prompt(self, transcription):
        """Build prompt for concise 2-3 sentence summary"""
        return f"""[INST] You are a meeting summarization assistant. Summarize the meeting transcript below in 2-3 sentences.

IMPORTANT: Do NOT include the original transcript in your response. Only output the summary.

Here is the meeting transcript to summarize:
{transcription}

Generate the summary now (2-3 sentences only): [/INST]"""
    
    def _build_detailed_prompt(self, transcription):
        """Build prompt for detailed summary with key points"""
        return f"""[INST] You are a meeting summarization assistant. Based on the meeting transcript below, create a detailed summary including:
- Main topics discussed
- Key decisions made
- Action items and follow-ups
- Attendees and their roles (if mentioned)

IMPORTANT: Do NOT include the original transcript in your response. Only output the summary.

Here is the meeting transcript to summarize:
{transcription}

Generate the detailed summary now (remember: do NOT include the transcript, only the summary): [/INST]"""
    
    def _build_structured_prompt(self, transcription):
        """Build simple prompt for detailed meeting summary"""
        return f"""[INST] Generate a detailed summary of the following meeting transcript. 
Meeting transcript:
{transcription}

Detailed summary: [/INST]"""
    
    def _build_prompt(self, transcription, summary_type="structured"):
        """Build prompt based on summary type"""
        if summary_type == "concise":
            return self._build_concise_prompt(transcription)
        elif summary_type == "detailed":
            return self._build_detailed_prompt(transcription)
        else:
            return self._build_structured_prompt(transcription)
    
    def _query_llama2_with_retry(self, prompt, temperature=0.3):
        """
        Query Ollama llama2:13b model with automatic retry and exponential backoff
        
        Args:
            prompt (str): Prompt to send to model
            temperature (float): Model temperature (0.0-1.0)
        
        Returns:
            str: Model response
        
        Raises:
            Exception: If all retries fail
        """
        for attempt in range(self.config.MAX_RETRIES):
            try:
                return self._query_llama2(prompt, temperature)
            except requests.exceptions.Timeout as e:
                if attempt < self.config.MAX_RETRIES - 1:
                    wait_time = self.config.RETRY_BACKOFF ** attempt  # 1s, 2s, 4s
                    logger.warning(f"⏱️  Timeout on attempt {attempt+1}/{self.config.MAX_RETRIES}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"❌ Failed after {self.config.MAX_RETRIES} retries")
                    raise
            except Exception as e:
                # Don't retry on non-timeout errors
                logger.error(f"❌ Non-recoverable error: {e}")
                raise
    
    def _query_llama2(self, prompt, temperature=0.3):
        """
        Query Ollama llama2:13b model
        Optimized for MacBook Pro CPU inference
        
        Args:
            prompt (str): Prompt to send to model
            temperature (float): Model temperature (0.0-1.0)
        
        Returns:
            str: Model response
        
        Raises:
            Exception: If API call fails
        """
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "top_p": 0.9,
                    "top_k": 40,
                    "num_predict": self.config.NUM_PREDICT,
                    "num_ctx": self.config.NUM_CTX,
                    "repeat_penalty": 1.1,
                }
            }
            
            logger.debug(f"Querying {self.model} (temp={temperature})...")
            
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code != 200:
                error_msg = response.text if response.text else f"Status {response.status_code}"
                raise Exception(f"Ollama API error: {error_msg}")
            
            result = response.json()
            
            if "response" not in result:
                raise Exception("Invalid response from Ollama")
            
            summary = result["response"].strip()
            
            if not summary:
                raise Exception("Empty response from model")
            
            logger.debug(f"Summary generated ({len(summary)} chars)")
            
            # Trace to Langfuse
            try:
                trace_ollama_generation(
                    prompt=prompt,
                    model=self.model,
                    temperature=temperature,
                    response=summary,
                    metadata={
                        "num_predict": self.config.NUM_PREDICT,
                        "num_ctx": self.config.NUM_CTX,
                        "prompt_length": len(prompt),
                        "response_length": len(summary)
                    }
                )
            except Exception as e:
                # Don't fail if Langfuse tracing fails
                logger.warning(f"Failed to trace to Langfuse: {e}")
            
            # Trace to OPIK
            try:
                trace_ollama_opik(
                    prompt=prompt,
                    model=self.model,
                    temperature=temperature,
                    response=summary,
                    metadata={
                        "num_predict": self.config.NUM_PREDICT,
                        "num_ctx": self.config.NUM_CTX,
                        "prompt_length": len(prompt),
                        "response_length": len(summary)
                    }
                )
            except Exception as e:
                # Don't fail if OPIK tracing fails
                logger.warning(f"Failed to trace to OPIK: {e}")
            
            return summary
            
        except requests.exceptions.Timeout:
            raise requests.exceptions.Timeout(
                f"Request timed out after {self.timeout}s. "
                "llama2:13b on CPU is slower. Timeout set to 15 minutes. "
                "If still timing out, please wait longer or reduce chunk size."
            )
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Could not connect to Ollama at {self.base_url}. "
                "Ensure Ollama is running: ollama serve"
            )
        except json.JSONDecodeError:
            raise Exception("Invalid JSON response from Ollama")
        except Exception as e:
            logger.error(f"Error querying {self.model}: {e}")
            raise
            
            if not summary:
                raise Exception("Empty response from model")
            
            # Validate summary - reject conversational responses
            conversational_phrases = [
                "thank you for providing",
                "i will create",
                "is there anything else",
                "i can help you",
                "let me know if",
                "please provide",
                "i need more information",
                "based on your instructions"
            ]
            summary_lower = summary.lower()
            is_conversational = any(phrase in summary_lower for phrase in conversational_phrases)
            
            # If it's a conversational response and too short, reject it
            if is_conversational and len(summary) < 500:
                logger.warning(f"⚠️  Rejected conversational response (length: {len(summary)} chars)")
                raise Exception("Model returned conversational response instead of summary. Please try again.")
            
            logger.debug(f"Summary generated ({len(summary)} chars)")
            
            return summary
            
        except requests.exceptions.Timeout:
            raise Exception(
                f"Request timed out after {self.timeout}s. "
                "llama2:13b on CPU is slower. Timeout has been set to 15 minutes. "
                "If still timing out, please wait longer or reduce chunk size."
            )
        except requests.exceptions.ConnectionError:
            raise Exception(
                f"Could not connect to Ollama at {self.base_url}. "
                "Ensure Ollama is running: ollama serve"
            )
        except json.JSONDecodeError:
            raise Exception("Invalid JSON response from Ollama")
        except Exception as e:
            logger.error(f"Error querying {self.model}: {e}")
            raise
    
    def _append_satisfaction_analysis(self, summary: str, satisfaction_analysis: dict) -> str:
        """
        Append satisfaction analysis section to the summary
        
        Args:
            summary: Original summary text
            satisfaction_analysis: Satisfaction analysis dictionary
        
        Returns:
            str: Summary with satisfaction analysis appended
        """
        satisfaction_score = satisfaction_analysis.get('satisfaction_score', 50.0)
        sentiment = satisfaction_analysis.get('sentiment', {})
        polarity = sentiment.get('polarity', 0.0)
        
        # Determine if client is happy
        is_happy = satisfaction_score >= 60
        client_status = "Client is satisfied/happy" if is_happy else "Client has concerns or is not fully satisfied"
        
        # Determine sentiment label
        if polarity > 0.1:
            sentiment_label = "Positive"
            sentiment_reason = "The conversation shows positive sentiment with constructive discussions and positive feedback."
        elif polarity < -0.1:
            sentiment_label = "Negative"
            sentiment_reason = "The conversation shows negative sentiment with concerns, issues, or dissatisfaction expressed."
        else:
            sentiment_label = "Neutral"
            sentiment_reason = "The conversation shows neutral sentiment with factual discussions and no strong emotional indicators."
        
        # Add more specific reason based on satisfaction score
        if satisfaction_score >= 75:
            sentiment_reason += f" High satisfaction score ({satisfaction_score:.1f}/100) indicates strong positive engagement."
        elif satisfaction_score < 40:
            sentiment_reason += f" Low satisfaction score ({satisfaction_score:.1f}/100) indicates concerns or issues need attention."
        
        # Build satisfaction section
        satisfaction_section = f"""

---

## Satisfaction Analysis

**Client Status:** {client_status}

**Sentiment:** {sentiment_label}
**Reason:** {sentiment_reason}

**Satisfaction Score:** {satisfaction_score:.1f}/100

"""
        
        return summary + satisfaction_section
    
    def summarize_with_client_pulse(self, transcription, client_name="Client", month="January 2025"):
        """
        Generate summary + Client Pulse Report (like your Barton Mechanical example)
        """
        if not self.is_ollama_running():
            raise ConnectionError(f"Ollama is not running at {self.base_url}")
        
        # First get concise summary
        concise_summary = self.summarize(transcription, summary_type="concise")
        
        # Then extract sentiment and themes for pulse report
        prompt = f"""Analyze this meeting transcript and extract for a CLIENT PULSE REPORT.

Meeting Transcript:
{transcription}

Return ONLY valid JSON (no markdown, no extra text):
{{
  "meeting_date": "YYYY-MM-DD or 'Not mentioned'",
  "sentiment_overall": "Positive|Neutral|Negative",
  "sentiment_count": {{"positive": 0, "neutral": 0, "negative": 0}},
  "key_themes": [
    {{"theme": "Theme Name", "frequency": "High|Medium|Low", "example": "Brief example"}}
  ],
  "client_priorities": ["Priority 1", "Priority 2", "Priority 3"],
  "positive_mentions": ["mention1", "mention2"],
  "negative_mentions": ["concern1", "concern2"],
  "documents_required": [
    {{"type": "Report|Drawing|Plan|Checklist|Other", "name": "Document name", "due_date": "date or 'ASAP'", "owner": "Name"}}
  ],
  "recommended_followups": ["action1", "action2"]
}}"""
        
        pulse_data = self._query_llama2_with_retry(prompt, temperature=0.2)
        
        # Format as Client Pulse Report
        report = self._format_client_pulse_report(pulse_data, client_name, month)
        
        logger.info(f"✅ Summary + Pulse Report generated")
        return {
            "concise_summary": concise_summary,
            "client_pulse_report": report,
            "pulse_data": pulse_data
        }
    
    def _format_client_pulse_report(self, pulse_data, client_name, month):
        """Format pulse data into readable Client Pulse Report"""
        try:
            import json
            data = json.loads(pulse_data)
        except:
            logger.warning("Could not parse pulse data, returning raw format")
            return pulse_data
        
        report = f"""# CLIENT PULSE REPORT

**Client:** {client_name}

**Month:** {month}

**Overall Sentiment:** {data.get('sentiment_overall', 'Unknown')}

## SENTIMENT SUMMARY

- Positive: {data.get('sentiment_count', {}).get('positive', 0)} mentions

- Neutral: {data.get('sentiment_count', {}).get('neutral', 0)} mentions  

- Negative: {data.get('sentiment_count', {}).get('negative', 0)} mentions

## KEY THEMES

"""
        for theme in data.get('key_themes', []):
            report += f"\n**{theme['theme']}** ({theme['frequency']})\n- Example: {theme['example']}"
        
        report += f"\n\n## CLIENT PRIORITIES\n"
        for priority in data.get('client_priorities', []):
            report += f"• {priority}\n"
        
        if data.get('documents_required'):
            report += f"\n## DOCUMENTS REQUIRED\n"
            report += "| Type | Name | Due | Owner |\n|------|------|-----|-------|\n"
            for doc in data.get('documents_required', []):
                report += f"| {doc['type']} | {doc['name']} | {doc['due_date']} | {doc['owner']} |\n"
        
        report += f"\n## RECOMMENDED FOLLOW-UPS\n"
        for followup in data.get('recommended_followups', []):
            report += f"• {followup}\n"
        
        return report


class TranscriptChunker:
    """Helper class to split long transcripts into chunks for processing"""
    
    @staticmethod
    def chunk_transcript(transcription, max_tokens=1000, overlap=50):
        """
        Split transcript into chunks for processing
        Optimized for llama2:13b on MacBook Pro
        
        Args:
            transcription (str): Full transcription
            max_tokens (int): Max tokens per chunk (rough estimate)
            overlap (int): Overlapping words between chunks for context
        
        Returns:
            list: List of transcript chunks
        """
        # Rough estimate: 1 token ≈ 4 characters
        max_chars = max_tokens * 4
        
        words = transcription.split()
        chunks = []
        current_chunk = []
        current_length = 0
        
        for word in words:
            word_length = len(word) + 1  # +1 for space
            
            if current_length + word_length > max_chars and current_chunk:
                # Start new chunk with overlap
                chunks.append(' '.join(current_chunk))
                
                # Keep last 'overlap' words for context
                current_chunk = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                current_length = sum(len(w) + 1 for w in current_chunk)
            
            current_chunk.append(word)
            current_length += word_length
        
        if current_chunk:
            chunks.append(' '.join(current_chunk))
        
        return chunks if chunks else [transcription]