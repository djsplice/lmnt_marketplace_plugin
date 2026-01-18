#!/bin/bash
# LMNT Marketplace Plugin Uninstaller for Moonraker
# This script removes the LMNT Marketplace Plugin components

set -e

# Define directories
PLUGIN_NAME="lmnt-marketplace"
# Use directory containing this script's parent as plugin dir to be safe relative to install location
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PLUGIN_DIR="$( dirname "$SCRIPT_DIR" )"

MOONRAKER_DIR="${HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"
PRINTER_DATA_DIR="${HOME}/printer_data"
CONFIG_DIR="${PRINTER_DATA_DIR}/config"
DATA_DIR="${PRINTER_DATA_DIR}/lmnt_marketplace"
KLIPPER_DIR="${HOME}/klipper"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}LMNT Marketplace Plugin Uninstaller${NC}"
echo "-------------------------------------"

# Check if Moonraker config exists
if [ ! -f "${CONFIG_DIR}/moonraker.conf" ]; then
    echo -e "${RED}Error: moonraker.conf not found at ${CONFIG_DIR}/moonraker.conf${NC}"
    # Fallback to home config if printer_data logic fails (older installs)
    if [ -f "${HOME}/printer_data/config/moonraker.conf" ]; then
        CONFIG_DIR="${HOME}/printer_data/config"
    elif [ -f "${HOME}/klipper_config/moonraker.conf" ]; then
        CONFIG_DIR="${HOME}/klipper_config"
    fi
fi

# 1. Ask about Data Backup
if [ -d "$DATA_DIR" ]; then
    echo -e "${YELLOW}Found plugin data directory at $DATA_DIR${NC}"
    echo "This contains your printer registration keys and downloaded files."
    read -p "Do you want to create a backup before uninstalling? (y/n): " backup_choice
    if [[ "$backup_choice" =~ ^[Yy]$ ]]; then
        BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
        BACKUP_NAME="lmnt_marketplace_backup_${BACKUP_TIMESTAMP}"
        BACKUP_PATH="${PRINTER_DATA_DIR}/${BACKUP_NAME}"
        cp -r "$DATA_DIR" "$BACKUP_PATH"
        echo -e "${GREEN}Backup created at ${BACKUP_PATH}${NC}"
    fi
fi

# 2. Stop Services
echo "Stopping Moonraker service..."
sudo systemctl stop moonraker

# 3. Remove Symlinks
echo "Removing symlinks from Moonraker components directory..."
rm -f "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"
rm -f "${COMPONENT_DIR}/encrypted_print.py"
rm -f "${COMPONENT_DIR}/encrypted_provider.py"
rm -f "${COMPONENT_DIR}/lmnt_marketplace"

# 4. Remove Klipper Extensions
if [ -f "${KLIPPER_DIR}/klippy/extras/encrypted_file_bridge.py" ]; then
    echo "Removing encrypted_file_bridge.py..."
    rm "${KLIPPER_DIR}/klippy/extras/encrypted_file_bridge.py"
fi

if [ -f "${KLIPPER_DIR}/klippy/extras/secure_print.py" ]; then
    echo "Removing secure_print.py..."
    rm "${KLIPPER_DIR}/klippy/extras/secure_print.py"
fi

# 5. Clean Config
echo "Removing configuration from moonraker.conf..."
if [ -f "${CONFIG_DIR}/moonraker.conf" ]; then
    # Create backup
    cp "${CONFIG_DIR}/moonraker.conf" "${CONFIG_DIR}/moonraker.conf.uninstall.bak"
    echo -e "${GREEN}Created backup at ${CONFIG_DIR}/moonraker.conf.uninstall.bak${NC}"

    # Use sed to delete the section block [lmnt_marketplace_plugin] ... until next section or EOF
    # Using strict anchors
    sed -i '/^\[lmnt_marketplace_plugin\]/,/^$/d' "${CONFIG_DIR}/moonraker.conf"
    sed -i '/^\[encrypted_print\]/,/^$/d' "${CONFIG_DIR}/moonraker.conf"
    sed -i '/^\[update_manager lmnt_marketplace\]/,/^$/d' "${CONFIG_DIR}/moonraker.conf"
    # Also remove any straggling comments if we added them specifically
    sed -i '/^# LMNT Marketplace Plugin configuration/d' "${CONFIG_DIR}/moonraker.conf"
    
    echo -e "${GREEN}Configuration cleaned.${NC}"
else
    echo -e "${YELLOW}Warning: moonraker.conf not found, skipping config cleanup.${NC}"
fi

# 5b. Clean printer.cfg
echo "Removing configuration from printer.cfg..."
if [ -f "${CONFIG_DIR}/printer.cfg" ]; then
    # Create backup
    cp "${CONFIG_DIR}/printer.cfg" "${CONFIG_DIR}/printer.cfg.uninstall.bak"
    echo -e "${GREEN}Created backup at ${CONFIG_DIR}/printer.cfg.uninstall.bak${NC}"

    # Remove sections using strict anchoring
    # We attempt to remove the block ending at the first blank line.
    sed -i '/^\[encrypted_file_bridge\]/,/^$/d' "${CONFIG_DIR}/printer.cfg"
    sed -i '/^\[secure_print\]/,/^$/d' "${CONFIG_DIR}/printer.cfg"
    
    # Remove the specific comment if it acts as a standalone line
    sed -i '/^# LMNT Marketplace Plugin Klipper configuration/d' "${CONFIG_DIR}/printer.cfg"
    
    echo -e "${GREEN}printer.cfg cleaned.${NC}"
else
    echo -e "${YELLOW}Warning: printer.cfg not found at ${CONFIG_DIR}/printer.cfg${NC}"
fi

# 6. Remove Plugin Code
echo "Removing plugin source directory..."
rm -rf "${PLUGIN_DIR}"

# 7. Ask about Data Removal
if [ -d "$DATA_DIR" ]; then
    echo -e "\n${RED}WARNING: Data Removal${NC}"
    read -p "Do you want to delete the data directory (${DATA_DIR})? This will delete ALL KEYS and FILES. (y/n): " delete_choice
    if [[ "$delete_choice" =~ ^[Yy]$ ]]; then
        rm -rf "$DATA_DIR"
        echo -e "${GREEN}Data directory removed.${NC}"
    else
        echo "Data directory left intact."
    fi
fi

echo -e "\n${GREEN}Uninstallation complete!${NC}"

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
        echo "Please restart manually to finish cleanup:"
        echo "sudo systemctl restart moonraker"
        echo "sudo systemctl restart klipper"
    fi
else
    echo "Running in non-interactive mode."
    echo "Please restart Klipper and Moonraker manually."
fi
