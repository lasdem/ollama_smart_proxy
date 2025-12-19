"""
VRAM Monitor - Uses Ollama API /api/ps to track model VRAM usage
No external dependencies - fully self-contained
"""
import asyncio
import httpx
import time
from typing import Dict, Optional, List
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class ModelInfo:
    """Information about a loaded model from /api/ps"""
    name: str
    size_bytes: int
    size_vram: int  # Actual VRAM usage in bytes
    digest: str
    parameter_size: str
    quantization: str
    context_length: int
    expires_at: str


class VRAMMonitor:
    """
    Monitors Ollama /api/ps endpoint to track VRAM usage.
    Maintains history of VRAM requirements for scheduling.
    """
    
    def __init__(self, ollama_base_url: str, poll_interval: int = 5):
        self.ollama_base_url = ollama_base_url.rstrip('/')
        self.poll_interval = poll_interval
        
        # Currently loaded models from /api/ps
        self.currently_loaded: Dict[str, ModelInfo] = {}
        
        # Historical VRAM usage by model name (for estimates when model not loaded)
        # Key: model_name, Value: list of observed VRAM sizes (keep last 10)
        self.vram_history: Dict[str, List[int]] = defaultdict(list)
        
        # Last successful poll time
        self.last_poll_time = 0
        
        # Running flag
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    def start(self):
        """Start background monitoring task"""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._monitor_loop())
            print(f"📡 VRAM Monitor started (polling every {self.poll_interval}s)")
    
    def stop(self):
        """Stop background monitoring"""
        self._running = False
        if self._task:
            self._task.cancel()
    
    async def _monitor_loop(self):
        """Background loop that polls /api/ps"""
        while self._running:
            try:
                await self._poll_ollama_ps()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️  VRAM Monitor error: {e}")
                await asyncio.sleep(self.poll_interval)
    
    async def _poll_ollama_ps(self):
        """Poll /api/ps and update state"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.ollama_base_url}/api/ps")
                
                if response.status_code != 200:
                    print(f"⚠️  /api/ps returned {response.status_code}")
                    return
                
                data = response.json()
                models = data.get("models", [])
                
                # Clear currently loaded
                self.currently_loaded.clear()
                
                # Update with current models
                for model_data in models:
                    model_name = model_data.get("model", "unknown")
                    size_vram = model_data.get("size_vram", 0)
                    
                    # Create ModelInfo
                    details = model_data.get("details", {})
                    model_info = ModelInfo(
                        name=model_name,
                        size_bytes=model_data.get("size", 0),
                        size_vram=size_vram,
                        digest=model_data.get("digest", ""),
                        parameter_size=details.get("parameter_size", ""),
                        quantization=details.get("quantization_level", ""),
                        context_length=model_data.get("context_length", 0),
                        expires_at=model_data.get("expires_at", "")
                    )
                    
                    self.currently_loaded[model_name] = model_info
                    
                    # Update history (keep last 10 observations)
                    if size_vram > 0:
                        self.vram_history[model_name].append(size_vram)
                        if len(self.vram_history[model_name]) > 10:
                            self.vram_history[model_name].pop(0)
                
                self.last_poll_time = time.time()
                
                if models:
                    total_vram_mb = sum(m.size_vram for m in self.currently_loaded.values()) / (1024 * 1024)
                    model_names = ", ".join(self.currently_loaded.keys())
                    print(f"🔍 Loaded: {model_names} | Total VRAM: {total_vram_mb:.1f} MB")
                
        except Exception as e:
            print(f"❌ Failed to poll /api/ps: {e}")
    
    def get_vram_for_model(self, model_name: str) -> Optional[int]:
        """
        Get VRAM requirement for a model in bytes.
        
        Returns:
            - Actual VRAM if model is currently loaded
            - Average from history if we've seen it before
            - Estimated VRAM based on parameter size if available
            - None if no information available
        """
        # Check if currently loaded
        if model_name in self.currently_loaded:
            return self.currently_loaded[model_name].size_vram
        
        # Check historical average
        if model_name in self.vram_history and self.vram_history[model_name]:
            return int(sum(self.vram_history[model_name]) / len(self.vram_history[model_name]))
        
        # No data - will need to estimate or wait for first load
        return None
    
    def estimate_vram_from_params(self, param_size: str, quant: str = "Q4_K_M", context_length: int = 32768) -> int:
        """
        Estimate VRAM based on parameter size and quantization.
        
        Rough estimates based on typical models:
        - Q4_K_M: ~0.55 GB per billion params + context overhead
        - Q8_0: ~1.0 GB per billion params + context overhead
        - F16/BF16: ~2.0 GB per billion params + context overhead
        """
        # Parse parameter size (e.g., "70.6B", "8.2B", "4.3B")
        import re
        match = re.match(r'([0-9.]+)(B|M)', param_size)
        if not match:
            return 8 * 1024 * 1024 * 1024  # Default 8GB in bytes
        
        value = float(match.group(1))
        unit = match.group(2)
        
        if unit == 'M':
            params_billions = value / 1000
        else:  # B
            params_billions = value
        
        # Quantization multipliers (GB per billion params)
        quant_multipliers = {
            'Q4_K_M': 0.55,
            'Q4_0': 0.50,
            'Q8_0': 1.0,
            'F16': 2.0,
            'BF16': 2.0,
            'MXFP4': 0.55,
        }
        
        multiplier = quant_multipliers.get(quant, 0.55)  # Default to Q4_K_M
        
        # Base VRAM
        base_vram_gb = params_billions * multiplier
        
        # Context overhead (rough estimate: ~0.1 GB per billion params per 32K context)
        context_multiplier = context_length / 32768
        context_overhead_gb = params_billions * 0.1 * context_multiplier
        
        total_gb = base_vram_gb + context_overhead_gb
        
        return int(total_gb * 1024 * 1024 * 1024)  # Convert to bytes
    
    def can_fit_parallel(self, model_name: str, total_vram_bytes: int) -> bool:
        """
        Check if a model can fit alongside currently loaded models.
        
        Args:
            model_name: Name of model to check
            total_vram_bytes: Total available VRAM in bytes
        
        Returns:
            True if model can fit, False otherwise
        """
        if not self.currently_loaded:
            return True
        
        model_vram = self.get_vram_for_model(model_name)
        if model_vram is None:
            # Unknown model - conservatively assume it won't fit
            return False
        
        currently_used = sum(m.size_vram for m in self.currently_loaded.values())
        
        return (currently_used + model_vram) <= total_vram_bytes
    
    def get_currently_loaded_models(self) -> List[str]:
        """Get list of currently loaded model names"""
        return list(self.currently_loaded.keys())
    
    def get_total_vram_used(self) -> int:
        """Get total VRAM currently in use (bytes)"""
        return sum(m.size_vram for m in self.currently_loaded.values())
    
    def get_stats(self) -> dict:
        """Get monitoring statistics"""
        return {
            "loaded_models": len(self.currently_loaded),
            "total_vram_used_mb": self.get_total_vram_used() / (1024 * 1024),
            "models": {
                name: {
                    "vram_mb": info.size_vram / (1024 * 1024),
                    "params": info.parameter_size,
                    "quant": info.quantization,
                    "context": info.context_length
                }
                for name, info in self.currently_loaded.items()
            },
            "historical_models": len(self.vram_history),
            "last_poll_seconds_ago": int(time.time() - self.last_poll_time) if self.last_poll_time > 0 else None
        }


# Example usage and testing
if __name__ == "__main__":
    async def test_monitor():
        # Test with local ollama
        monitor = VRAMMonitor("http://localhost:11434", poll_interval=2)
        monitor.start()
        
        # Let it poll a few times
        await asyncio.sleep(10)
        
        # Check stats
        stats = monitor.get_stats()
        print(f"\nMonitor Stats: {stats}")
        
        # Test VRAM lookup
        for model_name in monitor.get_currently_loaded_models():
            vram = monitor.get_vram_for_model(model_name)
            print(f"{model_name}: {vram / (1024*1024):.1f} MB VRAM")
        
        # Test estimation
        estimated = monitor.estimate_vram_from_params("70.6B", "Q4_K_M", 131072)
        print(f"\nEstimated 70B Q4_K_M @ 131K ctx: {estimated / (1024*1024*1024):.1f} GB")
        
        monitor.stop()
    
    asyncio.run(test_monitor())
