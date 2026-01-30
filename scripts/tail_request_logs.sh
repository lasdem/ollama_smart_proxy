#!/usr/bin/env bash

DB_PATH="db/smart_proxy.db"
TABLE="request_logs"

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# 1. SQL Query
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

# 3. AWK Format (No trailing newline)
AWK_FMT="%-6s %-19s %-10.10s %-8s %-8s %-8s %-15.15s %-20.20s %-4s"

# ==============================================================================
# SETUP
# ==============================================================================

# Cleanup function to restore terminal state on exit (Ctrl+C)
cleanup() {
    tput cnorm       # Show cursor
    stty echo        # Enable input echoing
    tput cup $(tput lines) 0
    echo ""
    exit
}
trap cleanup SIGINT

# Visual styles
HEADER_BG=$(tput setab 4) # Blue Background
HEADER_FG=$(tput setaf 7) # White Text
RESET=$(tput sgr0)

# Prepare Terminal
clear
tput civis   # Hide cursor
stty -echo   # Disable input echoing (prevents typing artifacts)

# ==============================================================================
# MAIN LOOP
# ==============================================================================

while true; do
    # 1. Get Terminal Dimensions
    # We re-check this every loop so resizing the window works instantly
    LINES=$(tput lines)
    COLS=$(tput cols)

    # Calculate safe width (COLS - 1) to prevent auto-wrapping
    # Calculate max data rows (Total lines - 1 Header - 1 Bottom Buffer)
    SAFE_WIDTH=$((COLS - 1))
    MAX_ROWS=$((LINES - 2))

    if [[ $MAX_ROWS -lt 1 ]]; then MAX_ROWS=0; fi

    # 2. Draw Header (Always redraw to ensure it stays fixed)
    # Format header, truncate to safe width
    RAW_HEADER=$(echo "$HEADERS" | awk -F'|' "{printf \"$AWK_FMT\", \$1, \$2, \$3, \$4, \$5, \$6, \$7, \$8, \$9}")
    
    # Pad the header with spaces to fill the bar
    # 'printf %-*s' means: print string left-aligned in a field of width N
    tput cup 0 0
    printf "${HEADER_BG}${HEADER_FG}%-*.*s${RESET}" "$COLS" "$COLS" "$RAW_HEADER"

    # 3. Fetch Data
    if [[ $MAX_ROWS -gt 0 ]]; then
        # Fetch N rows
        RAW_DATA=$(sqlite3 -separator '|' "$DB_PATH" \
            "SELECT * FROM ( $QUERY_SQL WHERE id > 0 ORDER BY id DESC LIMIT $MAX_ROWS ) ORDER BY id ASC;")
    else
        RAW_DATA=""
    fi

    # 4. Draw Rows
    # We use mapfile (readarray) to split output into an array safely
    mapfile -t DATA_ARRAY <<< "$RAW_DATA"

    # Loop through the available screen space (1 to MAX_ROWS)
    for ((i=0; i<MAX_ROWS; i++)); do
        # Calculate screen line (Header is 0, so data starts at 1)
        SCREEN_LINE=$((i + 1))
        
        # Get data for this row (if available)
        ROW_DATA="${DATA_ARRAY[$i]}"

        tput cup $SCREEN_LINE 0

        if [[ -n "$ROW_DATA" ]]; then
            # Format columns using AWK
            FORMATTED=$(echo "$ROW_DATA" | awk -F'|' "{printf \"$AWK_FMT\", \$1, \$2, \$3, \$4, \$5, \$6, \$7, \$8, \$9}")
            
            # PRINTING TRICK:
            # %-*.*s : Left-align, Pad to Width, Truncate to Width.
            # This ensures we overwrite previous text completely (no flicker) 
            # and never exceed line width (no scroll).
            printf "%-*.*s" "$SAFE_WIDTH" "$SAFE_WIDTH" "$FORMATTED"
        else
            # If no data for this row (DB has fewer rows than screen), print empty spaces to clear old data
            printf "%-*s" "$SAFE_WIDTH" ""
        fi
    done

    # 5. Wait
    sleep 1
done