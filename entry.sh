#!/bin/sh
# Exit immediately if a command exits with a non-zero status
set -e

echo "Initializing environment..."

# Behind a reverse proxy (Caddy, Nginx Proxy Manager, ...), set FORWARDED_ALLOW_IPS to the proxy's address or
# network, or * if nothing but the proxy can reach this container. uvicorn then takes each visitor's address and
# HTTPS from the proxy's X-Forwarded-For/-Proto headers: the login limit counts each visitor on their own, and
# the session cookie is marked Secure. uvicorn reads the variable itself; unset, it trusts only localhost.
echo "Trusting proxy headers from: ${FORWARDED_ALLOW_IPS:-127.0.0.1,::1 (default)}"

# LoadPilot runs as PUID:PGID (1000:1000 unless set), never as root. Started as root (the default), this hands the
# data folder to that user first: images before this one ran as root and left everything in it root-owned.
# Started with `docker run --user`, it runs as that user and the folder must already be writable by it.
if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"
    DATA_DIR="${LOADPILOT_DATA_DIR:-/config}"
    mkdir -p "$DATA_DIR"
    # Only files that need it, so a large folder doesn't slow every start
    find "$DATA_DIR" \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown "$PUID:$PGID" {} + ||
        echo "Warning: couldn't give $DATA_DIR to $PUID:$PGID; LoadPilot may not be able to save anything."
    # root's home isn't readable by the app user; libraries that look for a cache or config there get /tmp
    export HOME=/tmp
    echo "Starting LoadPilot on port 8000 as $PUID:$PGID..."
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups uvicorn app:app --host 0.0.0.0 --port 8000
fi

echo "Starting LoadPilot on port 8000 as $(id -u):$(id -g)..."
exec uvicorn app:app --host 0.0.0.0 --port 8000
