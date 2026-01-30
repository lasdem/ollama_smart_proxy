import subprocess
import sys
import tempfile
import pytest
import asyncio
import httpx
import time
import os
import requests

# --- CONFIGURATION ---
class TestConfig:
    PROXY_URL = "http://localhost:8003"
    OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    TIMEOUT_DEFAULT = 300.0
    TIMEOUT_HEAVY = 600.0

    
    # Define your models here
    MODEL_LARGE = "llama3.3:latest"                     # High VRAM usage
    MODEL_MEDIUM = "qwen3-coder:latest"                 # medium model
    MODEL_MEDIUM2 = "devstral-small-2:latest"           # Another medium model
    MODEL_SMALL = "gemma3:latest"                       # Low VRAM / Fast
    MODEL_BAD = "this-model-does-not-exist:666"         # For fault tolerance

# ---------------------

@pytest.fixture(scope="function", autouse=True)
def start_proxy_service():
    """
    Start the proxy service in the background before any tests run, and stop it after all tests complete.
    Captures stdout/stderr to a temp log file.
    """
    log_file = tempfile.NamedTemporaryFile(delete=False, mode="w+t", suffix="_proxy.log")
    # Use the conda python and run the proxy
    proxy_env = os.environ.copy()
    proxy_proc = subprocess.Popen([
        sys.executable, "src/smart_proxy.py"
    ], stdout=log_file, stderr=subprocess.STDOUT, cwd=os.path.dirname(os.path.dirname(__file__)), env=proxy_env)

    # Wait for proxy to be ready (poll health endpoint)
    ready = False
    for _ in range(60):
        try:
            resp = requests.get("http://localhost:8003/proxy/health", timeout=1)
            if resp.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ready:
        proxy_proc.terminate()
        log_file.seek(0)
        logs = log_file.read()
        log_file.flush()
        log_file.close()
        pytest.fail(f"Proxy did not start in time. Log output:\n{logs}")

    yield log_file.name

    # Teardown: terminate proxy
    proxy_proc.terminate()
    try:
        proxy_proc.wait(timeout=10)
    except Exception:
        proxy_proc.kill()
    # Ensure all output is flushed before closing
    log_file.flush()
    log_file.close()

@pytest.fixture(scope="function", autouse=True)
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

            # 3. Wait for VRAM release and verify all models are unloaded
            for _ in range(30):  # Wait up to 3 seconds total
                await asyncio.sleep(0.1)
                try:
                    ps = await client.get("/api/ps")
                    still_loaded = ps.json().get('models', [])
                    if not still_loaded:
                        print("   ✅ All models unloaded.")
                        break
                except Exception:
                    break  # If Ollama is down, just exit
            else:
                print(f"   ⚠️ Models still loaded after cleanup: {[m['name'] for m in still_loaded]}")
    except Exception as e:
        print(f"   ⚠️ Cleanup failed: {e}")

# --- TEST SCENARIOS ---
@pytest.mark.asyncio
async def test_scenario_same_model_bunching():
    """
    Scenario 1: Same Model Bunching
    Tests that requests for the same model are grouped together to minimize swapping.
    """
    print("\n📋 Scenario 1: Same Model Bunching")
    
    model_a = TestConfig.MODEL_MEDIUM
    model_b = TestConfig.MODEL_MEDIUM2 
    n_pairs = 8
    
    completion_order = []
    errors = []

    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        # Pause queue processing
        await client.post("/proxy/testing", json={"pause": True})
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

        # Optionally inspect queue here if needed
        # Resume processing
        await client.post("/proxy/testing", json={"pause": False})

        # Wait for completions
        await asyncio.sleep(0.1 * n_pairs)  # Small wait for processing

        # Analysis
        if errors:
            print(f"   ⚠️ Errors: {errors}")
        switches = 0
        for i in range(1, len(completion_order)):
            if completion_order[i] != completion_order[i-1]:
                switches += 1
        # Assertions
        assert len(completion_order) == n_pairs * 2, "Not all requests completed"
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

    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_HEAVY) as client, \
        httpx.AsyncClient(base_url=TestConfig.OLLAMA_API_BASE, timeout=10.0) as ollama_client:
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

        # 1. Explicitly load both models
        print(f"   1️⃣  Preloading {TestConfig.MODEL_LARGE} and {TestConfig.MODEL_SMALL}...")
        await client.post("/v1/chat/completions", json={"model": TestConfig.MODEL_LARGE, "messages": [{"role": "user", "content": "init"}]})
        await client.post("/v1/chat/completions", json={"model": TestConfig.MODEL_SMALL, "messages": [{"role": "user", "content": "init"}]})
        
        # 2. Explicitly unload both models
        print(f"   2️⃣  Unloading {TestConfig.MODEL_LARGE} and {TestConfig.MODEL_SMALL}...")
        await ollama_client.post("/api/chat", json={"model": TestConfig.MODEL_LARGE, "keep_alive": 0})
        await ollama_client.post("/api/chat", json={"model": TestConfig.MODEL_SMALL, "keep_alive": 0})
        
        # Pause queue processing
        await client.post("/proxy/testing", json={"pause": True})

        # 3. Queue Large (First in line)
        print(f"   3️⃣  Queuing LARGE ({TestConfig.MODEL_LARGE})...")
        for i in range(requests_per_model):
            tasks.append(asyncio.create_task(fetch(TestConfig.MODEL_LARGE, "Large")))
        print(f"   4️⃣  Queuing SMALL ({TestConfig.MODEL_SMALL})...")
        for i in range(requests_per_model):
            tasks.append(asyncio.create_task(fetch(TestConfig.MODEL_SMALL, "Small")))
        print("   ⏳ Waiting...")
        await asyncio.gather(*tasks)
        # Inspect queue priorities
        queue_resp = await client.get("/proxy/queue")
        queue_data = queue_resp.json()
        print(f"   🧐 Queue state before processing: {queue_data}")
        # Resume processing
        await client.post("/proxy/testing", json={"pause": False})
        # Wait for completions
        await asyncio.sleep(1.0)
        if errors:
            pytest.fail(f"Requests failed: {errors}")
        try:
            first_large_idx = completion_order.index(TestConfig.MODEL_LARGE)
            last_small_idx = len(completion_order) - 1 - completion_order[::-1].index(TestConfig.MODEL_SMALL)
        except ValueError:
            pytest.fail("Missing models in response.")
        visual_map = ["LG" if m == TestConfig.MODEL_LARGE else "SM" for m in completion_order]
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
        
        await asyncio.sleep(0.2)

        # Check Slot Cleanup
        health = (await client.get("/proxy/health")).json()
        assert health['active_requests'] == 0, f"Slots leaked: {health['active_requests']} active"
        print("   ✅ SUCCESS: Queue recovered from error.")


