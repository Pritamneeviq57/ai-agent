"""
Claude Summarizer for Cloud Deployment
Minimal implementation using Anthropic Claude API
"""
import os
import time
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Only import if API key is set
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


class ClaudeSummarizer:
    """Simple summarizer using Anthropic Claude API"""
    
    def __init__(self, model="claude-opus-4-5-20251101"):
        """
        Initialize Claude summarizer
        
        Args:
            model: Claude model to use
                   - claude-3-haiku-20240307 (fastest, cheapest)
                   - claude-3-sonnet-20240229 (balanced)
                   - claude-3-opus-20240229 (best, expensive)
                   - claude-opus-4-5-20251101 (latest, most advanced)
        """
        self.model = model
        self.client = None
        
        if not ANTHROPIC_API_KEY:
            logger.warning("ANTHROPIC_API_KEY not set - summarization won't work")
        else:
            try:
                from anthropic import Anthropic, RateLimitError
                self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
                logger.info(f"✅ Claude summarizer initialized with {model}")
            except ImportError:
                logger.error("anthropic package not installed. Run: pip install anthropic")
            except Exception as e:
                logger.error(f"Failed to initialize Claude client: {e}")
    
    def is_available(self):
        """Check if Claude is available"""
        return self.client is not None
    
    def _call_with_retry(self, api_call_func, max_retries=5, initial_delay=2):
        """
        Execute API call with retry logic for connection errors
        
        Args:
            api_call_func: Function that makes the API call
            max_retries: Maximum number of retry attempts (default: 5)
            initial_delay: Initial delay in seconds before retry (exponential backoff, default: 2s)
        
        Returns:
            Result from api_call_func
        """
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                return api_call_func()
            except Exception as e:
                error_str = str(e).lower()
                error_type = type(e).__name__
                
                # Check for rate limit errors (429)
                # Try to check exception type, but fall back to string matching
                try:
                    from anthropic import RateLimitError
                    is_rate_limit_type = isinstance(e, RateLimitError)
                except (ImportError, NameError):
                    is_rate_limit_type = False
                
                is_rate_limit = (
                    is_rate_limit_type or
                    "429" in error_str or
                    "rate_limit" in error_str or
                    "rate limit" in error_str or
                    "exceed the rate limit" in error_str or
                    error_type == "RateLimitError"
                )
                
                # Check if it's a connection/network error that we should retry
                is_retryable = (
                    "connection" in error_str or
                    "timeout" in error_str or
                    "network" in error_str or
                    "temporary" in error_str or
                    "socket" in error_str or
                    "connect" in error_str or
                    error_type in ["ConnectionError", "TimeoutError", "APIConnectionError", "APITimeoutError"]
                )
                
                # Rate limit errors should be retried with longer delays
                if is_rate_limit:
                    # For rate limits, use moderate delays: 30s, 60s, 90s (reduced from 60s, 120s, 180s)
                    # Also reduce max retries for rate limits to avoid long waits
                    rate_limit_max_retries = 3  # Only retry 3 times for rate limits
                    if attempt < rate_limit_max_retries - 1:
                        delay = 30 + (attempt * 30)  # 30s, 60s, 90s
                        logger.warning(f"Rate limit hit (attempt {attempt + 1}/{rate_limit_max_retries}): {error_type} - {e}. Waiting {delay}s before retry...")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Rate limit error after {attempt + 1} attempts. Error: {e}")
                        raise
                
                if not is_retryable or attempt == max_retries - 1:
                    # Not retryable error or last attempt - log full error details and raise
                    logger.error(f"API call failed after {attempt + 1} attempts. Error type: {error_type}, Error: {e}")
                    raise
                
                last_exception = e
                delay = initial_delay * (2 ** attempt)  # Exponential backoff: 2s, 4s, 8s, 16s, 32s
                logger.warning(f"API call failed (attempt {attempt + 1}/{max_retries}): {error_type} - {e}. Retrying in {delay}s...")
                time.sleep(delay)
        
        # Should never reach here, but just in case
        if last_exception:
            raise last_exception
    
    def summarize(self, transcription, summary_type="structured", **kwargs):
        """
        Generate meeting summary using Claude
        
        Args:
            transcription: Meeting transcript text
            summary_type: Type of summary (ignored for now, always structured)
        
        Returns:
            str: Summary text
        """
        if not self.client:
            raise Exception("Claude client not initialized. Set ANTHROPIC_API_KEY.")
        
        prompt = f"""Create a concise meeting summary from this transcript.

Meeting Transcript:
{transcription}

Provide summary in this format:

## MEETING SUMMARY

**Attendees:** [key people]

### KEY DECISIONS
- [Decision 1]
- [Decision 2]

### ACTION ITEMS
| Owner | Task | Deadline |
|-------|------|----------|
| [Name] | [Task] | [When] |

### RISKS & BLOCKERS
- [Risk/Blocker] - Impact: [High/Medium/Low]

### NEXT STEPS
- [Next step 1]
- [Next step 2]

Keep it under 400 words. Be specific with names and dates."""

        try:
            logger.info(f"Generating summary with {self.model}...")
            
            def api_call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=2000,
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
            
            response = self._call_with_retry(api_call)
            
            summary = response.content[0].text
            logger.info(f"✅ Summary generated ({len(summary)} chars)")
            
            # Small delay after successful API call to avoid rate limits
            time.sleep(2)
            
            return summary
            
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            raise
    
    def generate_client_pulse_report(self, transcription, client_name="Client", month="Current"):
        """
        Generate CLIENT PULSE REPORT format summary using Claude
        
        Args:
            transcription: Meeting transcript text
            client_name: Name of the client
            month: Month/period for the report
        
        Returns:
            str: Client pulse report text
        """
        if not self.client:
            raise Exception("Claude client not initialized. Set ANTHROPIC_API_KEY.")
        
        prompt = f"""Generate a comprehensive CLIENT PULSE REPORT for {client_name} based on this meeting transcript.

Meeting Transcript:
{transcription}

Create a detailed client pulse report in the following format:

# CLIENT PULSE REPORT: {client_name}
**Period:** {month}

## EXECUTIVE SUMMARY
[2-3 sentence overview of the meeting and overall client sentiment]

## STAKEHOLDERS & KEY DECISION MAKERS
- [Primary decision maker and their role]
- [Key stakeholders and dependencies]

## OVERALL SENTIMENT
**Sentiment:** [Positive/Neutral/Concerned/Negative]
**Reasoning:** [Why this sentiment - specific examples from transcript]
**Trend:** [Improving/Stable/Declining]

## CRITICAL ITEMS & DEADLINES
1. [Critical item 1] - Deadline: [Date]
2. [Critical item 2] - Deadline: [Date]
3. [Critical item 3] - Deadline: [Date]

## ACTION ITEMS
| Owner | Task | Deadline | Priority |
|-------|------|---------|----------|
| [Name] | [Task] | [Date] | [High/Medium/Low] |

## ROOT CAUSES & CONCERNS
1. [Root cause/concern 1] - Impact: [High/Medium/Low]
2. [Root cause/concern 2] - Impact: [High/Medium/Low]
3. [Root cause/concern 3] - Impact: [High/Medium/Low]

## RISKS & BLOCKERS
1. [Risk/Blocker 1] - Severity: [High/Medium/Low]
2. [Risk/Blocker 2] - Severity: [High/Medium/Low]
3. [Risk/Blocker 3] - Severity: [High/Medium/Low]

## KEY THEMES
1. [Theme 1] - [Brief description]
2. [Theme 2] - [Brief description]
3. [Theme 3] - [Brief description]

## CLIENT PRIORITIES
1. [Priority 1] - [Why it matters]
2. [Priority 2] - [Why it matters]
3. [Priority 3] - [Why it matters]

## STRATEGIC CONTEXT
[Strategic context and background that informs the meeting discussion]

## KEY PROJECTS MENTIONED
- [Project 1]: [Brief status]
- [Project 2]: [Brief status]

## RECOMMENDED FOLLOW-UPS
1. [Follow-up action 1]
2. [Follow-up action 2]
3. [Follow-up action 3]

Be specific, use actual names and dates from the transcript. Focus on actionable insights and client sentiment."""

        try:
            logger.info(f"Generating client pulse report for {client_name} with {self.model}...")
            
            def api_call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=4000,
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
            
            response = self._call_with_retry(api_call)
            
            report = response.content[0].text
            logger.info(f"✅ Client pulse report generated ({len(report)} chars)")
            
            # Small delay after successful API call to avoid rate limits
            time.sleep(2)
            
            return report
            
        except Exception as e:
            logger.error(f"Claude API error generating pulse report: {e}")
            raise
    
    def aggregate_pulse_reports(self, pulse_reports_list, client_name, date_range):
        """
        Aggregate multiple client pulse reports into one comprehensive report using LLM
        
        Args:
            pulse_reports_list: List of pulse report texts to aggregate
            client_name: Name of the client
            date_range: Date range string (e.g., "2025-12-20 to 2026-01-05")
        
        Returns:
            str: Aggregated pulse report text
        """
        if not self.client:
            raise Exception("Claude client not initialized. Set ANTHROPIC_API_KEY.")
        
        if not pulse_reports_list:
            raise ValueError("No pulse reports provided for aggregation")
        
        # Combine all pulse reports
        combined_reports = "\n\n---\n\n".join([
            f"## PULSE REPORT {i+1}\n{report}" 
            for i, report in enumerate(pulse_reports_list)
        ])
        
        prompt = f"""You are analyzing {len(pulse_reports_list)} client pulse reports for {client_name} covering the period {date_range}.

Analyze all the reports below and create a comprehensive AGGREGATED CLIENT PULSE REPORT that:
1. Identifies trends across all meetings
2. Highlights recurring themes and concerns
3. Shows sentiment evolution over time
4. Consolidates critical items and deadlines
5. Aggregates action items and priorities
6. Identifies patterns in risks and blockers
7. Provides strategic insights across the entire period

Individual Pulse Reports:
{combined_reports}

Create a comprehensive aggregated report in this format:

# AGGREGATED CLIENT PULSE REPORT: {client_name}
**Period:** {date_range}
**Number of Meetings Analyzed:** {len(pulse_reports_list)}

## EXECUTIVE SUMMARY
[Overall summary of the {len(pulse_reports_list)} meetings, key trends, and overall client relationship status]

## SENTIMENT TREND ANALYSIS
**Overall Sentiment:** [Aggregated sentiment across all meetings]
**Trend:** [How sentiment changed over the period - improving/stable/declining]
**Key Observations:** [Specific examples of sentiment shifts]

## RECURRING THEMES (Across All Meetings)
1. [Theme 1] - [How it appeared across meetings]
2. [Theme 2] - [How it appeared across meetings]
3. [Theme 3] - [How it appeared across meetings]

## CRITICAL ITEMS & DEADLINES (Consolidated)
[All critical items from all meetings, organized by priority and deadline]

## ACTION ITEMS (Aggregated)
[All action items from all meetings, organized by owner and priority]

## ROOT CAUSES & CONCERNS (Pattern Analysis)
[Identify patterns in concerns across meetings, which concerns are recurring vs one-time]

## RISKS & BLOCKERS (Trend Analysis)
[Consolidated view of risks, showing which are escalating, stable, or resolved]

## CLIENT PRIORITIES (Evolution)
[How client priorities evolved or remained consistent across meetings]

## STRATEGIC INSIGHTS
[High-level strategic observations based on all meetings combined]

## KEY PROJECTS STATUS
[Status of all projects mentioned across meetings]

## RECOMMENDATIONS
[Actionable recommendations based on the aggregated analysis]

Focus on identifying patterns, trends, and insights that emerge when looking at all meetings together, not just individual meeting details."""

        try:
            logger.info(f"Aggregating {len(pulse_reports_list)} pulse reports for {client_name}...")
            
            def api_call():
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=6000,
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
            
            response = self._call_with_retry(api_call)
            
            aggregated_report = response.content[0].text
            logger.info(f"✅ Aggregated pulse report generated ({len(aggregated_report)} chars)")
            
            # Small delay after successful API call to avoid rate limits
            time.sleep(2)
            
            return aggregated_report
            
        except Exception as e:
            logger.error(f"Claude API error aggregating pulse reports: {e}")
            raise

