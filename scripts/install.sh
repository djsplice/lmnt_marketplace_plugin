#!/bin/bash
# LMNT Marketplace Plugin Installer for Moonraker
# This script installs the LMNT Marketplace Plugin

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

# Create symlinks directly to the repo (no copying needed)
# This ensures updates via git pull are immediately active
echo "Cleaning up old components..."
rm -rf "${COMPONENT_DIR}/lmnt_marketplace"
rm -rf "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"
rm -rf "${COMPONENT_DIR}/encrypted_print.py"
rm -rf "${COMPONENT_DIR}/encrypted_provider.py"
rm -rf "${COMPONENT_DIR}/ui"

echo "Creating symlinks in Moonraker components directory..."
ln -sf "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace" "${COMPONENT_DIR}/lmnt_marketplace"
ln -sf "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace_plugin.py" "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"
ln -sf "${REPO_DIR}/moonraker/moonraker/components/encrypted_print.py" "${COMPONENT_DIR}/encrypted_print.py"
ln -sf "${REPO_DIR}/moonraker/moonraker/components/encrypted_provider.py" "${COMPONENT_DIR}/encrypted_provider.py"

# Symlink UI files if they exist
if [ -d "${REPO_DIR}/moonraker/moonraker/components/ui" ]; then
    ln -sf "${REPO_DIR}/moonraker/moonraker/components/ui" "${COMPONENT_DIR}/ui"
fi


# Copy Klipper macros if they exist
if [ -d "${REPO_DIR}/kalico_mods/extras" ]; then
    echo "Copying Klipper Plugin..."
    mkdir -p "${KLIPPER_DIR}/klippy/extras"
    cp "${REPO_DIR}/kalico_mods/extras/"*.py "${KLIPPER_DIR}/klippy/extras/" 2>/dev/null || true
fi

# Update moonraker.conf
echo "Updating moonraker.conf..."
if [ -f "${CONFIG_DIR}/moonraker.conf" ]; then
    if ! grep -q "\[lmnt_marketplace_plugin\]" "${CONFIG_DIR}/moonraker.conf"; then
        echo -e "\n# LMNT Marketplace Plugin configuration" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "[lmnt_marketplace_plugin]" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "marketplace_url: https://printers.lmnt.co" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "firebase_project_id: lmnt-dev" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "check_interval: 0" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "\n[encrypted_print]" >> "${CONFIG_DIR}/moonraker.conf"
        echo "Added [lmnt_marketplace_plugin] to moonraker.conf"
    fi

    # Check for [update_manager lmnt_marketplace]
    if ! grep -q "\[update_manager lmnt_marketplace\]" "${CONFIG_DIR}/moonraker.conf"; then
        echo -e "\n[update_manager lmnt_marketplace]" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "type: git_repo" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "path: ${REPO_DIR}" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "origin: https://github.com/djsplice/lmnt_marketplace_plugin.git" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "primary_branch: main" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "env: ${HOME}/moonraker-env/bin/python" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "requirements: requirements.txt" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "install_script: scripts/install.sh" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "is_system_service: False" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "info_tags:" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "  desc: LMNT Marketplace Plugin" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "  channel: stable" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "  notes: https://github.com/djsplice/lmnt_marketplace_plugin/blob/main/CHANGELOG.md" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "managed_services:" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "    moonraker" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "    klipper" >> "${CONFIG_DIR}/moonraker.conf"
        echo "Added [update_manager lmnt_marketplace] to moonraker.conf"
    fi
else
    echo "Warning: moonraker.conf not found at ${CONFIG_DIR}/moonraker.conf"
    echo "Please manually add the following to your moonraker.conf:"
    echo -e "\n[lmnt_marketplace_plugin]\n\n[encrypted_print]\n"
fi

echo "Installation complete!"

if [ -t 0 ]; then
    echo "WARNING: Restarting Moonraker and Klipper will stop any active print jobs."
    read -p "Do you want to restart Moonraker and Klipper now? (y/N) " -n 1 -r
    echo    # (optional) move to a new line
    if [[ $REPLY =~ ^[Yy]$ ]]
    then
        echo "Restarting services... (sudo password may be required)"
        sudo systemctl restart moonraker
        sudo systemctl restart klipper
        echo "Services restarted."
    else
        echo "Skipping restart."
        echo "Please restart manually to activate the plugin:"
        echo "sudo systemctl restart moonraker"
        echo "sudo systemctl restart klipper"
    fi
else
    echo "Running in non-interactive mode (Update Manager)."
    echo "Skipping manual service restart."
    echo "Moonraker should handle service restarts automatically if configured in managed_services."
fi
