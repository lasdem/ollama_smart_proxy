#!/usr/bin/env python3
"""
Test Scenarios for Smart Proxy
"""
import asyncio
import httpx
import pytest
from typing import Dict, Any, List


PROXY_URL = "http://localhost:8003"


import asyncio
import httpx
import pytest

# Ensure PROXY_URL is defined somewhere
# PROXY_URL = "http://localhost:8000" 

@pytest.mark.asyncio
async def test_scenario_same_model_bunching():
    """Test that same-model requests are processed consecutively (Bunching)"""
    print("\n📋 Scenario 1: Same Model Bunching")
    
    # 1. Setup
    n_pairs = 10
    total_requests = n_pairs * 2
    print(f"   Sending {total_requests} requests for qwen2.5:7b and llama3.2-vision:latest interleaved.")
    
    completion_order = []
    errors = []

    async with httpx.AsyncClient(base_url=PROXY_URL, timeout=60.0) as client:
        # 2. Pre-check: Ensure server is healthy
        try:
            health = await client.get("/proxy/health")
            assert health.status_code == 200, f"Proxy is not healthy: {health.text}"
            print(f"   ✅ Proxy is healthy. Active requests: {health.json()['active_requests']}")
        except Exception as e:
            pytest.fail(f"Could not connect to proxy at {PROXY_URL}: {e}")

        # 3. Define the fetcher task
        async def fetch(model, index):
            try:
                # Use a very short prompt to speed up the test
                resp = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Say Hi."}],
                        "stream": False
                    }
                )
                if resp.status_code == 200:
                    # Append strictly after completion
                    completion_order.append(model)
                    return True
                else:
                    errors.append(f"Status {resp.status_code}: {resp.text}")
                    return False
            except Exception as e:
                errors.append(str(e))
                return False

        # 4. Create interleaved tasks
        tasks = []
        for i in range(n_pairs):
            tasks.append(asyncio.create_task(fetch("qwen2.5:7b", i)))
            tasks.append(asyncio.create_task(fetch("llama3.2-vision:latest", i)))
        
        # 5. Wait for completion
        print("   ⏳ Waiting for responses...")
        await asyncio.gather(*tasks)

        # 6. Debugging if things went wrong
        if len(completion_order) < total_requests:
            print(f"\n   ❌ Failed! Only got {len(completion_order)}/{total_requests} responses.")
            print(f"   ⚠️ Errors encountered: {errors}")
            
            # Fetch server state to see what's stuck
            try:
                queue_state = (await client.get("/proxy/queue")).json()
                health_state = (await client.get("/proxy/health")).json()
                print(f"   🔍 Server Debug - Queue Depth: {queue_state['queue_depth']}")
                print(f"   🔍 Server Debug - Active Requests: {health_state['active_requests']}")
            except:
                print("   Could not fetch debug info.")

        # 7. Analyze Bunching
        switches = 0
        for i in range(1, len(completion_order)):
            if completion_order[i] != completion_order[i-1]:
                switches += 1

        short_names = ["Qwen" if "qwen" in m else "Llama" for m in completion_order]
        
        print(f"\n   📊 Actual completion order: {short_names}")
        print(f"   🔄 Number of model switches: {switches}")
        print(f"   🎯 Target switches: <= 3")

        # Assertions
        assert len(completion_order) == total_requests, \
            f"Expected {total_requests} successful responses, got {len(completion_order)}"
        
        assert switches <= 3, \
            f"Expected bunching (<=3 switches), but got {switches}. The proxy is not prioritizing correctly."
            
        print("   ✅ SUCCESS: Requests were successfully bunched.")

