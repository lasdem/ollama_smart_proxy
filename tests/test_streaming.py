"""
Test Streaming and Non-Streaming Requests
Tests the proxy's handling of stream parameter for different Ollama endpoints.
"""
import subprocess
import sys
import tempfile
import pytest
import asyncio
import httpx
import time
import os
import requests
import json

# --- CONFIGURATION ---
class TestConfig:
    PROXY_URL = "http://localhost:8003"
    OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE") or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    TIMEOUT_DEFAULT = 60.0
    
    # Test model - use a small, fast model
    MODEL = os.getenv("TEST_MODEL", "gemma3:latest")

# ---------------------

@pytest.fixture(scope="function", autouse=True)
def start_proxy_service():
    """
    Start the proxy service in the background before tests run, and stop it after tests complete.
    """
    log_file = tempfile.NamedTemporaryFile(delete=False, mode="w+t", suffix="_proxy.log")
    proxy_env = os.environ.copy()
    proxy_proc = subprocess.Popen([
        sys.executable, "src/smart_proxy.py"
    ], stdout=log_file, stderr=subprocess.STDOUT, cwd=os.path.dirname(os.path.dirname(__file__)), env=proxy_env)

    # Wait for proxy to be ready
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
    log_file.flush()
    log_file.close()

@pytest.fixture(scope="function", autouse=True)
async def unload_all_models():
    """Helper to force unload all models from VRAM via Ollama API"""
    try:
        async with httpx.AsyncClient(base_url=TestConfig.OLLAMA_API_BASE, timeout=5.0) as client:
            try:
                ps = await client.get("/api/ps")
                active_models = ps.json().get('models', [])
            except Exception:
                return

            if not active_models:
                return

            print(f"   🧹 Cleanup: Unloading {len(active_models)} models...")
            for m in active_models:
                await client.post("/api/chat", json={
                    "model": m['name'],
                    "keep_alive": 0
                })

            # Wait for VRAM release
            for _ in range(30):
                await asyncio.sleep(0.1)
                try:
                    ps = await client.get("/api/ps")
                    still_loaded = ps.json().get('models', [])
                    if not still_loaded:
                        print("   ✅ All models unloaded.")
                        break
                except Exception:
                    break
    except Exception as e:
        print(f"   ⚠️ Cleanup failed: {e}")


# --- TEST SCENARIOS ---

@pytest.mark.asyncio
async def test_api_chat_streaming_explicit_true():
    """
    Test /api/chat with stream=true explicitly set.
    Should return streaming response in NDJSON format.
    """
    print("\n📋 Test: /api/chat with stream=true (explicit)")
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        # Make streaming request
        async with client.stream(
            "POST",
            "/api/chat",
            json={
                "model": TestConfig.MODEL,
                "messages": [{"role": "user", "content": "Say 'hello' in one word."}],
                "stream": True
            }
        ) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            
            # Check response headers
            content_type = response.headers.get("content-type", "")
            assert "application/x-ndjson" in content_type or "application/json" in content_type, \
                f"Expected NDJSON content type, got: {content_type}"
            
            # Collect chunks
            chunks = []
            full_content = ""
            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        chunk_data = json.loads(line)
                        chunks.append(chunk_data)
                        if "message" in chunk_data and "content" in chunk_data["message"]:
                            full_content += chunk_data["message"]["content"]
                    except json.JSONDecodeError:
                        pytest.fail(f"Invalid JSON in stream: {line}")
            
            # Verify we got multiple chunks
            assert len(chunks) > 1, f"Expected multiple chunks, got {len(chunks)}"
            
            # Verify final chunk has done=true
            assert chunks[-1].get("done") == True, "Final chunk should have done=true"
            
            # Verify we got some content
            assert len(full_content) > 0, "No content received in stream"
            
            print(f"   ✅ Received {len(chunks)} chunks with content: '{full_content.strip()}'")


