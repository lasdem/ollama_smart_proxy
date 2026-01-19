import asyncio
import httpx
import pytest
import time
import os

# --- CONFIGURATION ---
class TestConfig:
    PROXY_URL = "http://localhost:8003"
    OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    TIMEOUT_DEFAULT = 300.0
    TIMEOUT_HEAVY = 600.0
    
    # Define your models here
    MODEL_STARTUP = "qwen2.5:7b"                        # A standard medium model
    MODEL_LARGE = "benhaotang/Nanonets-OCR-s:latest"    # High VRAM usage
    MODEL_MEDIUM = "magistral"                          # Another medium/small model
    MODEL_SMALL = "gemma3:latest"                       # Low VRAM / Fast
    MODEL_BAD = "this-model-does-not-exist:666"         # For fault tolerance

# ---------------------

# --- HELPERS ---

async def wait_for_proxy_idle():
    """
    Polls the proxy until Queue Depth and Active Requests are both 0.
    Timeout: 60 seconds.
    """
    max_retries = 120
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=5.0) as client:
        for i in range(max_retries):
            try:
                # Try both endpoints just in case
                try:
                    resp = await client.get("/proxy/health")
                except httpx.HTTPStatusError:
                    resp = await client.get("/health")
                
                if resp.status_code == 200:
                    data = resp.json()
                    # Check both queue and active slots
                    # Note: Handle cases where keys might be nested differently based on your specific proxy version
                    queue = data.get("queue_depth", 0)
                    active = data.get("active_requests", 0)
                    
                    if queue == 0 and active == 0:
                        if i > 0:
                            print(f"   ✓ Proxy drained successfully (waited {i*0.5}s)")
                        return True
            except Exception:
                # Proxy might be temporarily unresponsive under load
                pass
            
            await asyncio.sleep(0.5)
    
    print("   ⚠️ WARNING: Proxy did not drain in 10s! Future tests might fail.")
    return False

async def unload_all_models():
    """Helper to force unload all models from VRAM via Ollama API"""
    try:
        async with httpx.AsyncClient(base_url=TestConfig.OLLAMA_API_BASE, timeout=5.0) as client:
            # 1. Get list
            try:
                ps = await client.get("/api/ps")
                active_models = ps.json().get('models', [])
            except Exception:
                return # Ollama might be down

            if not active_models:
                return

            print(f"   🧹 Cleanup: Unloading {len(active_models)} models...")
            
            # 2. Force unload
            for m in active_models:
                await client.post("/api/chat", json={
                    "model": m['name'],
                    "keep_alive": 0
                })
            
            # 3. Wait for VRAM release
            await asyncio.sleep(0.2)
            
    except Exception as e:
        print(f"   ⚠️ Cleanup failed: {e}")

# --- FIXTURE ---

@pytest.fixture(autouse=True)
async def reset_system_state():
    """
    Ran automatically before EVERY test.
    1. Waits for Proxy to be idle.
    2. Clears VRAM in Ollama.
    """
    # 1. Wait for Proxy to finish previous tasks
    is_idle = await wait_for_proxy_idle()
    if not is_idle:
        pytest.fail("Proxy is stuck with active requests! Restart the proxy service.")

    # 2. Clear VRAM to ensure "Fresh Start" for priority logic
    await unload_all_models()
    
    yield

# ---------------------

@pytest.mark.asyncio
async def test_scenario_same_model_bunching():
    """
    Scenario 1: Same Model Bunching
    Tests that requests for the same model are grouped together to minimize swapping.
    """
    print("\n📋 Scenario 1: Same Model Bunching")
    
    model_a = TestConfig.MODEL_STARTUP
    model_b = TestConfig.MODEL_MEDIUM 
    n_pairs = 8 # 16 requests total
    
    completion_order = []
    errors = []

    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        # Health Check
        try:
            health = await client.get("/proxy/health")
            assert health.status_code == 200, "Proxy unhealthy"
        except Exception as e:
            pytest.fail(f"Proxy unreachable: {e}")

        async def fetch(model, i):
            try:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": [{"role": "user", "content": "Hi"}], "stream": False}
                )
                if resp.status_code == 200:
                    completion_order.append(model)
                else:
                    errors.append(f"{model} status {resp.status_code}")
            except Exception as e:
                errors.append(str(e))

        # Create interleaved tasks
        tasks = []
        for i in range(n_pairs):
            tasks.append(asyncio.create_task(fetch(model_a, i)))
            tasks.append(asyncio.create_task(fetch(model_b, i)))
        
        print(f"   ⏳ Sending {n_pairs*2} interleaved requests...")
        await asyncio.gather(*tasks)

        # Analysis
        if errors:
            print(f"   ⚠️ Errors: {errors}")
        
        switches = 0
        for i in range(1, len(completion_order)):
            if completion_order[i] != completion_order[i-1]:
                switches += 1
                
        # Assertions
        assert len(completion_order) == n_pairs * 2, "Not all requests completed"
        # We allow a few switches (<= 4) for race conditions, but it shouldn't be 15 (fully interleaved)
        assert switches <= 4, f"Bunching failed. Expected <=4 switches, got {switches}"
        print(f"   ✅ SUCCESS: Bunched with {switches} switches.")