@pytest.mark.asyncio
async def test_scenario_large_model_deferral():
    """
    Test that large models are deferred when small models are queued.
    
    STRICT PRIORITY MODE:
    1. Fill slots with 'Startup' (Qwen).
    2. Queue 5 'Large' requests (Nanonets).
    3. Queue 5 'Small' requests (magistral).
    
    Expectation:
    - The proxy must re-order the queue.
    - ALL 'Small' requests must finish before ANY 'Large' request finishes.
    - Execution flow: [Startup...] -> [All Smalls...] -> [All Larges...]
    """
    print("\n📋 Scenario 2: Large Model Deferral (Strict Priority)")
    
    # Models
    startup_model = "qwen2.5:7b"
    large_model = "benhaotang/Nanonets-OCR-s:latest" 
    small_model = "magistral" 
    
    requests_per_model = 5
    completion_order = []
    errors = []

    # High timeout to accommodate loading/unloading multiple models
    async with httpx.AsyncClient(base_url=PROXY_URL, timeout=300.0) as client:
        # 1. Health Check (Corrected Endpoint)
        try:
            resp = await client.get("/proxy/health")
            assert resp.status_code == 200, f"Health check failed: {resp.text}"
        except Exception as e:
            pytest.fail(f"Proxy unreachable at {PROXY_URL}: {e}")

        # 2. Worker Function
        async def fetch(model, label):
            try:
                # Use a specific prompt that isn't instant, but short
                resp = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Write one short sentence."}],
                        "stream": False
                    }
                )
                if resp.status_code == 200:
                    completion_order.append(model)
                else:
                    errors.append(f"{label} failed: {resp.status_code}")
            except Exception as e:
                errors.append(f"{label} error: {str(e)}")

        tasks = []

        # 3. Fill the queue (Startup)
        print(f"   1️⃣  Sending {requests_per_model} requests for {startup_model} (Blocking slots)...")
        for i in range(requests_per_model):
            tasks.append(asyncio.create_task(fetch(startup_model, "Startup")))
        
        # Wait 1.0s to ensure these are definitely "Processing" and blocking the GPU
        await asyncio.sleep(1.0)

        # 4. Queue Large Models (Entered First)
        print(f"   2️⃣  Queuing {requests_per_model} LARGE requests ({large_model})...")
        for i in range(requests_per_model):
            tasks.append(asyncio.create_task(fetch(large_model, "Large")))
        
        # Short delay to ensure Large is strictly registered in queue before Small arrives
        await asyncio.sleep(0.5)

        # 5. Queue Small Models (Entered Last)
        print(f"   3️⃣  Queuing {requests_per_model} SMALL requests ({small_model})...")
        for i in range(requests_per_model):
            tasks.append(asyncio.create_task(fetch(small_model, "Small")))

        # 6. Wait for all
        print("   ⏳ Waiting for responses...")
        await asyncio.gather(*tasks)

        if errors:
            print(f"   ⚠️ Errors encountered: {errors}")

        # 7. Visualization & Logic
        # Filter out startup models to see the core transition
        post_startup_order = [m for m in completion_order if m != startup_model]
        
        visual_map = ["LARGE" if m == large_model else "SMALL" for m in post_startup_order]
        print(f"\n   📊 Execution Stream (Post-Startup): {visual_map}")

        # --- STRICT ASSERTIONS ---
        
        # A. Completeness
        expected_total = requests_per_model * 3
        assert len(completion_order) == expected_total, f"Expected {expected_total} responses, got {len(completion_order)}"

        # B. Identify boundaries
        # We need to find the index of the FIRST Large model completion
        # and the index of the LAST Small model completion.
        
        try:
            first_large_index = completion_order.index(large_model)
        except ValueError:
            pytest.fail("❌ No Large models completed.")

        # Find the last occurrence of small_model
        # We reverse the list, find the index, and subtract from length
        try:
            last_small_index = len(completion_order) - 1 - completion_order[::-1].index(small_model)
        except ValueError:
             pytest.fail("❌ No Small models completed.")

        print(f"   📍 Last Small Completed Index: {last_small_index}")
        print(f"   📍 First Large Completed Index: {first_large_index}")

        # C. Strict Priority Check
        # The last Small model must finish BEFORE the first Large model finishes.
        # This implies the proxy cleared the entire backlog of Small requests 
        # before touching the Large requests.
        assert last_small_index < first_large_index, \
            (f"❌ Strict Priority Failed! \n"
             f"   A Large model finished (idx {first_large_index}) before the last Small model (idx {last_small_index}).\n"
             f"   The proxy mixed the queue or did not prioritize Small models strictly.")

        print("   ✅ SUCCESS: Strict priority enforced. All Small models cleared before Large models started.")

