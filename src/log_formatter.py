"""
Structured logging formatter with JSON and human-readable modes.
Supports Grafana/Loki integration with configurable output formats.

Date: 2026-01-19 (Fixed traceback support)
"""
import os
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Dict


# Environment variables
# We read this inside the formatter to support runtime reloading in tests
def get_log_mode():
    return os.getenv("LOG_FORMAT", "json").lower()


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
    
    def __init__(self, mode: str = None):
        super().__init__()
        # If mode not provided, defer to env var (useful for testing reload)
        self._mode = mode
    
    @property
    def mode(self):
        return self._mode or get_log_mode()
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record based on mode."""
        if self.mode == "json":
            return self._format_json(record)
        else:
            return self._format_human(record)
    
    def _format_json(self, record: logging.LogRecord) -> str:
        """Format as single-line JSON for Loki/Grafana."""
        
        # 1. Base Fields
        log_data: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
            "level": record.levelname,
        }

        # 2. Message
        if record.getMessage():
            log_data["message"] = record.getMessage()

        # 3. Extra Fields (logger.info(..., extra={...}))
        if hasattr(record, "event"):
            log_data["event"] = record.event

        # 4. Exception / Traceback Support (CRITICAL FIX)
        if record.exc_info:
            # Format the exception traceback as a string
            log_data["exc_info"] = "".join(traceback.format_exception(*record.exc_info))
        elif record.stack_info:
            log_data["stack_info"] = self.formatStack(record.stack_info)

        # 5. Add all other custom attributes from extra={}
        # We filter out standard LogRecord attributes
        standard_attrs = {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs",
            "message", "pathname", "process", "processName",
            "relativeCreated", "thread", "threadName", "exc_info",
            "exc_text", "stack_info", "event"
        }
        
        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                log_data[key] = value

        # 6. Serialize
        try:
            return json.dumps(log_data, default=str)
        except Exception:
            # Fallback if something is not serializable
            log_data["message"] = f"JSON Serialization Error: {record.getMessage()}"
            return json.dumps({k: str(v) for k, v in log_data.items()})
    
    def _format_human(self, record: logging.LogRecord) -> str:
        """Format as human-readable with emojis."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger_name = record.name
        
        # Get emoji if event type is present
        emoji = ""
        if hasattr(record, "event"):
            emoji = EVENT_EMOJIS.get(record.event, "") + " "
        
        # Build message parts
        message_parts = []
        if record.getMessage():
            message_parts.append(record.getMessage())
            
        # Add exceptions if present (CRITICAL FIX)
        if record.exc_info:
             # Just append the exception type and message for brevity in human logs,
             # or full trace if preferred.
             exc_class, exc_msg, _ = record.exc_info
             message_parts.append(f"\nExample: {exc_class.__name__}: {exc_msg}")
        
        # Add extra fields
        standard_attrs = {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs",
            "message", "pathname", "process", "processName",
            "relativeCreated", "thread", "threadName", "exc_info",
            "exc_text", "stack_info", "event"
        }

        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                if isinstance(value, float):
                    message_parts.append(f"{key}={value:.2f}")
                else:
                    message_parts.append(f"{key}={value}")
        
        message = " ".join(message_parts)
        return f"[{logger_name}] {timestamp} | {emoji}{message}"


class UvicornAccessFormatter(logging.Formatter):
    """Custom formatter for uvicorn access logs."""
    
    def __init__(self, mode: str = None):
        super().__init__()
        self._mode = mode
    
    @property
    def mode(self):
        return self._mode or get_log_mode()
    
    def format(self, record: logging.LogRecord) -> str:
        """Format uvicorn log."""
        if self.mode == "json":
            return self._format_json(record)
        else:
            return self._format_human(record)
    
    def _format_json(self, record: logging.LogRecord) -> str:
        """Format as JSON."""
        message = record.getMessage()
        
        # Skip logging for health and queue status endpoints
        import re
        match = re.match(r'([\d.]+:\d+) - "(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) ([^ ]+) [^"]+" (\d+)', message)
        
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": "uvicorn",
            "level": record.levelname,
        }
        
        if match:
            log_data["client"] = match.group(1)
            log_data["method"] = match.group(2)
            log_data["path"] = match.group(3)
            log_data["status"] = match.group(4)
        else:
            log_data["message"] = message
        
        return json.dumps(log_data)
    
    def _format_human(self, record: logging.LogRecord) -> str:
        """Format as human-readable."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = record.getMessage()
        
        import re
        match = re.match(r'([\d.]+:\d+) - "(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) ([^ ]+) [^"]+" (\d+)', message)
        
        if match:
            client = match.group(1)
            method = match.group(2)
            path = match.group(3)
            status = match.group(4)
            return f"[uvicorn] {timestamp} | {client} | {method} {path} | {status}"
        
        return f"[uvicorn] {timestamp} | {message}"


def setup_logging(level: str = "INFO", access_level: str = None):
    """
    Setup logging with structured formatters.
    
    Args:
        level: Log level for smart_proxy and application logs
        access_level: Log level for uvicorn access logs (defaults to level if not specified)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    access_log_level = getattr(logging, (access_level or level).upper(), log_level)
    
    # Create Root Handler
    # We attach the formatter HERE so it applies to everything bubbling up
    root_formatter = StructuredFormatter()
    root_handler = logging.StreamHandler()
    root_handler.setFormatter(root_formatter)
    
    # Configure Root Logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Nuke existing handlers to prevent duplication/legacy formats
    if root_logger.handlers:
        for h in root_logger.handlers[:]:
            root_logger.removeHandler(h)
            
    root_logger.addHandler(root_handler)
    
    # --- Configure Specific Loggers ---
    
    # Uvicorn Access (needs special parsing logic)
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.setLevel(access_log_level)
    # We clear handlers and ADD a specific one, preventing propagation to avoid double logging
    # (One structured, one raw from root)
    if uvicorn_access.handlers:
         for h in uvicorn_access.handlers[:]:
            uvicorn_access.removeHandler(h)
            
    uvicorn_handler = logging.StreamHandler()
    uvicorn_handler.setFormatter(UvicornAccessFormatter())
    uvicorn_access.addHandler(uvicorn_handler)
    uvicorn_access.propagate = False # STOP it from going to root (which uses generic formatter)

    # Other Uvicorn loggers (error, main) -> Propagate to Root (Generic JSON)
    for name in ["uvicorn", "uvicorn.error"]:
        l = logging.getLogger(name)
        l.setLevel(log_level)
        l.propagate = True
        # Ensure they don't have their own handlers
        for h in l.handlers[:]:
            l.removeHandler(h)

    # LiteLLM -> Propagate to Root
    for name in ["litellm", "litellm.proxy", "litellm.auth", "litellm.usage", "litellm.cache", "litellm.telemetry"]:
        l = logging.getLogger(name)
        l.setLevel(log_level)
        l.propagate = True
        for h in l.handlers[:]:
            l.removeHandler(h)

    # App Logger
    app_logger = logging.getLogger("smart_proxy")
    app_logger.setLevel(log_level)
    app_logger.propagate = True
    
    return app_logger