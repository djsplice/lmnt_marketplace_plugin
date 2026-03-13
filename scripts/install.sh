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

# Check if Klipper is installed
if [ ! -d "${KLIPPER_DIR}" ]; then
    echo "Klipper not found at ${KLIPPER_DIR}. Please install Klipper first."
    exit 1
fi

# Check if printer_data exists (standard Klipper install)
if [ ! -d "${HOME}/printer_data" ]; then
    echo "Standard 'printer_data' directory not found at ${HOME}/printer_data."
    echo "This plugin requires a standard Klipper/Moonraker folder structure."
    exit 1
fi

# -------------------------------------------------------------------------
# BOOTSTRAP REPO if running via pipe or outside repo
# -------------------------------------------------------------------------

# Check if we are running from within a valid repo structure
if [ ! -f "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace_plugin.py" ]; then
    echo "Installer running outside of plugin repository (likely via curl | bash)."
    
    REPO_DIR="${HOME}/lmnt_marketplace_plugin"
    REPO_URL="https://github.com/djsplice/lmnt_marketplace_plugin.git"
    
    if [ ! -d "${REPO_DIR}" ]; then
        echo "Cloning plugin repository to ${REPO_DIR}..."
        git clone "${REPO_URL}" "${REPO_DIR}"
    else
        echo "Updating existing plugin repository at ${REPO_DIR}..."
        cd "${REPO_DIR}"
        git fetch origin
        git reset --hard origin/main
    fi
else
    echo "Installer running from local repository at ${REPO_DIR}"
fi
# -------------------------------------------------------------------------

# -------------------------------------------------------------------------
# DEPENDENCIES (Virtual Environment)
# -------------------------------------------------------------------------
VENV_DIR="${REPO_DIR}/.venv"
echo "Setting up plugin isolated virtual environment at ${VENV_DIR}..."
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi
echo "Installing dependencies..."
"${VENV_DIR}/bin/pip" install --disable-pip-version-check -r "${REPO_DIR}/requirements.txt"
# -------------------------------------------------------------------------

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
        echo -e "marketplace_url: https://api.lmnt.co" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "firebase_project_id: lmnt-prod" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "check_interval: 0" >> "${CONFIG_DIR}/moonraker.conf"
        echo -e "\n[encrypted_print]" >> "${CONFIG_DIR}/moonraker.conf"
        echo "Added [lmnt_marketplace_plugin] to moonraker.conf"
    fi

    # Check if system supports update_manager by looking for ANY [update_manager...] block
    if grep -q "\[update_manager" "${CONFIG_DIR}/moonraker.conf"; then
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
        echo "Warning: [update_manager] not found in moonraker.conf. Skipping auto-update configuration."
        echo "You will need to run '~/lmnt_marketplace_plugin/scripts/update.sh' manually to update the plugin."
    fi
else
    echo "Warning: moonraker.conf not found at ${CONFIG_DIR}/moonraker.conf"
    echo "Please manually add the following to your moonraker.conf:"
    echo -e "\n[lmnt_marketplace_plugin]\n\n[encrypted_print]\n"
fi

