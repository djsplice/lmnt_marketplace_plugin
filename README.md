# Encrypted G-code Plugin for Klipper/Moonraker

A plugin system that enables secure handling of encrypted G-code files for Klipper-based 3D printers, integrating with Moonraker for web-based control.

## Features

- Secure G-code file handling with Fernet encryption
- Seamless integration with Klipper's native print process
- Real-time print status and statistics tracking
- Web API endpoint for slice-and-print operations
- Compatible with Mainsail and other Klipper web interfaces

## Components

### Moonraker Extension (`hedera_slicer.py`)
- Handles encrypted G-code file reception and management
- Provides `/machine/hedera_slicer/slice_and_print` API endpoint
- Manages print job scheduling and monitoring
- Handles file cleanup after print completion

### Klipper Modifications
- Enhanced `virtual_sdcard.py` for encrypted file operations
- Modified `print_stats.py` for accurate print statistics
- Secure G-code streaming implementation

## Installation

### 1. Moonraker Configuration
Add to your `moonraker.conf`:
```ini
[hedera_slicer]
```

### 2. NGINX Configuration
Add to `/etc/nginx/sites-available/mainsail`:
```nginx
location /hedera_slicer/slice_and_print {
    proxy_pass http://apiserver/hedera_slicer/slice_and_print;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Scheme $scheme;
    limit_except POST {
        deny all;
    }
}
```

Optionally, increase timeouts in `/etc/nginx/sites-available/default`:
```nginx
proxy_read_timeout 300s;
proxy_connect_timeout 300s;
proxy_send_timeout 300s;
```

Apply NGINX changes:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Usage

1. Start the `osx-local-app.py` slicer application
2. Send a slice request with your configuration:
```bash
curl -X POST \
  -F "data={\"wallet_address\":\"YOUR_WALLET\",\"token_id\":\"ID\",\"contract_address\":\"CONTRACT\",\"encryption_key\":\"KEY\",\"uri\":\"hedera://0.0.1047\",\"print\":\"true\"}" \
  -F "machine_settings=@printer/config.json" \
  -F "process_settings=@process/settings.json" \
  -F "filament=@filament/material.json" \
  http://localhost:5000/slice
```

## Debugging

View filtered Klippy logs:
```bash
tail -n 7200 ~/printer_data/logs/klippy.log | grep -v "Stats " | grep -v "Receive: " | grep -v "Sent " | grep -v "Received " | grep Reset
```

View Hedera Slicer logs:
```bash
cat ~/printer_data/logs/moonraker.log | grep "hedera_slicer"
```

Query file metadata:
```bash
curl -X GET "http://localhost:7125/server/files/metadata?filename=hedera_streamed_print.gcode"
```

## License

[Add license information]