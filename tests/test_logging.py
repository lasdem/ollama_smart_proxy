#!/usr/bin/env python3
"""Test logging formatter"""
import os
import sys

# Set JSON mode for testing
os.environ['LOG_FORMAT'] = 'json'

sys.path.insert(0, '/home/peterkrammer/ws/python/litellm_smart_proxy/src')

from log_formatter import setup_logging

# Test JSON mode
print("=== Testing JSON Mode ===")
logger = setup_logging("INFO")
logger.info("Test message", extra={"event": "request_queued", "request_id": "REQ0001", "ip": "127.0.0.1", "model": "gemma3", "queue_depth": 5})
logger.info("Processing request", extra={"event": "request_processing", "request_id": "REQ0001", "ip": "127.0.0.1", "model": "gemma3", "priority": 95})
logger.info("Completed", extra={"event": "request_completed", "request_id": "REQ0001", "ip": "127.0.0.1", "model": "gemma3", "duration_seconds": 5.26})
logger.error("Error occurred", extra={"event": "request_failed", "request_id": "REQ0001", "error": "Test error"})

# Test human mode
print("\n=== Testing Human Mode ===")
os.environ['LOG_FORMAT'] = 'human'
# Need to reimport to pick up new env var
import importlib
import log_formatter
importlib.reload(log_formatter)
logger2 = log_formatter.setup_logging("INFO")
logger2.info("Test message", extra={"event": "request_queued", "request_id": "REQ0001", "ip": "127.0.0.1", "model": "gemma3", "queue_depth": 5})
logger2.info("Processing request", extra={"event": "request_processing", "request_id": "REQ0001", "ip": "127.0.0.1", "model": "gemma3", "priority": 95})
logger2.info("Completed", extra={"event": "request_completed", "request_id": "REQ0001", "ip": "127.0.0.1", "model": "gemma3", "duration_seconds": 5.26})
logger2.error("Error occurred", extra={"event": "request_failed", "request_id": "REQ0001", "error": "Test error"})
