#!/usr/bin/env python3
"""
Test Runner with Log Analysis
Starts proxy, runs tests, analyzes logs
"""
import asyncio
import subprocess
import time
import sys
import os
import signal
from pathlib import Path

# Test scenarios
from test_scenarios import run_all_scenarios

# Add scripts to path for analyzer
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from analyze_logs import analyze_log_file


class ProxyTestRunner:
    def __init__(self, log_file="test_proxy.log"):
        self.log_file = Path(__file__).parent.parent / log_file
        self.proxy_process = None
        
    def start_proxy(self):
        """Start proxy in subprocess"""
        proxy_path = Path(__file__).parent.parent / "src" / "smart_proxy.py"
        
        print("🚀 Starting proxy for testing...")
        
        # Start proxy with output to log file
        with open(self.log_file, 'w') as log:
            self.proxy_process = subprocess.Popen(
                [sys.executable, str(proxy_path)],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=proxy_path.parent
            )
        
        # Wait for startup
        print("⏳ Waiting for proxy to start...")
        time.sleep(5)
        
        # Verify it's running
        if self.proxy_process.poll() is not None:
            print("❌ Proxy failed to start!")
            with open(self.log_file, 'r') as f:
                print(f.read())
            sys.exit(1)
        
        print(f"✅ Proxy started (PID: {self.proxy_process.pid})")
        
    def stop_proxy(self):
        """Stop proxy gracefully"""
        if self.proxy_process:
            print("🛑 Stopping proxy...")
            self.proxy_process.terminate()
            try:
                self.proxy_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("⚠️  Proxy didn't stop gracefully, killing...")
                self.proxy_process.kill()
            print("✅ Proxy stopped")
    
    async def run_tests(self):
        """Execute test scenarios"""
        print("\n" + "="*60)
        print("🧪 Running Test Scenarios")
        print("="*60)
        
        results = await run_all_scenarios()
        
        print("\n" + "="*60)
        print("📊 Test Results Summary")
        print("="*60)
        
        for scenario_name, result in results.items():
            status = "✅ PASS" if result["success"] else "❌ FAIL"
            print(f"{status} - {scenario_name}: {result['message']}")
        
        all_passed = all(r["success"] for r in results.values())
        return all_passed
    
    def analyze_logs(self, format_type="shell"):
        """Analyze logs and display statistics"""
        print("\n" + "="*60)
        print("📈 Log Analysis")
        print("="*60)
        
        analyze_log_file(str(self.log_file), output_format=format_type)


async def main():
    runner = ProxyTestRunner()
    
    try:
        # Start proxy
        runner.start_proxy()
        
        # Run tests
        all_passed = await runner.run_tests()
        
        # Analyze logs
        runner.analyze_logs(format_type="shell")
        
        # Exit with appropriate code
        sys.exit(0 if all_passed else 1)
        
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        runner.stop_proxy()


if __name__ == "__main__":
    asyncio.run(main())