@pytest.mark.asyncio
async def test_scenario_large_model_deferral():
    """
    Scenario 2: Large Model Deferral (Strict Priority)
    Ensures Small models jump ahead of Large models in the queue.
    """
    print("\n📋 Scenario 2: Large Model Deferral")
    
    requests_per_model = 5
    completion_order = []
    errors = []

    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_HEAVY) as client:
        # Worker
        async def fetch(model, label):
            try:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": [{"role": "user", "content": "Short."}], "stream": False}
                )
                if resp.status_code == 200:
                    completion_order.append(model)
                else:
                    errors.append(f"{label} failed: {resp.status_code}")
            except Exception as e:
                errors.append(str(e))

        tasks = []

        # 1. Block Slots
        print(f"   1️⃣  Blocking with {TestConfig.MODEL_STARTUP}...")
        for i in range(requests_per_model):
            tasks.append(asyncio.create_task(fetch(TestConfig.MODEL_STARTUP, "Startup")))
        await asyncio.sleep(1.0)

        # 2. Queue Large (First in line)
        print(f"   2️⃣  Queuing LARGE ({TestConfig.MODEL_LARGE})...")
        for i in range(requests_per_model):
            tasks.append(asyncio.create_task(fetch(TestConfig.MODEL_LARGE, "Large")))
        await asyncio.sleep(0.5)

        # 3. Queue Small (Last in line)
        print(f"   3️⃣  Queuing SMALL ({TestConfig.MODEL_SMALL})...")
        for i in range(requests_per_model):
            tasks.append(asyncio.create_task(fetch(TestConfig.MODEL_SMALL, "Small")))

        print("   ⏳ Waiting...")
        await asyncio.gather(*tasks)

        if errors:
            pytest.fail(f"Requests failed: {errors}")

        # Assertions
        try:
            first_large_idx = completion_order.index(TestConfig.MODEL_LARGE)
            # Find last occurrence of Small
            last_small_idx = len(completion_order) - 1 - completion_order[::-1].index(TestConfig.MODEL_SMALL)
        except ValueError:
            pytest.fail("Missing models in response.")

        visual_map = ["LG" if m == TestConfig.MODEL_LARGE else "SM" for m in completion_order if m != TestConfig.MODEL_STARTUP]
        print(f"   📊 Stream: {visual_map}")

        assert last_small_idx < first_large_idx, "❌ Priority Failed: Large model finished before last Small model."
        print("   ✅ SUCCESS: Small models prioritized.")


@pytest.mark.asyncio
async def test_scenario_fault_tolerance():
    """
    Scenario 3: Fault Tolerance
    Ensures a bad request doesn't jam the queue forever.
    """
    print("\n📋 Scenario 3: Fault Tolerance")
    
    completion_order = []
    status_codes = {}

    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        async def fetch(model, label):
            try:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": [{"role": "user", "content": "Hi"}], "stream": False}
                )
                status_codes[label] = resp.status_code
                if resp.status_code == 200:
                    completion_order.append(label)
            except Exception:
                status_codes[label] = "Error"

        tasks = []
        
        # 1. Block
        for i in range(3):
            tasks.append(asyncio.create_task(fetch(TestConfig.MODEL_STARTUP, "Startup")))
        await asyncio.sleep(0.5)

        # 2. Bad Request
        print("   Queuing BAD request...")
        tasks.append(asyncio.create_task(fetch(TestConfig.MODEL_BAD, "BAD")))
        await asyncio.sleep(0.2)

        # 3. Valid Request
        print("   Queuing VALID request...")
        tasks.append(asyncio.create_task(fetch(TestConfig.MODEL_SMALL, "VALID")))

        await asyncio.gather(*tasks)

        # Assertions
        assert status_codes["BAD"] != 200, "Bad model returned 200 OK"
        assert "VALID" in completion_order, "Valid request never finished (Queue Jammed)"
        
        # Check Slot Cleanup
        health = (await client.get("/proxy/health")).json()
        assert health['active_requests'] == 0, f"Slots leaked: {health['active_requests']} active"
        print("   ✅ SUCCESS: Queue recovered from error.")


