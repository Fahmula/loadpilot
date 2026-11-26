#!/bin/sh
# Exit immediately if a command exits with a non-zero status
set -e

echo "Initializing environment..."

# Behind a reverse proxy (Caddy, Nginx Proxy Manager, ...), set FORWARDED_ALLOW_IPS to the proxy's address or
# network, or * if nothing but the proxy can reach this container. uvicorn then takes each visitor's address and
# HTTPS from the proxy's X-Forwarded-For/-Proto headers: the login limit counts each visitor on their own, and
# the session cookie is marked Secure. uvicorn reads the variable itself; unset, it trusts only localhost.
echo "Trusting proxy headers from: ${FORWARDED_ALLOW_IPS:-127.0.0.1,::1 (default)}"

echo "Starting LoadPilot on port 8000..."

# Start the FastAPI application with Uvicorn
exec uvicorn app:app --host 0.0.0.0 --port 8000
