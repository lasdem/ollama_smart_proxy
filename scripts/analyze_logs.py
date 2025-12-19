#!/usr/bin/env python3
"""
Log Analyzer for Smart Proxy (JSON format)
Parses JSON logs and generates statistics
"""
import json
import sys
from typing import Dict, List, Any
from collections import defaultdict
from dataclasses import dataclass, asdict


@dataclass
class RequestStats:
    request_id: str
    model: str
    ip: str
    priority: int = None
    queue_depth: int = None
    wait_seconds: float = None
    duration_seconds: float = None
    vram_gb: float = None
    loaded: bool = None
    status: str = "unknown"  # queued, processing, completed, failed


class LogAnalyzer:
    def __init__(self, log_file: str):
        self.log_file = log_file
        self.requests: Dict[str, RequestStats] = {}
        self.total_requests = 0
        self.completed = 0
        self.failed = 0
        
    def parse_log(self):
        """Parse JSON log file and extract events"""
        with open(self.log_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    log_entry = json.loads(line)
                except json.JSONDecodeError:
                    # Skip non-JSON lines (startup messages, etc.)
                    continue
                
                # Only process proxy events (not uvicorn)
                if log_entry.get('logger') != 'proxy':
                    continue
                
                event = log_entry.get('event')
                request_id = log_entry.get('request_id')
                
                if not request_id:
                    continue
                
                # Initialize request if first time seeing it
                if request_id not in self.requests:
                    self.requests[request_id] = RequestStats(
                        request_id=request_id,
                        model=log_entry.get('model', ''),
                        ip=log_entry.get('ip', '')
                    )
                
                req = self.requests[request_id]
                
                # Update based on event type
                if event == 'request_queued':
                    self.total_requests += 1
                    req.queue_depth = log_entry.get('queue_depth')
                    req.status = 'queued'
                    
                elif event == 'request_processing':
                    req.priority = log_entry.get('priority')
                    req.vram_gb = log_entry.get('vram_gb')
                    req.loaded = log_entry.get('loaded')
                    req.wait_seconds = log_entry.get('wait_seconds')
                    req.status = 'processing'
                    
                elif event == 'request_completed':
                    req.duration_seconds = log_entry.get('duration_seconds')
                    req.status = 'completed'
                    self.completed += 1
                    
                elif event == 'request_failed':
                    req.status = 'failed'
                    self.failed += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Calculate statistics from parsed events"""
        stats = {
            'total_requests': self.total_requests,
            'completed': self.completed,
            'failed': self.failed,
            'models': defaultdict(lambda: {
                'count': 0,
                'avg_wait': 0.0,
                'avg_duration': 0.0,
                'avg_priority': 0.0
            })
        }
        
        # Per-model stats
        model_data = defaultdict(lambda: {'waits': [], 'durations': [], 'priorities': []})
        
        for req in self.requests.values():
            if req.status in ['completed', 'failed']:
                model = req.model
                stats['models'][model]['count'] += 1
                
                if req.wait_seconds is not None:
                    model_data[model]['waits'].append(req.wait_seconds)
                if req.duration_seconds is not None:
                    model_data[model]['durations'].append(req.duration_seconds)
                if req.priority is not None:
                    model_data[model]['priorities'].append(req.priority)
        
        # Calculate averages
        for model, data in model_data.items():
            if data['waits']:
                stats['models'][model]['avg_wait'] = sum(data['waits']) / len(data['waits'])
            if data['durations']:
                stats['models'][model]['avg_duration'] = sum(data['durations']) / len(data['durations'])
            if data['priorities']:
                stats['models'][model]['avg_priority'] = sum(data['priorities']) / len(data['priorities'])
        
        return dict(stats)
    
    def format_shell(self, stats: Dict[str, Any]):
        """Format stats as ASCII table for shell output"""
        print(f"\n{'='*60}")
        print(f"📊 Log Analysis Summary")
        print(f"{'='*60}")
        print(f"Total Requests: {stats['total_requests']}")
        print(f"Completed:      {stats['completed']}")
        print(f"Failed:         {stats['failed']}")
        
        if stats['models']:
            print(f"\n{'='*60}")
            print(f"Per-Model Statistics")
            print(f"{'='*60}")
            print(f"{'Model':<20} {'Count':>8} {'Avg Wait':>10} {'Avg Duration':>12} {'Avg Priority':>12}")
            print(f"{'-'*20} {'-'*8} {'-'*10} {'-'*12} {'-'*12}")
            
            for model, data in sorted(stats['models'].items()):
                print(f"{model:<20} {data['count']:>8} "
                      f"{data['avg_wait']:>10.2f}s {data['avg_duration']:>12.2f}s "
                      f"{data['avg_priority']:>12.1f}")
    
    def format_json(self, stats: Dict[str, Any]):
        """Format stats as JSON"""
        print(json.dumps(stats, indent=2))
    
    def format_markdown(self, stats: Dict[str, Any]):
        """Format stats as Markdown table"""
        print(f"# Log Analysis Summary\n")
        print(f"- **Total Requests**: {stats['total_requests']}")
        print(f"- **Completed**: {stats['completed']}")
        print(f"- **Failed**: {stats['failed']}\n")
        
        if stats['models']:
            print(f"## Per-Model Statistics\n")
            print(f"| Model | Count | Avg Wait (s) | Avg Duration (s) | Avg Priority |")
            print(f"|-------|------:|-------------:|-----------------:|-------------:|")
            
            for model, data in sorted(stats['models'].items()):
                print(f"| {model} | {data['count']} | "
                      f"{data['avg_wait']:.2f} | {data['avg_duration']:.2f} | "
                      f"{data['avg_priority']:.1f} |")


def analyze_log_file(log_file: str, output_format: str = "shell"):
    """
    Analyze log file and output statistics
    
    Args:
        log_file: Path to JSON log file
        output_format: Output format (shell, json, markdown)
    """
    analyzer = LogAnalyzer(log_file)
    analyzer.parse_log()
    stats = analyzer.get_stats()
    
    if output_format == "json":
        analyzer.format_json(stats)
    elif output_format == "markdown":
        analyzer.format_markdown(stats)
    else:
        analyzer.format_shell(stats)


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_logs.py <log_file> [format]")
        print("Formats: shell (default), json, markdown")
        sys.exit(1)
    
    log_file = sys.argv[1]
    output_format = sys.argv[2] if len(sys.argv) > 2 else "shell"
    
    analyze_log_file(log_file, output_format)


if __name__ == "__main__":
    main()
