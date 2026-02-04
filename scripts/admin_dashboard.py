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
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    # Look for .env in script dir, parent dir, or current dir
    script_dir = Path(__file__).parent.parent
    env_file = script_dir / '.env'
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()  # Try to load from current directory
except ImportError:
    pass  # python-dotenv not installed, will use system env vars only

# For terminal formatting
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich.columns import Columns
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Warning: 'rich' library not found. Install with: pip install rich")
    print("Falling back to basic text output.\n")


class ProxyDashboard:
    def __init__(self, proxy_url: str, admin_key: Optional[str] = None, refresh_interval: int = 5):
        self.proxy_url = proxy_url.rstrip('/')
        self.admin_key = admin_key
        self.refresh_interval = refresh_interval
        self.console = Console() if RICH_AVAILABLE else None
        self.session = requests.Session()
        
        # Set admin key header if provided
        if self.admin_key:
            self.session.headers['X-Admin-Key'] = self.admin_key
    
    def fetch_health(self) -> Dict[str, Any]:
        """Fetch health endpoint data"""
        try:
            resp = self.session.get(f"{self.proxy_url}/proxy/health", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}
    
    def fetch_queue(self) -> Dict[str, Any]:
        """Fetch queue endpoint data"""
        try:
            resp = self.session.get(f"{self.proxy_url}/proxy/queue", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}
    
    def fetch_vram(self) -> Dict[str, Any]:
        """Fetch VRAM endpoint data"""
        try:
            resp = self.session.get(f"{self.proxy_url}/proxy/vram", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}
    
    def fetch_analytics(self, hours: int = 24, group_by: str = "model_name", limit: int = 10) -> Dict[str, Any]:
        """Fetch analytics endpoint data"""
        try:
            resp = self.session.get(
                f"{self.proxy_url}/proxy/analytics",
                params={"hours": hours, "group_by": group_by, "limit": limit},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}
    
    def create_health_panel(self, health_data: Dict[str, Any]) -> Any:
        """Create health status panel"""
        if "error" in health_data:
            return Panel(f"[red]Error: {health_data['error']}[/red]", title="🏥 Health Status", border_style="red")
        
        status = health_data.get('status', 'unknown')
        paused = health_data.get('paused', False)
        color = "green" if status == "healthy" and not paused else "yellow" if paused else "red"
        
        info_text = Text()
        info_text.append(f"Status: ", style="bold")
        info_text.append(f"{status.upper()}", style=f"bold {color}")
        if paused:
            info_text.append(" [PAUSED]", style="bold yellow")
        info_text.append(f"\nQueue Depth: {health_data.get('queue_depth', 0)}\n")
        info_text.append(f"Active Requests: {health_data.get('active_requests', 0)}/{health_data.get('max_parallel', 0)}\n")
        info_text.append(f"Total Requests: {health_data.get('stats', {}).get('total_requests', 0)}\n")
        info_text.append(f"Completed: {health_data.get('stats', {}).get('completed_requests', 0)}\n")
        info_text.append(f"Failed: {health_data.get('stats', {}).get('failed_requests', 0)}\n")
        info_text.append(f"Max Queue Depth: {health_data.get('stats', {}).get('queue_depth_max', 0)}\n")
        
        return Panel(info_text, title="🏥 Health Status", border_style=color)
    
    def create_vram_panel(self, vram_data: Dict[str, Any]) -> Any:
        """Create VRAM status panel"""
        if "error" in vram_data:
            return Panel(f"[red]Error: {vram_data['error']}[/red]", title="💾 VRAM Status", border_style="red")
        
        # VRAM endpoint returns 'models' dict and 'loaded_models' count
        loaded_models = vram_data.get('models', {})
        total_vram_gb = vram_data.get('total_vram_gb', 0)
        used_vram_gb = vram_data.get('used_vram_gb', total_vram_gb)
        free_vram_gb = vram_data.get('free_vram_gb', 0)
        
        info_text = Text()
        info_text.append(f"Total VRAM: {total_vram_gb:.1f} GB\n", style="bold")
        info_text.append(f"Used: {used_vram_gb:.1f} GB | Free: {free_vram_gb:.1f} GB\n")
        info_text.append(f"\nLoaded Models ({len(loaded_models)}):\n", style="bold cyan")
        
        for model, size_gb in loaded_models.items():
            info_text.append(f"  • {model}: {size_gb:.2f} GB\n")
        
        if not loaded_models:
            info_text.append("  [dim]No models loaded[/dim]\n")
        
        return Panel(info_text, title="💾 VRAM Status", border_style="blue")
    
    def create_queue_table(self, queue_data: Dict[str, Any]) -> Any:
        """Create queue status table"""
        if "error" in queue_data:
            return Panel(f"[red]Error: {queue_data['error']}[/red]", title="📋 Queue Status", border_style="red")
        
        table = Table(title=f"📋 Queue Status (Total: {queue_data.get('total_depth', 0)})", box=box.ROUNDED)
        table.add_column("Status", style="cyan", width=10)
        table.add_column("Request ID", style="magenta", width=25)
        table.add_column("Model", style="green", width=25)
        table.add_column("IP", style="yellow", width=15)
        table.add_column("Wait/Duration", style="red", width=12)
        table.add_column("Priority", style="blue", width=8)
        
        requests = queue_data.get('requests', [])
        
        # Sort: processing first, then by priority
        requests.sort(key=lambda x: (0 if x.get('status') == 'processing' else 1, x.get('priority', 999)))
        
        for req in requests[:20]:  # Limit to top 20
            status = req.get('status', 'unknown')
            status_icon = "🔄" if status == "processing" else "⏳"
            wait_time = req.get('wait_time', req.get('total_duration', 0))
            
            table.add_row(
                f"{status_icon} {status}",
                req.get('request_id', 'N/A'),
                req.get('model', 'N/A')[:23],
                req.get('ip', 'N/A'),
                f"{wait_time}s",
                str(req.get('priority', 'N/A'))
            )
        
        if not requests:
            table.add_row("[dim]No requests in queue[/dim]", "", "", "", "", "")
        
        return table
    
    def create_analytics_tables(self, analytics_data: Dict[str, Any]) -> List[Any]:
        """Create analytics tables"""
        tables = []
        
        if "error" in analytics_data:
            return [Panel(f"[red]Error: {analytics_data['error']}[/red]", title="📊 Analytics", border_style="red")]
        
        # Request count by model
        table = Table(title="📊 Requests by Model", box=box.SIMPLE)
        table.add_column("Model", style="cyan")
        table.add_column("Count", style="green", justify="right")
        table.add_column("Avg Duration", style="yellow", justify="right")
        
        model_counts = analytics_data.get('request_count_by_model', [])
        model_durations = {d['model_name']: d for d in analytics_data.get('average_duration_by_model', [])}
        
        for item in model_counts[:10]:
            model = item.get('model_name', 'N/A')
            count = item.get('request_count', 0)
            avg_dur = model_durations.get(model, {}).get('avg_duration', 0)
            table.add_row(model, str(count), f"{avg_dur:.2f}s")
        
        if not model_counts:
            table.add_row("[dim]No data[/dim]", "", "")
        
        tables.append(table)
        
        # Request count by IP
        table = Table(title="🌐 Top IPs", box=box.SIMPLE)
        table.add_column("IP Address", style="cyan")
        table.add_column("Requests", style="green", justify="right")
        
        for item in analytics_data.get('request_count_by_ip', [])[:10]:
            table.add_row(item.get('source_ip', 'N/A'), str(item.get('request_count', 0)))
        
        tables.append(table)
        
        # Error rate analysis
        table = Table(title="⚠️ Error Rates", box=box.SIMPLE)
        table.add_column("Group", style="cyan")
        table.add_column("Total", style="blue", justify="right")
        table.add_column("Errors", style="red", justify="right")
        table.add_column("Rate %", style="yellow", justify="right")
        
        for item in analytics_data.get('error_rate_analysis', [])[:10]:
            group = item.get('model_name', item.get('hour', 'N/A'))
            total = item.get('total_requests', 0)
            errors = item.get('error_count', 0)
            rate = item.get('error_rate', 0)
            table.add_row(str(group), str(total), str(errors), f"{rate:.1f}%")
        
        tables.append(table)
        
        # Priority score distribution
        table = Table(title="🎯 Priority Scores", box=box.SIMPLE)
        table.add_column("Group", style="cyan")
        table.add_column("Avg", style="green", justify="right")
        table.add_column("Min", style="blue", justify="right")
        table.add_column("Max", style="yellow", justify="right")
        
        for item in analytics_data.get('priority_score_distribution', [])[:10]:
            group = item.get('model_name', item.get('hour', 'N/A'))
            avg = item.get('avg_priority', 0)
            min_p = item.get('min_priority', 0)
            max_p = item.get('max_priority', 0)
            table.add_row(str(group), f"{avg:.1f}", str(min_p), str(max_p))
        
        tables.append(table)
        
        return tables
    
    def render_dashboard_rich(self, hours: int = 24):
        """Render dashboard using rich library (interactive)"""
        
        def generate_layout():
            # Fetch all data
            health_data = self.fetch_health()
            queue_data = self.fetch_queue()
            vram_data = self.fetch_vram()
            analytics_data = self.fetch_analytics(hours=hours)
            
            # Create layout
            layout = Layout()
            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="body"),
                Layout(name="footer", size=3)
            )
            
            # Header
            header_text = Text()
            header_text.append("🚀 Ollama Smart Proxy Dashboard", style="bold cyan")
            header_text.append(f" | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")
            header_text.append(f" | Refreshing every {self.refresh_interval}s", style="dim yellow")
            layout["header"].update(Panel(header_text, border_style="cyan"))
            
            # Body - split into sections
            layout["body"].split_row(
                Layout(name="left"),
                Layout(name="right")
            )
            
            # Left side - Status panels
            layout["left"].split_column(
                Layout(self.create_health_panel(health_data), size=12),
                Layout(self.create_vram_panel(vram_data), size=12),
                Layout(self.create_queue_table(queue_data))
            )
            
            # Right side - Analytics
            analytics_tables = self.create_analytics_tables(analytics_data)
            if analytics_tables and isinstance(analytics_tables, list):
                analytics_layout = Layout(name="analytics")
                if len(analytics_tables) >= 4:
                    analytics_layout.split_column(
                        Layout(analytics_tables[0]),
                        Layout(analytics_tables[1]),
                        Layout(analytics_tables[2]),
                        Layout(analytics_tables[3])
                    )
                else:
                    for table in analytics_tables:
                        analytics_layout.update(table)
                layout["right"].update(analytics_layout)
            else:
                # Show error panel if analytics failed
                error_panel = analytics_tables[0] if analytics_tables else Panel("[red]No analytics data[/red]", title="📊 Analytics")
                layout["right"].update(error_panel)
            
            # Footer
            footer_text = Text()
            footer_text.append("Press ", style="dim")
            footer_text.append("Ctrl+C", style="bold red")
            footer_text.append(" to exit | Data from last ", style="dim")
            footer_text.append(f"{hours} hours", style="bold yellow")
            layout["footer"].update(Panel(footer_text, border_style="dim"))
            
            return layout
        
        # Live updating dashboard
        try:
            with Live(generate_layout(), refresh_per_second=1/self.refresh_interval, console=self.console) as live:
                while True:
                    time.sleep(self.refresh_interval)
                    live.update(generate_layout())
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Dashboard stopped by user[/yellow]")
    
    def render_dashboard_basic(self, hours: int = 24):
        """Render dashboard using basic text (one-time snapshot)"""
        print("=" * 100)
        print(f"OLLAMA SMART PROXY DASHBOARD - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 100)
        
        # Health
        print("\n[HEALTH STATUS]")
        health_data = self.fetch_health()
        if "error" in health_data:
            print(f"  Error: {health_data['error']}")
        else:
            print(f"  Status: {health_data.get('status', 'unknown').upper()}")
            print(f"  Paused: {health_data.get('paused', False)}")
            print(f"  Queue Depth: {health_data.get('queue_depth', 0)}")
            print(f"  Active Requests: {health_data.get('active_requests', 0)}/{health_data.get('max_parallel', 0)}")
            print(f"  Total/Completed/Failed: {health_data.get('stats', {}).get('total_requests', 0)} / {health_data.get('stats', {}).get('completed_requests', 0)} / {health_data.get('stats', {}).get('failed_requests', 0)}")
        
        # VRAM
        print("\n[VRAM STATUS]")
        vram_data = self.fetch_vram()
        if "error" in vram_data:
            print(f"  Error: {vram_data['error']}")
        else:
            print(f"  Total/Used/Free: {vram_data.get('total_vram_gb', 0):.1f} GB / {vram_data.get('used_vram_gb', 0):.1f} GB / {vram_data.get('free_vram_gb', 0):.1f} GB")
            loaded_models = vram_data.get('models', {})
            if loaded_models:
                print(f"  Loaded Models: {list(loaded_models.keys())}")
            else:
                print(f"  Loaded Models: None")
        
        # Queue
        print("\n[QUEUE STATUS]")
        queue_data = self.fetch_queue()
        if "error" in queue_data:
            print(f"  Error: {queue_data['error']}")
        else:
            print(f"  Total Depth: {queue_data.get('total_depth', 0)}")
            for req in queue_data.get('requests', [])[:10]:
                status = req.get('status', 'unknown')
                print(f"    {status}: {req.get('request_id')} | {req.get('model')} | Priority: {req.get('priority')}")
        
        # Analytics
        print(f"\n[ANALYTICS - Last {hours} hours]")
        analytics_data = self.fetch_analytics(hours=hours)
        if "error" in analytics_data:
            print(f"  Error: {analytics_data['error']}")
        else:
            print("  Requests by Model:")
            for item in analytics_data.get('request_count_by_model', [])[:5]:
                print(f"    {item.get('model_name')}: {item.get('request_count')} requests")
            
            print("  Top IPs:")
            for item in analytics_data.get('request_count_by_ip', [])[:5]:
                print(f"    {item.get('source_ip')}: {item.get('request_count')} requests")
        
        print("\n" + "=" * 100)
    
    def run(self, hours: int = 24, once: bool = False):
        """Run the dashboard"""
        if RICH_AVAILABLE and not once:
            self.render_dashboard_rich(hours=hours)
        else:
            self.render_dashboard_basic(hours=hours)


def main():
    parser = argparse.ArgumentParser(
        description="Ollama Smart Proxy - Admin Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive dashboard (default)
  python admin_dashboard.py --url http://localhost:8003 --key YOUR_ADMIN_KEY
  
  # One-time snapshot (no rich library or --once flag)
  python admin_dashboard.py --url http://localhost:8003 --key YOUR_ADMIN_KEY --once
  
  # Custom refresh interval and analytics window
  python admin_dashboard.py --url http://localhost:8003 --key YOUR_ADMIN_KEY --refresh 10 --hours 48

Environment Variables:
  PROXY_URL - Proxy URL (default: http://localhost:8003)
  PROXY_ADMIN_KEY - Admin key for authentication
        """
    )
    
    parser.add_argument(
        "--url",
        default=os.getenv("PROXY_URL", "http://localhost:8003"),
        help="Proxy URL (default: http://localhost:8003 or PROXY_URL env var)"
    )
    
    parser.add_argument(
        "--key",
        default=os.getenv("PROXY_ADMIN_KEY"),
        help="Admin key for authentication (or PROXY_ADMIN_KEY env var)"
    )
    
    parser.add_argument(
        "--refresh",
        type=int,
        default=5,
        help="Refresh interval in seconds for live dashboard (default: 5)"
    )
    
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Analytics time window in hours (default: 24)"
    )
    
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (snapshot mode)"
    )
    
    args = parser.parse_args()
    
    if not args.key:
        print("Warning: No admin key provided. Admin endpoints may return 403.")
        print("Provide via --key argument or PROXY_ADMIN_KEY environment variable.\n")
    
    dashboard = ProxyDashboard(
        proxy_url=args.url,
        admin_key=args.key,
        refresh_interval=args.refresh
    )
    
    try:
        dashboard.run(hours=args.hours, once=args.once)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