@pytest.mark.asyncio
async def test_scenario_ip_fairness():
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
        
        assert success_count >= 8, f"Expected at least 8 successful requests, got {success_count}"
        print(f"   ✅ {success_count}/10 requests completed (fairness check in logs)")


@pytest.mark.asyncio
async def test_scenario_wait_time_starvation():
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
        
        assert success_count == 2, f"Expected 2 successful requests, got {success_count}"
        print(f"   ✅ {success_count}/2 requests completed (check wait times in logs)")


@pytest.mark.asyncio
async def test_scenario_priority_reordering():
    """Test that higher priority requests jump ahead"""
    print("\n📋 Scenario 5: Priority Reordering")
    print("   Testing that higher priority requests jump ahead of lower priority ones")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Send low priority request (large model)
        task_low = client.post(
            f"{PROXY_URL}/v1/chat/completions",
            json={
                "model": "llama3.3",  # Large model
                "messages": [{"role": "user", "content": "Low priority"}],
                "stream": False
            }
        )
        
        # Wait to build up wait time
        await asyncio.sleep(5)
        
        # Send high priority request (same model already loaded)
        task_high = client.post(
            f"{PROXY_URL}/v1/chat/completions",
            json={
                "model": "llama3.3",  # Same model
                "messages": [{"role": "user", "content": "High priority"}],
                "stream": False
            }
        )
        
        responses = await asyncio.gather(task_low, task_high, return_exceptions=True)
        
        # Check completion order via response times
        low_time = responses[0].headers.get('x-response-time', 0) if not isinstance(responses[0], Exception) else float('inf')
        high_time = responses[1].headers.get('x-response-time', 0) if not isinstance(responses[1], Exception) else float('inf')
        
        success = not isinstance(responses[0], Exception) and not isinstance(responses[1], Exception) and high_time < low_time
        
        assert success, f"Expected high priority request to complete first, but low_time={low_time}, high_time={high_time}"
        print(f"   ✅ High priority request completed first (low_time={low_time}s, high_time={high_time}s)")


@pytest.mark.asyncio
async def test_scenario_rate_limiting():
    """Test that rapid requests from one IP get penalized"""
    print("\n📋 Scenario 6: Rate Limiting")
    print("   Sending rapid requests from one IP to test rate limiting")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = []
        
        # Send 10 rapid requests
        for i in range(10):
            task = client.post(
                f"{PROXY_URL}/v1/chat/completions",
                json={
                    "model": "qwen2.5:7b",
                    "messages": [{"role": "user", "content": f"Rapid {i}"}],
                    "stream": False
                }
            )
            tasks.append(task)
            await asyncio.sleep(0.1)  # Small delay to avoid overwhelming the server
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 200)
        
        assert success_count >= 8, f"Expected at least 8 successful requests, got {success_count}"
        print(f"   ✅ {success_count}/10 requests completed (check rate limiting in logs)")


@pytest.mark.asyncio
async def test_scenario_parallel_fitting():
    """Test that models fitting parallel get priority"""
    print("\n📋 Scenario 7: Parallel Model Fitting")
    print("   Testing that multiple requests for same small model run in parallel")
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        tasks = []
        
        # Send requests for models that should fit parallel
        # Assuming 80GB VRAM: 20GB + 20GB + 20GB = 60GB < 80GB
        for model in ["gemma3", "gemma3", "gemma3"]:
            task = client.post(
                f"{PROXY_URL}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Parallel test"}],
                    "stream": False
                }
            )
            tasks.append(task)
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 200)
        
        assert success_count == 3, f"Expected 3 successful requests, got {success_count}"
        print(f"   ✅ {success_count}/3 parallel requests completed")


