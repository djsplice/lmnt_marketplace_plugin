# LMNT Marketplace Plugin Installation Guide

This guide provides detailed instructions for installing, configuring, and maintaining the LMNT Marketplace Plugin for Moonraker/Klipper.

## Requirements

Before installing, ensure your system meets the following requirements:

*   **Klipper & Moonraker**: A functional installation of Klipper and Moonraker.
*   **SSH Access**: Ability to connect to your printer via SSH.
*   **Git**: `git` must be installed on your printer's host (e.g., Raspberry Pi).
*   **Slicer Configuration**: For optimal layer progress tracking, add the following to your slicer's machine profile (e.g., OrcaSlicer):
    *   **Start G-code**: `SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]`
    *   **Layer Change G-code**: `SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}`

## Quick Install (Recommended)

The easiest way to install the plugin is using the automated one-line installer. Run this command on your printer via SSH:

```bash
cd ~ && git clone https://github.com/djsplice/lmnt_marketplace_plugin.git && ./lmnt_marketplace_plugin/scripts/install.sh
```

Once the script completes, proceed to **Step 2: Configure Klipper** below.

## Manual Installation

If you prefer to install manually or the quick install fails, follow these steps:

### 1. Clone the Repository

SSH into your printer and clone the plugin repository:

```bash
cd ~
git clone https://github.com/djsplice/lmnt_marketplace_plugin.git
```

### 2. Run the Install Script

Run the helper script to set up dependencies and initial configurations:

```bash
cd ~/lmnt_marketplace_plugin
./scripts/install.sh
```

### 3. Configure Moonraker

The install script attempts to modify `moonraker.conf` automatically. Verify that the following blocks exist in your `moonraker.conf` file (usually in `~/printer_data/config/`):

```ini
[lmnt_marketplace_plugin]
marketplace_url: https://printers.lmnt.co
firebase_project_id: lmnt-dev
check_interval: 0

[encrypted_print]

[update_manager lmnt_marketplace]
type: git_repo
path: ~/lmnt_marketplace_plugin
origin: https://github.com/djsplice/lmnt_marketplace_plugin.git
primary_branch: main
env: ~/moonraker-env/bin/python
requirements: requirements.txt
install_script: scripts/install.sh
is_system_service: False
managed_services:
    moonraker
    klipper
```

### 4. Configure Klipper

Add the following sections to your `printer.cfg` file to enable the encryption bridge and G-code macros:

```ini
[encrypted_file_bridge]

[secure_print]

[gcode_macro SDCARD_PRINT_FILE]
rename_existing: BASE_SDCARD_PRINT_FILE
gcode:
    {% if params.FILENAME is defined and params.FILENAME.startswith('virtual_') %}
        SET_GCODE_FD FILENAME="{params.FILENAME}"
    {% else %}
        BASE_SDCARD_PRINT_FILE {rawparams}
    {% endif %}
```

### 5. Restart Services

Restart Moonraker and Klipper to apply all changes:

