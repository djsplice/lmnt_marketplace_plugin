#!/bin/bash
# LMNT Marketplace Plugin Updater for Moonraker
# This script updates the LMNT Marketplace Plugin and Hedera Slicer components

set -e

# Define directories
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PLUGIN_NAME="lmnt-marketplace"
PLUGIN_DIR="${HOME}/${PLUGIN_NAME}"
MOONRAKER_DIR="${HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"
KLIPPER_DIR="${HOME}/klipper"

# Check if the plugin is installed
if [ ! -d "${PLUGIN_DIR}" ]; then
    echo "Plugin not found at ${PLUGIN_DIR}. Please run install.sh first."
    exit 1
fi

# Backup configuration files
echo "Backing up configuration files..."
if [ -f "${PLUGIN_DIR}/component/lmnt_marketplace/config.py" ]; then
    cp "${PLUGIN_DIR}/component/lmnt_marketplace/config.py" "${PLUGIN_DIR}/config.py.bak"
fi

# Update plugin files
echo "Updating plugin files..."
cp "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace_plugin.py" "${PLUGIN_DIR}/component/"
#cp "${REPO_DIR}/moonraker/moonraker/components/hedera_slicer.py" "${PLUGIN_DIR}/component/"
cp -r "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace/"* "${PLUGIN_DIR}/component/lmnt_marketplace/"

# Restore configuration files
echo "Restoring configuration files..."
if [ -f "${PLUGIN_DIR}/config.py.bak" ]; then
    cp "${PLUGIN_DIR}/config.py.bak" "${PLUGIN_DIR}/component/lmnt_marketplace/config.py"
    rm "${PLUGIN_DIR}/config.py.bak"
fi

# Update Klipper extensions if they exist
#if [ -d "${REPO_DIR}/klipper/extras" ]; then
#    echo "Updating Klipper extensions..."
    # Backup existing files if they haven't been backed up
    #if [ -f "${KLIPPER_DIR}/klippy/extras/virtual_sdcard.py" ] && [ ! -f "${KLIPPER_DIR}/klippy/extras/virtual_sdcard.py.bak" ]; then
    #    cp "${KLIPPER_DIR}/klippy/extras/virtual_sdcard.py" "${KLIPPER_DIR}/klippy/extras/virtual_sdcard.py.bak"
    #fi
    
#    if [ -f "${KLIPPER_DIR}/klippy/extras/print_stats.py" ] && [ ! -f "${KLIPPER_DIR}/klippy/extras/print_stats.py.bak" ]; then
 #       cp "${KLIPPER_DIR}/klippy/extras/print_stats.py" "${KLIPPER_DIR}/klippy/extras/print_stats.py.bak"
  #  fi
    
    # Copy new files
   # cp "${REPO_DIR}/klipper/extras/"*.py "${KLIPPER_DIR}/klippy/extras/" 2>/dev/null || true
#fi

echo "Update complete!"
echo "Please restart Moonraker and Klipper to apply changes:"
echo "sudo systemctl restart moonraker"
#echo "sudo systemctl restart klipper"
