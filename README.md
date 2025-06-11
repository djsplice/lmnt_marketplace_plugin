# Encrypted G-code Plugin for Klipper/Moonraker

A plugin system that enables secure handling of encrypted G-code files for Klipper-based 3D printers, integrating with Moonraker for web-based control and the LMNT Marketplace for secure printer token management.

## Installation

The plugin can be installed using the provided installation scripts:

```bash
# Clone the repository
git clone https://github.com/your-username/encrypted_gcode_plugin.git
cd encrypted_gcode_plugin

# Run the installation script
./scripts/install.sh

# Or use make
make install

# Restart Moonraker and Klipper
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

For detailed installation instructions, see [Installation Guide](docs/installation.md).

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

## Configuration

The plugin can be configured in the `moonraker.conf` file. Here's an example configuration:

```ini
[lmnt_marketplace]
check_interval: 60
debug_mode: False
marketplace_url: https://api.lmnt.market
cws_url: https://cws.lmnt.market
```

### Configuration Options

- `check_interval`: How often (in seconds) to check for new jobs (default: 60)
- `debug_mode`: Enable verbose logging including sensitive information like tokens (default: False)
- `marketplace_url`: Override the default marketplace API URL (default: https://api.lmnt.market)
- `cws_url`: Override the default CWS API URL (default: https://cws.lmnt.market)

**Note:** The system will automatically attempt to open G-code files as encrypted first; if that fails, it falls back to standard plaintext mode. This ensures a seamless experience regardless of file type.

## Secure Print Workflow

This plugin enables a secure, end-to-end printing workflow orchestrated by the LMNT Marketplace.

```
[API: Job 'ready_to_print']
         ↓
[Plugin: Polls /api/poll-print-queue]
         ↓
[Plugin: Receives Job & Crypto Materials]
         ↓
[Plugin: Downloads Encrypted G-code via HTTPS]
         ↓
[Plugin: Decrypts G-code On-Printer in Memory]
         ↓
[Plugin: Streams Raw G-code to Klipper]
         ↓
[Klipper: Executes Print]
         ↓
[Plugin: Reports Status (printing, success) to API]
```

## Components

### Moonraker Extension (`hedera_slicer.py`)
- Handles encrypted G-code file reception and management
- Provides `/machine/hedera_slicer/slice_and_print` API endpoint
- Manages print job scheduling and monitoring
- Handles file cleanup after print completion
- Displays layer information on LCD via M117 commands

### LMNT Marketplace Plugin (`lmnt_marketplace_plugin.py`)
- **Job Polling**: Periodically polls the `/api/poll-print-queue` endpoint of the LMNT Marketplace API to check for new, `ready_to_print` jobs.
- **Secure G-code Download**: Downloads the encrypted G-code file over HTTPS from the URL provided by the API.
- **On-Printer Decryption**: Manages the entire decryption process on the printer. It uses the cryptographic materials fetched from the API to decrypt the G-code just-in-time for printing, without writing the plaintext G-code to disk permanently.
- **Klipper Integration**: Streams the decrypted, raw G-code lines directly to Klipper for printing.
- **Status Reporting**: Sends real-time job status updates (`processing`, `printing`, `success`, `failure`) back to the Marketplace API.
- **Authentication**: Manages printer registration and the secure storage and automatic refresh of printer JWTs.

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

### User Login

Endpoint for user authentication with the LMNT Marketplace.

```
POST /machine/lmnt_marketplace/user_login
```

Request body:
```json
{
  "username": "user@example.com",
  "password": "your_password"
}
```

Response:
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "user_id": "user-uuid"
}
```

### Printer Registration

Endpoint to register a printer with the LMNT Marketplace.

```
POST /machine/lmnt_marketplace/register_printer
```

Request body:
```json
{
  "user_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "printer_name": "My Printer",
  "manufacturer": "Printer Brand",
  "model": "Printer Model"
}
```

Response:
```json
{
  "id": "printer-uuid",
  "user_id": "user-uuid",
  "printer_name": "My Printer",
  "manufacturer": "Printer Brand",
  "model": "Printer Model",
  "kek_id": "encrypted-key-data",
  "printer_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_expires": "2025-07-10T06:18:50.357Z"
}
```

### Printer Token Refresh

Endpoint to refresh the printer token with the LMNT Marketplace.

```
POST /machine/lmnt_marketplace/refresh_token
```

No request body is needed - the plugin uses the stored printer token.

Response:
```json
{
  "status": "success",
  "printer_id": "printer-uuid",
  "expiry": "2025-07-10T06:44:30.708000+00:00"
}
```

### Job Status Check

Endpoint to check the current job status.

```
POST /machine/lmnt_marketplace/check_jobs
```

No request body is needed.

Response:
```json
{
  "status": "success",
  "message": "Job status retrieved",
  "job_status": {
    "current_job": null,
    "queue_length": 0,
    "job_started": false,
    "last_check": "2025-06-09T23:41:44.449775"
  }
}
```

### Status Endpoint

Endpoint to get the current status of the LMNT Marketplace integration.

```
GET /machine/lmnt_marketplace/status
```

Response:
```json
{
  "auth": {
    "authenticated": true,
    "printer_id": "printer-uuid",
    "token_expiry": "2025-07-10T06:40:50.066000+00:00"
  },
  "jobs": {
    "current_job": null,
    "queue_length": 0,
    "job_started": false,
    "last_check": "2025-06-09T23:41:12.569082"
  },
  "version": "1.0.0"
}
```

### Legacy Endpoints

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

### Basic Integration Testing

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

### Advanced Component Testing

Advanced component tests (`advanced_component_tests.py`) are available for comprehensive local testing of GCode and Job managers without external dependencies:

```bash
# Run the advanced component tests
python advanced_component_tests.py
```

These tests leverage a dynamic extension system that patches the GCodeManager and JobManager classes with test-specific methods:

#### GCode Manager Extensions
- `extract_metadata`: Extracts metadata from encrypted GCode files
- `extract_thumbnails`: Extracts and saves thumbnails from encrypted GCode files
- `decrypt_and_stream`: Decrypts GCode in memory and streams it line-by-line to Klipper

#### Job Manager Extensions
- `add_job`: Adds a job to the queue
- `get_next_job`: Gets the next job from the queue
- `remove_job`: Removes a job from the queue
- `update_job_status`: Updates the status of a job
- `get_job_status`: Gets the current status of a job
- `process_job`: Processes a job through its lifecycle

#### Test Coverage
- GCode metadata extraction
- Thumbnail extraction
- Memory-efficient GCode streaming
- Error handling during decryption and streaming
- Job queue management
- Job status updates

The extension system is designed to be modular and can be easily extended with additional test methods as needed.

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