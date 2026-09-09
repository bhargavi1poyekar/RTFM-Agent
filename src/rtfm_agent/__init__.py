# src/rtfm_agent/__init__.py
"""
RTFM Agent - AI-powered AWS documentation assistant.
"""
__version__ = "1.0.0"
__author__ = "Bhargavi Poyekar"

from config.settings import settings
from config.logging import logger, setup_logging

# Initialize logging when package is imported
setup_logging()

__all__ = [
    "settings",
    "logger",
    "setup_logging",
]