@pytest.mark.asyncio
async def test_api_chat_non_streaming_explicit_false():
    """
    Test /api/chat with stream=false explicitly set.
    Should return single JSON response.
    """
    print("\n📋 Test: /api/chat with stream=false (explicit)")
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        response = await client.post(
            "/api/chat",
            json={
                "model": TestConfig.MODEL,
                "messages": [{"role": "user", "content": "Say 'hello' in one word."}],
                "stream": False
            }
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        # Verify it's JSON
        data = response.json()
        
        # Verify structure matches Ollama chat format
        assert "model" in data, "Response missing 'model' field"
        assert "message" in data, "Response missing 'message' field"
        assert "done" in data, "Response missing 'done' field"
        assert data["done"] == True, "done should be true"
        
        # Verify message structure
        assert "role" in data["message"], "Message missing 'role' field"
        assert "content" in data["message"], "Message missing 'content' field"
        assert data["message"]["role"] == "assistant", "Role should be 'assistant'"
        
        content = data["message"]["content"]
        assert len(content) > 0, "Content should not be empty"
        
        print(f"   ✅ Received single response with content: '{content.strip()}'")


@pytest.mark.asyncio
async def test_api_chat_default_streaming():
    """
    Test /api/chat without stream parameter.
    Should default to stream=true (Ollama's default behavior).
    """
    print("\n📋 Test: /api/chat with no stream parameter (should default to streaming)")
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        # Make request without stream parameter
        async with client.stream(
            "POST",
            "/api/chat",
            json={
                "model": TestConfig.MODEL,
                "messages": [{"role": "user", "content": "Say 'hello' in one word."}]
                # Note: NO stream parameter
            }
        ) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            
            # Check it's streaming
            content_type = response.headers.get("content-type", "")
            assert "application/x-ndjson" in content_type or "application/json" in content_type, \
                f"Expected streaming content type, got: {content_type}"
            
            # Collect chunks
            chunks = []
            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        chunk_data = json.loads(line)
                        chunks.append(chunk_data)
                    except json.JSONDecodeError:
                        pytest.fail(f"Invalid JSON in stream: {line}")
            
            # Verify we got multiple chunks (streaming behavior)
            assert len(chunks) > 1, f"Expected streaming (multiple chunks), got {len(chunks)} - default should be stream=true"
            
            print(f"   ✅ Default behavior is streaming: received {len(chunks)} chunks")


@pytest.mark.asyncio
async def test_api_generate_streaming():
    """
    Test /api/generate with stream=true.
    Should return streaming response in NDJSON format.
    """
    print("\n📋 Test: /api/generate with stream=true")
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        async with client.stream(
            "POST",
            "/api/generate",
            json={
                "model": TestConfig.MODEL,
                "prompt": "Say hello in one word.",
                "stream": True
            }
        ) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            
            # Collect chunks
            chunks = []
            full_response = ""
            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        chunk_data = json.loads(line)
                        chunks.append(chunk_data)
                        if "response" in chunk_data:
                            full_response += chunk_data["response"]
                    except json.JSONDecodeError:
                        pytest.fail(f"Invalid JSON in stream: {line}")
            
            # Verify streaming
            assert len(chunks) > 1, f"Expected multiple chunks, got {len(chunks)}"
            assert chunks[-1].get("done") == True, "Final chunk should have done=true"
            assert len(full_response) > 0, "No response content received"
            
            print(f"   ✅ Received {len(chunks)} chunks with response: '{full_response.strip()}'")


@pytest.mark.asyncio
async def test_api_generate_non_streaming():
    """
    Test /api/generate with stream=false.
    Should return single JSON response.
    """
    print("\n📋 Test: /api/generate with stream=false")
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        response = await client.post(
            "/api/generate",
            json={
                "model": TestConfig.MODEL,
                "prompt": "Say hello in one word.",
                "stream": False
            }
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # Verify Ollama generate format
        assert "model" in data, "Response missing 'model' field"
        assert "response" in data, "Response missing 'response' field"
        assert "done" in data, "Response missing 'done' field"
        assert data["done"] == True, "done should be true"
        
        response_text = data["response"]
        assert len(response_text) > 0, "Response should not be empty"
        
        print(f"   ✅ Received single response: '{response_text.strip()}'")


@pytest.mark.asyncio
async def test_openai_chat_streaming():
    """
    Test /v1/chat/completions with stream=true.
    Should return SSE (Server-Sent Events) format.
    """
    print("\n📋 Test: /v1/chat/completions with stream=true")
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": TestConfig.MODEL,
                "messages": [{"role": "user", "content": "Say 'hello' in one word."}],
                "stream": True
            }
        ) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            
            # Check for SSE content type
            content_type = response.headers.get("content-type", "")
            # May be text/event-stream or similar
            
            chunks = []
            async for line in response.aiter_lines():
                line = line.strip()
                if line.startswith("data: "):
                    data_str = line[6:]  # Remove "data: " prefix
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(data_str)
                        chunks.append(chunk_data)
                    except json.JSONDecodeError:
                        # Skip invalid JSON
                        pass
            
            # Verify streaming
            assert len(chunks) > 0, "Expected streaming chunks"
            
            print(f"   ✅ Received {len(chunks)} SSE chunks")


