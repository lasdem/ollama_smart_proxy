# Admin Dashboard

Interactive terminal dashboard for monitoring Ollama Smart Proxy status and analytics.

## Features

- **Live Dashboard**: Real-time updating view with rich formatting
- **Health Monitoring**: Current proxy status, queue depth, request counts
- **VRAM Status**: See which models are loaded and memory usage
- **Queue Visualization**: View all queued and processing requests with priorities
- **Analytics**: Historical data including:
  - Request counts by model and IP
  - Average durations
  - Priority score distributions
  - Error rates
  - Model bunching detection

## Installation

### On Admin Laptop

```bash
# Install dependencies
pip install -r requirements-dashboard.txt

# Or install individually
pip install requests rich python-dotenv
```

**Note**: The dashboard automatically loads configuration from `.env` file if present in the project root.

## Usage

### Interactive Dashboard (Recommended)

```bash
# Using command-line arguments
python admin_dashboard.py --url http://your-proxy:8003 --key YOUR_ADMIN_KEY

# Using environment variables
export PROXY_URL=http://your-proxy:8003
export PROXY_ADMIN_KEY=your_secret_key
python admin_dashboard.py

# Custom refresh interval (10 seconds) and analytics window (48 hours)
python admin_dashboard.py --refresh 10 --hours 48
```

### Snapshot Mode (One-time)

```bash
# Run once and exit (useful for scripts or when rich is not available)
python admin_dashboard.py --once

# Can also be used without rich library installed
python admin_dashboard.py --url http://your-proxy:8003 --key YOUR_ADMIN_KEY --once
```

## Authentication

The dashboard requires admin authentication to access analytics and protected endpoints. Provide the admin key via:

1. **Command-line argument**: `--key YOUR_ADMIN_KEY`
2. **Environment variable**: `export PROXY_ADMIN_KEY=your_secret_key`
3. **.env file**: Add `PROXY_ADMIN_KEY=your_secret_key` to `.env` in project root (automatically loaded)

The same admin key configured in the proxy's `.env` file as `PROXY_ADMIN_KEY`.

**Tip**: If running from the project directory, the dashboard will automatically load `PROXY_ADMIN_KEY` and `PROXY_URL` from the `.env` file, so you can simply run:
```bash
python scripts/admin_dashboard.py
```

## Command-Line Options

```
--url URL          Proxy URL (default: http://localhost:8003)
--key KEY          Admin key for authentication
--refresh SECONDS  Refresh interval for live dashboard (default: 5)
--hours HOURS      Analytics time window in hours (default: 24)
--once             Run once and exit (snapshot mode)
```

## Display Modes

### Rich Interactive Mode (Default)
- Requires `rich` library
- Live updating dashboard
- Color-coded status indicators
- Organized panels and tables
- Optimized for 1080p fullscreen terminal

### Basic Text Mode
- Fallback when `rich` is not available
- One-time snapshot
- Plain text output
- Can be used in scripts or logs

## Keyboard Controls

- **Ctrl+C**: Exit the dashboard

## Examples

### Monitor production proxy
```bash
python admin_dashboard.py \
  --url https://proxy.example.com:8003 \
  --key $(cat ~/.proxy_admin_key) \
  --refresh 3 \
  --hours 168  # 1 week
```

### Quick status check
```bash
python admin_dashboard.py --once
```

### Local development monitoring
```bash
export PROXY_URL=http://localhost:8003
export PROXY_ADMIN_KEY=dev_key_123
python admin_dashboard.py --refresh 1
```

## Terminal Setup

For best results with the interactive dashboard:

- **Terminal Size**: 1920x1080 (1080p fullscreen)
- **Font**: Monospace font recommended
- **Color Support**: 256-color or truecolor terminal
- **Terminals Tested**: iTerm2, Windows Terminal, GNOME Terminal, Konsole

## Troubleshooting

### "rich library not found"
```bash
pip install rich
```

### "403 Forbidden" errors
- Ensure admin key is correct
- Check proxy's `PROXY_ADMIN_KEY` and `ADMIN_IPS` settings
- Try authenticating via `/proxy/auth` first

### Connection errors
- Verify proxy URL and port
- Check firewall rules
- Ensure proxy is running (`curl http://proxy:8003/proxy/health`)

## Data Refresh

The dashboard fetches data from these endpoints:

- `/proxy/health` - System health and stats
- `/proxy/queue` - Current queue status
- `/proxy/vram` - VRAM and loaded models
- `/proxy/analytics` - Historical analytics data

All analytics endpoints require admin authentication.
