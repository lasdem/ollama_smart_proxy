"""
Structured logging formatter with JSON and human-readable modes.
Supports Grafana/Loki integration with configurable output formats.

Date: 2025-12-19
"""
import os
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict


# Environment variables
LOG_FORMAT = os.getenv("LOG_FORMAT", "json").lower()  # json|human (default: json)


# Emoji mapping for human-readable mode
EVENT_EMOJIS = {
    "request_queued": "📨",
    "request_processing": "⚡",
    "request_completed": "✅",
    "request_failed": "❌",
    "vram_loaded": "🔍",
    "vram_poll": "🔍",
    "proxy_startup": "🚀",
    "proxy_shutdown": "👋",
}


class StructuredFormatter(logging.Formatter):
    """Custom formatter supporting JSON and human-readable modes."""
    
    def __init__(self, mode: str = "json"):
        super().__init__()
        self.mode = mode
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record based on mode."""
        if self.mode == "json":
            return self._format_json(record)
        else:
            return self._format_human(record)
    
    def _format_json(self, record: logging.LogRecord) -> str:
        """Format as single-line JSON for Loki/Grafana."""
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
            "level": record.levelname,
        }
        
        # Add message if present
        if record.getMessage():
            log_data["message"] = record.getMessage()
        
        # Add all extra fields from logger.info("event", extra={...})
        if hasattr(record, "event"):
            log_data["event"] = record.event
        
        # Add all custom fields
        for key, value in record.__dict__.items():
            if key not in ["name", "msg", "args", "created", "filename", "funcName",
                          "levelname", "levelno", "lineno", "module", "msecs",
                          "message", "pathname", "process", "processName",
                          "relativeCreated", "thread", "threadName", "exc_info",
                          "exc_text", "stack_info", "event"]:
                log_data[key] = value
        
        return json.dumps(log_data)
    
    def _format_human(self, record: logging.LogRecord) -> str:
        """Format as human-readable with emojis."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger_name = record.name
        
        # Get emoji if event type is present
        emoji = ""
        if hasattr(record, "event"):
            emoji = EVENT_EMOJIS.get(record.event, "") + " "
        
        # Build message from extra fields
        message_parts = []
        
        # Start with the base message if present
        if record.getMessage():
            message_parts.append(record.getMessage())
        
        # Add extra fields as key=value pairs
        for key, value in record.__dict__.items():
            if key not in ["name", "msg", "args", "created", "filename", "funcName",
                          "levelname", "levelno", "lineno", "module", "msecs",
                          "message", "pathname", "process", "processName",
                          "relativeCreated", "thread", "threadName", "exc_info",
                          "exc_text", "stack_info", "event"]:
                if isinstance(value, float):
                    message_parts.append(f"{key}={value:.2f}")
                else:
                    message_parts.append(f"{key}={value}")
        
        message = " ".join(message_parts)
        
        return f"[{logger_name}] {timestamp} | {emoji}{message}"


class UvicornAccessFormatter(logging.Formatter):
    """Custom formatter for uvicorn access logs."""
    
    def __init__(self, mode: str = "json"):
        super().__init__()
        self.mode = mode
    
    def format(self, record: logging.LogRecord) -> str:
        """Format uvicorn log."""
        if self.mode == "json":
            return self._format_json(record)
        else:
            return self._format_human(record)
    
    def _format_json(self, record: logging.LogRecord) -> str:
        """Format as JSON."""
        # Parse uvicorn message: "client - method path status"
        # Example: "127.0.0.1:39886 - "GET /queue HTTP/1.1" 200"
        message = record.getMessage()
        
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": "uvicorn",
            "level": record.levelname,
        }
        
        # Try to parse structured fields
        # Pattern: IP:PORT - "METHOD PATH PROTOCOL" STATUS
        import re
        match = re.match(r'([\d.]+:\d+) - "(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) ([^ ]+) [^"]+" (\d+)', message)
        
        if match:
            log_data["client"] = match.group(1)
            log_data["method"] = match.group(2)
            log_data["path"] = match.group(3)
            log_data["status"] = int(match.group(4))
        else:
            # Fallback to raw message
            log_data["message"] = message
        
        return json.dumps(log_data)
    
    def _format_human(self, record: logging.LogRecord) -> str:
        """Format as human-readable."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = record.getMessage()
        
        # Parse and reformat
        import re
        match = re.match(r'([\d.]+:\d+) - "(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) ([^ ]+) [^"]+" (\d+)', message)
        
        if match:
            client = match.group(1)
            method = match.group(2)
            path = match.group(3)
            status = match.group(4)
            return f"[uvicorn] {timestamp} | {client} | {method} {path} | {status}"
        
        # Fallback
        return f"[uvicorn] {timestamp} | {message}"


def setup_logging(level: str = "INFO"):
    """
    Setup logging with structured formatters.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Create formatters
    app_formatter = StructuredFormatter(mode=LOG_FORMAT)
    uvicorn_formatter = UvicornAccessFormatter(mode=LOG_FORMAT)
    
    # Setup application logger
    app_logger = logging.getLogger("proxy")
    app_logger.setLevel(log_level)
    app_logger.propagate = False
    
    app_handler = logging.StreamHandler()
    app_handler.setFormatter(app_formatter)
    app_logger.addHandler(app_handler)
    
    # Setup uvicorn access logger
    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_logger.setLevel(log_level)
    uvicorn_logger.propagate = False
    
    uvicorn_handler = logging.StreamHandler()
    uvicorn_handler.setFormatter(uvicorn_formatter)
    uvicorn_logger.addHandler(uvicorn_handler)
    
    # Setup uvicorn error logger (for startup messages and errors)
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_error_logger.setLevel(log_level)
    uvicorn_error_logger.propagate = False
    
    uvicorn_error_handler = logging.StreamHandler()
    uvicorn_error_handler.setFormatter(uvicorn_formatter)
    uvicorn_error_logger.addHandler(uvicorn_error_handler)
    
    # Setup uvicorn main logger (catches startup messages)
    uvicorn_main_logger = logging.getLogger("uvicorn")
    uvicorn_main_logger.setLevel(log_level)
    uvicorn_main_logger.propagate = False
    
    uvicorn_main_handler = logging.StreamHandler()
    uvicorn_main_handler.setFormatter(uvicorn_formatter)
    uvicorn_main_logger.addHandler(uvicorn_main_handler)
    
    return app_logger
