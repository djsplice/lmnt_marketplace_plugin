#!/bin/bash
# LMNT Marketplace Plugin Installer for Moonraker
# This script installs the LMNT Marketplace Plugin

set -e

# Define directories
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PLUGIN_NAME="lmnt-marketplace"

# -------------------------------------------------------------------------
# Resolve TARGET_HOME (where Moonraker / Klipper / printer_data live).
# On the Snapmaker U1, the printer user is `lava` and the only persistent
# location is /oem/printer_data (exposed via /home/lava/printer_data). The
# installer needs root for U1, but root's $HOME is /root which is wrong.
# So if /home/lava/printer_data points at /oem/printer_data, force the
# target paths to /home/lava regardless of who is running the script.
# -------------------------------------------------------------------------
if [ -L "/home/lava/printer_data" ] && \
   [ "$(readlink -f /home/lava/printer_data)" = "/oem/printer_data" ]; then
    TARGET_HOME="/home/lava"
else
    TARGET_HOME="${HOME}"
fi

PLUGIN_DIR="${TARGET_HOME}/${PLUGIN_NAME}"
MOONRAKER_DIR="${TARGET_HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"
CONFIG_DIR="${TARGET_HOME}/printer_data/config"
KLIPPER_DIR="${TARGET_HOME}/klipper"

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
if [ ! -d "${TARGET_HOME}/printer_data" ]; then
    echo "Standard 'printer_data' directory not found at ${TARGET_HOME}/printer_data."
    echo "This plugin requires a standard Klipper/Moonraker folder structure."
    exit 1
fi

# -------------------------------------------------------------------------
# SNAPMAKER U1 DETECTION
# -------------------------------------------------------------------------
# The U1 firmware resets /home/lava on every reboot. Only /oem/printer_data
# (the symlink target of ~/printer_data) survives. We detect the U1 by that
# symlink and re-route the install into the persistent partition.
IS_SNAPMAKER_U1=0
if [ -L "${TARGET_HOME}/printer_data" ] && \
   [ "$(readlink -f "${TARGET_HOME}/printer_data")" = "/oem/printer_data" ] && \
   [ -d "/oem" ]; then
    IS_SNAPMAKER_U1=1
    PERSISTENT_REPO_DIR="/oem/printer_data/lmnt_marketplace_plugin"
    echo "Detected Snapmaker U1 (persistent install path: ${PERSISTENT_REPO_DIR})"

    # Fail fast: the U1 install requires root to touch /oem/.debug and
    # write the wifi backup. The firmware has no sudo, so the user must
    # run via `su -`. Bail out before we do any work.
    if [ "$(id -u)" -ne 0 ]; then
        echo ""
        echo "=========================================================="
        echo " ERROR: Snapmaker U1 install requires root."
        echo ""
        echo " The U1 firmware does not include sudo. Please re-run as"
        echo " root:"
        echo ""
        echo "   su -"
        echo "   ${PERSISTENT_REPO_DIR}/scripts/install.sh"
        echo "   # (or whatever path you ran this script from)"
        echo "=========================================================="
        exit 1
    fi
fi
# -------------------------------------------------------------------------

# -------------------------------------------------------------------------
# BOOTSTRAP REPO if running via pipe or outside repo
# -------------------------------------------------------------------------

