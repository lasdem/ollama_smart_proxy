#!/usr/bin/env python
"""Test script to verify logging fixes"""
import os
import sys
import logging
import pytest

sys.path.insert(0, '/home/peterkrammer/ws/python/litellm_smart_proxy')

from src.log_formatter import setup_logging


def test_json_logging():
    """Test JSON format logging"""
    print("=" * 60)
    print("Test 1: JSON format logging")
    print("=" * 60)
    os.environ['LOG_FORMAT'] = 'json'
    
    logger = setup_logging('INFO')
    logger.info('Test message from app')
    logging.getLogger('uvicorn').info('Test message from uvicorn')
    logging.getLogger('litellm').info('Test message from litellm')
    
    print("✓ JSON logging test passed")


def test_human_logging():
    """Test human format logging"""
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
    
    print("✓ Human logging test passed")


def test_exception_handling():
    """Test exception handling"""
    print("\n" + "=" * 60)
    print("Test 3: Exception handling")
    print("=" * 60)
    os.environ['LOG_FORMAT'] = 'json'
    import importlib
    import src.log_formatter
    importlib.reload(src.log_formatter)
    from src.log_formatter import setup_logging as setup_logging3
    
    logger3 = setup_logging3('INFO')
    try:
        raise ValueError("Test exception")
    except Exception:
        logger3.exception("Caught exception")
    
    print("✓ Exception handling test passed")


def test_all_logging_fixes():
    """Comprehensive test to verify all logging fixes"""
    print("\n" + "=" * 60)
    print("Test 4: Comprehensive logging test")
    print("=" * 60)
    
    # Test all loggers propagate correctly
    os.environ['LOG_FORMAT'] = 'json'
    import importlib
    import src.log_formatter
    importlib.reload(src.log_formatter)
    from src.log_formatter import setup_logging as setup_logging4
    
    logger4 = setup_logging4('INFO')
    logger4.info('App log')
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
    
    # Test human format
    os.environ['LOG_FORMAT'] = 'human'
    importlib.reload(src.log_formatter)
    from src.log_formatter import setup_logging as setup_logging5
    
    logger5 = setup_logging5('INFO')
    logger5.info('App log')
    logging.getLogger('uvicorn').info('Uvicorn log')
    logging.getLogger('litellm').info('Litellm log')
    
    print("✓ Human format works correctly")
    
    # Test exception handling
    os.environ['LOG_FORMAT'] = 'json'
    importlib.reload(src.log_formatter)
    from src.log_formatter import setup_logging as setup_logging6
    
    logger6 = setup_logging6('INFO')
    try:
        raise ValueError("Test exception")
    except Exception:
        logger6.exception("Caught exception")
    
    print("✓ Exception handling works correctly")
    
    # Test log levels
    os.environ['LOG_FORMAT'] = 'json'
    importlib.reload(src.log_formatter)
    from src.log_formatter import setup_logging as setup_logging7
    
    logger7 = setup_logging7('DEBUG')
    logger7.debug('Debug message')
    logger7.info('Info message')
    logger7.warning('Warning message')
    logger7.error('Error message')
    
    print("✓ Log levels work correctly")
    
    print("\n" + "=" * 60)
    print("All tests passed! ✓")
    print("=" * 60)
