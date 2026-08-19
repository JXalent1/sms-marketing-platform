#!/usr/bin/env bash
# Push local code to the server and restart.
#
# Sync only what the app needs. Never rsync the whole directory: data/ holds the
# live database and .env holds the server's own credentials, and overwriting
# either from a laptop is how you lose a client's contact list.
set -euo pipefail

SERVER="${SERVER:?set SERVER=user@host}"
REMOTE_DIR="${REMOTE_DIR:-/home/appuser/app}"
SERVICE="${SERVICE:?set SERVICE=your-service-name}"

echo "Deploying to $SERVER:$REMOTE_DIR"

# The stylesheet is a build artifact (gitignored), and the server carries no node
# toolchain — so it is compiled here, before the sync, and shipped as a file.
# Skipping this step serves the whole dashboard with no CSS at all.
echo "Building stylesheet..."
npm run build:css
test -s app/static/app.css || { echo "app/static/app.css missing after build"; exit 1; }

rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    app/ "$SERVER:$REMOTE_DIR/app/"

rsync -avz requirements.txt scripts/ "$SERVER:$REMOTE_DIR/"

ssh "$SERVER" bash -s <<REMOTE
set -euo pipefail
cd "$REMOTE_DIR"
./venv/bin/pip install --quiet -r requirements.txt
sudo systemctl restart "$SERVICE"
sleep 2
systemctl is-active "$SERVICE"
REMOTE

echo "Deployed. Verify: curl -sf https://YOUR_DOMAIN/health"
