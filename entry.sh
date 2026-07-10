#!/bin/sh
# Exit immediately if a command exits with a non-zero status
set -e

echo "Initializing environment..."

echo "Starting LoadPilot on port 8000..."

# Start the FastAPI application with Uvicorn
exec uvicorn app:app --host 0.0.0.0 --port 8000