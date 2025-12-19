#!/usr/bin/env python3
"""
Log Analyzer for Smart Proxy
Parses logs and generates statistics
"""
import re
import json
from typing import Dict, List, Any
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class RequestEvent:
    request_id: str
    model: str
    ip: str
    event_type: str  # 'queued', 'processing', 'completed', 'error'
    timestamp: float = 0.0
    priority: int = None
    queue_depth: int = None
    wait_time: float = None
    processing_time: float = None
    vram_gb: float = None
    loaded: bool = None


class LogAnalyzer:
    def __init__(self, log_file: str):
        self.log_file = log_file
        self.events: List[RequestEvent] = []
        self.requests: Dict[str, Dict[str, Any]] = defaultdict(dict)
        
    def parse_log(self):
        """Parse log file and extract events"""
        # Regex patterns - updated to handle empty VRAM field
        queued_pattern = r'📨 Queued: \[(REQ\d+_[^\]]+)\] (\S+) from (\S+) \(queue_depth=(\d+)\)'
        # Processing pattern now handles: "priority=X, queue=Y, , loaded=Z" or "priority=X, queue=Y, VRAM: X.XGB, loaded=Z"
        processing_pattern = r'⚡ Processing: \[(REQ\d+_[^\]]+)\] (\S+) from (\S+) \(priority=(\d+), queue=(\d+), (?:VRAM: ([\d.]+)GB, |, )?loaded=(\w+), ip_queued=(\d+), ip_recent=(\d+), wait=(\d+)s\)'
        completed_pattern = r'✅ Completed: \[(REQ\d+_[^\]]+)\] (\S+) in ([\d.]+)s'
        error_pattern = r'❌ Error: \[(REQ\d+_[^\]]+)\] (\S+): (.+)'
        
        with open(self.log_file, 'r') as f:
            for line in f:
                # Queued events
                match = re.search(queued_pattern, line)
                if match:
                    req_id, model, ip, queue_depth = match.groups()
                    event = RequestEvent(
                        request_id=req_id,
                        model=model,
                        ip=ip,
                        event_type='queued',
                        queue_depth=int(queue_depth)
                    )
                    self.events.append(event)
                    self.requests[req_id]['queued'] = event
                    continue
                
                # Processing events
                match = re.search(processing_pattern, line)
                if match:
                    req_id, model, ip, priority, queue, vram, loaded, ip_queued, ip_recent, wait = match.groups()
                    event = RequestEvent(
                        request_id=req_id,
                        model=model,
                        ip=ip,
                        event_type='processing',
                        priority=int(priority),
                        queue_depth=int(queue),
                        wait_time=int(wait),
                        vram_gb=float(vram) if vram else None,
                        loaded=(loaded == 'True')
                    )
                    self.events.append(event)
                    self.requests[req_id]['processing'] = event
                    continue
                
                # Completed events
                match = re.search(completed_pattern, line)
                if match:
                    req_id, model, proc_time = match.groups()
                    event = RequestEvent(
                        request_id=req_id,
                        model=model,
                        ip='',
                        event_type='completed',
                        processing_time=float(proc_time)
                    )
                    self.events.append(event)
                    self.requests[req_id]['completed'] = event
                    continue
                
                # Error events
                match = re.search(error_pattern, line)
                if match:
                    req_id, model, error_msg = match.groups()
                    event = RequestEvent(
                        request_id=req_id,
                        model=model,
                        ip='',
                        event_type='error'
                    )
                    self.events.append(event)
                    self.requests[req_id]['error'] = event
    
    def calculate_statistics(self) -> Dict[str, Any]:
        """Calculate statistics from parsed events"""
        stats = {
            'total_requests': len(self.requests),
            'completed': 0,
            'failed': 0,
            'by_model': defaultdict(lambda: {
                'count': 0,
                'avg_wait_time': 0.0,
                'avg_processing_time': 0.0,
                'total_wait': 0.0,
                'total_processing': 0.0
            }),
            'priority_distribution': defaultdict(int),
            'model_bunching': [],
            'max_queue_depth': 0
        }
        
        for req_id, events in self.requests.items():
            # Count completions
            if 'completed' in events:
                stats['completed'] += 1
            elif 'error' in events:
                stats['failed'] += 1
            
            # Per-model stats
            if 'processing' in events:
                proc = events['processing']
                model = proc.model
                
                stats['by_model'][model]['count'] += 1
                
                if proc.wait_time is not None:
                    stats['by_model'][model]['total_wait'] += proc.wait_time
                
                if 'completed' in events:
                    proc_time = events['completed'].processing_time
                    stats['by_model'][model]['total_processing'] += proc_time
                
                # Priority distribution
                if proc.priority is not None:
                    priority_bucket = (proc.priority // 100) * 100
                    stats['priority_distribution'][priority_bucket] += 1
            
            # Max queue depth
            if 'queued' in events:
                stats['max_queue_depth'] = max(stats['max_queue_depth'], events['queued'].queue_depth)
        
        # Calculate averages
        for model, data in stats['by_model'].items():
            if data['count'] > 0:
                data['avg_wait_time'] = data['total_wait'] / data['count']
                data['avg_processing_time'] = data['total_processing'] / data['count']
        
        # Calculate model bunching (consecutive same-model processing)
        bunches = []
        current_bunch = []
        prev_model = None
        
        for event in self.events:
            if event.event_type == 'processing':
                if event.model == prev_model:
                    current_bunch.append(event.request_id)
                else:
                    if len(current_bunch) > 1:
                        bunches.append({
                            'model': prev_model,
                            'count': len(current_bunch),
                            'requests': current_bunch
                        })
                    current_bunch = [event.request_id]
                    prev_model = event.model
        
        if len(current_bunch) > 1:
            bunches.append({
                'model': prev_model,
                'count': len(current_bunch),
                'requests': current_bunch
            })
        
        stats['model_bunching'] = bunches
        
        return stats
    
    def format_shell_output(self, stats: Dict[str, Any]):
        """Format statistics as ASCII table for shell"""
        print("\n" + "="*80)
        print("📊 STATISTICS SUMMARY")
        print("="*80)
        
        # Overall stats
        print(f"\nTotal Requests:     {stats['total_requests']}")
        print(f"Completed:          {stats['completed']} ✅")
        print(f"Failed:             {stats['failed']} ❌")
        print(f"Max Queue Depth:    {stats['max_queue_depth']}")
        
        # Per-model stats
        if stats['by_model']:
            print("\n" + "-"*80)
            print("📈 PER-MODEL STATISTICS")
            print("-"*80)
            print(f"{'Model':<25} {'Count':>8} {'Avg Wait (s)':>15} {'Avg Process (s)':>18}")
            print("-"*80)
            
            for model, data in sorted(stats['by_model'].items()):
                print(f"{model:<25} {data['count']:>8} {data['avg_wait_time']:>15.2f} {data['avg_processing_time']:>18.2f}")
        
        # Priority distribution
        if stats['priority_distribution']:
            print("\n" + "-"*80)
            print("🎯 PRIORITY DISTRIBUTION")
            print("-"*80)
            print(f"{'Priority Range':<20} {'Count':>10}")
            print("-"*80)
            
            for priority, count in sorted(stats['priority_distribution'].items()):
                print(f"{priority}-{priority+99:<20} {count:>10}")
        
        # Model bunching
        if stats['model_bunching']:
            print("\n" + "-"*80)
            print("🔗 MODEL BUNCHING (Consecutive same-model requests)")
            print("-"*80)
            print(f"{'Model':<25} {'Bunch Size':>12}")
            print("-"*80)
            
            for bunch in stats['model_bunching']:
                print(f"{bunch['model']:<25} {bunch['count']:>12}")
            
            total_bunches = len(stats['model_bunching'])
            avg_bunch_size = sum(b['count'] for b in stats['model_bunching']) / total_bunches if total_bunches > 0 else 0
            print(f"\nTotal bunches: {total_bunches}, Average size: {avg_bunch_size:.1f}")
        
        print("\n" + "="*80)
    
    def format_json_output(self, stats: Dict[str, Any]) -> str:
        """Format statistics as JSON"""
        # Convert defdicts to regular dicts for JSON serialization
        json_stats = {
            'total_requests': stats['total_requests'],
            'completed': stats['completed'],
            'failed': stats['failed'],
            'max_queue_depth': stats['max_queue_depth'],
            'by_model': dict(stats['by_model']),
            'priority_distribution': dict(stats['priority_distribution']),
            'model_bunching': stats['model_bunching']
        }
        
        return json.dumps(json_stats, indent=2)
    
    def format_markdown_output(self, stats: Dict[str, Any]) -> str:
        """Format statistics as Markdown"""
        md = []
        md.append("# Smart Proxy Test Results\n")
        md.append("## Summary\n")
        md.append(f"- **Total Requests**: {stats['total_requests']}")
        md.append(f"- **Completed**: {stats['completed']} ✅")
        md.append(f"- **Failed**: {stats['failed']} ❌")
        md.append(f"- **Max Queue Depth**: {stats['max_queue_depth']}\n")
        
        if stats['by_model']:
            md.append("## Per-Model Statistics\n")
            md.append("| Model | Count | Avg Wait (s) | Avg Process (s) |")
            md.append("|-------|-------|--------------|-----------------|")
            
            for model, data in sorted(stats['by_model'].items()):
                md.append(f"| {model} | {data['count']} | {data['avg_wait_time']:.2f} | {data['avg_processing_time']:.2f} |")
        
        if stats['priority_distribution']:
            md.append("\n## Priority Distribution\n")
            md.append("| Priority Range | Count |")
            md.append("|----------------|-------|")
            
            for priority, count in sorted(stats['priority_distribution'].items()):
                md.append(f"| {priority}-{priority+99} | {count} |")
        
        if stats['model_bunching']:
            md.append("\n## Model Bunching\n")
            md.append("| Model | Bunch Size |")
            md.append("|-------|------------|")
            
            for bunch in stats['model_bunching']:
                md.append(f"| {bunch['model']} | {bunch['count']} |")
            
            total_bunches = len(stats['model_bunching'])
            avg_bunch_size = sum(b['count'] for b in stats['model_bunching']) / total_bunches if total_bunches > 0 else 0
            md.append(f"\n- Total bunches: {total_bunches}")
            md.append(f"- Average bunch size: {avg_bunch_size:.1f}")
        
        return "\n".join(md)


def analyze_log_file(log_file: str, output_format: str = "shell"):
    """Main entry point for log analysis"""
    analyzer = LogAnalyzer(log_file)
    analyzer.parse_log()
    stats = analyzer.calculate_statistics()
    
    if output_format == "json":
        print(analyzer.format_json_output(stats))
    elif output_format == "markdown":
        print(analyzer.format_markdown_output(stats))
    else:  # shell (default)
        analyzer.format_shell_output(stats)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python analyze_logs.py <log_file> [format]")
        print("  format: shell (default), json, markdown")
        sys.exit(1)
    
    log_file = sys.argv[1]
    output_format = sys.argv[2] if len(sys.argv) > 2 else "shell"
    
    analyze_log_file(log_file, output_format)