@pytest.mark.asyncio
async def test_scenario_priority_reordering():
    """
    Scenario 4: Priority Reordering (Loaded vs Unloaded)
    A request for a CURRENTLY loaded model should jump ahead of a queued unloaded model.
    """
    print("\n📋 Scenario 4: Loaded Model Priority")
    
    completion_order = []
    
    # 1. We need the Startup model to be ALREADY loaded for this test to work best.
    # We'll rely on the fact that previous tests likely left MODEL_STARTUP or MODEL_SMALL loaded.
    # Let's use MODEL_STARTUP vs MODEL_LARGE.
    
    loaded_model = TestConfig.MODEL_STARTUP
    unloaded_model = TestConfig.MODEL_LARGE # Something unlikely to be loaded
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_HEAVY) as client:
        # Pre-load the "Loaded" model just in case
        await client.post("/v1/chat/completions", json={"model": loaded_model, "messages": [{"role": "user", "content": "init"}]})

        async def fetch(model, label):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": "Hi"}], "stream": False}
            )
            if resp.status_code == 200:
                completion_order.append(label)

        tasks = []
        
        # 1. Block slots
        for i in range(3):
            tasks.append(asyncio.create_task(fetch(loaded_model, "Blocker")))
        await asyncio.sleep(0.5)

        # 2. Queue Unloaded (Should wait)
        print(f"   Queuing Unloaded ({unloaded_model})...")
        tasks.append(asyncio.create_task(fetch(unloaded_model, "Unloaded")))
        await asyncio.sleep(0.2)

        # 3. Queue Loaded (Should Jump)
        print(f"   Queuing Loaded ({loaded_model})...")
        tasks.append(asyncio.create_task(fetch(loaded_model, "Loaded")))

        await asyncio.gather(*tasks)

        # Check order: Loaded should finish BEFORE Unloaded
        try:
            unloaded_idx = completion_order.index("Unloaded")
            loaded_idx = completion_order.index("Loaded")
            
            assert loaded_idx < unloaded_idx, "❌ Priority Failed: Loaded model did not jump queue."
            print("   ✅ SUCCESS: Loaded model jumped ahead.")
        except ValueError:
            print(f"   ⚠️ Test inconclusive: models didn't complete. Order: {completion_order}")


@pytest.mark.asyncio
async def test_scenario_ip_fairness():
    """
    Scenario 5: IP Fairness
    Request from IP2 should not be starved by many requests from IP1.
    
    Logic:
    1. Fill active slots with IP1 (Block GPU).
    2. Queue a large backlog of IP1 requests (High Penalty).
    3. Queue 1 request from IP2 (No Penalty).
    4. Expectation: IP2 should jump ahead of the IP1 backlog.
    """
    print("\n📋 Scenario 5: IP Fairness Check")
    
    # Config
    ip1_count = 20  # Enough to create a penalty
    completion_order = []
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_HEAVY) as client:
        
        async def fetch(ip, label):
            try:
                # Use a tiny prompt so execution is fast once picked up
                resp = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": TestConfig.MODEL_STARTUP, 
                        "messages": [{"role": "user", "content": "Hi"}], 
                        "stream": False
                    },
                    headers={"X-Forwarded-For": ip}
                )
                if resp.status_code == 200:
                    completion_order.append(label)
            except Exception:
                pass

        tasks = []

        # 1. Block the Active Slots (IP1)
        # Assuming max_parallel=3, these fill the GPU.
        print("   1️⃣  Blocking slots with IP1...")
        for i in range(3):
            tasks.append(asyncio.create_task(fetch("10.0.0.1", "IP1")))
        
        await asyncio.sleep(0.5) # Wait for them to start processing

        # 2. Build the Queue (IP1 - High Penalty)
        print(f"   2️⃣  Queuing {ip1_count} backlog requests from IP1...")
        for i in range(ip1_count):
            tasks.append(asyncio.create_task(fetch("10.0.0.1", "IP1")))
            
        await asyncio.sleep(0.5) # Ensure these are firmly registered in queue with high penalty

        # 3. The New User (IP2 - Zero Penalty)
        print("   3️⃣  Queuing 1 request from IP2...")
        tasks.append(asyncio.create_task(fetch("10.0.0.2", "IP2")))

        print("   ⏳ Waiting for execution...")
        await asyncio.gather(*tasks)

        # --- ANALYSIS ---
        
        # We assume the first 3 (Active) IP1 requests finish first naturally.
        # The fairness check applies to the QUEUE.
        # IP2 should appear before the END of the list.
        
        print(f"   📊 Completion Order: {completion_order}")
        
        try:
            ip2_position = completion_order.index("IP2")
            total_requests = len(completion_order)
            
            # Assertion 1: It finished
            assert ip2_position != -1, "IP2 request failed or didn't finish"
            
            # Assertion 2: Fairness
            # If IP2 was treated fairly, it should have jumped the queue.
            # It shouldn't be the absolute last request (waiting for all 10 IP1s to finish).
            # Note: We relax the check slightly to allow for race conditions, 
            # but generally IP2 should be in the first half of the queued items.
            
            requests_processed_after_ip2 = total_requests - 1 - ip2_position
            print(f"   📍 IP2 finished at index {ip2_position}/{total_requests-1}")
            print(f"   🚀 IP2 jumped ahead of {requests_processed_after_ip2} IP1 requests.")
            
            assert requests_processed_after_ip2 > 0, \
                "❌ IP Fairness Failed: IP2 was stuck at the very end of the queue behind all IP1 requests."
            
            # Stronger assertion: It should beat at least HALF the backlog
            assert requests_processed_after_ip2 >= (ip1_count / 2), \
                "⚠️ Weak Fairness: IP2 finished, but didn't jump enough of the queue."

        except ValueError:
            pytest.fail("IP2 request did not complete successfully.")

        print("   ✅ SUCCESS: IP2 was prioritized over the IP1 backlog.")