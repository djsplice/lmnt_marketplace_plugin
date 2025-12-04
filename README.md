# Encrypted G-code Plugin for Klipper/Moonraker

The official LMNT Marketplace plugin for Klipper. This component enables secure, end-to-end encrypted printing by handling on-device decryption and seamless integration with the LMNT Marketplace ecosystem. It protects creator intellectual property while delivering a native, hassle-free printing experience for users.

## Requirements

Before installing, ensure your system meets the following requirements:

*   **Klipper & Moonraker**: A functional installation of Klipper and Moonraker.
*   **Slicer Configuration**: For optimal layer progress tracking in Mainsail/Fluidd during encrypted prints, add the following to your slicer's machine profile (e.g., OrcaSlicer):
    *   **Start G-code**: `SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]`
    *   **Layer Change G-code**: `SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}`

## Quickstart Guide

Follow these three steps to get up and running quickly.

### 1. Install the Plugin

Run the following command on your printer via SSH:

```bash
cd ~ && git clone https://github.com/djsplice/lmnt_marketplace_plugin.git && ./lmnt_marketplace_plugin/scripts/install.sh
```

### 2. Configure Klipper

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

**Restart your services** to apply changes:
```bash
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

### 3. Register Your Printer

To link your printer to your LMNT Marketplace account:

1.  Ensure your printer and computer are on the same network.
2.  Visit the registration page in your browser:
    `http://<printer-ip>/machine/lmnt_marketplace/ui`
    *(Replace `<printer-ip>` with your printer's actual IP address)*
3.  Follow the on-screen instructions to complete registration.

*Note: This process generates a unique keypair on your printer. The private key never leaves your device, ensuring only your printer can decrypt your purchased files.*

---

## Features

- **Automatic Detection**: Seamlessly handles both encrypted and plaintext G-code files.
- **Native Integration**: Works directly with Klipper's print process and web interfaces like Mainsail.
- **Real-time Tracking**: Provides accurate print status and statistics for encrypted jobs.
- **Secure Architecture**: Uses industry-standard public-key cryptography for end-to-end security.

## Advanced Configuration

The plugin is automatically configured in `moonraker.conf` by the installer. You can customize it if needed:

```ini
[lmnt_marketplace_plugin]
check_interval: 0
debug_mode: False
marketplace_url: https://printers.lmnt.co
```

*   `check_interval`: Polling interval in seconds (Default: 0/Auto-Polling).
*   `debug_mode`: Enable verbose logging (Default: False).
*   `marketplace_url`: API endpoint (Default: https://printers.lmnt.co).

For manual installation instructions, see [docs/installation.md](docs/installation.md).

## Troubleshooting

**View Logs**:
```bash
# Filtered Klippy logs
tail -n 7200 ~/printer_data/logs/klippy.log | grep -v "Stats " | grep -v "Receive: " | grep -v "Sent "

# Plugin logs
cat ~/printer_data/logs/moonraker.log | grep "lmnt_marketplace"
```

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
