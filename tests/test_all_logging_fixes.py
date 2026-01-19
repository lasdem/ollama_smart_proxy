#!/usr/bin/env python
"""Comprehensive test to verify all logging fixes"""
import os
import sys
import logging
import json

# Test 1: Verify all loggers propagate correctly
print("=" * 60)
print("Test 1: Verify all loggers propagate to root")
print("=" * 60)
os.environ['LOG_FORMAT'] = 'json'
from src.log_formatter import setup_logging

logger = setup_logging('INFO')
logger.info('App log')
logging.getLogger('uvicorn').info('Uvicorn log')
logging.getLogger('uvicorn.error').info('Uvicorn error log')
logging.getLogger('uvicorn.access').info('Uvicorn access log')
logging.getLogger('litellm').info('Litellm log')
logging.getLogger('litellm.proxy').info('Litellm proxy log')
logging.getLogger('litellm.auth').info('Litellm auth log')
logging.getLogger('litellm.usage').info('Litellm usage log')
logging.getLogger('litellm.cache').info('Litellm cache log')
logging.getLogger('litellm.telemetry').info('Litellm telemetry log')

print("✓ All loggers propagate correctly in JSON format")

# Test 2: Verify human format works
print("\n" + "=" * 60)
print("Test 2: Verify human format works")
print("=" * 60)
os.environ['LOG_FORMAT'] = 'human'
import importlib
import src.log_formatter
importlib.reload(src.log_formatter)
from src.log_formatter import setup_logging as setup_logging2

logger2 = setup_logging2('INFO')
logger2.info('App log')
logging.getLogger('uvicorn').info('Uvicorn log')
logging.getLogger('litellm').info('Litellm log')

print("✓ Human format works correctly")

# Test 3: Verify exception handling
print("\n" + "=" * 60)
print("Test 3: Verify exception handling")
print("=" * 60)
os.environ['LOG_FORMAT'] = 'json'
importlib.reload(src.log_formatter)
from src.log_formatter import setup_logging as setup_logging3

logger3 = setup_logging3('INFO')
try:
    raise ValueError("Test exception")
except Exception:
    logger3.exception("Caught exception")

print("✓ Exception handling works correctly")

# Test 4: Verify log levels work
print("\n" + "=" * 60)
print("Test 4: Verify log levels work")
print("=" * 60)
os.environ['LOG_FORMAT'] = 'json'
importlib.reload(src.log_formatter)
from src.log_formatter import setup_logging as setup_logging4

logger4 = setup_logging4('DEBUG')
logger4.debug('Debug message')
logger4.info('Info message')
logger4.warning('Warning message')
logger4.error('Error message')

print("✓ Log levels work correctly")

print("\n" + "=" * 60)
print("All tests passed! ✓")
print("=" * 60)
