# run.py
"""
Development server entry point.
"""
import uvicorn
from config.settings import settings

if __name__ == "__main__":
    uvicorn.run(
        "src.rtfm_agent.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.APP_ENV == "development",
        log_level=settings.LOG_LEVEL.lower(),
    )