if __name__ == "__main__":
    # Can run scenarios standalone for testing
    asyncio.run(run_all_scenarios())


async def test_scenario_ip_fairness():
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

@pytest.mark.asyncio
async def test_scenario_fault_tolerance():
    """
    Test that invalid requests fail gracefully and release their slots immediately.
    
    Scenario:
    1. Fill slots with a slow 'Startup' model.
    2. Queue a request for a NON-EXISTENT model.
    3. Queue a valid 'Small' request.
    
    Expectation:
    - The non-existent model request should return 400/404/500 immediately.
    - It must NOT block the queue.
    - The 'Small' request should be picked up immediately after the error clears.
    """
    print("\n📋 Scenario 3: Fault Tolerance & Slot Recovery")
    
    # Config
    startup_model = "qwen2.5:7b"
    bad_model = "this-model-does-not-exist:666"
    valid_model = "magistral" # Using your working small model
    
    completion_order = []
    status_codes = {}

    async with httpx.AsyncClient(base_url=PROXY_URL, timeout=60.0) as client:
        # 1. Health check
        try:
            await client.get("/proxy/health")
        except:
             pytest.fail("Proxy unreachable")

        async def fetch(model, label):
            try:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": False
                    }
                )
                # Store status code
                status_codes[label] = resp.status_code
                
                if resp.status_code == 200:
                    completion_order.append(label)
            except Exception as e:
                status_codes[label] = "Exception"

        tasks = []

        # 2. Fill Slots (Startup)
        # Assuming MAX_PARALLEL=3, we send 3 to block
        print(f"   1️⃣  Blocking slots with {startup_model}...")
        for i in range(3):
            tasks.append(asyncio.create_task(fetch(startup_model, f"Startup-{i}")))
            
        await asyncio.sleep(0.5)

        # 3. Queue the BAD request
        print(f"   2️⃣  Queuing BAD model request ({bad_model})...")
        tasks.append(asyncio.create_task(fetch(bad_model, "BAD_REQUEST")))
        
        await asyncio.sleep(0.2)

        # 4. Queue a VALID request
        print(f"   3️⃣  Queuing VALID model request ({valid_model})...")
        tasks.append(asyncio.create_task(fetch(valid_model, "VALID_REQUEST")))

        print("   ⏳ Waiting for responses...")
        await asyncio.gather(*tasks)

        # --- ASSERTIONS ---
        
        # Check Bad Request behavior
        bad_status = status_codes.get("BAD_REQUEST")
        print(f"   🔍 Bad Request Status: {bad_status}")
        
        assert bad_status != 200, "The non-existent model should NOT return 200 OK!"
        assert bad_status in [400, 404, 500], f"Expected 4xx/5xx for bad model, got {bad_status}"

        # Check Slot Recovery
        # If the bad request froze the slot, VALID_REQUEST might not have finished
        assert "VALID_REQUEST" in completion_order, "The valid request never finished! The bad request likely jammed the queue."
        
        # Optional: Check server health to ensure active_requests is back to 0
        health = (await client.get("/proxy/health")).json()
        active = health.get("active_requests", -1)
        print(f"   🏥 Final Active Requests on Server: {active}")
        
        assert active == 0, f"Server leaked slots! Expected 0 active requests, got {active}"

        print("   ✅ SUCCESS: Bad request failed gracefully and slot was recovered.")

async def run_all_scenarios() -> Dict[str, Any]:
    """Run all test scenarios"""
    results = {}
    
    # Run each scenario
    scenarios = [
        ("Same Model Bunching", test_scenario_same_model_bunching),
        ("Large Model Deferral", test_scenario_large_model_deferral),
        ("IP Fairness", test_scenario_ip_fairness),
        ("Wait Time Starvation", test_scenario_wait_time_starvation),
        ("Priority Reordering", test_scenario_priority_reordering),
        ("Rate Limiting", test_scenario_rate_limiting),
        ("Parallel Model Fitting", test_scenario_parallel_fitting),
        ("Fault Tolerance", test_scenario_fault_tolerance)
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
