"""
OPIK client wrapper for LLM observability
Provides tracing capabilities for Ollama LLM calls
"""
import os
from typing import Optional, Dict, Any
from contextlib import contextmanager
from config.settings import Settings
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Lazy import - only import if OPIK is enabled
_opik = None
_opik_enabled = False
_opik_initialized = False


def initialize_opik():
    """
    Initialize OPIK client (singleton pattern)
    
    Returns:
        bool: True if initialized successfully, False otherwise
    """
    global _opik, _opik_enabled, _opik_initialized
    
    if not Settings.OPIK_ENABLED:
        return False
    
    if _opik_initialized:
        return _opik_enabled
    
    try:
        import opik
        import sys
        from io import StringIO
        
        # Set environment variables to avoid interactive prompts
        os.environ['OPIK_URL'] = Settings.OPIK_HOST
        os.environ['OPIK_INSTANCE_URL'] = Settings.OPIK_HOST
        
        # Set API key if provided
        if Settings.OPIK_API_KEY:
            os.environ['OPIK_API_KEY'] = Settings.OPIK_API_KEY
            os.environ['OPIK_API_KEY'] = Settings.OPIK_API_KEY
        
        # Configure OPIK - try multiple methods to avoid interactive prompts
        try:
            if hasattr(opik, 'configure'):
                # Method 1: Try with API key and URL
                if Settings.OPIK_API_KEY:
                    try:
                        opik.configure(api_key=Settings.OPIK_API_KEY, url=Settings.OPIK_HOST)
                    except (TypeError, ValueError, AttributeError):
                        try:
                            opik.configure(api_key=Settings.OPIK_API_KEY)
                        except (TypeError, ValueError, AttributeError):
                            # Fallback to use_local
                            opik.configure(use_local=True)
                else:
                    # Method 2: Try use_local (should work without API key for local)
                    try:
                        opik.configure(use_local=True)
                    except (TypeError, ValueError, AttributeError):
                        try:
                            opik.configure(url=Settings.OPIK_HOST)
                        except (TypeError, ValueError, AttributeError):
                            # If configure fails, environment variables should work
                            pass
        except Exception as config_error:
            logger.debug(f"OPIK configure attempt: {config_error}")
        
        _opik = opik
        _opik_enabled = True
        _opik_initialized = True
        
        if Settings.OPIK_API_KEY:
            logger.info(f"✅ OPIK client initialized (host: {Settings.OPIK_HOST}, API key configured)")
        else:
            logger.info(f"✅ OPIK client initialized (host: {Settings.OPIK_HOST})")
            logger.warning("⚠️  OPIK API key not set. You may be prompted for it. Get it from: http://localhost:5173/api/my/settings/")
        
        return True
        
    except ImportError:
        logger.warning(
            "⚠️  OPIK package not installed. Install with: pip install opik. "
            "Disabling OPIK tracing."
        )
        _opik_enabled = False
        _opik_initialized = True
        return False
    except KeyboardInterrupt:
        # User interrupted during interactive prompt
        logger.warning("⚠️  OPIK configuration interrupted. Disabling OPIK tracing.")
        _opik_enabled = False
        _opik_initialized = True
        return False
    except Exception as e:
        logger.error(f"❌ Failed to initialize OPIK client: {e}")
        logger.info("💡 Tip: Make sure OPIK is running at http://localhost:5173")
        logger.info("💡 You can disable OPIK by setting OPIK_ENABLED=false in your .env file")
        _opik_enabled = False
        _opik_initialized = True
        return False


def is_opik_enabled():
    """Check if OPIK is enabled and available"""
    if not Settings.OPIK_ENABLED:
        return False
    
    # Only initialize once to avoid repeated prompts
    if not _opik_initialized:
        initialize_opik()
    
    return _opik_enabled


