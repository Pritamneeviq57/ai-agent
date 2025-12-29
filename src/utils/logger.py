import logging
import os
from datetime import datetime

def setup_logger(name):
    """Setup logger with file and console handlers"""
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Try to create logs directory and file handler (may fail on Railway)
    try:
        if not os.path.exists("logs"):
            os.makedirs("logs")
        
        log_filename = f"logs/app_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_filename)
        file_handler.setLevel(logging.DEBUG)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # If file logging fails (e.g., on Railway), just use console
        import sys
        print(f"Warning: Could not setup file logging: {e}", file=sys.stderr)
    
    # Console handler (always available)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger