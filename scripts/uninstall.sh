#!/bin/bash
# LMNT Marketplace Plugin Uninstaller for Moonraker
# This script removes the LMNT Marketplace Plugin and Hedera Slicer components

set -e

# Define directories
PLUGIN_NAME="lmnt-marketplace"
PLUGIN_DIR="${HOME}/${PLUGIN_NAME}"
MOONRAKER_DIR="${HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"
CONFIG_DIR="${HOME}/printer_data/config"
KLIPPER_DIR="${HOME}/klipper"

# Check if the plugin is installed
if [ ! -d "${PLUGIN_DIR}" ]; then
    echo "Plugin not found at ${PLUGIN_DIR}. Nothing to uninstall."
    exit 0
fi

# Remove symlinks
echo "Removing symlinks from Moonraker components directory..."
rm -f "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"
rm -f "${COMPONENT_DIR}/encrypted_print.py"
rm -f "${COMPONENT_DIR}/encrypted_provider.py"
rm -f "${COMPONENT_DIR}/lmnt_marketplace"

# Remove plugin directory
echo "Removing plugin directory..."
rm -rf "${PLUGIN_DIR}"

# Remove Klipper extensions if they exist
if [ -f "${KLIPPER_DIR}/klippy/extras/encrypted_file_bridge.py" ]; then
    echo "Removing encrypted_file_bridge.py..."
    rm "${KLIPPER_DIR}/klippy/extras/encrypted_file_bridge.py"
fi

if [ -f "${KLIPPER_DIR}/klippy/extras/secure_print.py" ]; then
    echo "Removing secure_print.py..."
    rm "${KLIPPER_DIR}/klippy/extras/secure_print.py"
fi

# Update moonraker.conf
echo "Updating moonraker.conf..."
if [ -f "${CONFIG_DIR}/moonraker.conf" ]; then
    # Create a temporary file
    TEMP_FILE=$(mktemp)
    
    # Remove the plugin configuration sections
    grep -v -E "^\[lmnt_marketplace_plugin\]|^# LMNT Marketplace Plugin configuration" "${CONFIG_DIR}/moonraker.conf" > "${TEMP_FILE}"
    grep -v -E "^\[hedera_slicer\]|^# Hedera Slicer configuration" "${TEMP_FILE}" > "${CONFIG_DIR}/moonraker.conf"
    
    # Remove the temporary file
    rm "${TEMP_FILE}"
    
    echo "Removed plugin configurations from moonraker.conf"
else
    echo "Warning: moonraker.conf not found at ${CONFIG_DIR}/moonraker.conf"
fi

echo "Uninstallation complete!"
echo "Please restart Moonraker and Klipper to apply changes:"
echo "sudo systemctl restart moonraker"
echo "sudo systemctl restart klipper"
