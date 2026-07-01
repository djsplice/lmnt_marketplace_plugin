# Snapmaker U1 Installation Guide

The LMNT Marketplace plugin is now supported on the **Snapmaker U1** running the [PAXX12 Extended Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware) — the first commercial 3D printer in the LMNT Marketplace ecosystem. **Snapmaker U1 support is currently in Beta.**

This guide covers everything you need to install, run, and recover the plugin on a U1. For all other printers, see the [general installation guide](installation.md).

---

## How It Works

The Snapmaker U1 uses a read-only squashfs root filesystem with an overlayfs upper layer. By default, the upper layer is wiped on every boot, which means any files written under `/home/lava` or `/etc` disappear after a restart.

The Extended Firmware enables persistence when the file `/oem/.debug` exists. Once touched, the entire root filesystem overlay persists across reboots, including the plugin source, symlinks, WiFi configuration, and the `lmnt_decrypt` binary symlink.

**The only exception is firmware updates.** Every firmware update removes `/oem/.debug` and resets the overlay. After a firmware update, you must re-run the installer to restore the plugin.

---

## Requirements

- Snapmaker U1 with the **Extended Firmware** installed
- SSH access enabled (default user: `lava`, default password: `lava`)
- Root access via `su -` — the firmware does not include `sudo`
- A network connection (the installer will preserve your WiFi credentials)

---

## Installation

### 1. Connect to the printer as root

```bash
ssh lava@<printer-ip>
su -
```

The installer **must** run as root. If you run it as the `lava` user, it will fail immediately and display instructions to use `su -`.

### 2. Download the plugin to the persistent location

If `git` is installed:

```bash
cd /oem/printer_data
git clone https://github.com/djsplice/lmnt_marketplace_plugin.git
```

If `git` is **not** installed, download and extract the latest archive:

```bash
cd /oem/printer_data
curl -L https://github.com/djsplice/lmnt_marketplace_plugin/archive/refs/heads/main.tar.gz -o lmnt_marketplace_plugin.tar.gz
rm -rf lmnt_marketplace_plugin-main lmnt_marketplace_plugin
tar -xzf lmnt_marketplace_plugin.tar.gz
rm -f lmnt_marketplace_plugin.tar.gz
mv lmnt_marketplace_plugin-main lmnt_marketplace_plugin
```

`/oem/printer_data` is the persistent location that survives reboots. `/home/lava/printer_data` is a symlink that points to this same directory.

### 3. Run the installer

```bash
/oem/printer_data/lmnt_marketplace_plugin/scripts/install.sh
```

The installer will:

1. Detect the U1 automatically.
2. Relocate the plugin source to `/oem/printer_data/lmnt_marketplace_plugin/` if needed.
3. Create a Python virtual environment inside the persistent repo directory.
4. Preserve your WiFi credentials by snapshotting them to the persistent partition.
5. Touch `/oem/.debug` to enable rootfs persistence.
6. Create symlinks for Moonraker components, Klipper extras, and the `lmnt_decrypt` binary.
7. Update `moonraker.conf` and `printer.cfg` with the required plugin sections.

### 4. Restart the services

The installer will prompt you to restart services if it is running interactively. If you skip the prompt, or if you need to restart later, run as root:

```bash
/etc/init.d/S61moonraker restart
/etc/init.d/S60klipper restart
```

The U1 uses BusyBox `init` and SysVinit-style scripts, not `systemd`.

### 5. Register your printer

Once services are back up, open a browser on a computer connected to the same network:

```
http://<printer-ip>/machine/lmnt_marketplace/ui
```

Follow the on-screen instructions to link the printer to your LMNT Marketplace account.

---

## Post-Install Behavior

After `/oem/.debug` is enabled, the plugin is fully persistent across reboots:

- **Plugin source**: `/oem/printer_data/lmnt_marketplace_plugin/`
- **Moonraker components**: symlinks in `/home/lava/moonraker/moonraker/components/`
- **Klipper extras**: symlinks in `/home/lava/klipper/klippy/extras/`
- **WiFi config**: `/etc/wpa_supplicant.conf`
- **Binary symlink**: `/oem/printer_data/lmnt_marketplace_plugin/bin/lmnt_decrypt`

You do not need to re-run the installer after a normal reboot.

---

## Firmware Updates

**Important:** Snapmaker firmware updates wipe `/oem/.debug` and reset the overlayfs upper layer. After every firmware update, the plugin must be reinstalled.

### Recovery steps

1. Re-enable SSH and WiFi through the printer touchscreen.
2. SSH in as root:
   ```bash
   ssh lava@<printer-ip>
   su -
   ```
3. Re-run the installer:
   ```bash
   /oem/printer_data/lmnt_marketplace_plugin/scripts/install.sh
   ```

Your registration data (including the printer keypair) is stored in the persistent `/oem/printer_data` directory, so you typically do not need to re-register the printer after a firmware update.

---

## Manual Recovery

If symlinks, WiFi, or other files get out of sync, run the bootstrap helper as root:

```bash
su -
/oem/printer_data/lmnt_marketplace_plugin/scripts/u1_bootstrap.sh
```

The helper is idempotent and will:

- Restore `/etc/wpa_supplicant.conf` from the backup if it is missing or empty.
- Re-create Moonraker component symlinks.
- Re-create Klipper extras symlinks.
- Link the correct `lmnt_decrypt` binary for the U1's ARM64 architecture.

After running the helper, restart the services:

```bash
/etc/init.d/S61moonraker restart
/etc/init.d/S60klipper restart
```

---

## Troubleshooting

### I ran the installer as `lava` and it failed

The U1 firmware has no `sudo`. Switch to root and re-run:

```bash
su -
/oem/printer_data/lmnt_marketplace_plugin/scripts/install.sh
```

### WiFi is lost after the first reboot

The installer automatically preserves your WiFi credentials, but if they were not captured correctly:

```bash
su -
/oem/printer_data/lmnt_marketplace_plugin/scripts/u1_bootstrap.sh
/etc/init.d/S61moonraker restart
```

### Moonraker reports missing components after reboot

Run the bootstrap helper as root and restart services:

```bash
su -
/oem/printer_data/lmnt_marketplace_plugin/scripts/u1_bootstrap.sh
/etc/init.d/S61moonraker restart
/etc/init.d/S60klipper restart
```

### Plugin disappeared after a firmware update

This is expected behavior. Re-run the installer as root:

```bash
su -
/oem/printer_data/lmnt_marketplace_plugin/scripts/install.sh
```

### Viewing logs

```bash
# Plugin log output inside Moonraker
cat /home/lava/printer_data/logs/moonraker.log | grep lmnt_marketplace

# Bootstrap helper log
cat /home/lava/printer_data/logs/lmnt_bootstrap.log

# Klipper log (filtered)
tail -n 7200 /home/lava/printer_data/logs/klippy.log | grep -v "Stats " | grep -v "Receive: " | grep -v "Sent "
```

---

## Architecture Notes

The U1 is not a standard Klipper/Moonraker host. The plugin handles these differences transparently:

- **No systemd**: The installer detects SysVinit and uses `/etc/init.d/S60klipper` and `/etc/init.d/S61moonraker`.
- **No sudo**: The installer fails fast if it is not run as root.
- **Overlayfs persistence**: `/oem/.debug` is the on/off switch for the entire root filesystem.
- **Target home resolution**: The installer always resolves paths to `/home/lava` regardless of which user invokes the script.
- **Python virtualenv**: The venv is created inside the persistent repo directory so it survives reboots.

For more details about the Extended Firmware's persistence model, see the [SnapmakerU1-Extended-Firmware documentation](https://github.com/SnapmakerU1-Extended-Firmware).
