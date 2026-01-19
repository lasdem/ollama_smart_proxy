#!/usr/bin/env python
"""Test script to verify logging fixes"""
import os
import sys
import logging

# Test 1: JSON format
print("=" * 60)
print("Test 1: JSON format logging")
print("=" * 60)
os.environ['LOG_FORMAT'] = 'json'
from src.log_formatter import setup_logging

logger = setup_logging('INFO')
logger.info('Test message from app')
logging.getLogger('uvicorn').info('Test message from uvicorn')
logging.getLogger('litellm').info('Test message from litellm')

# Test 2: Human format
print("\n" + "=" * 60)
print("Test 2: Human format logging")
print("=" * 60)
os.environ['LOG_FORMAT'] = 'human'
# Need to reload to pick up new env var
import importlib
import src.log_formatter
importlib.reload(src.log_formatter)
from src.log_formatter import setup_logging as setup_logging2

logger2 = setup_logging2('INFO')
logger2.info('Test message from app')
logging.getLogger('uvicorn').info('Test message from uvicorn')
logging.getLogger('litellm').info('Test message from litellm')

# Test 3: Exception handling
print("\n" + "=" * 60)
print("Test 3: Exception handling")
print("=" * 60)
os.environ['LOG_FORMAT'] = 'json'
importlib.reload(src.log_formatter)
from src.log_formatter import setup_logging as setup_logging3

logger3 = setup_logging3('INFO')
try:
    raise ValueError("Test exception")
except Exception:
    logger3.exception("Caught exception")

print("\n" + "=" * 60)
print("All tests completed successfully!")
print("=" * 60)
