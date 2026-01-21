#!/usr/bin/env bash

DB_PATH="db/smart_proxy.db"
TABLE="request_logs"

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# 1. SQL Query
# - strftime: Removes milliseconds from timestamp
# - ROUND(..., 3): Rounds floats to 3 decimals
# - IFNULL: Handles nulls so the layout doesn't break
QUERY_SQL="SELECT 
    id, 
    strftime('%Y-%m-%d %H:%M:%S', created_at), 
    IFNULL(status, '-'), 
    ROUND(IFNULL(duration_seconds, 0), 3), 
    ROUND(IFNULL(queue_wait_seconds, 0), 3), 
    ROUND(IFNULL(processing_time_seconds, 0), 3), 
    IFNULL(source_ip, '-'), 
    IFNULL(model_name, '-'), 
    IFNULL(priority_score, 0)
FROM $TABLE"

# 2. Header Labels
HEADERS="ID|CREATED_AT|STATUS|DUR(s)|WAIT(s)|PROC(s)|SOURCE_IP|MODEL|PRIO"

# 3. AWK Format String
# %-Ns   = Left align, width N
# %.Ns   = Hard truncate at N chars (prevents wrapping lines)
# We increased the number columns to 8 chars to fit "10.123" comfortably.
AWK_FMT="%-6s %-19s %-10.10s %-8s %-8s %-8s %-15.15s %-20.20s %-4s\n"

# ==============================================================================
# SETUP
# ==============================================================================

cleanup() {
    tput csr 0 $(tput lines)  # Reset scrolling
    tput cnorm                # Show cursor
    echo ""
    exit
}
trap cleanup SIGINT

HEADER_BG=$(tput setab 4) # Blue Background
HEADER_FG=$(tput setaf 7) # White Text
RESET=$(tput sgr0)

# ==============================================================================
# INITIALIZATION
# ==============================================================================

clear
tput civis # Hide cursor

# 1. Initialize LAST_ID
# Start 15 records back to fill the screen on load
MAX_ID=$(sqlite3 "$DB_PATH" "SELECT MAX(id) FROM $TABLE;")
if [[ -z "$MAX_ID" ]]; then 
    LAST_ID=0 
else 
    LAST_ID=$((MAX_ID - 15))
    if [ "$LAST_ID" -lt 0 ]; then LAST_ID=0; fi
fi

# 2. Draw Sticky Header
tput cup 0 0
# Draw background bar
printf "${HEADER_BG}%*s${RESET}\n" "$(tput cols)" "" 
tput cup 0 0
# Print formatted header
echo "$HEADERS" | awk -F'|' "{printf \"$AWK_FMT\", \$1, \$2, \$3, \$4, \$5, \$6, \$7, \$8, \$9}" | sed "s/^/${HEADER_BG}${HEADER_FG}/" | sed "s/$/${RESET}/"

# 3. Set Scrolling Region (Freeze top line)
tput csr 1 $(($(tput lines) - 1)) 

# ==============================================================================
# MAIN LOOP
# ==============================================================================

while true; do
    # Fetch new records as a pipe-separated list
    NEW_DATA=$(sqlite3 -separator '|' "$DB_PATH" \
        "$QUERY_SQL WHERE id > $LAST_ID ORDER BY id ASC;")

    if [ ! -z "$NEW_DATA" ]; then
        
        # Move cursor to bottom of scroll region
        tput cup $(($(tput lines) - 1)) 0
        
        # Process and Print strictly formatted lines
        echo "$NEW_DATA" | awk -F'|' "{printf \"$AWK_FMT\", \$1, \$2, \$3, \$4, \$5, \$6, \$7, \$8, \$9}"

        # Update LAST_ID to the ID of the last record found
        NEW_LAST_ID=$(echo "$NEW_DATA" | tail -n 1 | cut -d'|' -f1)
        
        if [[ "$NEW_LAST_ID" =~ ^[0-9]+$ ]]; then
            LAST_ID=$NEW_LAST_ID
        fi
    fi

    sleep 1
done