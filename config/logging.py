# config/logging.py
"""
Logging configuration with structured logging support.
"""
import logging
import sys
from typing import Optional
from pythonjsonlogger import jsonlogger
from config.settings import settings

class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with additional fields."""
    
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["app"] = settings.APP_NAME
        log_record["env"] = settings.APP_ENV
        if not log_record.get("timestamp"):
            log_record["timestamp"] = self.formatTime(record, self.datefmt)

def setup_logging(
    log_level: Optional[str] = None,
    log_format: Optional[str] = None
) -> None:
    """
    Configure logging for the application.
    
    Args:
        log_level: Override log level
        log_format: Override log format ('json' or 'text')
    """
    level = log_level or settings.LOG_LEVEL
    format_type = log_format or settings.LOG_FORMAT
    
    # Set up root logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    
    if format_type == "json":
        formatter = CustomJsonFormatter(
            "%(timestamp)s %(level)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z"
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Set third-party loggers to warning level
    for lib in ["urllib3", "requests", "httpx", "chromadb"]:
        logging.getLogger(lib).setLevel(logging.WARNING)
    
    logger.info(f"Logging configured with level={level}, format={format_type}")

# Create a logger instance
logger = logging.getLogger("rtfm_agent")