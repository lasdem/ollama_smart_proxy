#!/bin/bash
# Test script to verify model name normalization

echo "=== Testing Model Name Normalization ==="

cd ~/ws/python/litellm_smart_proxy

# Test with Python
.conda/bin/python << 'EOF'
from vram_monitor import VRAMMonitor
import asyncio

async def test():
    monitor = VRAMMonitor("http://gpuserver1.neterra.skrill.net:8002", poll_interval=2)
    monitor.start()
    
    # Wait for first poll
    await asyncio.sleep(3)
    
    # Test lookups
    print("\n=== Testing VRAM Lookups ===")
    
    # Test with :latest suffix
    vram1 = monitor.get_vram_for_model("gemma3:latest")
    print(f"gemma3:latest -> {vram1/(1024*1024) if vram1 else 'Not found'} MB")
    
    # Test without :latest (should still work)
    vram2 = monitor.get_vram_for_model("gemma3")
    print(f"gemma3 -> {vram2/(1024*1024) if vram2 else 'Not found'} MB")
    
    # Show what's actually loaded
    print(f"\nLoaded models: {list(monitor.currently_loaded.keys())}")
    
    monitor.stop()

asyncio.run(test())
EOF
