# LMNT Marketplace Plugin Installation Guide

This guide provides instructions for installing the LMNT Marketplace Plugin for Moonraker/Klipper.

## Prerequisites

- A working Klipper/Moonraker installation.
- SSH access to your printer.
- `git` installed on your printer.

## Installation

1.  **SSH into your printer** and navigate to your home directory:
    ```bash
    cd ~
    ```

2.  **Clone the repository**:
    ```bash
    git clone https://github.com/djsplice/lmnt_marketplace_plugin.git
    ```

3.  **Run the installation script**:
    ```bash
    cd ~/lmnt_marketplace_plugin
    ./scripts/install.sh
    ```
    This script will copy the necessary plugin files and create symlinks in your Moonraker components directory.

4.  **Add the plugin configuration** to your `moonraker.conf` file. You can usually find this file at `~/printer_data/config/moonraker.conf`. Add the following lines:
    ```ini
    [lmnt_marketplace_plugin]

    [encrypted_print]
    ```

5.  **Restart Moonraker and Klipper** for the changes to take effect:
    ```bash
    sudo systemctl restart moonraker
    sudo systemctl restart klipper
    ```

## Updating the Plugin

To update the plugin to the latest version, pull the latest changes from the repository and run the update script.

1.  **Navigate to the repository directory**:
    ```bash
    cd ~/lmnt_marketplace_plugin
    ```

2.  **Pull the latest changes**:
    ```bash
    git pull
    ```

3.  **Run the update script**:
    ```bash
    ./scripts/update.sh
    ```

4.  **Restart Moonraker and Klipper** to apply the updates:
    ```bash
    sudo systemctl restart moonraker
    sudo systemctl restart klipper
    ```

## Uninstalling the Plugin

To uninstall the plugin:

```bash
cd ~/encrypted_gcode_plugin
./scripts/uninstall.sh
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

Or using make:

```bash
cd ~/encrypted_gcode_plugin
make uninstall
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

## Verifying the Installation

After installation, you can verify that the plugin is loaded correctly by checking the Moonraker logs:

```bash
tail -f ~/printer_data/logs/moonraker.log
```

Look for messages indicating that the LMNT Marketplace Plugin and Hedera Slicer components have been loaded.