# Update printer.cfg
# Update printer.cfg
echo "Updating printer.cfg..."
if [ -f "${CONFIG_DIR}/printer.cfg" ]; then
    SAVE_CONFIG_MARKER="#*# <---------------------- SAVE_CONFIG ---------------------->"
    
    # Helper function to insert config
    insert_config() {
        local content="$1"
        local check_grep="$2"
        local file="${CONFIG_DIR}/printer.cfg"
        
        if ! grep -q "$check_grep" "$file"; then
            if grep -qF "$SAVE_CONFIG_MARKER" "$file"; then
                # Insert before SAVE_CONFIG
                # Use a temp file to hold the content to allow safe insertion with newlines
                local tmp_conf=$(mktemp)
                echo -e "$content" > "$tmp_conf"
                
                # Use sed to read in the temp file before the marker
                # We use specific syntax to make it robust
                sed -i -e "/$SAVE_CONFIG_MARKER/e cat $tmp_conf" -e "//N" "$file" 2>/dev/null || \
                sed -i "/$SAVE_CONFIG_MARKER/i $content" "$file" 
                
                # The above sed tricks are complex/risky across versions. 
                # Simpler approach: Split file options.
                
                # Let's use a standard robust approach:
                # 1. Line number of marker
                local line_num=$(grep -nF "$SAVE_CONFIG_MARKER" "$file" | cut -d: -f1 | head -n 1)
                
                if [ -n "$line_num" ]; then
                    # Insert content at that line (shifting existing down)
                    # We can use sed to read the content file in at that address
                    sed -i "${line_num}i $content" "$file"
                    echo "Inserted config before SAVE_CONFIG block."
                else
                    # Fallback
                     echo -e "$content" >> "$file"
                fi
                rm -f "$tmp_conf" 2>/dev/null
            else
                echo -e "$content" >> "$file"
                echo "Appended config to end of file."
            fi
        fi
    }

    # Construct clean content blocks
    BLOCK1="\n# LMNT Marketplace Plugin Klipper configuration\n[encrypted_file_bridge]"
    BLOCK2="\n[secure_print]"

    # We need to verify if we can simply pass newlines to sed 'i' command.
    # On many linux sed versions, we can use literal backslash newlines or just multiple -e.
    # But a safer way is to use a temp file and `r` command in sed, OR simply split the file.
    
    # Let's use the line number + head/tail approach which is POSIX safe and robust.
    
    # 1. Encrypted File Bridge
    if ! grep -q "\[encrypted_file_bridge\]" "${CONFIG_DIR}/printer.cfg"; then
        if grep -qF "$SAVE_CONFIG_MARKER" "${CONFIG_DIR}/printer.cfg"; then
            LINE=$(grep -nF "$SAVE_CONFIG_MARKER" "${CONFIG_DIR}/printer.cfg" | cut -d: -f1 | head -n 1)
            # Create a temp file with the header + remaining file from marker
            tail -n +$LINE "${CONFIG_DIR}/printer.cfg" > "${CONFIG_DIR}/printer.cfg.tail"
            # Truncate original to before marker
            head -n $((LINE-1)) "${CONFIG_DIR}/printer.cfg" > "${CONFIG_DIR}/printer.cfg.tmp"
            
            # Append our config
            echo -e "$BLOCK1" >> "${CONFIG_DIR}/printer.cfg.tmp"
            
            # Append tail back
            cat "${CONFIG_DIR}/printer.cfg.tail" >> "${CONFIG_DIR}/printer.cfg.tmp"
            
            # Move back
            mv "${CONFIG_DIR}/printer.cfg.tmp" "${CONFIG_DIR}/printer.cfg"
            rm "${CONFIG_DIR}/printer.cfg.tail"
            echo "Inserted [encrypted_file_bridge] before SAVE_CONFIG."
        else
            echo -e "$BLOCK1" >> "${CONFIG_DIR}/printer.cfg"
             echo "Appended [encrypted_file_bridge] to printer.cfg"
        fi
    fi

    # 2. Secure Print
    if ! grep -q "\[secure_print\]" "${CONFIG_DIR}/printer.cfg"; then
         if grep -qF "$SAVE_CONFIG_MARKER" "${CONFIG_DIR}/printer.cfg"; then
            LINE=$(grep -nF "$SAVE_CONFIG_MARKER" "${CONFIG_DIR}/printer.cfg" | cut -d: -f1 | head -n 1)
            tail -n +$LINE "${CONFIG_DIR}/printer.cfg" > "${CONFIG_DIR}/printer.cfg.tail"
            head -n $((LINE-1)) "${CONFIG_DIR}/printer.cfg" > "${CONFIG_DIR}/printer.cfg.tmp"
            
            echo -e "$BLOCK2" >> "${CONFIG_DIR}/printer.cfg.tmp"
            cat "${CONFIG_DIR}/printer.cfg.tail" >> "${CONFIG_DIR}/printer.cfg.tmp"
            
            mv "${CONFIG_DIR}/printer.cfg.tmp" "${CONFIG_DIR}/printer.cfg"
            rm "${CONFIG_DIR}/printer.cfg.tail"
            echo "Inserted [secure_print] before SAVE_CONFIG."
        else
            echo -e "$BLOCK2" >> "${CONFIG_DIR}/printer.cfg"
            echo "Appended [secure_print] to printer.cfg"
        fi
    fi

else
    echo "Warning: printer.cfg not found at ${CONFIG_DIR}/printer.cfg"
    echo "Please manually add [encrypted_file_bridge] and [secure_print] to your printer.cfg (ABOVE invalid sections like SAVE_CONFIG)."
fi

echo "Installation complete!"

restart_services() {
    # Try systemd first
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet init >/dev/null 2>&1 || [ -d /run/systemd/system ]; then
        sudo -n systemctl restart moonraker 2>/dev/null || sudo systemctl restart moonraker
        sudo -n systemctl restart klipper 2>/dev/null || sudo systemctl restart klipper
        return 0
    # Try SysVinit (for Snapmaker U1 and similar custom firmwares)
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
    echo    # (optional) move to a new line
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
    fi
else
    echo "Running in non-interactive mode."
    
    # Smart Auto-Restart Logic
    PARENT_COMM=$(ps -o comm= $PPID 2>/dev/null || echo "unknown")
    
    if [[ "$PARENT_COMM" == *"python"* ]]; then
        echo "Detected execution by Moonraker Update Manager (Parent: $PARENT_COMM)."
        echo "Skipping manual service restart to prevent interrupting the update process."
    else
        echo "Detected independent execution (Parent: $PARENT_COMM)."
        echo "Attempting to restart services automatically..."
        
        if restart_services; then
            echo -e "\033[0;32mServices restarted successfully!\033[0m"
        else
            echo -e "\033[0;33mCould not auto-restart services (sudo password required or unsupported init system).\033[0m"
            echo "Please restart manually."
        fi
    fi
fi
