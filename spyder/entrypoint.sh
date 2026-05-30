#!/bin/bash
# Spyder service entrypoint — runs the realtime poller in a loop
# and periodic daily tasks (netCDF sync, solar indices import).

set -e

cd /app

# Run daily tasks once at startup then every 24 hours
daily_tasks() {
    while true; do
        echo "$(date) [spyder] Running daily tasks …"
        python spyder/netcdf_sync.py 2>&1 || true
        python api/indices_importer.py 2>&1 || true
        echo "$(date) [spyder] Daily tasks done. Sleeping 24h."
        sleep 86400
    done
}

# Start daily tasks in background
daily_tasks &

# Run realtime poller in foreground (exits on failure, Docker restarts it)
exec python spyder/realtime.py --loop 300
