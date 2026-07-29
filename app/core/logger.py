import logging
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict

class JSONFormatter(logging.Formatter):
    """
    Formatter that outputs JSON strings after parsing the LogRecord.
    """
    def format(self, record: logging.LogRecord) -> str:
        log_record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
            
        if hasattr(record, "request_id"):
            log_record["request_id"] = record.request_id

        return json.dumps(log_record)

def setup_logger(name: str = "gallery_vault") -> logging.Logger:
    logger = logging.getLogger(name)
    
    # Prevent duplicate logs if initialized multiple times
    if logger.hasHandlers():
        return logger
        
    logger.setLevel(logging.INFO)
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    
    # Do not propagate to root logger to avoid standard unformatted output
    logger.propagate = False
    
    return logger

logger = setup_logger()
