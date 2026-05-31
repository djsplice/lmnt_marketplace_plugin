#!/bin/sh
# LMNT Marketplace Plugin - Snapmaker U1 Setup / Recovery Helper
#
# Run as root to (re-)establish the plugin's symlinks and restore the wifi
# config from a previous backup if /etc/wpa_supplicant.conf is missing or
# empty. Idempotent.
#
# Normally invoked once by scripts/install.sh on a U1. You can also run it
# by hand if anything ever gets weird:
#
#   su -
#   /oem/printer_data/lmnt_marketplace_plugin/scripts/u1_bootstrap.sh
#
# Prerequisites:
#   - /oem/.debug exists (so /etc changes persist across reboots)
#   - Plugin source is at /oem/printer_data/lmnt_marketplace_plugin

REPO_DIR="/oem/printer_data/lmnt_marketplace_plugin"
MOONRAKER_COMPONENTS="/home/lava/moonraker/moonraker/components"
KLIPPER_EXTRAS="/home/lava/klipper/klippy/extras"
LOG="/home/lava/printer_data/logs/lmnt_bootstrap.log"
WIFI_BACKUP="/oem/printer_data/lmnt_install_backup/wpa_supplicant.conf.bak"

log() {
    echo "[lmnt-bootstrap $(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG" 2>/dev/null
}

start() {
    log "Starting LMNT plugin bootstrap"

    # --- Wifi safety net ---------------------------------------------------
    # If /etc/wpa_supplicant.conf is empty/missing but we have a backup,
    # restore it so the printer can reach the network on boot.
    if [ -s "$WIFI_BACKUP" ]; then
        if [ ! -s /etc/wpa_supplicant.conf ] || \
           ! grep -q 'network=' /etc/wpa_supplicant.conf 2>/dev/null; then
            cp "$WIFI_BACKUP" /etc/wpa_supplicant.conf
            chmod 600 /etc/wpa_supplicant.conf
            log "Restored /etc/wpa_supplicant.conf from backup"
        fi
    fi
    # -----------------------------------------------------------------------

    if [ ! -d "$REPO_DIR" ]; then
        log "ERROR: $REPO_DIR not found; nothing to bootstrap"
        return 1
    fi

    # Moonraker component symlinks
    if [ -d "$MOONRAKER_COMPONENTS" ]; then
        ln -sf "$REPO_DIR/moonraker/moonraker/components/lmnt_marketplace" \
               "$MOONRAKER_COMPONENTS/lmnt_marketplace"
        ln -sf "$REPO_DIR/moonraker/moonraker/components/lmnt_marketplace_plugin.py" \
               "$MOONRAKER_COMPONENTS/lmnt_marketplace_plugin.py"
        ln -sf "$REPO_DIR/moonraker/moonraker/components/encrypted_print.py" \
               "$MOONRAKER_COMPONENTS/encrypted_print.py"
        ln -sf "$REPO_DIR/moonraker/moonraker/components/encrypted_provider.py" \
               "$MOONRAKER_COMPONENTS/encrypted_provider.py"
        if [ -d "$REPO_DIR/moonraker/moonraker/components/ui" ]; then
            ln -sf "$REPO_DIR/moonraker/moonraker/components/ui" \
                   "$MOONRAKER_COMPONENTS/ui"
        fi
        log "Moonraker component symlinks restored"
    else
        log "WARN: $MOONRAKER_COMPONENTS not found; skipping moonraker links"
    fi

    # Klipper extras symlinks
    if [ -d "$KLIPPER_EXTRAS" ] && [ -d "$REPO_DIR/kalico_mods/extras" ]; then
        for f in "$REPO_DIR/kalico_mods/extras/"*.py; do
            [ -f "$f" ] && ln -sf "$f" "$KLIPPER_EXTRAS/$(basename "$f")"
        done
        log "Klipper extras symlinks restored"
    fi

    # lmnt_decrypt arch binary symlink
    ARCH=$(uname -m)
    case "$ARCH" in
        aarch64|arm64) SRC="$REPO_DIR/bin/lmnt_decrypt_aarch64" ;;
        armv7l|armv7)  SRC="$REPO_DIR/bin/lmnt_decrypt_armv7"   ;;
        x86_64|amd64)  SRC="$REPO_DIR/bin/lmnt_decrypt_x86_64"  ;;
        *)             SRC="" ;;
    esac
    if [ -n "$SRC" ] && [ -f "$SRC" ]; then
        chmod +x "$SRC" 2>/dev/null
        ln -sf "$SRC" "$REPO_DIR/bin/lmnt_decrypt"
        log "lmnt_decrypt binary linked for $ARCH"
    fi

    log "Bootstrap complete"
}

start
exit $?
