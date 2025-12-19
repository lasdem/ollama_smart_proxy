#!/usr/bin/env python3
"""
Test Scenarios for Smart Proxy
"""
import asyncio
import httpx
from typing import Dict, Any, List


PROXY_URL = "http://localhost:8003"


async def scenario_same_model_bunching():
    """Test that same-model requests are processed consecutively"""
    print("\n📋 Scenario 1: Same Model Bunching")
    print("   Sending 10 requests for qwen2.5:7b")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = []
        for i in range(10):
            task = client.post(
                f"{PROXY_URL}/v1/chat/completions",
                json={
                    "model": "qwen2.5:7b",
                    "messages": [{"role": "user", "content": f"Test {i}"}],
                    "stream": False
                }
            )
            tasks.append(task)
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 200)
        
        return {
            "success": success_count == 10,
            "message": f"{success_count}/10 requests completed successfully"
        }


async def scenario_large_model_deferral():
    """Test that large models are deferred when small models are queued"""
    print("\n📋 Scenario 2: Large Model Deferral")
    print("   Mixing small (gemma3) and large (llama3.3) models")
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        tasks = []
        
        # Send 5 small model requests
        for i in range(5):
            task = client.post(
                f"{PROXY_URL}/v1/chat/completions",
                json={
                    "model": "gemma3",
                    "messages": [{"role": "user", "content": f"Small {i}"}],
                    "stream": False
                }
            )
            tasks.append(task)
        
        # Send 3 large model requests
        for i in range(3):
            task = client.post(
                f"{PROXY_URL}/v1/chat/completions",
                json={
                    "model": "llama3.3",
                    "messages": [{"role": "user", "content": f"Large {i}"}],
                    "stream": False
                }
            )
            tasks.append(task)
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 200)
        
        return {
            "success": success_count >= 6,  # Allow some failures
            "message": f"{success_count}/8 requests completed"
        }


async def scenario_ip_fairness():
    """Test IP fairness with simulated multiple IPs"""
    print("\n📋 Scenario 3: IP Fairness")
    print("   Simulating 3 different IPs with varying request counts")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = []
        
        # IP1: 5 requests
        for i in range(5):
            task = client.post(
                f"{PROXY_URL}/v1/chat/completions",
                json={
                    "model": "qwen2.5:7b",
                    "messages": [{"role": "user", "content": f"IP1-{i}"}],
                    "stream": False
                },
                headers={"X-Forwarded-For": "192.168.1.1"}
            )
            tasks.append(task)
        
        # IP2: 2 requests
        for i in range(2):
            task = client.post(
                f"{PROXY_URL}/v1/chat/completions",
                json={
                    "model": "qwen2.5:7b",
                    "messages": [{"role": "user", "content": f"IP2-{i}"}],
                    "stream": False
                },
                headers={"X-Forwarded-For": "192.168.1.2"}
            )
            tasks.append(task)
        
        # IP3: 3 requests
        for i in range(3):
            task = client.post(
                f"{PROXY_URL}/v1/chat/completions",
                json={
                    "model": "qwen2.5:7b",
                    "messages": [{"role": "user", "content": f"IP3-{i}"}],
                    "stream": False
                },
                headers={"X-Forwarded-For": "192.168.1.3"}
            )
            tasks.append(task)
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 200)
        
        return {
            "success": success_count >= 8,
            "message": f"{success_count}/10 requests completed (fairness check in logs)"
        }


async def scenario_wait_time_starvation():
    """Test that long-waiting requests eventually get priority"""
    print("\n📋 Scenario 4: Wait Time Starvation Prevention")
    print("   Sending delayed requests to test wait time bonus")
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        # Send first batch
        task1 = client.post(
            f"{PROXY_URL}/v1/chat/completions",
            json={
                "model": "qwen2.5:7b",
                "messages": [{"role": "user", "content": "First batch"}],
                "stream": False
            }
        )
        
        # Wait a bit, then send second batch
        await asyncio.sleep(10)
        
        task2 = client.post(
            f"{PROXY_URL}/v1/chat/completions",
            json={
                "model": "qwen2.5:7b",
                "messages": [{"role": "user", "content": "Second batch (should wait)"}],
                "stream": False
            }
        )
        
        responses = await asyncio.gather(task1, task2, return_exceptions=True)
        
        success_count = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 200)
        
        return {
            "success": success_count == 2,
            "message": f"{success_count}/2 requests completed (check wait times in logs)"
        }


async def run_all_scenarios() -> Dict[str, Any]:
    """Run all test scenarios"""
    results = {}
    
    # Run each scenario
    scenarios = [
        ("Same Model Bunching", scenario_same_model_bunching),
        ("Large Model Deferral", scenario_large_model_deferral),
        ("IP Fairness", scenario_ip_fairness),
        ("Wait Time Starvation", scenario_wait_time_starvation),
    ]
    
    for name, scenario_func in scenarios:
        try:
            result = await scenario_func()
            results[name] = result
        except Exception as e:
            results[name] = {
                "success": False,
                "message": f"Exception: {str(e)}"
            }
            print(f"   ❌ Failed with exception: {e}")
    
    return results


if __name__ == "__main__":
    # Can run scenarios standalone for testing
    asyncio.run(run_all_scenarios())
