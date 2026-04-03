#!/bin/bash
# Example: Using the Analytics API and Dashboard

# =============================================================================
# 1. ANALYTICS API ENDPOINT EXAMPLES
# =============================================================================

echo "=== Analytics API Examples ==="
echo ""

echo "1. Get analytics for last 24 hours (default):"
curl -s -H "X-Admin-Key: $PROXY_ADMIN_KEY" \
  "$OLLAMA_HOST/proxy/analytics" | jq '.'

echo ""
echo "2. Get analytics for last 48 hours, grouped by model:"
curl -s -H "X-Admin-Key: $PROXY_ADMIN_KEY" \
  "$OLLAMA_HOST/proxy/analytics?hours=48&group_by=model_name" | jq '.request_count_by_model'

echo ""
echo "3. Error rates grouped by hour:"
curl -s -H "X-Admin-Key: $PROXY_ADMIN_KEY" \
  "$OLLAMA_HOST/proxy/analytics?hours=24&group_by=hour" | jq '.error_rate_analysis'

echo ""
echo "4. Get top 20 IPs by request count:"
curl -s -H "X-Admin-Key: $PROXY_ADMIN_KEY" \
  "$OLLAMA_HOST/proxy/analytics?limit=20" | jq '.request_count_by_ip'

echo ""
echo "5. Precomputed histogram (hourly, last 7 days, requests by model/IP):"
curl -s -H "X-Admin-Key: $PROXY_ADMIN_KEY" \
  "$OLLAMA_HOST/proxy/analytics/histogram?view=hourly&metric=requests&top_n=10" | jq '{view, metric, bucket_count: (.buckets|length)}'

echo ""
echo "6. Daily histogram (last 90 days):"
curl -s -H "X-Admin-Key: $PROXY_ADMIN_KEY" \
  "$OLLAMA_HOST/proxy/analytics/histogram?view=daily&metric=error_rate&top_n=8" | jq '{view, metric}'

# =============================================================================
# 2. COMBINED MONITORING SCRIPT
# =============================================================================

echo ""
echo "=== Combined Monitoring Script ==="
echo ""

# Function to get health
get_health() {
  curl -s "$OLLAMA_HOST/proxy/health" | jq '{
    status: .status,
    paused: .paused,
    queue_depth: .queue_depth,
    active: .active_requests,
    max_parallel: .max_parallel,
    total: .stats.total_requests,
    completed: .stats.completed_requests,
    failed: .stats.failed_requests
  }'
}

# Function to get queue
get_queue() {
  curl -s "$OLLAMA_HOST/proxy/queue" | jq '{
    total_depth: .total_depth,
    processing: .processing_count,
    queued: .queued_count,
    top_requests: [.requests[] | {
      status: .status,
      model: .model,
      priority: .priority,
      wait_time: .wait_time // .total_duration
    }] | .[0:5]
  }'
}

# Function to get VRAM
get_vram() {
  curl -s "$OLLAMA_HOST/proxy/vram" | jq '{
    total_gb: .total_vram_gb,
    used_gb: .used_vram_gb,
    free_gb: .free_vram_gb,
    loaded_models: .loaded_models
  }'
}

echo "Health Status:"
get_health
echo ""

echo "Queue Status:"
get_queue
echo ""

echo "VRAM Status:"
get_vram
echo ""

echo "Analytics Summary (last 24h):"
curl -s -H "X-Admin-Key: $PROXY_ADMIN_KEY" \
  "$OLLAMA_HOST/proxy/analytics?hours=24" | jq '{
  time_range: .time_range,
  top_models: [.request_count_by_model[] | {model: .model, count: .request_count}] | .[0:3],
  top_ips: [.request_count_by_ip[] | {ip: .ip_address, count: .request_count}] | .[0:3],
  error_summary: [.error_rate_analysis[] | {group: .group, errors: .error_count, rate: .error_rate_percent}] | .[0:3]
}'

# =============================================================================
# 3. ADMIN DASHBOARD EXAMPLES
# =============================================================================

echo ""
echo "=== Admin Dashboard Examples ==="
echo ""

echo "1. Interactive dashboard (live updating):"
echo "   python scripts/admin_dashboard.py --url $OLLAMA_HOST --key $PROXY_ADMIN_KEY"
echo ""

echo "2. Quick snapshot:"
echo "   python scripts/admin_dashboard.py --once"
echo ""

echo "3. Fast refresh (1 second) for active monitoring:"
echo "   python scripts/admin_dashboard.py --refresh 1 --hours 1"
echo ""

echo "4. Long-term analysis (1 week):"
echo "   python scripts/admin_dashboard.py --hours 168"
echo ""

echo "5. Using environment variables:"
echo "   export OLLAMA_HOST=$OLLAMA_HOST"
echo "   export PROXY_PROXY_ADMIN_KEY=$PROXY_ADMIN_KEY"
echo "   python scripts/admin_dashboard.py"
echo ""

# =============================================================================
# 4. MONITORING AUTOMATION EXAMPLES
# =============================================================================

echo "=== Automation Examples ==="
echo ""

echo "1. Check if proxy is healthy (exit code):"
cat << 'EOF'
#!/bin/bash
STATUS=$(curl -s http://localhost:8003/proxy/health | jq -r '.status')
if [ "$STATUS" = "healthy" ]; then
  exit 0
else
  exit 1
fi
EOF

echo ""
echo "2. Alert if queue depth exceeds threshold:"
cat << 'EOF'
#!/bin/bash
THRESHOLD=10
DEPTH=$(curl -s http://localhost:8003/proxy/health | jq -r '.queue_depth')
if [ "$DEPTH" -gt "$THRESHOLD" ]; then
  echo "WARNING: Queue depth is $DEPTH (threshold: $THRESHOLD)"
  # Send alert (email, Slack, PagerDuty, etc.)
fi
EOF

echo ""
echo "3. Collect metrics for external monitoring:"
cat << 'EOF'
#!/bin/bash
# Export metrics to Prometheus/InfluxDB/etc.
METRICS=$(curl -s http://localhost:8003/proxy/health)
echo "proxy_queue_depth $(echo $METRICS | jq '.queue_depth')"
echo "proxy_active_requests $(echo $METRICS | jq '.active_requests')"
echo "proxy_total_requests $(echo $METRICS | jq '.stats.total_requests')"
echo "proxy_failed_requests $(echo $METRICS | jq '.stats.failed_requests')"
EOF

echo ""
echo "4. Daily analytics report:"
cat << 'EOF'
#!/bin/bash
# Run daily at midnight via cron
PROXY_ADMIN_KEY="your_key"
DATE=$(date +%Y-%m-%d)
curl -s -H "X-Admin-Key: $PROXY_ADMIN_KEY" \
  "http://localhost:8003/proxy/analytics?hours=24" \
  > "/var/log/proxy/analytics-$DATE.json"
EOF

echo ""
echo "=== Setup Complete ==="
echo "All examples above are ready to use!"
echo "Replace PROXY_ADMIN_KEY and OLLAMA_HOST with your actual values."
