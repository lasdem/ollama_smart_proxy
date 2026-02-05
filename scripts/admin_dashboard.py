#!/usr/bin/env python3
"""
Ollama Smart Proxy - Admin Dashboard
Interactive terminal dashboard for monitoring proxy status and analytics
"""
import os
import sys
import requests
import time
import argparse
import threading
import hashlib
import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

# Load .env file
try:
    from dotenv import load_dotenv
    script_dir = Path(__file__).parent.parent
    env_file = script_dir / '.env'
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()
except ImportError:
    pass

# Import Rich
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Warning: 'rich' library not found. Install with: pip install rich")
    print("Falling back to basic text output.\n")


class DashboardModel:
    """Thread-safe model to hold dashboard state"""
    def __init__(self):
        self._lock = threading.Lock()
        self._data = None
        self._last_update_ts = 0
        self._data_hash = ""

    def update(self, health, queue, vram, analytics):
        """Update model with new data"""
        new_data = {
            "health": health,
            "queue": queue,
            "vram": vram,
            "analytics": analytics,
            "timestamp": datetime.now()
        }
        
        # Create a simple hash to detect changes efficiently
        # We allow 'timestamp' to change without triggering a hash change
        # so the UI only repaints if ACTUAL data changes
        compare_obj = {k: v for k, v in new_data.items() if k != 'timestamp'}
        new_hash = hashlib.md5(json.dumps(compare_obj, sort_keys=True, default=str).encode()).hexdigest()

        with self._lock:
            if new_hash != self._data_hash:
                self._data = new_data
                self._data_hash = new_hash
                self._last_update_ts = time.time()
                return True # Data changed
            return False # Data identical

    def get_data(self):
        """Get current snapshot"""
        with self._lock:
            return self._data

