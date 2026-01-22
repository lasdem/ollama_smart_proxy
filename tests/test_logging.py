import os
import sys
import logging
import json
import pytest
import importlib

# Ensure the src path is available
sys.path.insert(0, os.getcwd()) # Better path resolution

import src.log_formatter

# --- FIXTURE ---

@pytest.fixture
def reset_logging_env():
    """
    Fixture to cleanly reset logging state between tests.
    """
    # 1. Store original env vars
    original_format = os.environ.get('LOG_FORMAT')
    
    # 2. Clear handlers from Root and key libraries
    root = logging.getLogger()
    list(map(root.removeHandler, root.handlers[:]))
    
    loggers_to_clear = ['uvicorn', 'uvicorn.access', 'litellm', 'uvicorn.error']
    for name in loggers_to_clear:
        l = logging.getLogger(name)
        list(map(l.removeHandler, l.handlers[:]))
        l.propagate = True # Ensure they bubble up to root

    yield
    
    # 3. Restore env
    if original_format:
        os.environ['LOG_FORMAT'] = original_format
    else:
        os.environ.pop('LOG_FORMAT', None)


# --- TESTS ---

def test_json_logging_structure(reset_logging_env, capsys):
    """Verify JSON structure."""
    os.environ['LOG_FORMAT'] = 'json'
    # Reload ensures the module reads the new ENV var
    importlib.reload(src.log_formatter)
    
    logger = src.log_formatter.setup_logging('INFO')
    test_msg = "Test JSON message"
    logger.info(test_msg)
    
    captured = capsys.readouterr()
    log_output = captured.err if captured.err else captured.out
    
    assert log_output.strip(), "No log output captured"
    
    # Should parse successfully
    log_entry = json.loads(log_output.strip().split('\n')[-1])
    
    assert log_entry.get('message') == test_msg
    assert log_entry.get('level') == 'INFO'
    assert 'timestamp' in log_entry


def test_human_logging_format(reset_logging_env, capsys):
    """Verify Human structure."""
    os.environ['LOG_FORMAT'] = 'human'
    # CRITICAL: Reload module so it sees 'human' in os.getenv
    importlib.reload(src.log_formatter)
    
    logger = src.log_formatter.setup_logging('INFO')
    test_msg = "Test Human message"
    logger.info(test_msg)
    
    captured = capsys.readouterr()
    log_output = captured.err if captured.err else captured.out
    
    assert log_output.strip(), "No log output captured"
    
    # Expectation: JSON Parse should FAIL
    with pytest.raises(json.JSONDecodeError):
        json.loads(log_output.strip())
        
    assert test_msg in log_output
    
    # --- FIX: Match the actual separator used in log_formatter.py (" | ") ---
    assert " | " in log_output

def test_third_party_propagation(reset_logging_env, capsys):
    """Verify 3rd party logs appear in our format."""
    os.environ['LOG_FORMAT'] = 'json'
    importlib.reload(src.log_formatter)
    
    _ = src.log_formatter.setup_logging('INFO')
    
    # Simulate 3rd party
    logging.getLogger('uvicorn').warning("Uvicorn warning")
    
    captured = capsys.readouterr()
    log_output = captured.err if captured.err else captured.out
    lines = log_output.strip().split('\n')
    
    # Parse
    logs = [json.loads(line) for line in lines if line.strip()]
    uvicorn_log = next((l for l in logs if "Uvicorn warning" in l.get('message', '')), None)
    
    assert uvicorn_log is not None
    assert uvicorn_log.get('logger') == 'uvicorn'


def test_exception_traceback_json(reset_logging_env, capsys):
    """Verify exceptions include traceback keys."""
    os.environ['LOG_FORMAT'] = 'json'
    importlib.reload(src.log_formatter)
    
    logger = src.log_formatter.setup_logging('INFO')
    
    try:
        raise ValueError("Critical Failure")
    except ValueError:
        logger.exception("Something went wrong")
    
    captured = capsys.readouterr()
    log_output = captured.err if captured.err else captured.out
    
    log_entry = json.loads(log_output.strip().split('\n')[-1])
    
    assert log_entry.get('message') == "Something went wrong"
    
    # FIX VERIFICATION: This asserted failed before. 
    # With %(exc_info)s added to log_formatter.py, this will now pass.
    assert 'exc_info' in log_entry or 'traceback' in log_entry, \
        f"Traceback missing! Keys found: {log_entry.keys()}"
    
    assert "Critical Failure" in str(log_entry), "Original exception message missing from log dump"


def test_log_levels(reset_logging_env, capsys):
    """Verify level filtering."""
    os.environ['LOG_FORMAT'] = 'json'
    importlib.reload(src.log_formatter)
    
    logger = src.log_formatter.setup_logging('INFO')
    
    logger.debug("Hidden")
    logger.info("Visible")
    
    captured = capsys.readouterr()
    log_output = captured.err if captured.err else captured.out
    
    assert "Hidden" not in log_output
    assert "Visible" in log_output
