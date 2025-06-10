#!/bin/bash
# LMNT Marketplace Plugin Installer for Moonraker
# This script installs the LMNT Marketplace Plugin and Hedera Slicer components

set -e

# Define directories
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PLUGIN_NAME="lmnt-marketplace"
PLUGIN_DIR="${HOME}/${PLUGIN_NAME}"
MOONRAKER_DIR="${HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"
CONFIG_DIR="${HOME}/printer_data/config"
KLIPPER_DIR="${HOME}/klipper"

# Check if moonraker is installed
if [ ! -d "${MOONRAKER_DIR}" ]; then
    echo "Moonraker not found at ${MOONRAKER_DIR}. Please install Moonraker first."
    exit 1
fi

# Check if the components directory exists
if [ ! -d "${COMPONENT_DIR}" ]; then
    echo "Moonraker components directory not found at ${COMPONENT_DIR}."
    echo "Please check your Moonraker installation."
    exit 1
fi

# Create plugin directory
echo "Creating plugin directory at ${PLUGIN_DIR}..."
mkdir -p "${PLUGIN_DIR}/component"
mkdir -p "${PLUGIN_DIR}/component/lmnt_marketplace"

# Copy plugin files
echo "Copying plugin files..."
cp "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace_plugin.py" "${PLUGIN_DIR}/component/"
cp "${REPO_DIR}/moonraker/moonraker/components/hedera_slicer.py" "${PLUGIN_DIR}/component/"
cp -r "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace/"* "${PLUGIN_DIR}/component/lmnt_marketplace/"

# Create symlinks
echo "Creating symlinks in Moonraker components directory..."
ln -sf "${PLUGIN_DIR}/component/lmnt_marketplace_plugin.py" "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"
ln -sf "${PLUGIN_DIR}/component/hedera_slicer.py" "${COMPONENT_DIR}/hedera_slicer.py"
ln -sf "${PLUGIN_DIR}/component/lmnt_marketplace" "${COMPONENT_DIR}/lmnt_marketplace"

# Copy Klipper macros if they exist
#if [ -d "${REPO_DIR}/klipper/extras" ]; then
#    echo "Copying Klipper extensions..."
#    mkdir -p "${KLIPPER_DIR}/klippy/extras"
#    cp "${REPO_DIR}/klipper/extras/"*.py "${KLIPPER_DIR}/klippy/extras/" 2>/dev/null || true
#fi

# Update moonraker.conf
echo "Updating moonraker.conf..."
if [ -f "${CONFIG_DIR}/moonraker.conf" ]; then
    if ! grep -q "\[lmnt_marketplace_plugin\]" "${CONFIG_DIR}/moonraker.conf"; then
        echo -e "\n# LMNT Marketplace Plugin configuration" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "[lmnt_marketplace_plugin]" >> "${CONFIG_DIR}/moonraker.conf"
        echo "Added [lmnt_marketplace_plugin] to moonraker.conf"
    fi
    
    if ! grep -q "\[hedera_slicer\]" "${CONFIG_DIR}/moonraker.conf"; then
        echo -e "\n# Hedera Slicer configuration" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "[hedera_slicer]" >> "${CONFIG_DIR}/moonraker.conf"
        echo "Added [hedera_slicer] to moonraker.conf"
    fi
else
    echo "Warning: moonraker.conf not found at ${CONFIG_DIR}/moonraker.conf"
    echo "Please manually add the following to your moonraker.conf:"
    echo -e "\n[lmnt_marketplace_plugin]\n\n[hedera_slicer]"
fi

echo "Installation complete!"
echo "Please restart Moonraker and Klipper to activate the plugin:"
echo "sudo systemctl restart moonraker"
#echo "sudo systemctl restart klipper"