class ProxyDashboard:
    def __init__(self, proxy_url: str, admin_key: Optional[str] = None, refresh_interval: int = 5):
        self.proxy_url = proxy_url.rstrip('/')
        self.admin_key = admin_key
        self.refresh_interval = refresh_interval
        self.console = Console() if RICH_AVAILABLE else None
        self.session = requests.Session()
        
        # State Management
        self.model = DashboardModel()
        self.running = False
        
        # Pre-calculated Layout (Stability Fix)
        self.layout = None 
        
        if self.admin_key:
            self.session.headers['X-Admin-Key'] = self.admin_key
    
    # --- Network Methods ---
    def fetch_json(self, endpoint: str, params: dict = None) -> Dict:
        try:
            resp = self.session.get(f"{self.proxy_url}/proxy/{endpoint}", params=params, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def _background_worker(self, hours: int):
        """Worker thread: Fetches data -> Updates Model"""
        while self.running:
            try:
                # Fetch all data sequentially (blocking this thread only)
                h_data = self.fetch_json("health")
                q_data = self.fetch_json("queue")
                v_data = self.fetch_json("vram")
                a_data = self.fetch_json("analytics", params={"hours": hours, "limit": 10})
                
                # Update model
                self.model.update(h_data, q_data, v_data, a_data)
                
            except Exception:
                pass # Squelch network errors in worker
            
            # Sleep in small increments to allow fast shutdown
            for _ in range(self.refresh_interval * 5): 
                if not self.running: return
                time.sleep(0.2)

    # --- UI Component Generators ---
    def _make_header(self, timestamp=None):
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right")
        
        title = Text("🚀 Ollama Smart Proxy", style="bold cyan")
        
        if timestamp:
            meta = Text(f"{timestamp.strftime('%H:%M:%S')} | {self.refresh_interval}s refresh", style="dim")
        else:
            meta = Text("Connecting...", style="dim yellow")
            
        grid.add_row(title, meta)
        return Panel(grid, style="cyan", box=box.ROUNDED)

    def _make_health(self, data):
        if not data or "error" in data: return Panel(f"[red]{data.get('error','No Data')}[/red]", title="Health")
        
        status = data.get('status', 'unknown')
        color = "green" if status == "healthy" and not data.get('paused') else "red"
        
        txt = Text()
        txt.append(f"{status.upper()}", style=f"bold {color}")
        if data.get('paused'): txt.append(" [PAUSED]", style="bold yellow")
        
        # Stats Grid
        stats_grid = Table.grid(expand=True)
        stats_grid.add_column(style="dim"); stats_grid.add_column(justify="right")
        stats_grid.add_row("Active:", f"{data.get('active_requests',0)}/{data.get('max_parallel',0)}")
        stats_grid.add_row("Queue:", str(data.get('queue_depth',0)))
        stats_grid.add_row("Total:", str(data.get('stats',{}).get('total_requests',0)))
        
        # Container Grid (Stacks Text on top of Stats)
        container = Table.grid(expand=True)
        container.add_row(txt)
        container.add_row(stats_grid)
        
        return Panel(container, title="🏥 Health", border_style=color)

    def _make_vram(self, data):
        if not data or "error" in data: return Panel("N/A", title="VRAM")
        
        total = data.get('total_vram_used_mb', 0)
        models = data.get('models', {})
        
        t = Table(show_header=False, box=None, expand=True)
        t.add_column("Model"); t.add_column("Size", justify="right")
        
        for m, info in list(models.items())[:4]: # Limit to 4 lines to prevent jitter
            sz = info.get('vram_mb',0) if isinstance(info, dict) else 0
            t.add_row(m[:20], f"{sz/1024:.1f}GB")
        
        # Container Grid
        container = Table.grid(expand=True)
        container.add_row(Text(f"Total: {total/1024:.1f} GB Used", style="bold"))
        container.add_row(t)
            
        return Panel(container, title="💾 VRAM", border_style="blue")

    def _make_queue(self, data):
        if not data or "error" in data: return Panel("Error", title="Queue")
        
        t = Table(box=None, expand=True, show_header=True, padding=(0,1))
        t.add_column("St", width=2)
        t.add_column("Model")
        t.add_column("IP", style="dim")
        t.add_column("Wait", justify="right")
        
        reqs = data.get('requests', [])
        reqs.sort(key=lambda x: (0 if x.get('status')=='processing' else 1, x.get('priority',999)))
        
        for r in reqs[:25]:
            icon = "⚡" if r.get('status') == 'processing' else "⏳"
            dur = r.get('wait_time', r.get('total_duration',0))
            t.add_row(icon, r.get('model','?')[:20], r.get('ip','?')[:15], f"{dur}s")
            
        if not reqs:
            return Panel(Text("Queue Empty", justify="center", style="dim"), title=f"📋 Queue ({data.get('total_depth',0)})")
            
        return Panel(t, title=f"📋 Queue ({data.get('total_depth',0)})")

    def _make_top_models(self, data):
        t = Table(title="Top Models", box=box.SIMPLE, expand=True)
        t.add_column("Name"); t.add_column("Reqs", justify="right")
        for x in data.get('request_count_by_model', [])[:5]:
            t.add_row(x['model'], str(x['request_count']))
        return t

    def _make_model_errors(self, data):
        t = Table(title="Errors by Model", box=box.SIMPLE, expand=True)
        t.add_column("Name"); t.add_column("%", justify="right")
        for x in data.get('error_rate_analysis', [])[:5]:
             t.add_row(str(x.get('group', '?'))[:40], f"{x.get('error_rate_percent',0):.1f}%")
        return t

    def _make_model_perf(self, data):
        t = Table(title="Avg Wait/Proc (Model)", box=box.SIMPLE, expand=True)
        t.add_column("Name")
        t.add_column("Wait (s)", justify="right")
        t.add_column("Proc (s)", justify="right")
        for x in data.get('perf_by_model', [])[:5]:
            w = x.get('avg_wait_seconds', 0)
            p = x.get('avg_processing_seconds', 0)
            t.add_row(str(x.get('group', '?'))[:15], f"{w:.1f}", f"{p:.1f}")
        return t

    def _make_top_ips(self, data):
        t = Table(title="Top IPs", box=box.SIMPLE, expand=True)
        t.add_column("IP"); t.add_column("Reqs", justify="right")
        for x in data.get('request_count_by_ip', [])[:5]:
            t.add_row(x.get('ip_address','?'), str(x.get('request_count',0)))
        return t

    def _make_ip_errors(self, data):
        t = Table(title="Errors by IP", box=box.SIMPLE, expand=True)
        t.add_column("IP"); t.add_column("%", justify="right")
        for x in data.get('error_rate_by_ip', [])[:5]:
            t.add_row(str(x.get('group', '?')), f"{x.get('error_rate_percent',0):.1f}%")
        return t

    def _make_ip_perf(self, data):
        t = Table(title="Avg Wait/Proc (IP)", box=box.SIMPLE, expand=True)
        t.add_column("IP")
        t.add_column("Wait (s)", justify="right")
        t.add_column("Proc (s)", justify="right")
        for x in data.get('perf_by_ip', [])[:5]:
            w = x.get('avg_wait_seconds', 0)
            p = x.get('avg_processing_seconds', 0)
            t.add_row(str(x.get('group', '?')), f"{w:.1f}", f"{p:.1f}")
        return t

    def _init_layout(self):
        """Initialize the static layout tree once"""
        self.layout = Layout()
        
        # Top Header (Fixed height 3)
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3)
        )
        
        # Body split Left/Middle/Right
        self.layout["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="middle", ratio=1),
            Layout(name="right", ratio=1)
        )
        
        # Left side: Fixed slots for Health, VRAM, Queue
        self.layout["left"].split_column(
            Layout(name="health", size=8),
            Layout(name="vram", ratio=1),
            Layout(name="queue", ratio=3)
        )

        # Middle side (Model Analytics)
        self.layout["middle"].split_column(
            Layout(name="m1", ratio=1),
            Layout(name="m2", ratio=1),
            Layout(name="m3", ratio=1)
        )
        
        # Right side (IP Analytics)
        self.layout["right"].split_column(
            Layout(name="r1", ratio=1),
            Layout(name="r2", ratio=1),
            Layout(name="r3", ratio=1)
        )
        
        # Set initial content
        self.layout["header"].update(self._make_header())
        self.layout["footer"].update(Panel("Ctrl+C to Exit", style="dim"))

    def render_rich(self, hours: int):
        self._init_layout()
        self.running = True
        
        # Start Worker
        t = threading.Thread(target=self._background_worker, args=(hours,))
        t.daemon = True
        t.start()
        
        # Wait for first bit of data
        with self.console.status("[bold green]Fetching data..."):
            for _ in range(20):
                if self.model.get_data(): break
                time.sleep(0.1)

        last_render_hash = ""

        try:
            # Refresh UI at 10fps (smooth), but content only changes when model changes
            with Live(self.layout, console=self.console, screen=True, refresh_per_second=10) as live:
                while True:
                    data = self.model.get_data()
                    
                    if data:
                        # Only update Layout renderables if data changed
                        # We use the hash from the model to check idempotency
                        if self.model._data_hash != last_render_hash:
                            last_render_hash = self.model._data_hash
                            
                            # Header
                            self.layout["header"].update(self._make_header(data['timestamp']))
                            
                            # Left Column
                            self.layout["health"].update(self._make_health(data['health']))
                            self.layout["vram"].update(self._make_vram(data['vram']))
                            self.layout["queue"].update(self._make_queue(data['queue']))
                            
                            # Middle Column (Model Analytics)
                            self.layout["m1"].update(self._make_top_models(data['analytics']))
                            self.layout["m2"].update(self._make_model_errors(data['analytics']))
                            self.layout["m3"].update(self._make_model_perf(data['analytics']))

                            # Right Column (IP Analytics)
                            self.layout["r1"].update(self._make_top_ips(data['analytics']))
                            self.layout["r2"].update(self._make_ip_errors(data['analytics']))
                            self.layout["r3"].update(self._make_ip_perf(data['analytics']))
                    
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            t.join(timeout=1)

    def run(self, hours: int, once: bool):
        if not RICH_AVAILABLE or once:
            # Fallback to simple print
            print("Basic Snapshot Mode...")
            self.fetch_json("health") # warm up
            print("Done.")
        else:
            self.render_rich(hours)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("PROXY_URL", "http://localhost:8003"))
    parser.add_argument("--key", default=os.getenv("PROXY_ADMIN_KEY"))
    parser.add_argument("--refresh", type=int, default=5)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    
    dash = ProxyDashboard(args.url, args.key, args.refresh)
    try:
        dash.run(args.hours, args.once)
    except Exception as e:
        print(e)

if __name__ == "__main__":
    main()