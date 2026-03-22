#!/bin/bash
# LMNT Marketplace Plugin Updater for Moonraker
# This script updates the LMNT Marketplace Plugin components

set -e

# Define directories
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PLUGIN_NAME="lmnt-marketplace"
PLUGIN_DIR="${HOME}/lmnt_marketplace_plugin"
MOONRAKER_DIR="${HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"
CONFIG_DIR="${HOME}/printer_data/config"
KLIPPER_DIR="${HOME}/klipper"

# Check if the plugin repository is available
if [ ! -d "${PLUGIN_DIR}" ]; then
    echo "Plugin repository not found at ${PLUGIN_DIR}. Please install the plugin first."
    exit 1
fi

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

# Check if Klipper is installed
if [ ! -d "${KLIPPER_DIR}" ]; then
    echo "Klipper not found at ${KLIPPER_DIR}. Please install Klipper first."
    exit 1
fi

# Check if printer_data exists
if [ ! -d "${HOME}/printer_data" ]; then
    echo "Standard 'printer_data' directory not found at ${HOME}/printer_data."
    echo "This plugin requires a standard Klipper/Moonraker folder structure."
    exit 1
fi

# Check if we are running from within a valid repo structure
if [ ! -f "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace_plugin.py" ]; then
    echo "Updater must be run from within a valid plugin repository."
    echo "Expected file not found: ${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace_plugin.py"
    exit 1
fi

# Dependencies (Virtual Environment)
VENV_DIR="${REPO_DIR}/.venv"
echo "Refreshing plugin isolated virtual environment at ${VENV_DIR}..."
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi
echo "Installing dependencies..."
"${VENV_DIR}/bin/pip" install --disable-pip-version-check -r "${REPO_DIR}/requirements.txt"

echo "Refreshing Moonraker components..."
rm -rf "${COMPONENT_DIR}/lmnt_marketplace"
rm -rf "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"
rm -rf "${COMPONENT_DIR}/encrypted_print.py"
rm -rf "${COMPONENT_DIR}/encrypted_provider.py"
rm -rf "${COMPONENT_DIR}/ui"

ln -sf "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace" "${COMPONENT_DIR}/lmnt_marketplace"
ln -sf "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace_plugin.py" "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"
ln -sf "${REPO_DIR}/moonraker/moonraker/components/encrypted_print.py" "${COMPONENT_DIR}/encrypted_print.py"
ln -sf "${REPO_DIR}/moonraker/moonraker/components/encrypted_provider.py" "${COMPONENT_DIR}/encrypted_provider.py"

if [ -d "${REPO_DIR}/moonraker/moonraker/components/ui" ]; then
    ln -sf "${REPO_DIR}/moonraker/moonraker/components/ui" "${COMPONENT_DIR}/ui"
fi

if [ -d "${REPO_DIR}/kalico_mods/extras" ]; then
    echo "Refreshing Klipper extensions..."
    mkdir -p "${KLIPPER_DIR}/klippy/extras"
    cp "${REPO_DIR}/kalico_mods/extras/"*.py "${KLIPPER_DIR}/klippy/extras/" 2>/dev/null || true
fi

echo "Verifying configuration..."
if [ -f "${CONFIG_DIR}/moonraker.conf" ]; then
    if ! grep -q "\[lmnt_marketplace_plugin\]" "${CONFIG_DIR}/moonraker.conf"; then
        echo "Warning: [lmnt_marketplace_plugin] not found in ${CONFIG_DIR}/moonraker.conf"
    fi
    if ! grep -q "\[encrypted_print\]" "${CONFIG_DIR}/moonraker.conf"; then
        echo "Warning: [encrypted_print] not found in ${CONFIG_DIR}/moonraker.conf"
    fi
else
    echo "Warning: moonraker.conf not found at ${CONFIG_DIR}/moonraker.conf"
fi

if [ -f "${CONFIG_DIR}/printer.cfg" ]; then
    if ! grep -q "\[encrypted_file_bridge\]" "${CONFIG_DIR}/printer.cfg"; then
        echo "Warning: [encrypted_file_bridge] not found in ${CONFIG_DIR}/printer.cfg"
    fi
    if ! grep -q "\[secure_print\]" "${CONFIG_DIR}/printer.cfg"; then
        echo "Warning: [secure_print] not found in ${CONFIG_DIR}/printer.cfg"
    fi
else
    echo "Warning: printer.cfg not found at ${CONFIG_DIR}/printer.cfg"
fi

echo "Update complete!"

restart_services() {
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet init >/dev/null 2>&1 || [ -d /run/systemd/system ]; then
        sudo -n systemctl restart moonraker 2>/dev/null || sudo systemctl restart moonraker
        sudo -n systemctl restart klipper 2>/dev/null || sudo systemctl restart klipper
        return 0
    elif [ -x "/etc/init.d/S61moonraker" ]; then
        if [ "$EUID" -eq 0 ]; then
            /etc/init.d/S61moonraker restart
            /etc/init.d/S60klipper restart
            return 0
        elif command -v sudo >/dev/null 2>&1; then
            sudo -n /etc/init.d/S61moonraker restart 2>/dev/null || sudo /etc/init.d/S61moonraker restart
            sudo -n /etc/init.d/S60klipper restart 2>/dev/null || sudo /etc/init.d/S60klipper restart
            return 0
        else
            echo "Cannot restart services automatically. 'sudo' is not installed and script is not running as root."
            echo "Please log in as root and manually run:"
            echo "  /etc/init.d/S61moonraker restart"
            echo "  /etc/init.d/S60klipper restart"
            return 1
        fi
    else
        return 1
    fi
}

if [ -t 0 ]; then
    echo "WARNING: Restarting Moonraker and Klipper will stop any active print jobs."
    read -p "Do you want to restart Moonraker and Klipper now? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]
    then
        echo "Restarting services... (sudo password may be required)"
        if restart_services; then
            echo "Services restarted."
        else
            echo "Could not auto-restart services (unsupported init system or lack of permissions)."
            echo "Please restart manually."
        fi
    else
        echo "Skipping restart."
        echo "Please restart Moonraker and Klipper manually to apply changes."
    fi
else
    echo "Running in non-interactive mode."
    echo "Please restart Moonraker and Klipper manually to apply changes."
fi