```bash
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

---

## Snapmaker U1 Installation

The Snapmaker U1 custom firmware uses an overlayfs-based root filesystem. Without `/oem/.debug`, the upper layer (where all changes live) is wiped on every boot. The installer handles U1 detection and persistence setup automatically.

### Prerequisites

- U1 with the Extended Firmware installed
- SSH access (default user: `lava`, password: `lava`)
- Root access (via `su -`) — the firmware has no `sudo`

### Installation Steps

**1. SSH into the printer and become root:**
```bash
ssh lava@<printer-ip>
su -
```

**2. Clone the plugin to the persistent location:**
```bash
cd /oem/printer_data
git clone https://github.com/djsplice/lmnt_marketplace_plugin.git
```

**3. Run the installer:**
```bash
./lmnt_marketplace_plugin/scripts/install.sh
```

The installer will:
- Detect the U1 automatically
- Relocate the plugin source to `/oem/printer_data/lmnt_marketplace_plugin` (always persistent)
- Build a Python virtual environment at that location with correct shebangs
- Back up your current WiFi config (`/etc/wpa_supplicant.conf`) to `/oem/printer_data/lmnt_install_backup/`
- Touch `/oem/.debug` to enable rootfs persistence across reboots
- Re-write the WiFi config to ensure it's captured in the now-persistent upper overlay
- Run `u1_bootstrap.sh` once to create symlinks into Moonraker and Klipper component directories

**4. Restart services:**

The U1 uses SysVinit instead of systemd. As root:
```bash
/etc/init.d/S61moonraker restart
/etc/init.d/S60klipper restart
```

### Post-Install Behavior

Once `/oem/.debug` is enabled, **everything persists** across reboots:
- Plugin source in `/oem/printer_data/lmnt_marketplace_plugin/`
- Symlinks in `/home/lava/moonraker/moonraker/components/`
- Klipper extras in `/home/lava/klipper/klippy/extras/`
- WiFi config in `/etc/wpa_supplicant.conf`
- The lmnt_decrypt binary symlink

The WiFi backup at `/oem/printer_data/lmnt_install_backup/wpa_supplicant.conf.bak` serves as a safety net. If WiFi config is ever lost, you can restore it with:
```bash
su -
/oem/printer_data/lmnt_marketplace_plugin/scripts/u1_bootstrap.sh
```

### Firmware Updates

**Critical:** Firmware updates wipe `/oem/.debug` and reset the overlay upper layer. After every firmware update:

1. Re-enable SSH and WiFi via the touchscreen
2. SSH in as root:
   ```bash
   ssh lava@<printer-ip>
   su -
   ```
3. Re-run the installer:
   ```bash
   /oem/printer_data/lmnt_marketplace_plugin/scripts/install.sh
   ```

### Running as Non-Root (lava user)

If you accidentally run `install.sh` as the `lava` user, it will **fail fast** before doing any work and display:
```
ERROR: Snapmaker U1 install requires root.

The U1 firmware does not include sudo. Please re-run as root:

  su -
  /oem/printer_data/lmnt_marketplace_plugin/scripts/install.sh
```

Simply run `su -` and re-execute the script.

### Manual Recovery

If anything gets out of sync (lost symlinks, missing WiFi, etc.), the `u1_bootstrap.sh` helper can restore everything idempotently:

```bash
su -
/oem/printer_data/lmnt_marketplace_plugin/scripts/u1_bootstrap.sh
```

This script:
- Restores WiFi config from backup if `/etc/wpa_supplicant.conf` is missing or empty
- Re-creates all Moonraker component symlinks
- Re-creates all Klipper extras symlinks
- Links the correct `lmnt_decrypt` binary for your architecture

---

## Printer Registration

To link your printer to your LMNT Marketplace account:

1.  Ensure your printer and computer are on the same network.
2.  Visit the registration page in your browser:
    `http://<printer-ip>/machine/lmnt_marketplace/ui`
    *(Replace `<printer-ip>` with your printer's actual IP address)*
3.  Follow the on-screen instructions to complete registration.

*Note: This process generates a unique keypair on your printer. The private key never leaves your device, ensuring only your printer can decrypt your purchased files.*

## Maintenance

### Updating the Plugin

To update to the latest version:

1.  **Pull the latest changes**:
    ```bash
    cd ~/lmnt_marketplace_plugin
    git pull
    ```

2.  **Run the update script**:
    ```bash
    ./scripts/update.sh
    ```

3.  **Restart services**:
    ```bash
    sudo systemctl restart moonraker
    sudo systemctl restart klipper
    ```

### Uninstalling

To remove the plugin:

```bash
cd ~/lmnt_marketplace_plugin
./scripts/uninstall.sh
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

## Troubleshooting

**View Logs**:
```bash
# Filtered Klippy logs
tail -n 7200 ~/printer_data/logs/klippy.log | grep -v "Stats " | grep -v "Receive: " | grep -v "Sent "

# Plugin logs
cat ~/printer_data/logs/moonraker.log | grep "lmnt_marketplace"
```
