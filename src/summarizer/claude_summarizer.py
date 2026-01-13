"""
Claude Summarizer for Cloud Deployment
Supports both direct Anthropic API and Azure AI Foundry
"""
import os
import time
import requests
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Check for API keys - Azure AI Foundry takes precedence
AZURE_AI_FOUNDRY_API_KEY = os.getenv("AZURE_AI_FOUNDRY_API_KEY", "")
AZURE_AI_FOUNDRY_ENDPOINT = os.getenv("AZURE_AI_FOUNDRY_ENDPOINT", "")
AZURE_AI_FOUNDRY_DEPLOYMENT = os.getenv("AZURE_AI_FOUNDRY_DEPLOYMENT", "claude-opus-4-5-20251101")
AZURE_AI_FOUNDRY_REGION = os.getenv("AZURE_AI_FOUNDRY_REGION", "")  # e.g., "eastus"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


class ClaudeSummarizer:
    """Simple summarizer using Anthropic Claude API or Azure AI Foundry"""
    
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
        self.use_azure = False
        self.azure_endpoint = None
        self.azure_api_key = None
        self.azure_deployment = None
        
        # Prefer Azure AI Foundry if available
        if AZURE_AI_FOUNDRY_API_KEY:
            self.use_azure = True
            self.azure_api_key = AZURE_AI_FOUNDRY_API_KEY
            self.azure_deployment = AZURE_AI_FOUNDRY_DEPLOYMENT or model
            self.azure_region = AZURE_AI_FOUNDRY_REGION
            
            # Azure AI Foundry endpoint format
            if AZURE_AI_FOUNDRY_ENDPOINT:
                endpoint = AZURE_AI_FOUNDRY_ENDPOINT.rstrip('/')
                # Check if endpoint already includes full path with /chat/completions
                # If so, keep it as-is (user provided complete URL)
                if '/chat/completions' in endpoint:
                    # Full URL provided - use as-is
                    self.azure_endpoint = endpoint
                    self.azure_endpoint_is_full_url = True
                    logger.info(f"   Using full endpoint URL as provided")
                elif '/openai/deployments/' in endpoint or '/deployments/' in endpoint or '/inference/' in endpoint or '/v1/' in endpoint:
                    # Has path but not /chat/completions - extract base URL
                    from urllib.parse import urlparse
                    parsed = urlparse(endpoint)
                    self.azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"
                    self.azure_endpoint_is_full_url = False
                    logger.info(f"   Extracted base endpoint from URL with path: {self.azure_endpoint}")
                else:
                    # Base URL only
                    self.azure_endpoint = endpoint
                    self.azure_endpoint_is_full_url = False
            else:
                # Try to construct endpoint from region if provided
                if self.azure_region:
                    # Azure AI Foundry inference endpoint format with region
                    # Format: https://<resource-name>.<region>.inference.ai.azure.com
                    logger.warning("AZURE_AI_FOUNDRY_ENDPOINT not set, but region provided. Please set full endpoint URL.")
                    self.azure_endpoint = None
                else:
                    logger.warning("AZURE_AI_FOUNDRY_ENDPOINT not set - will try to infer from standard Azure OpenAI format")
                    self.azure_endpoint = None
            
            logger.info(f"✅ Azure AI Foundry summarizer initialized with deployment: {self.azure_deployment}")
            logger.info(f"   Endpoint: {self.azure_endpoint}")
            if self.azure_region:
                logger.info(f"   Region: {self.azure_region}")
            logger.info(f"   API Key: {'*' * (len(self.azure_api_key) - 4) + self.azure_api_key[-4:] if len(self.azure_api_key) > 4 else '****'}")
        elif ANTHROPIC_API_KEY:
            try:
                from anthropic import Anthropic, RateLimitError
                self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
                logger.info(f"✅ Claude summarizer initialized with {model} (direct Anthropic API)")
            except ImportError:
                logger.error("anthropic package not installed. Run: pip install anthropic")
            except Exception as e:
                logger.error(f"Failed to initialize Claude client: {e}")
        else:
            logger.warning("Neither ANTHROPIC_API_KEY nor AZURE_AI_FOUNDRY_API_KEY is set - summarization won't work")
    
    def is_available(self):
        """Check if Claude is available"""
        return self.client is not None or self.use_azure
    
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
                
                # Check for HTTP errors from Azure API
                http_status = None
                if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                    http_status = e.response.status_code
                elif isinstance(e, requests.exceptions.HTTPError) and hasattr(e, 'response'):
                    http_status = e.response.status_code
                
                # Check for rate limit errors (429)
                # Try to check exception type, but fall back to string matching
                try:
                    from anthropic import RateLimitError
                    is_rate_limit_type = isinstance(e, RateLimitError)
                except (ImportError, NameError):
                    is_rate_limit_type = False
                
                is_rate_limit = (
                    is_rate_limit_type or
                    http_status == 429 or
                    "429" in error_str or
                    "rate_limit" in error_str or
                    "rate limit" in error_str or
                    "exceed the rate limit" in error_str or
                    error_type == "RateLimitError"
                )
                
                # Check for authentication errors (401) - don't retry these
                is_auth_error = (
                    http_status == 401 or
                    "401" in error_str or
                    "authentication" in error_str or
                    "unauthorized" in error_str or
                    "invalid" in error_str and ("api" in error_str or "key" in error_str)
                )
                
                # Check if it's a connection/network error that we should retry
                is_retryable = (
                    "connection" in error_str or
                    "timeout" in error_str or
                    "network" in error_str or
                    "temporary" in error_str or
                    "socket" in error_str or
                    "connect" in error_str or
                    error_type in ["ConnectionError", "TimeoutError", "APIConnectionError", "APITimeoutError", "RequestException"]
                )
                
                # Don't retry authentication errors - they won't succeed
                if is_auth_error:
                    logger.error(f"Authentication error (not retrying): {error_type} - {e}")
                    raise
                
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
    
    def _call_azure_api(self, prompt, max_tokens=2000):
        """
        Call Azure AI Foundry API for Claude models
        
        Args:
            prompt: The prompt text
            max_tokens: Maximum tokens to generate
        
        Returns:
            str: Generated text
        """
        if not self.azure_endpoint:
            raise Exception("AZURE_AI_FOUNDRY_ENDPOINT not set. Please set the endpoint URL.")
        
        from urllib.parse import urlparse, parse_qs
        
        # Check if endpoint is already a full URL with /chat/completions
        if hasattr(self, 'azure_endpoint_is_full_url') and self.azure_endpoint_is_full_url:
            # Endpoint already includes full path - use as-is
            endpoint_paths = [self.azure_endpoint]
            # Extract API version from query string if present
            parsed = urlparse(self.azure_endpoint)
            query_params = parse_qs(parsed.query)
            api_versions = []
            if 'api-version' in query_params:
                api_versions = [query_params['api-version'][0]]  # Use the provided version
            else:
                api_versions = ["2024-02-15-preview", "2024-06-01", "2023-12-01-preview", "2024-05-01-preview", "2024-08-01-preview", "2025-01-01-preview"]
        else:
            # Endpoint is base URL only - construct paths
            endpoint_base = f"{parsed.scheme}://{parsed.netloc}"
            
            # If region is provided and endpoint doesn't include it, try region-specific formats
            region_suffix = f".{self.azure_region}" if self.azure_region and self.azure_region not in endpoint_base else ""
            
            # Try different endpoint path formats
            endpoint_paths = []
            
            # Standard Azure OpenAI format
            endpoint_paths.append(f"{endpoint_base}/openai/deployments/{self.azure_deployment}/chat/completions")
            
            # Azure AI Foundry inference endpoint formats
            if self.azure_region:
                # Region-specific inference endpoint
                if ".inference.ai.azure.com" in endpoint_base or ".openai.azure.com" in endpoint_base:
                    # Endpoint already has full format
                    endpoint_paths.append(f"{endpoint_base}/inference/v1/chat/completions")
                else:
                    # Try constructing region-specific endpoint
                    # Format: https://<resource>.<region>.inference.ai.azure.com/inference/v1/chat/completions
                    resource_name = endpoint_base.replace("https://", "").replace("http://", "").split(".")[0]
                    region_endpoint = f"https://{resource_name}{region_suffix}.inference.ai.azure.com"
                    endpoint_paths.append(f"{region_endpoint}/inference/v1/chat/completions")
            
            # Other formats
            endpoint_paths.extend([
                f"{endpoint_base}/inference/v1/chat/completions",
                f"{endpoint_base}/deployments/{self.azure_deployment}/chat/completions",
                f"{endpoint_base}/v1/chat/completions"
            ])
            
            # Try different API version formats
            api_versions = ["2024-02-15-preview", "2024-06-01", "2023-12-01-preview", "2024-05-01-preview", "2024-08-01-preview", "2025-01-01-preview"]
        
        headers = {
            "Content-Type": "application/json",
            "api-key": self.azure_api_key
        }
        
        last_error = None
        last_url_tried = None
        
        for endpoint_path in endpoint_paths:
            for api_version in api_versions:
                # Check if URL already has api-version in query string
                parsed_path = urlparse(endpoint_path)
                has_api_version = 'api-version' in parse_qs(parsed_path.query)
                
                # For inference endpoint, model goes in payload, not URL
                if "/inference/v1/" in endpoint_path or "/v1/" in endpoint_path:
                    payload = {
                        "model": self.azure_deployment,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.7
                    }
                    # If URL already has api-version, use as-is, otherwise add it
                    if has_api_version:
                        url = endpoint_path
                    else:
                        url = f"{endpoint_path}?api-version={api_version}"
                else:
                    payload = {
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.7
                    }
                    # If URL already has api-version, use as-is, otherwise add it
                    if has_api_version:
                        url = endpoint_path
                    else:
                        url = f"{endpoint_path}?api-version={api_version}"
                
                try:
                    last_url_tried = url
                    logger.debug(f"Trying Azure AI Foundry endpoint: {url}")
                    response = requests.post(url, headers=headers, json=payload, timeout=120)
                    response.raise_for_status()
                    result = response.json()
                    
                    # Extract text from response
                    if "choices" in result and len(result["choices"]) > 0:
                        logger.info(f"✅ Successfully called Azure AI Foundry endpoint: {url}")
                        return result["choices"][0]["message"]["content"]
                    else:
                        raise Exception(f"Unexpected response format: {result}")
                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code
                    error_detail = None
                    try:
                        error_json = e.response.json()
                        error_detail = error_json
                        if "error" in error_json:
                            error_detail = error_json["error"]
                    except:
                        error_detail = e.response.text[:200] if e.response.text else str(e)
                    
                    if status_code == 404:
                        # Try next endpoint format or API version
                        logger.debug(f"404 error for {url}: {error_detail}")
                        last_error = e
                        continue
                    elif status_code == 401:
                        # Authentication error - don't try other versions
                        logger.error(f"401 Authentication error for {url}: {error_detail}")
                        raise Exception(f"Azure AI Foundry authentication error (401): {error_detail}")
                    else:
                        # Other HTTP errors - log and try next
                        logger.debug(f"HTTP {status_code} error for {url}: {error_detail}")
                        last_error = e
                        continue
                except requests.exceptions.RequestException as e:
                    # Network/connection errors - try next
                    logger.debug(f"Request exception for {url}: {e}")
                    last_error = e
                    continue
                except Exception as e:
                    logger.debug(f"Exception for {url}: {e}")
                    last_error = e
                    continue
        
        # If we get here, all attempts failed
        error_msg = "All endpoint formats failed"
        if last_error:
            if hasattr(last_error, 'response') and hasattr(last_error.response, 'json'):
                try:
                    error_json = last_error.response.json()
                    if "error" in error_json:
                        error_msg = str(error_json["error"])
                    else:
                        error_msg = str(error_json)
                except:
                    if hasattr(last_error.response, 'text'):
                        error_msg = last_error.response.text[:200] if last_error.response.text else str(last_error)
                    else:
                        error_msg = str(last_error)
            else:
                error_msg = str(last_error)
        
        logger.error(f"Azure AI Foundry API call failed. Last URL tried: {last_url_tried}. Error: {error_msg}")
        raise Exception(f"Azure AI Foundry API call failed: {error_msg}")
    
    def summarize(self, transcription, summary_type="structured", **kwargs):
        """
        Generate meeting summary using Claude
        
        Args:
            transcription: Meeting transcript text
            summary_type: Type of summary (ignored for now, always structured)
        
        Returns:
            str: Summary text
        """
        if not self.is_available():
            if self.use_azure:
                raise Exception("Azure AI Foundry client not initialized. Check AZURE_AI_FOUNDRY_API_KEY and AZURE_AI_FOUNDRY_ENDPOINT.")
            else:
                raise Exception("Claude client not initialized. Set ANTHROPIC_API_KEY or AZURE_AI_FOUNDRY_API_KEY.")
        
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
            
            if self.use_azure:
                def api_call():
                    return self._call_azure_api(prompt, max_tokens=2000)
                
                summary = self._call_with_retry(api_call)
            else:
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
        if not self.is_available():
            if self.use_azure:
                raise Exception("Azure AI Foundry client not initialized. Check AZURE_AI_FOUNDRY_API_KEY and AZURE_AI_FOUNDRY_ENDPOINT.")
            else:
                raise Exception("Claude client not initialized. Set ANTHROPIC_API_KEY or AZURE_AI_FOUNDRY_API_KEY.")
        
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
            
            if self.use_azure:
                def api_call():
                    return self._call_azure_api(prompt, max_tokens=4000)
                
                report = self._call_with_retry(api_call)
            else:
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
        if not self.is_available():
            if self.use_azure:
                raise Exception("Azure AI Foundry client not initialized. Check AZURE_AI_FOUNDRY_API_KEY and AZURE_AI_FOUNDRY_ENDPOINT.")
            else:
                raise Exception("Claude client not initialized. Set ANTHROPIC_API_KEY or AZURE_AI_FOUNDRY_API_KEY.")
        
        if not pulse_reports_list:
            raise ValueError("No pulse reports provided for aggregation")
        
        # Combine all pulse reports
        combined_reports = "\n\n---\n\n".join([
            f"## PULSE REPORT {i+1}\n{report}" 
            for i, report in enumerate(pulse_reports_list)
        ])
        
        prompt = f"""You are analyzing {len(pulse_reports_list)} client pulse reports for {client_name} covering the period {date_range}.

Create a CONCISE, QUICK-READ aggregated report (target: 5-minute read) that highlights the most important information.

IMPORTANT: Keep it SHORT and SCANNABLE. Focus on:
- Key trends and patterns (not every detail)
- Critical items and deadlines only
- Top 3-5 action items (not exhaustive lists)
- Major risks/blockers (not minor issues)
- Strategic insights (high-level only)

Individual Pulse Reports:
{combined_reports}

Create a concise aggregated report in this format (keep each section brief):

# AGGREGATED CLIENT PULSE REPORT: {client_name}
**Period:** {date_range}
**Number of Meetings Analyzed:** {len(pulse_reports_list)}

## EXECUTIVE SUMMARY
[2-3 sentences: Overall relationship status, key trends, and main takeaway]

## SENTIMENT TREND
**Overall:** [Positive/Neutral/Negative] | **Trend:** [Improving/Stable/Declining]
[1-2 key observations only]

## TOP THEMES
1. [Theme 1] - [Brief description]
2. [Theme 2] - [Brief description]
3. [Theme 3] - [Brief description]
[Limit to 3-5 most important themes]

## CRITICAL ITEMS
[Only HIGH priority items with upcoming deadlines - use bullet points, max 5-7 items]

## KEY ACTION ITEMS
[Top 3-5 most important action items by owner - use bullet points]

## MAJOR RISKS
[Only significant risks that need attention - max 3-5 items]

## STRATEGIC INSIGHTS
[2-3 high-level insights only - what matters most for decision-making]

## PROJECT STATUS
[Brief status for each active project - one line each]

## RECOMMENDATIONS
[2-3 actionable recommendations only]

Keep the entire report under 800 words. Use bullet points and short sentences. Focus on what the reader needs to know, not every detail."""

        try:
            logger.info(f"Aggregating {len(pulse_reports_list)} pulse reports for {client_name}...")
            
            if self.use_azure:
                def api_call():
                    return self._call_azure_api(prompt, max_tokens=6000)
                
                aggregated_report = self._call_with_retry(api_call)
            else:
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

