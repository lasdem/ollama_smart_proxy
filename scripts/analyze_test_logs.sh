#!/bin/bash
# Analysis Script: Extract priority and order from logs
# Usage: ./analyze_test_logs.sh <logfile>

if [ -z "$1" ]; then
  echo "Usage: $0 <proxy_log_file>"
  echo "Example: $0 proxy.log"
  exit 1
fi

echo "📊 Test Results Analysis"
echo "======================="
echo ""

echo "1️⃣ Priority Score Distribution:"
echo "--------------------------------"
grep "📤 Processing:" "$1" | awk -F'priority=' '{print $2}' | awk '{print $1}' | sort -n | uniq -c
echo ""

echo "2️⃣ Processing Order by Model:"
echo "------------------------------"
grep "📤 Processing:" "$1" | awk '{
  for(i=1;i<=NF;i++) {
    if($i=="Processing:") print $(i+1)
  }
}'
echo ""

echo "3️⃣ Model Loading Status:"
echo "-------------------------"
grep "📤 Processing:" "$1" | grep -o "loaded=[^,]*" | sort | uniq -c
echo ""

echo "4️⃣ IP Active Count Progression:"
echo "--------------------------------"
grep "📤 Processing:" "$1" | grep -o "ip_active=[0-9]*" | cut -d= -f2 | head -20
echo ""

echo "5️⃣ VRAM Values Detected:"
echo "------------------------"
grep "📤 Processing:" "$1" | grep -o "VRAM: [0-9.]*GB" | sort -u
echo ""

echo "6️⃣ First 10 Requests Processed:"
echo "--------------------------------"
grep "📤 Processing:" "$1" | head -10 | awk '{
  for(i=1;i<=NF;i++) {
    if($i=="Processing:") model=$(i+1)
    if($i~"priority=") priority=$i
    if($i~"loaded=") loaded=$i
    if($i~"ip_active=") ip=$i
  }
  print model, priority, loaded, ip
}'
echo ""

echo "✅ Analysis complete!"
