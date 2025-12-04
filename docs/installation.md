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