@pytest.mark.asyncio
async def test_openai_chat_non_streaming():
    """
    Test /v1/chat/completions with stream=false.
    Should return OpenAI-format JSON response.
    """
    print("\n📋 Test: /v1/chat/completions with stream=false")
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": TestConfig.MODEL,
                "messages": [{"role": "user", "content": "Say 'hello' in one word."}],
                "stream": False
            }
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        
        # Verify OpenAI format
        assert "choices" in data, "Response missing 'choices' field"
        assert len(data["choices"]) > 0, "Choices should not be empty"
        assert "message" in data["choices"][0], "Choice missing 'message' field"
        assert "content" in data["choices"][0]["message"], "Message missing 'content' field"
        
        content = data["choices"][0]["message"]["content"]
        assert len(content) > 0, "Content should not be empty"
        
        print(f"   ✅ Received OpenAI format response: '{content.strip()}'")


@pytest.mark.asyncio
async def test_mixed_streaming_concurrent():
    """
    Test concurrent requests with mixed streaming settings.
    Ensures the proxy handles both streaming and non-streaming requests simultaneously.
    """
    print("\n📋 Test: Concurrent mixed streaming/non-streaming requests")
    
    async with httpx.AsyncClient(base_url=TestConfig.PROXY_URL, timeout=TestConfig.TIMEOUT_DEFAULT) as client:
        results = {"streaming": 0, "non_streaming": 0, "errors": []}
        
        async def streaming_request():
            try:
                async with client.stream(
                    "POST",
                    "/api/chat",
                    json={
                        "model": TestConfig.MODEL,
                        "messages": [{"role": "user", "content": "Count to 3."}],
                        "stream": True
                    }
                ) as response:
                    chunks = []
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                chunks.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                    if len(chunks) > 1:
                        results["streaming"] += 1
            except Exception as e:
                results["errors"].append(f"Streaming error: {e}")
        
        async def non_streaming_request():
            try:
                response = await client.post(
                    "/api/chat",
                    json={
                        "model": TestConfig.MODEL,
                        "messages": [{"role": "user", "content": "Say hi."}],
                        "stream": False
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    if "message" in data and data.get("done"):
                        results["non_streaming"] += 1
            except Exception as e:
                results["errors"].append(f"Non-streaming error: {e}")
        
        # Create mixed tasks
        tasks = []
        for _ in range(3):
            tasks.append(asyncio.create_task(streaming_request()))
            tasks.append(asyncio.create_task(non_streaming_request()))
        
        await asyncio.gather(*tasks)
        
        # Verify all completed successfully
        assert len(results["errors"]) == 0, f"Errors occurred: {results['errors']}"
        assert results["streaming"] == 3, f"Expected 3 streaming requests, got {results['streaming']}"
        assert results["non_streaming"] == 3, f"Expected 3 non-streaming requests, got {results['non_streaming']}"
        
        print(f"   ✅ Successfully handled {results['streaming']} streaming + {results['non_streaming']} non-streaming concurrent requests")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
