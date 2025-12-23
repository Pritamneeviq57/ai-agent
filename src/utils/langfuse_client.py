"""
Langfuse client wrapper for LLM observability
Provides tracing capabilities for Ollama LLM calls
"""
import os
from typing import Optional, Dict, Any
from config.settings import Settings
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Lazy import - only import if Langfuse is enabled
_langfuse = None
_langfuse_enabled = False


def get_langfuse_client():
    """
    Get or create Langfuse client instance (singleton pattern)
    
    Returns:
        Langfuse client instance or None if disabled
    """
    global _langfuse, _langfuse_enabled
    
    if not Settings.LANGFUSE_ENABLED:
        return None
    
    if _langfuse is not None:
        return _langfuse
    
    try:
        from langfuse import Langfuse
        
        # Check if API keys are configured
        if not Settings.LANGFUSE_PUBLIC_KEY or not Settings.LANGFUSE_SECRET_KEY:
            logger.warning(
                "⚠️  Langfuse is enabled but API keys are not configured. "
                "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables. "
                "Disabling Langfuse tracing."
            )
            _langfuse_enabled = False
            return None
        
        _langfuse = Langfuse(
            public_key=Settings.LANGFUSE_PUBLIC_KEY,
            secret_key=Settings.LANGFUSE_SECRET_KEY,
            host=Settings.LANGFUSE_HOST
        )
        _langfuse_enabled = True
        logger.info(f"✅ Langfuse client initialized (host: {Settings.LANGFUSE_HOST})")
        return _langfuse
        
    except ImportError:
        logger.warning(
            "⚠️  Langfuse package not installed. Install with: pip install langfuse. "
            "Disabling Langfuse tracing."
        )
        _langfuse_enabled = False
        return None
    except Exception as e:
        logger.error(f"❌ Failed to initialize Langfuse client: {e}")
        _langfuse_enabled = False
        return None


def trace_ollama_generation(
    prompt: str,
    model: str,
    temperature: float,
    response: str,
    metadata: Optional[Dict[str, Any]] = None,
    trace_name: Optional[str] = None,
    user_id: Optional[str] = None
):
    """
    Trace an Ollama LLM generation call to Langfuse
    
    Args:
        prompt: The input prompt sent to the model
        model: Model name (e.g., "llama2:13b")
        temperature: Temperature parameter used
        response: The model's response
        metadata: Optional metadata to attach (e.g., transcript length, chunk info)
        trace_name: Optional custom trace name (default: "ollama_summarization")
        user_id: Optional user ID for tracking
    """
    langfuse = get_langfuse_client()
    if not langfuse:
        return
    
    try:
        trace_name = trace_name or "ollama_summarization"
        
        # Create a trace context with trace ID
        from langfuse.types import TraceContext
        trace_context = TraceContext(
            trace_id=langfuse.create_trace_id(),
            name=trace_name
        )
        
        # Start a generation observation with input and output
        generation = langfuse.start_observation(
            trace_context=trace_context,
            name=f"{model}_generation",
            as_type="generation",
            model=model,
            model_parameters={
                "temperature": temperature,
                "stream": False
            },
            input=prompt,
            output=response,
            metadata={
                "model": model,
                "temperature": temperature,
                "user_id": user_id,
                **(metadata or {})
            }
        )
        
        # End the observation
        generation.end()
        
        # Flush to ensure data is sent
        langfuse.flush()
        
        logger.debug(f"📊 Langfuse trace created: {trace_name}")
        
    except Exception as e:
        logger.error(f"❌ Failed to trace to Langfuse: {e}")


def trace_summarization(
    transcription: str,
    summary_type: str,
    model: str,
    temperature: float,
    summary: str,
    user_id: Optional[str] = None,
    meeting_id: Optional[str] = None
):
    """
    Trace a complete summarization operation to Langfuse
    
    Args:
        transcription: The input transcription text
        summary_type: Type of summary (e.g., "concise", "ultra_concise", "detailed")
        model: Model name used
        temperature: Temperature parameter
        summary: The generated summary
        user_id: Optional user ID
        meeting_id: Optional meeting ID for tracking
    """
    metadata = {
        "summary_type": summary_type,
        "transcript_length": len(transcription),
        "summary_length": len(summary),
        "meeting_id": meeting_id
    }
    
    trace_ollama_generation(
        prompt=transcription[:1000] + "..." if len(transcription) > 1000 else transcription,  # Truncate for display
        model=model,
        temperature=temperature,
        response=summary,
        metadata=metadata,
        trace_name=f"summarization_{summary_type}",
        user_id=user_id
    )

