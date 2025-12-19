"""
VRAM Cache Utilities for Smart Proxy
Parses ollama_details.cache and provides VRAM-aware scheduling
"""
import os
import re
from typing import Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ModelVRAMInfo:
    """VRAM information for a model"""
    model_id: str
    params: str
    quant: str
    max_context: int
    disk_size_mb: int
    vram_mb: int
    processor: str
    used_context: int
    
    @property
    def is_gpu_only(self) -> bool:
        return "100% GPU" in self.processor or "GPU" in self.processor


class VRAMCache:
    """Manages VRAM cache data from ollama_details.cache"""
    
    def __init__(self, cache_path: str = None):
        if cache_path is None:
            cache_path = os.path.expanduser(
                "~/ws/ollama/ollama_admin_tools/ollama_details.cache"
            )
        self.cache_path = cache_path
        self.cache: Dict[str, ModelVRAMInfo] = {}
        self._load_cache()
    
    def _parse_size_to_mb(self, size_str: str) -> int:
        """Convert size strings like '32 GB', '555 MB' to MB"""
        match = re.match(r'([0-9.]+)\s*(GB|MB)', size_str)
        if not match:
            return 0
        
        value = float(match.group(1))
        unit = match.group(2)
        
        if unit == 'GB':
            return int(value * 1024)
        else:  # MB
            return int(value)
    
    def _load_cache(self):
        """Load cache file and parse entries"""
        if not os.path.exists(self.cache_path):
            print(f"Warning: VRAM cache not found at {self.cache_path}")
            return
        
        with open(self.cache_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # Format: model_id:params:quant:max_ctx:disk:vram:processor:used_ctx
                parts = line.split(':')
                if len(parts) < 8:
                    continue
                
                model_id = parts[0]
                params = parts[1]
                quant = parts[2]
                max_context = int(parts[3])
                disk = parts[4]
                vram = parts[5]
                processor = parts[6]
                used_context = int(parts[7])
                
                info = ModelVRAMInfo(
                    model_id=model_id,
                    params=params,
                    quant=quant,
                    max_context=max_context,
                    disk_size_mb=self._parse_size_to_mb(disk),
                    vram_mb=self._parse_size_to_mb(vram),
                    processor=processor,
                    used_context=used_context
                )
                
                self.cache[model_id] = info
        
        print(f"Loaded VRAM data for {len(self.cache)} model configurations")
    
    def get_vram_for_model_id(self, model_id: str) -> Optional[int]:
        """Get VRAM requirement in MB for a model ID"""
        if model_id in self.cache:
            return self.cache[model_id].vram_mb
        return None
    
    def estimate_vram_from_params(self, params_str: str, context_window: int = 32768) -> int:
        """
        Estimate VRAM based on parameter count when cache miss
        
        Rough formula based on your data:
        - Base VRAM = params_gb * quantization_multiplier
        - Context overhead = context_window / 1024 * 0.5 MB per 1K tokens
        """
        # Parse parameter count (e.g., "70.6B" -> 70.6)
        match = re.match(r'([0-9.]+)(B|M)', params_str)
        if not match:
            return 8000  # Default 8GB if unknown
        
        value = float(match.group(1))
        unit = match.group(2)
        
        if unit == 'M':
            params_gb = value / 1000
        else:  # B
            params_gb = value
        
        # Quantization estimates (Q4_K_M is most common)
        # Q4_K_M ≈ 0.5-0.6 GB per billion params
        # Q8_0 ≈ 1.0 GB per billion params
        # Full precision ≈ 2.0 GB per billion params
        base_vram_gb = params_gb * 0.55  # Conservative Q4 estimate
        
        # Context window overhead (very rough)
        context_overhead_gb = (context_window / 32768) * (params_gb * 0.1)
        
        total_gb = base_vram_gb + context_overhead_gb
        return int(total_gb * 1024)  # Convert to MB
    
    def find_best_match(self, model_name: str) -> Optional[ModelVRAMInfo]:
        """
        Try to find VRAM info for a model name
        
        Handles cases like:
        - "llama3.3:latest" -> find ID that matches
        - "qwen2.5-coder:32b" -> find matching model
        """
        # First try direct ID lookup (unlikely but possible)
        if model_name in self.cache:
            return self.cache[model_name]
        
        # TODO: We need to map model names to IDs
        # This requires calling 'ollama list' or maintaining a name->ID mapping
        # For now, return None and rely on estimation
        return None
    
    def reload(self):
        """Reload cache from disk (call periodically to pick up new models)"""
        self.cache.clear()
        self._load_cache()


# Example usage
if __name__ == "__main__":
    cache = VRAMCache()
    
    # Test lookups
    test_ids = [
        "2514812443b0",  # llama3.3:latest (70.6B)
        "5f8672eff6ca",  # llama3.2:latest (3.2B)
        "6c42a05bf34a",  # qwen2.5-coder:32b
    ]
    
    for model_id in test_ids:
        vram = cache.get_vram_for_model_id(model_id)
        if vram:
            info = cache.cache[model_id]
            print(f"{model_id}: {info.params} {info.quant} @ {info.max_context} ctx = {vram} MB VRAM")
    
    # Test estimation
    print("\nEstimations:")
    print(f"70B model @ 32K ctx: {cache.estimate_vram_from_params('70.6B', 32768)} MB")
    print(f"8B model @ 100K ctx: {cache.estimate_vram_from_params('8.2B', 102400)} MB")
