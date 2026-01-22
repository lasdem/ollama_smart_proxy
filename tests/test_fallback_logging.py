#!/usr/bin/env python3
"""
Test script for fallback logging functionality
"""
import os
import sys
import tempfile
import shutil
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from database import DatabaseConnection, init_db, close_db
from data_access import RequestLogRepository

def test_fallback_logging():
    """Test fallback logging mechanism"""
    print("Testing fallback logging...")
    
    # Create temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set environment variables for testing
        os.environ['DB_TYPE'] = 'sqlite'
        os.environ['SQLITE_DB_PATH'] = os.path.join(temp_dir, 'test.db')
        os.environ['FALLBACK_LOG_DIR'] = os.path.join(temp_dir, 'fallback_logs')
        
        # Initialize database
        init_db()
        
        # Create repository
        repo = RequestLogRepository()
        
        # Test data
        test_request = {
            'request_id': 'test-request-123',
            'source_ip': '192.168.1.1',
            'model_name': 'gpt-4',
            'prompt_text': 'Hello, world!',
            'status': 'completed',
            'duration_seconds': 1.5,
            'priority_score': 100,
            'timestamp_received': datetime.utcnow()
        }
        
        # Test 1: Normal operation (should succeed)
        print("Test 1: Normal database operation")
        result = repo.create_request_log(test_request)
        print(f"  Result: {result}")
        print(f"  Request ID: {result.request_id}")
        
        # Test 2: Simulate database failure by closing connection
        print("\nTest 2: Simulating database failure")
        close_db()
        
        # Try to create another request (should fail and use fallback)
        test_request2 = {
            'request_id': 'test-request-456',
            'source_ip': '192.168.1.2',
            'model_name': 'gpt-3.5',
            'prompt_text': 'Test fallback',
            'status': 'completed',
            'duration_seconds': 2.0,
            'priority_score': 50,
            'timestamp_received': datetime.utcnow()
        }
        
        result2 = repo.create_request_log(test_request2)
        print(f"  Result: {result2}")
        print(f"  Request ID: {result2.request_id}")
        
        # Check if fallback file was created
        fallback_dir = Path(os.environ['FALLBACK_LOG_DIR'])
        fallback_files = list(fallback_dir.glob('fallback_*.jsonl'))
        print(f"  Fallback files created: {len(fallback_files)}")
        
        if fallback_files:
            print(f"  Fallback file: {fallback_files[0]}")
            with open(fallback_files[0], 'r') as f:
                content = f.read()
                print(f"  Content: {content[:100]}...")
        
        # Test 3: Recovery from fallback files
        print("\nTest 3: Recovery from fallback files")
        
        # Reinitialize database (should trigger recovery)
        init_db()
        
        # Check if recovered data exists
        recovered = repo.get_request_log('test-request-456')
        if recovered:
            print(f"  Recovered request: {recovered.request_id}")
            print(f"  Status: {recovered.status}")
            print(f"  Model: {recovered.model_name}")
        else:
            print("  No recovered data found")
        
        # Check if fallback files were removed
        fallback_files_after = list(fallback_dir.glob('fallback_*.jsonl'))
        print(f"  Fallback files remaining: {len(fallback_files_after)}")
        
        # Cleanup
        close_db()
        
    print("\nTest completed successfully!")

if __name__ == '__main__':
    test_fallback_logging()