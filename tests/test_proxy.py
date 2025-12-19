#!/usr/bin/env python3
"""Quick test script for smart_proxy_v2.py"""
import asyncio
import httpx

async def test_proxy():
    async with httpx.AsyncClient() as client:
        # Test health endpoint
        print("Testing /health...")
        response = await client.get("http://localhost:8003/health")
        print(f"Health: {response.json()}")
        
        # Test chat completion
        print("\nTesting /v1/chat/completions...")
        response = await client.post(
            "http://localhost:8003/v1/chat/completions",
            json={
                "model": "qwen2.5:7b",
                "messages": [{"role": "user", "content": "Say hello!"}],
                "stream": False
            },
            timeout=60.0
        )
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            print(f"Response: {response.json()}")

if __name__ == "__main__":
    asyncio.run(test_proxy())