# Check if we are running from within a valid repo structure
if [ ! -f "${REPO_DIR}/moonraker/moonraker/components/lmnt_marketplace_plugin.py" ]; then
    echo "Installer running outside of plugin repository (likely via curl | bash)."
    
    REPO_DIR="${TARGET_HOME}/lmnt_marketplace_plugin"
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
# SNAPMAKER U1: relocate repo to persistent partition
# -------------------------------------------------------------------------
# On the U1 the rootfs (/home/lava) is wiped on every reboot. Move the repo
# into /oem/printer_data which always persists. The .venv is created inside
# the repo dir, so it also lives on the persistent partition.
if [ "${IS_SNAPMAKER_U1}" = "1" ] && [ "${REPO_DIR}" != "${PERSISTENT_REPO_DIR}" ]; then
    echo "Relocating plugin repo to persistent location: ${PERSISTENT_REPO_DIR}"
    mkdir -p "$(dirname "${PERSISTENT_REPO_DIR}")"
    # Always exclude .venv from the copy - shebangs in the source venv point
    # at the volatile path and would break on reboot. The DEPENDENCIES step
    # below will (re)build the venv in-place at the persistent location.
    if [ -d "${PERSISTENT_REPO_DIR}" ]; then
        echo "Updating existing persistent copy..."
        rsync -a --delete \
            --exclude='.venv' \
            "${REPO_DIR}/" "${PERSISTENT_REPO_DIR}/" 2>/dev/null || {
            # busybox cp fallback (no --exclude support)
            for entry in "${REPO_DIR}"/* "${REPO_DIR}"/.[!.]*; do
                [ -e "$entry" ] || continue
                base="$(basename "$entry")"
                [ "$base" = ".venv" ] && continue
                rm -rf "${PERSISTENT_REPO_DIR}/${base}"
                cp -rf "$entry" "${PERSISTENT_REPO_DIR}/${base}"
            done
        }
    else
        mkdir -p "${PERSISTENT_REPO_DIR}"
        for entry in "${REPO_DIR}"/* "${REPO_DIR}"/.[!.]*; do
            [ -e "$entry" ] || continue
            base="$(basename "$entry")"
            [ "$base" = ".venv" ] && continue
            cp -rf "$entry" "${PERSISTENT_REPO_DIR}/${base}"
        done
    fi

    # If a stale venv exists at the persistent path with shebangs pointing
    # outside REPO_DIR (e.g. from a previous install), nuke it so it gets
    # rebuilt with correct shebangs.
    if [ -x "${PERSISTENT_REPO_DIR}/.venv/bin/python3" ]; then
        VENV_SHEBANG="$(head -n 1 "${PERSISTENT_REPO_DIR}/.venv/bin/pip" 2>/dev/null || echo "")"
        case "${VENV_SHEBANG}" in
            *"${PERSISTENT_REPO_DIR}"*) : ;;  # ok
            *) echo "Stale venv shebang detected; rebuilding"
               rm -rf "${PERSISTENT_REPO_DIR}/.venv" ;;
        esac
    fi

    REPO_DIR="${PERSISTENT_REPO_DIR}"
    echo "REPO_DIR is now ${REPO_DIR}"
fi
# -------------------------------------------------------------------------

# -------------------------------------------------------------------------
# DEPENDENCIES (Virtual Environment)
# -------------------------------------------------------------------------
VENV_DIR="${REPO_DIR}/.venv"
echo "Setting up plugin isolated virtual environment at ${VENV_DIR}..."

# Detect Python version mismatch (can happen after firmware upgrade on U1)
SYSTEM_PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ -d "${VENV_DIR}" ]; then
    VENV_PY_LIB="${VENV_DIR}/lib/python${SYSTEM_PY_VERSION}"
    if [ ! -d "${VENV_PY_LIB}" ]; then
        echo "Python version changed (system is ${SYSTEM_PY_VERSION}); rebuilding venv..."
        rm -rf "${VENV_DIR}"
    fi
fi

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

# -------------------------------------------------------------------------
# CONFIGURE lmnt_decrypt HELPER BINARY
# -------------------------------------------------------------------------
BINARY_ROOT="${REPO_DIR}/bin"
ARCH=$(uname -m)

case "${ARCH}" in
    aarch64|arm64)  SOURCE_BINARY="${BINARY_ROOT}/lmnt_decrypt_aarch64" ;;
    armv7l|armv7)   SOURCE_BINARY="${BINARY_ROOT}/lmnt_decrypt_armv7"   ;;
    x86_64|amd64)   SOURCE_BINARY="${BINARY_ROOT}/lmnt_decrypt_x86_64"  ;;
    *)
        echo "WARNING: Unsupported architecture '${ARCH}'. The lmnt_decrypt helper binary may not be available."
        SOURCE_BINARY=""
        ;;
esac

if [ -n "${SOURCE_BINARY}" ]; then
    BINARY_PATH="${BINARY_ROOT}/lmnt_decrypt"
    
    if [ -f "${SOURCE_BINARY}" ]; then
        echo "Configuring lmnt_decrypt helper binary for ${ARCH}..."
        chmod +x "${SOURCE_BINARY}"
        # Create a stable symlink 'lmnt_decrypt' -> 'lmnt_decrypt_<arch>'
        ln -sf "${SOURCE_BINARY}" "${BINARY_PATH}"
        echo "lmnt_decrypt helper binary configured at ${BINARY_PATH}"
    else
        echo "ERROR: lmnt_decrypt binary not found for architecture '${ARCH}' at ${SOURCE_BINARY}."
        echo "Please ensure the repository was cloned correctly or run 'git pull'."
    fi
fi
# -------------------------------------------------------------------------


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
            echo -e "env: ${TARGET_HOME}/moonraker-env/bin/python" >> "${CONFIG_DIR}/moonraker.conf"
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

# -------------------------------------------------------------------------
# SNAPMAKER U1: enable rootfs persistence + run setup helper once
# -------------------------------------------------------------------------
# The U1 root filesystem is a squashfs+overlayfs combination. Without
# /oem/.debug, the overlay upper layer (/oem/overlay/upper) is wiped on
# every boot. With /oem/.debug present, the entire rootfs persists across
# reboots, including:
#   - Symlinks under /home/lava/moonraker/.../components/
#   - Klipper extras under /home/lava/klipper/klippy/extras/
#   - The lmnt_decrypt binary symlink
#   - /etc/wpa_supplicant.conf
#
# So we don't need a boot-time hook -- once persistence is on, everything
# we set up below stays put. The only delicate moment is the FIRST reboot
# after enabling /oem/.debug, when the wifi config in the (about-to-persist)
# upper layer may be empty. We snapshot and re-write it to be safe.
#
# Firmware updates remove /oem/.debug and wipe the upper overlay. After any
# firmware update the user must SSH in as root and re-run this installer.
if [ "${IS_SNAPMAKER_U1}" = "1" ]; then
    BOOTSTRAP_SCRIPT="${REPO_DIR}/scripts/u1_bootstrap.sh"
    BACKUP_DIR="/oem/printer_data/lmnt_install_backup"
    # Root requirement already enforced at the top of this script.

    # --- Preserve wifi credentials across /oem/.debug activation ---------
    mkdir -p "${BACKUP_DIR}"
    if [ -s /etc/wpa_supplicant.conf ] && \
       grep -q 'network=' /etc/wpa_supplicant.conf 2>/dev/null; then
        cp /etc/wpa_supplicant.conf "${BACKUP_DIR}/wpa_supplicant.conf.bak"
        chmod 600 "${BACKUP_DIR}/wpa_supplicant.conf.bak"
        echo "Backed up wifi credentials to ${BACKUP_DIR}/wpa_supplicant.conf.bak"
    fi

    echo "Enabling rootfs persistence (touch /oem/.debug)..."
    touch /oem/.debug

    # Force the persistent upper layer to capture wifi creds NOW.
    if [ -s "${BACKUP_DIR}/wpa_supplicant.conf.bak" ]; then
        cp "${BACKUP_DIR}/wpa_supplicant.conf.bak" /etc/wpa_supplicant.conf
        chmod 600 /etc/wpa_supplicant.conf
        echo "Re-wrote /etc/wpa_supplicant.conf to capture it in the persistent overlay"
    fi
    # ---------------------------------------------------------------------

    # Run the setup helper once to (re-)create symlinks deterministically.
    if [ -x "${BOOTSTRAP_SCRIPT}" ]; then
        echo "Running U1 setup helper: ${BOOTSTRAP_SCRIPT}"
        "${BOOTSTRAP_SCRIPT}" || true
    else
        echo "WARNING: ${BOOTSTRAP_SCRIPT} not found or not executable"
    fi

    echo ""
    echo "=========================================================="
    echo " Snapmaker U1 install complete"
    echo ""
    echo "   - Plugin source:  ${REPO_DIR}  (persistent)"
    echo "   - /oem/.debug touched -> rootfs now persists across reboots"
    echo "   - Wifi backup:    ${BACKUP_DIR}/wpa_supplicant.conf.bak"
    echo "   - Recovery tool:  ${BOOTSTRAP_SCRIPT}"
    echo ""
    echo "   IMPORTANT: Firmware updates wipe /oem/.debug and the rootfs"
    echo "   overlay. After every firmware update, SSH in as root and"
    echo "   re-run ${REPO_DIR}/scripts/install.sh"
    echo "=========================================================="
    echo ""
fi
# -------------------------------------------------------------------------

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