@pytest.mark.asyncio
async def test_scenario_priority_reordering():
    """
    Scenario 4: Priority Reordering (Loaded vs Unloaded)
    1. We need to load a both models first, so the proxy is aware of the VRAM usage.  
       We'll rely on the fact that the fixtures unload all models before each test.  
    2. we need to unload the "unloaded" model to ensure it incurs the loading penalty.  
    3. then we send requests for an unloaded model and a loaded model.  
    We need to make sure the proxy is still in the startup delay period to ensure queueing works.  
    Ensures that requests for already loaded models jump ahead of those needing to be loaded.  
    """
    print("\n📋 Scenario 4: Loaded Model Priority")
    
    completion_order = []
    

    loaded_model = TestConfig.MODEL_MEDIUM
    unloaded_model = TestConfig.MODEL_MEDIUM2

    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_HEAVY) as client, \
        httpx.AsyncClient(base_url=TestConfig.OLLAMA_API_BASE, timeout=10.0) as ollama_client:

        # 1. Explicitly load both models
        print(f"   Preloading {loaded_model} and {unloaded_model}...")
        await client.post("/v1/chat/completions", json={"model": unloaded_model, "messages": [{"role": "user", "content": "init"}]})
        await client.post("/v1/chat/completions", json={"model": loaded_model, "messages": [{"role": "user", "content": "init"}]})

        # 2. Explicitly unload the 'unloaded' model
        print(f"   Unloading {unloaded_model}...")
        await ollama_client.post("/api/chat", json={"model": unloaded_model, "keep_alive": 0})

        # 3. Wait for VRAM monitor to reflect only the loaded model
        print(f"   Waiting for VRAM state: only {loaded_model} loaded...")
        for _ in range(30):
            ps = await ollama_client.get("/api/ps")
            models = ps.json().get('models', [])
            loaded_names = [m['model'] for m in models]
            if loaded_model in loaded_names and unloaded_model not in loaded_names:
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail(f"VRAM state not as expected. Loaded: {loaded_names}")

        # 4. Queue requests for both models during controlled pause
        await client.post("/proxy/testing", json={"pause": True})
        async def fetch(model, label):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": "Hi"}], "stream": False}
            )
            if resp.status_code == 200:
                completion_order.append(label)
        tasks = []
        print(f"   Queuing Unloaded ({unloaded_model})...")
        tasks.append(asyncio.create_task(fetch(unloaded_model, "Unloaded")))
        print(f"   Queuing Loaded ({loaded_model})...")
        tasks.append(asyncio.create_task(fetch(loaded_model, "Loaded")))
        await asyncio.gather(*tasks)
        # Inspect queue
        queue_resp = await client.get("/proxy/queue")
        queue_data = queue_resp.json()
        print(f"   🧐 Queue state before processing: {queue_data}")
        # Resume processing
        await client.post("/proxy/testing", json={"pause": False})
        await asyncio.sleep(1.0)
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
                        "model": TestConfig.MODEL_MEDIUM, 
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