@contextmanager
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
    Trace an Ollama LLM generation call to OPIK using context manager
    
    Args:
        prompt: The input prompt sent to the model
        model: Model name (e.g., "llama2:13b")
        temperature: Temperature parameter used
        response: The model's response
        metadata: Optional metadata to attach (e.g., transcript length, chunk info)
        trace_name: Optional custom trace name (default: "ollama_summarization")
        user_id: Optional user ID for tracking
    
    Usage:
        with trace_ollama_generation(prompt, model, temp, response, metadata):
            # Your code here
            pass
    """
    if not is_opik_enabled():
        yield
        return
    
    try:
        trace_name = trace_name or "ollama_summarization"
        
        # Start a trace
        with _opik.start_as_current_trace(name=trace_name):
            # Create a span for the generation
            with _opik.start_as_current_span(name=f"{model}_generation") as span:
                # Set span metadata (Opik 1.9+ uses metadata dict)
                if span.metadata is None:
                    span.metadata = {}
                
                span.metadata["model"] = model
                span.metadata["temperature"] = str(temperature)
                span.metadata["prompt_length"] = str(len(prompt))
                span.metadata["response_length"] = str(len(response))
                
                if user_id:
                    span.metadata["user_id"] = user_id
                
                # Add metadata as attributes
                if metadata:
                    for key, value in metadata.items():
                        span.metadata[str(key)] = str(value)
                
                # Set input and output (Opik expects dict format)
                span.input = {"text": prompt[:1000] if len(prompt) > 1000 else prompt}
                span.output = {"text": response[:1000] if len(response) > 1000 else response}
                
                yield span
                
    except Exception as e:
        logger.error(f"❌ Failed to trace to OPIK: {e}")
        yield


def trace_ollama_generation_sync(
    prompt: str,
    model: str,
    temperature: float,
    response: str,
    metadata: Optional[Dict[str, Any]] = None,
    trace_name: Optional[str] = None,
    user_id: Optional[str] = None
):
    """
    Trace an Ollama LLM generation call to OPIK (synchronous version)
    
    Args:
        prompt: The input prompt sent to the model
        model: Model name (e.g., "llama2:13b")
        temperature: Temperature parameter used
        response: The model's response
        metadata: Optional metadata to attach (e.g., transcript length, chunk info)
        trace_name: Optional custom trace name (default: "ollama_summarization")
        user_id: Optional user ID for tracking
    """
    if not is_opik_enabled():
        return
    
    try:
        trace_name = trace_name or "ollama_summarization"
        
        # Start a trace - OPIK API may vary, so we handle different patterns
        try:
            # Try the context manager pattern
            if hasattr(_opik, 'start_as_current_trace'):
                with _opik.start_as_current_trace(name=trace_name):
                    # Create a span for the generation
                    if hasattr(_opik, 'start_as_current_span'):
                        with _opik.start_as_current_span(name=f"{model}_generation") as span:
                            # Set metadata (Opik 1.9+ uses metadata dict)
                            if span.metadata is None:
                                span.metadata = {}
                            
                            span.metadata["model"] = model
                            span.metadata["temperature"] = str(temperature)
                            span.metadata["prompt_length"] = str(len(prompt))
                            span.metadata["response_length"] = str(len(response))
                            
                            if user_id:
                                span.metadata["user_id"] = user_id
                            
                            # Add metadata as attributes
                            if metadata:
                                for key, value in metadata.items():
                                    try:
                                        span.metadata[str(key)] = str(value)
                                    except (TypeError, ValueError):
                                        pass
                            
                            # Set input and output (Opik expects dict format)
                            try:
                                span.input = {"text": prompt[:1000] if len(prompt) > 1000 else prompt}
                                span.output = {"text": response[:1000] if len(response) > 1000 else response}
                            except (TypeError, ValueError):
                                pass
                    else:
                        # No span support, just use trace
                        pass
            else:
                # Fallback: use @track decorator if available
                if hasattr(_opik, 'track'):
                    @_opik.track(name=trace_name)
                    def _trace_wrapper():
                        pass
                    _trace_wrapper()
                else:
                    logger.debug(f"📊 OPIK trace attempted: {trace_name} (API methods not available)")
        except Exception as inner_e:
            # Handle errors in the inner try block
            logger.debug(f"📊 OPIK trace attempted with fallback: {trace_name} (Error: {inner_e})")
        
        logger.debug(f"📊 OPIK trace created: {trace_name}")
        
    except Exception as e:
        logger.error(f"❌ Failed to trace to OPIK: {e}")


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
    Trace a complete summarization operation to OPIK
    
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
    
    trace_ollama_generation_sync(
        prompt=transcription[:1000] + "..." if len(transcription) > 1000 else transcription,  # Truncate for display
        model=model,
        temperature=temperature,
        response=summary,
        metadata=metadata,
        trace_name=f"summarization_{summary_type}",
        user_id=user_id
    )

