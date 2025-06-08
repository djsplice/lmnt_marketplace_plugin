# Encrypted G-code Plugin for Klipper/Moonraker

A plugin system that enables secure handling of encrypted G-code files for Klipper-based 3D printers, integrating with Moonraker for web-based control and the LMNT Marketplace for secure printer token management.

## Features

- Secure G-code file handling with Fernet encryption
- **Automatic detection and support for both encrypted and plaintext G-code files**
- Seamless integration with Klipper's native print process
- Real-time print status and statistics tracking
- Web API endpoint for slice-and-print operations
- Compatible with Mainsail and other Klipper web interfaces
- **Clear separation of responsibilities between Klipper and Moonraker components**
- **LMNT Marketplace integration with secure printer token management**
- **Printer-specific encryption key (PSEK) handling for secure G-code decryption**

**Note:** The system will automatically attempt to open G-code files as encrypted first; if that fails, it falls back to standard plaintext mode. This ensures a seamless experience regardless of file type.

## Flow
```
[Encrypted G-code Upload]
         ↓
[Decryption & Metadata Extraction]
         ↓
[Print Start → print_stats notified]
         ↓
[Layer/Progress Updates in print_stats (Klipper)]
         ↓
[notify_status_update WebSocket Event]
         ↓
[Clients: Mainsail, Mobileraker, Mooncord]
         ↓
[Real-Time UI Updates]
```

## Components

### Moonraker Extension (`hedera_slicer.py`)
- Handles encrypted G-code file reception and management
- Provides `/machine/hedera_slicer/slice_and_print` API endpoint
- Manages print job scheduling and monitoring
- Handles file cleanup after print completion
- Displays layer information on LCD via M117 commands

### LMNT Marketplace Plugin (`lmnt_marketplace_plugin.py`)
- Manages printer registration with the LMNT Marketplace
- Handles secure printer JWT token storage and automatic refresh via dedicated `/api/refresh-printer-token` endpoint
- Manages printer-specific encryption keys (PSEKs) for secure G-code decryption
- Provides integration with the Custodial Wallet Service (CWS) for key management

### Klipper Modifications
- Enhanced `virtual_sdcard.py` for encrypted and plaintext G-code file operations with automatic detection and fallback
- Modified `print_stats.py` for accurate print statistics
- Secure G-code streaming implementation
- **Single source of truth for layer tracking and print statistics**

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

- Upload either encrypted or plaintext G-code files; the system will handle both transparently.
- Start the `osx-local-app.py` slicer application
- Send a slice request with your configuration:
```bash
curl -X POST \
  -F "data={\"wallet_address\":\"YOUR_WALLET\",\"token_id\":\"ID\",\"contract_address\":\"CONTRACT\",\"encryption_key\":\"KEY\",\"uri\":\"hedera://0.0.1047\",\"print\":\"true\"}" \
  -F "machine_settings=@printer/config.json" \
  -F "process_settings=@process/settings.json" \
  -F "filament=@filament/material.json" \
  http://localhost:5000/slice
```

**Note:** The system now seamlessly switches between encrypted and plaintext G-code files, improving error handling and user experience.

## Debugging

View filtered Klippy logs:
```bash
tail -n 7200 ~/printer_data/logs/klippy.log | grep -v "Stats " | grep -v "Receive: " | grep -v "Sent " | grep -v "Received " | grep Reset
```

View Hedera Slicer logs:
```bash
cat ~/printer_data/logs/moonraker.log | grep "hedera_slicer"
```

View LMNT Marketplace Plugin logs:
```bash
cat ~/printer_data/logs/moonraker.log | grep "lmnt_marketplace"
```

## LMNT Marketplace API Endpoints

### Printer Token Refresh
The system uses a dedicated endpoint for refreshing printer tokens:

```
POST /api/refresh-printer-token
```

This endpoint:
- Validates the current printer token via middleware
- Issues a new token with extended expiration (30 days)
- Returns the new token and expiry date
- Requires printer-specific JWT authentication
- Prevents non-printer tokens from being refreshed

The printer plugin automatically handles token refresh when tokens approach expiration.

## Testing

A standalone test script (`test_marketplace_integration.py`) is available for testing the LMNT Marketplace integration:

```bash
# Run the full test with login credentials
python test_marketplace_integration.py --email your@email.com --password yourpassword

# Test only token refresh
python test_marketplace_integration.py --refresh-only

# Test only decryption
python test_marketplace_integration.py --decrypt-only

# Enable debug logging
python test_marketplace_integration.py --debug
```

This script tests:
- User login to the Custodial Wallet Service (CWS)
- Printer registration with the Marketplace API
- Printer token refresh flow
- PSEK decryption via CWS
- Simulated G-code encryption/decryption

Query file metadata:
```bash
curl -X GET "http://localhost:7125/server/files/metadata?filename=hedera_streamed_print.gcode"
```

## Decryption and Streaming Flow (HederaSlicer)
1. Encrypted File is Read
The encrypted G-code file is read entirely into memory:
python
CopyInsert
with open(encrypted_filepath, "rb") as f:
    encrypted_gcode = f.read()

2. Decryption (In-Memory)
The entire file is decrypted in memory using Fernet:
python
CopyInsert
decrypted_gcode = cipher.decrypt(encrypted_gcode).decode()
At this point, the entire decrypted G-code is held in memory as a string.

3. Chunking for Streaming
The decrypted G-code is split into lines:
python
CopyInsert
lines = decrypted_gcode.splitlines()
The code then processes these lines for metadata extraction (e.g., layer count, thumbnails, etc.).

4. Streaming to Klipper
The lines are sent one by one to Klipper via the STREAM_GCODE_LINE command (see lines 627–629 in your grep results):
python
CopyInsert
await klippy_apis.run_gcode(f'STREAM_GCODE_LINE LINE="{escaped_line}"')
await asyncio.sleep(0.001)  # Small delay to avoid flooding
This is a true streaming process: the file is not saved as a decrypted file on disk; instead, it is streamed line-by-line from memory.

5. No Decrypted File on Disk
The decrypted G-code is never written to the filesystem as a whole file. It exists only in memory during streaming. Only metadata or thumbnails might be temporarily written for inspection, but not the main G-code.

6. Integration with virtual_sdcard
Klipper receives each line as if it were reading from a file, but in reality, it’s being fed lines over the API.
This allows the print to proceed as if it was reading from a virtual SD card, but with the added security and flexibility of on-the-fly decryption and streaming.

Summary Table
| Step | File on Disk? | In Memory? | Streaming? | Notes | 
|-----------------|--------------|------------|------------|----------------------------------------| 
| Encrypted Read | Yes | Yes | No | Reads encrypted file from disk | 
| Decrypt | No | Yes | No | Decrypts entire file in memory | 
| Chunk/Stream | No | Yes | Yes | Streams lines to Klipper one at a time | 
| Decrypted File | No | Yes | Yes | Never saved as a decrypted file |

Conclusion
You are correct: The file is never fully decrypted and saved to disk. The decrypted data exists only in memory and is streamed to Klipper line-by-line.
This approach is secure (no decrypted file left on disk) and efficient, but does require enough RAM to hold the largest decrypted G-code you expect to process.
If you want to explore true chunked decryption (decrypting and streaming in blocks, not the whole file at once), that would require a different encryption mode (not Fernet) and more complex logic.

## License

[Add license information]