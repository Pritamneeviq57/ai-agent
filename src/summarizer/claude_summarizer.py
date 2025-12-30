"""
Claude Summarizer for Cloud Deployment
Minimal implementation using Anthropic Claude API
"""
import os
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
                from anthropic import Anthropic
                self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
                logger.info(f"✅ Claude summarizer initialized with {model}")
            except ImportError:
                logger.error("anthropic package not installed. Run: pip install anthropic")
            except Exception as e:
                logger.error(f"Failed to initialize Claude client: {e}")
    
    def is_available(self):
        """Check if Claude is available"""
        return self.client is not None
    
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
            
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            summary = response.content[0].text
            logger.info(f"✅ Summary generated ({len(summary)} chars)")
            return summary
            
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            raise

