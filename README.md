# Encrypted G-code Plugin for Klipper/Moonraker

The official LMNT Marketplace plugin for Klipper. This component enables secure, end-to-end encrypted printing by handling on-device decryption and seamless integration with the LMNT Marketplace ecosystem. It protects creator intellectual property while delivering a native, hassle-free printing experience for users.

> **🎉 Snapmaker U1 support is now in Beta — the first commercial 3D printer in the LMNT Marketplace ecosystem.**
>
> The PAXX12 Extended Firmware is supported with a dedicated installation flow, persistent storage across reboots, and automatic WiFi credential preservation.
> See the [Snapmaker U1 Installation Guide](docs/snapmaker_u1.md) for details.

## Demo: Mutli-color print-on-demand support with the U1

[![Snapmaker U1 integration demo](https://img.youtube.com/vi/wa4FWRCFPCA/hqdefault.jpg)](https://youtu.be/wa4FWRCFPCA)

End-to-end test: LMNT marketplace purchase, print authorization, cloud slicing using native Orcaslicer profiles, encrypted G-code stream, full print on U1 (tool change included). No clear-text model downloads, or local slicing.

**U1-specific Install & configuration:** [docs/snapmaker_u1.md](docs/snapmaker_u1.md)

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

If your printer doesn't have `git` installed:
```bash
cd ~
curl -L https://github.com/djsplice/lmnt_marketplace_plugin/archive/refs/heads/main.tar.gz -o lmnt_marketplace_plugin-main.tar.gz
rm -rf lmnt_marketplace_plugin-main
tar -xzf lmnt_marketplace_plugin-main.tar.gz
rm -rf lmnt_marketplace_plugin
mv lmnt_marketplace_plugin-main lmnt_marketplace_plugin
~/lmnt_marketplace_plugin/scripts/install.sh
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
development_mode: False
marketplace_url: https://api.lmnt.co
firebase_project_id: lmnt-prod
```

*   `check_interval`: Polling interval in seconds (Default: 0/Auto-Polling).
*   `debug_mode`: Enable verbose logging (Default: False).
*   `development_mode`: Bypass certain readiness checks for local testing (Default: False).
*   `marketplace_url`: API endpoint (Default: https://api.lmnt.co).
*   `firebase_project_id`: Signaling for print job availability (Default: lmnt-prod)

### Snapmaker U1 Custom Firmware (Beta)

The U1 is the first commercial printer supported by the LMNT Marketplace plugin. It uses an overlayfs-based root filesystem that resets `/home/lava` on every reboot unless `/oem/.debug` is present. The installer handles persistence automatically.

**Quick install (run as root):**
```bash
ssh lava@<printer-ip>
su -
cd /oem/printer_data

# If git is installed:
git clone https://github.com/djsplice/lmnt_marketplace_plugin.git

# If git is not installed:
curl -L https://github.com/djsplice/lmnt_marketplace_plugin/archive/refs/heads/main.tar.gz -o lmnt_marketplace_plugin.tar.gz
rm -rf lmnt_marketplace_plugin-main lmnt_marketplace_plugin
tar -xzf lmnt_marketplace_plugin.tar.gz && rm -f lmnt_marketplace_plugin.tar.gz
mv lmnt_marketplace_plugin-main lmnt_marketplace_plugin

./lmnt_marketplace_plugin/scripts/install.sh
```

After installation, the plugin is fully persistent across reboots. Firmware updates are the only exception — they wipe `/oem/.debug`, so you must re-run the installer after each firmware update.

For the complete U1 guide, troubleshooting, and recovery steps, see the **[Snapmaker U1 Installation Guide](docs/snapmaker_u1.md)**.

### Other Custom Firmwares

For custom Klipper environments that do not map to traditional installation paradigms (e.g., `SysVinit` instead of `systemd`, or missing virtual environments), the installer attempts to auto-detect and adapt:

- **Automated Restarts:** If your firmware's primary user lacks `sudo` privileges, scripts will complete successfully but ask you to log in as `root` to manually restart services.
- **Updates:** Custom firmwares often cannot use Moonraker's built-in `[update_manager]`. To update manually, SSH into the printer and run `./scripts/update.sh`.
- **Monitoring Compatibility:** Print status polling uses Moonraker's canonical `query_objects()` signature with a fallback for legacy wrappers.

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
