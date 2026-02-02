#!/usr/bin/env python3
"""
Test script for fallback logging functionality
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from database import init_db, close_db, get_db
from data_access import get_request_log_repo, init_repositories

def test_fallback_logging():
    """Test fallback logging mechanism"""
    print("Testing fallback logging...")
    
    # Create temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Close any existing database connection first
        close_db()
        
        # Set environment variables for testing
        os.environ['DB_TYPE'] = 'sqlite'
        os.environ['SQLITE_DB_PATH'] = os.path.join(temp_dir, 'test.db')
        os.environ['FALLBACK_LOG_DIR'] = os.path.join(temp_dir, 'fallback_logs')
        
        # Initialize database and repositories (fresh instances)
        init_db()
        init_repositories()
        
        # Create repository
        repo = get_request_log_repo()
        db = get_db()
        
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
        assert result is not None, "Should create request log"
        print(f"  ✓ Result: {result}")
        print(f"  ✓ Request ID: {result.request_id}")
        
        # Test 2: Simulate database failure
        print("\nTest 2: Simulating database failure")
        db.set_simulated_unavailable(True)
        
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
        
        try:
            result2 = repo.create_request_log(test_request2)
            print(f"  ✓ Fallback triggered, returned mock result")
            print(f"  ✓ Request ID: {result2.request_id}")
        except Exception as e:
            print(f"  ✓ Expected exception caught: {type(e).__name__}")
        
        # Check if fallback file was created
        fallback_dir = Path(os.environ['FALLBACK_LOG_DIR'])
        fallback_files = list(fallback_dir.glob('fallback_*.jsonl'))
        print(f"  ✓ Fallback files created: {len(fallback_files)}")
        assert len(fallback_files) > 0, "Should create fallback file"
        
        if fallback_files:
            print(f"  ✓ Fallback file: {fallback_files[0].name}")
            with open(fallback_files[0], 'r') as f:
                content = f.read()
                print(f"  ✓ Content preview: {content[:100]}...")
                assert 'test-request-456' in content, "Should contain request ID"
        
        # Test 3: Recovery from fallback files
        print("\nTest 3: Recovery from fallback files")
        
        # Restore DB availability (should trigger recovery)
        db.set_simulated_unavailable(False)
        
        # Trigger recovery
        recovered_count = db.recover_from_fallback_files()
        print(f"  ✓ Recovered {recovered_count} records")
        assert recovered_count > 0, "Should recover at least one record"
        
        # Verify recovery happened
        assert recovered_count > 0, "Should recover at least one record"
        
        # Check if recovered data exists in database
        recovered = repo.get_request_log('test-request-456')
        assert recovered is not None, "Should find recovered data in database"
        print(f"  ✓ Recovered request: {recovered.request_id}")
        print(f"  ✓ Status: {recovered.status}")
        print(f"  ✓ Model: {recovered.model_name}")
        assert recovered.status == 'completed', "Should have correct status"
        assert recovered.model_name == 'gpt-3.5', "Should have correct model"
        
        # Check if fallback files were removed after successful recovery
        fallback_files_after = list(fallback_dir.glob('fallback_*.jsonl'))
        print(f"  ✓ Fallback files remaining: {len(fallback_files_after)}")
        assert len(fallback_files_after) == 0, "Should remove fallback files after successful recovery"
        
        # Test 4: Test multiple failures and recovery
        print("\nTest 4: Multiple failures and batch recovery")
        
        # Simulate DB unavailability again
        db.set_simulated_unavailable(True)
        
        # Create multiple requests
        for i in range(3):
            test_request_multi = {
                'request_id': f'test-multi-{i}',
                'source_ip': '192.168.1.100',
                'model_name': 'llama2',
                'prompt_text': f'Test {i}',
                'status': 'completed',
                'duration_seconds': 1.0 + i,
                'priority_score': 100 + i,
                'timestamp_received': datetime.utcnow()
            }
            try:
                repo.create_request_log(test_request_multi)
            except:
                pass  # Expected to fail
        
        # Check fallback files
        fallback_files_multi = list(fallback_dir.glob('fallback_*.jsonl'))
        print(f"  ✓ Created {len(fallback_files_multi)} fallback file(s)")
        assert len(fallback_files_multi) > 0, "Should create fallback files"
        
        # Restore and recover
        db.set_simulated_unavailable(False)
        recovered_multi = db.recover_from_fallback_files()
        print(f"  ✓ Recovered {recovered_multi} records from batch")
        assert recovered_multi >= 3, "Should recover all 3 records"
        
        # Verify all were recovered
        for i in range(3):
            rec = repo.get_request_log(f'test-multi-{i}')
            assert rec is not None, f"Should recover test-multi-{i}"
        
        # Verify cleanup
        fallback_files_final = list(fallback_dir.glob('fallback_*.jsonl'))
        print(f"  ✓ Final fallback files: {len(fallback_files_final)}")
        assert len(fallback_files_final) == 0, "Should remove all fallback files"
        
        # Cleanup
        close_db()
        
    print("\n✅ All tests completed successfully!")

if __name__ == '__main__':
    test_fallback_logging()
