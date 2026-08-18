#!/usr/bin/env bash
# Local development server.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Creating virtualenv..."
    python3 -m venv venv
    ./venv/bin/pip install --quiet --upgrade pip
    ./venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -f .env ]; then
    echo "No .env found. Copying .env.example — edit it before going live."
    cp .env.example .env
fi

exec ./venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
