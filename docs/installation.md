# LMNT Marketplace Plugin Installation Guide

This guide provides instructions for installing the LMNT Marketplace Plugin for Moonraker/Klipper.

## Prerequisites

- A Klipper/Moonraker installation
- SSH access to your printer
- Git installed on your printer

## Installation Methods

### Method 1: Automatic Installation (Recommended)

1. SSH into your printer
2. Clone the repository:
   ```bash
   cd ~
   git clone https://github.com/your-username/encrypted_gcode_plugin.git
   cd encrypted_gcode_plugin
   ```

3. Run the installation script:
   ```bash
   ./scripts/install.sh
   ```

4. Restart Moonraker and Klipper:
   ```bash
   sudo systemctl restart moonraker
   sudo systemctl restart klipper
   ```

### Method 2: Using Make

1. SSH into your printer
2. Clone the repository:
   ```bash
   cd ~
   git clone https://github.com/your-username/encrypted_gcode_plugin.git
   cd encrypted_gcode_plugin
   ```

3. Run make install:
   ```bash
   make install
   ```

4. Restart Moonraker and Klipper:
   ```bash
   sudo systemctl restart moonraker
   sudo systemctl restart klipper
   ```

### Method 3: Manual Installation

1. SSH into your printer
2. Create a directory for the plugin:
   ```bash
   mkdir -p ~/lmnt-marketplace/component
   mkdir -p ~/lmnt-marketplace/component/lmnt_marketplace
   ```

3. Copy the plugin files:
   ```bash
   cp ~/encrypted_gcode_plugin/moonraker/moonraker/components/lmnt_marketplace_plugin.py ~/lmnt-marketplace/component/
   cp ~/encrypted_gcode_plugin/moonraker/moonraker/components/hedera_slicer.py ~/lmnt-marketplace/component/
   cp -r ~/encrypted_gcode_plugin/moonraker/moonraker/components/lmnt_marketplace/* ~/lmnt-marketplace/component/lmnt_marketplace/
   ```

4. Create symlinks in Moonraker's components directory:
   ```bash
   ln -sf ~/lmnt-marketplace/component/lmnt_marketplace_plugin.py ~/moonraker/moonraker/components/lmnt_marketplace_plugin.py
   ln -sf ~/lmnt-marketplace/component/hedera_slicer.py ~/moonraker/moonraker/components/hedera_slicer.py
   ln -sf ~/lmnt-marketplace/component/lmnt_marketplace ~/moonraker/moonraker/components/lmnt_marketplace
   ```

5. Add the plugin configuration to your moonraker.conf:
   ```
   [lmnt_marketplace_plugin]
   
   [hedera_slicer]
   ```

6. Restart Moonraker and Klipper:
   ```bash
   sudo systemctl restart moonraker
   sudo systemctl restart klipper
   ```

## Updating the Plugin

To update the plugin to the latest version:

```bash
cd ~/encrypted_gcode_plugin
git pull
./scripts/update.sh
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

Or using make:

```bash
cd ~/encrypted_gcode_plugin
git pull
make update
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
