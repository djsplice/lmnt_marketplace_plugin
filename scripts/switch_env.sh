#!/bin/bash

# Configuration
PRINTER_DATA_DIR="${HOME}/printer_data"
MOONRAKER_CONF="${PRINTER_DATA_DIR}/config/moonraker.conf"
MARKETPLACE_DIR="${PRINTER_DATA_DIR}/lmnt_marketplace"
DEV_DIR="${PRINTER_DATA_DIR}/lmnt_marketplace_dev"
PROD_DIR="${PRINTER_DATA_DIR}/lmnt_marketplace_prod"

# Prod Settings
PROD_URL="https://api.lmnt.co"
PROD_PROJECT="lmnt-prod"

# Dev Settings
DEV_URL="https://dev-api.lmnt.co"
DEV_PROJECT="lmnt-dev"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[LMNT Switch]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Check if running on a Klipper host (basic check)
if [ ! -d "$PRINTER_DATA_DIR" ]; then
    error "Printer data directory not found at $PRINTER_DATA_DIR. Is this a Klipper host?"
fi

if [ ! -f "$MOONRAKER_CONF" ]; then
    error "moonraker.conf not found at $MOONRAKER_CONF."
fi

TARGET_ENV=$1

if [ -z "$TARGET_ENV" ]; then
    echo "Usage: $0 [dev|prod]"
    exit 1
fi

if [[ "$TARGET_ENV" != "dev" && "$TARGET_ENV" != "prod" ]]; then
    error "Invalid environment. Use 'dev' or 'prod'."
fi

log "Switching to $TARGET_ENV environment..."

# Detect current environment from config
CURRENT_URL=$(grep "marketplace_url:" "$MOONRAKER_CONF" | awk '{print $2}')

if [[ "$TARGET_ENV" == "prod" && "$CURRENT_URL" == "$PROD_URL" ]]; then
    warn "Already configured for Production ($PROD_URL). Nothing to do."
    exit 0
fi

if [[ "$TARGET_ENV" == "dev" && "$CURRENT_URL" == "$DEV_URL" ]]; then
    warn "Already configured for Development ($DEV_URL). Nothing to do."
    exit 0
fi

log "Stopping Moonraker service..."
sudo systemctl stop moonraker

log "Swapping data directories..."

# Safety check: ensure main directory exists or was moved
if [ ! -d "$MARKETPLACE_DIR" ]; then
    # if it doesn't exist, we just create it later, but warn
    warn "Main lmnt_marketplace directory missing. Will create fresh if needed."
fi

if [ "$TARGET_ENV" == "prod" ]; then
    # We are currently in DEV (presumably), switching to PROD
    
    # 1. Back up current (Dev) to DEV_DIR
    if [ -d "$MARKETPLACE_DIR" ]; then
        if [ -d "$DEV_DIR" ]; then
            warn "Existing development backup found at $DEV_DIR. Overwriting!"
            rm -rf "$DEV_DIR"
        fi
        mv "$MARKETPLACE_DIR" "$DEV_DIR"
        log "Moved current (dev) data to $DEV_DIR"
    fi

    # 2. Restore PROD_DIR if exists, else create new
    if [ -d "$PROD_DIR" ]; then
        mv "$PROD_DIR" "$MARKETPLACE_DIR"
        log "Restored production data from $PROD_DIR"
    else
        log "No existing production data found. Creating fresh directory."
        mkdir -p "$MARKETPLACE_DIR"
        # Ensure permissions are correct (usually pi:pi or similar)
        chown -R $USER:$USER "$MARKETPLACE_DIR"
    fi

    # 3. Update Config
    log "Updating moonraker.conf to Production..."
    sed -i "s|marketplace_url:.*|marketplace_url: $PROD_URL|" "$MOONRAKER_CONF"
    sed -i "s|firebase_project_id:.*|firebase_project_id: $PROD_PROJECT|" "$MOONRAKER_CONF"

elif [ "$TARGET_ENV" == "dev" ]; then
    # We are currently in PROD, switching to DEV

    # 1. Back up current (Prod) to PROD_DIR
    if [ -d "$MARKETPLACE_DIR" ]; then
        if [ -d "$PROD_DIR" ]; then
            warn "Existing production backup found at $PROD_DIR. Overwriting!"
            rm -rf "$PROD_DIR"
        fi
        mv "$MARKETPLACE_DIR" "$PROD_DIR"
        log "Moved current (prod) data to $PROD_DIR"
    fi

    # 2. Restore DEV_DIR if exists, else create new
    if [ -d "$DEV_DIR" ]; then
        mv "$DEV_DIR" "$MARKETPLACE_DIR"
        log "Restored development data from $DEV_DIR"
    else
        log "No existing development data found. Creating fresh directory."
        mkdir -p "$MARKETPLACE_DIR"
        chown -R $USER:$USER "$MARKETPLACE_DIR"
    fi

    # 3. Update Config
    log "Updating moonraker.conf to Development..."
    sed -i "s|marketplace_url:.*|marketplace_url: $DEV_URL|" "$MOONRAKER_CONF"
    sed -i "s|firebase_project_id:.*|firebase_project_id: $DEV_PROJECT|" "$MOONRAKER_CONF"
fi

log "Restarting Moonraker..."
sudo systemctl restart moonraker

log "Done! Switched to $TARGET_ENV."
