#!/bin/bash
# LMNT Marketplace Plugin Test Script
# This script tests the installation of the LMNT Marketplace Plugin

set -e

echo "Testing LMNT Marketplace Plugin installation..."

# Define directories
PLUGIN_NAME="lmnt-marketplace"
PLUGIN_DIR="${HOME}/${PLUGIN_NAME}"
MOONRAKER_DIR="${HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"
CONFIG_DIR="${HOME}/printer_data/config"

# Check if the plugin directory exists
if [ ! -d "${PLUGIN_DIR}" ]; then
    echo "ERROR: Plugin directory not found at ${PLUGIN_DIR}"
    echo "Please run the installation script first."
    exit 1
else
    echo "✓ Plugin directory found at ${PLUGIN_DIR}"
fi

# Check if the component files exist
if [ ! -f "${PLUGIN_DIR}/component/lmnt_marketplace_plugin.py" ]; then
    echo "ERROR: lmnt_marketplace_plugin.py not found in ${PLUGIN_DIR}/component/"
    exit 1
else
    echo "✓ lmnt_marketplace_plugin.py found"
fi

if [ ! -f "${PLUGIN_DIR}/component/hedera_slicer.py" ]; then
    echo "ERROR: hedera_slicer.py not found in ${PLUGIN_DIR}/component/"
    exit 1
else
    echo "✓ hedera_slicer.py found"
fi

if [ ! -d "${PLUGIN_DIR}/component/lmnt_marketplace" ]; then
    echo "ERROR: lmnt_marketplace directory not found in ${PLUGIN_DIR}/component/"
    exit 1
else
    echo "✓ lmnt_marketplace directory found"
fi

# Check if the symlinks exist
if [ ! -L "${COMPONENT_DIR}/lmnt_marketplace_plugin.py" ]; then
    echo "ERROR: Symlink for lmnt_marketplace_plugin.py not found in ${COMPONENT_DIR}"
    exit 1
else
    echo "✓ Symlink for lmnt_marketplace_plugin.py found"
fi

if [ ! -L "${COMPONENT_DIR}/hedera_slicer.py" ]; then
    echo "ERROR: Symlink for hedera_slicer.py not found in ${COMPONENT_DIR}"
    exit 1
else
    echo "✓ Symlink for hedera_slicer.py found"
fi

if [ ! -L "${COMPONENT_DIR}/lmnt_marketplace" ]; then
    echo "ERROR: Symlink for lmnt_marketplace directory not found in ${COMPONENT_DIR}"
    exit 1
else
    echo "✓ Symlink for lmnt_marketplace directory found"
fi

# Check if the configuration exists in moonraker.conf
if [ -f "${CONFIG_DIR}/moonraker.conf" ]; then
    if grep -q "\[lmnt_marketplace_plugin\]" "${CONFIG_DIR}/moonraker.conf"; then
        echo "✓ [lmnt_marketplace_plugin] configuration found in moonraker.conf"
    else
        echo "WARNING: [lmnt_marketplace_plugin] configuration not found in moonraker.conf"
    fi
    
    if grep -q "\[hedera_slicer\]" "${CONFIG_DIR}/moonraker.conf"; then
        echo "✓ [hedera_slicer] configuration found in moonraker.conf"
    else
        echo "WARNING: [hedera_slicer] configuration not found in moonraker.conf"
    fi
else
    echo "WARNING: moonraker.conf not found at ${CONFIG_DIR}/moonraker.conf"
fi

# Check Moonraker logs for errors
if [ -f "${HOME}/printer_data/logs/moonraker.log" ]; then
    echo "Checking Moonraker logs for errors..."
    if grep -i "error.*lmnt_marketplace" "${HOME}/printer_data/logs/moonraker.log" | tail -n 10; then
        echo "WARNING: Errors found in Moonraker logs. See above."
    else
        echo "✓ No recent errors found in Moonraker logs related to the plugin"
    fi
else
    echo "WARNING: Moonraker log file not found"
fi

echo "Test completed. If all checks passed, the plugin is installed correctly."
echo "To verify functionality, restart Moonraker and check the logs:"
echo "sudo systemctl restart moonraker"
echo "tail -f ~/printer_data/logs/moonraker.log"
