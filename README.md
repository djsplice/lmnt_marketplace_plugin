# Encrypted G-code Plugin for Klipper/Moonraker

A plugin system that enables secure handling of encrypted G-code files for Klipper-based 3D printers, integrating with Moonraker for web-based control and the LMNT Marketplace for secure printer token management.

## Installation

1.  **SSH into your printer** and clone the repository:
    ```bash
    cd ~
    git clone https://github.com/djsplice/lmnt_marketplace_plugin.git
    ```

2.  **Run the installation script**:
    ```bash
    cd ~/lmnt_marketplace_plugin
    ./scripts/install.sh
    ```

3.  **Configure `moonraker.conf`** by adding:
    ```ini
    [lmnt_marketplace_plugin]
    marketplace_url: http://192.168.1.215:8088
    cws_url: http://192.168.1.215:8080
    [encrypted_print]
    ```

4.  **Add the Klipper plugin configuration** to your `printer.cfg` file. You can usually find this file at `~/printer_data/config/printer.cfg`. Add the following lines:
    ```ini
    [encrypted_file_bridge]
    
    [secure_print]
    ```

5.  **Add the G-code macro configuration** to your `printer.cfg` file. You can usually find this file at `~/printer_data/config/printer.cfg`. Add the following lines:
    ```ini
    [gcode_macro SDCARD_PRINT_FILE]
    rename_existing: BASE_SDCARD_PRINT_FILE
    gcode:
        {% if params.FILENAME is defined and params.FILENAME.startswith('virtual_') %}
            SET_GCODE_FD FILENAME="{params.FILENAME}"
        {% else %}
            BASE_SDCARD_PRINT_FILE {rawparams}
        {% endif %}
    ```
    
6.  **Restart services**:
    ```bash
    sudo systemctl restart moonraker
    sudo systemctl restart klipper
    ```

For more details, see the [Installation Guide](docs/installation.md).

## Printer Registration and Key Management

This plugin uses a robust public-key cryptography system to ensure that only your printer can decrypt and print your files.

1.  **Key Generation**: When you register your printer with the LMNT Marketplace for the first time, the plugin automatically generates a unique and permanent **public/private keypair** on your printer.
2.  **Private Key**: The private key is stored securely on your printer and **never leaves the device**. It is your printer's unique secret for decrypting job-specific keys.
3.  **Public Key**: The public key is sent to the LMNT Marketplace and linked to your account.
4.  **Secure Job Creation**: When a new print job is created, the marketplace encrypts the G-code with a temporary, single-use key. It then encrypts that temporary key using your printer's public key.
5.  **Decryption**: Only your printer, with its unique private key, can decrypt the temporary key and, in turn, decrypt the G-code file. This ensures end-to-end security for your print jobs.

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
[lmnt_marketplace_plugin]
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

### Moonraker: Encrypted Print (`encrypted_print.py`)
- Handles in-memory decryption and streaming of encrypted G-code.
- Provides the `/machine/encrypted_print/start_print` endpoint to initiate a secure print.
- Manages the virtual file in Moonraker so that Klipper can see and print the file.

### Moonraker: LMNT Marketplace Plugin (`lmnt_marketplace_plugin.py`)
- **Job Polling**: Periodically polls the `/api/poll-print-queue` endpoint of the LMNT Marketplace API to check for new, `ready_to_print` jobs.
- **Secure G-code Download**: Downloads the encrypted G-code file over HTTPS from the URL provided by the API.
- **On-Printer Decryption**: Manages the entire decryption process on the printer. It uses the cryptographic materials fetched from the API to decrypt the G-code just-in-time for printing, without writing the plaintext G-code to disk.
- **Klipper Integration**: Streams the decrypted, raw G-code lines directly to Klipper for printing.
- **Status Reporting**: Sends real-time job status updates (`processing`, `printing`, `success`, `failure`) back to the Marketplace API.
- **Authentication**: Manages printer registration and the secure storage and automatic refresh of printer JWTs.

### Klipper: G-code Macro Implementation (`secure_print.py`)

This plugin integrates with Klipper's `virtual_sdcard.py` to handle the printing of encrypted G-code files. This is achieved without modifying Klipper's source code by leveraging a G-code macro in `printer.cfg`.

This macro intercepts the `SDCARD_PRINT_FILE` command, inspects the filename, and intelligently routes the request:
- **Encrypted Files** (e.g., `virtual_...`): Are sent to the plugin's `SET_GCODE_FD` command for secure, in-memory printing.
- **Standard Files**: Are passed directly to the original Klipper command, ensuring normal functionality is unaffected.

This approach guarantees stability, maintainability, and seamless compatibility with future Klipper updates.

## Debugging

View filtered Klippy logs:
```bash
tail -n 7200 ~/printer_data/logs/klippy.log | grep -v "Stats " | grep -v "Receive: " | grep -v "Sent " | grep -v "Received " | grep Reset
```

View Encrypted Print logs:
```bash
cat ~/printer_data/logs/moonraker.log | grep "encrypted_print"
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

Endpoint to register a printer with the LMNT Marketplace. On first registration, this generates a permanent public/private keypair on the printer, sending the public key to the marketplace. It returns a printer-specific JWT for authenticating future requests.

When a printer is registered using the /machine/lmnt_marketplace/register_printer endpoint, the API returns the following key-related material:

1. `printer_token`: This is a JSON Web Token (JWT) that is unique to the registered printer. The printer uses this token to authenticate itself for future communications with the LMNT Marketplace API, such as polling for print jobs or refreshing its token.
2. **`gcode_dek_package`**: This field contains the job's Data Encryption Key (DEK), which has been encrypted using the printer's public key. The printer uses its unique, on-device private key to decrypt this package, revealing the DEK needed to decrypt the G-code file. This ensures that only the target printer can ever decrypt the file.

```
POST /machine/lmnt_marketplace/register_printer
http://mainsail.lmnt.local/machine/lmnt_marketplace/register_printer
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

Full Example
```
curl -X POST http://mainsail.lmnt.local/machine/lmnt_marketplace/register_printer \
  -H "Content-Type: application/json" \
  -d '{
    "user_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjbG91ZC1jdXN0b2RpYWwtd2FsbGV0LXNlcnZpY2UiLCJzdWIiOiIwZTllMDRkZi0xNTNhLTQyMDItYjRhOS1kZDNlNWM2YjI2ZDUiLCJhdWQiOiJjbG91ZC1jdXN0b2RpYWwtd2FsbGV0LWNsaWVudCIsImlhdCI6MTc0OTUzNTI1MCwibmJmIjoxNzQ5NTM1MjUwLCJ1c2VySWQiOiIwZTllMDRkZi0xNTNhLTQyMDItYjRhOS1kZDNlNWM2YjI2ZDUiLCJlbWFpbCI6InB1cmNoYXNlcl8xNzQ5Mzk3MjAxOTAxQGV4YW1wbGUuY29tIiwibmFtZSI6bnVsbCwiYWNjb3VudFR5cGUiOiJsb2NhbCIsImhlZGVyYUFjY291bnRJZCI6bnVsbCwid2FsbGV0SWQiOm51bGwsImV2bUFkZHJlc3MiOm51bGwsIndhbGxldENyZWF0ZWQiOm51bGwsInJvbGVzIjpbXSwicGVybWlzc2lvbnMiOltdLCJzY29wZSI6bnVsbCwicHJvZmlsZVZlcnNpb24iOjEsImxhc3RMb2dpbiI6bnVsbCwiZGV2aWNlSWQiOm51bGwsImV4cCI6MTc0OTUzODg1MH0.uaY3Am7CZ1-fbtTOh_60J-SFz_Ydu94iiu84oUhhS88",
    "printer_name": "NachoPostmanTestPrinterz",
    "manufacturer": "NachoCorp",
    "model": "ZeroG-Mercury"
  }'
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

## Decryption and Printing Flow

The plugin uses a sophisticated in-memory file streaming technique to print encrypted files securely without ever writing the decrypted G-code to disk.

1.  **Job Initiation**: The process begins when the `JobManager` polls the LMNT Marketplace and receives a new job. It then makes an API call to the `EncryptedPrint` component (`/machine/encrypted_print/start_print`) to start the printing process.

2.  **In-Memory File Creation**: The `EncryptedPrint` component creates an anonymous file in memory using a Linux `memfd` (memory file descriptor). This creates a file that lives entirely in RAM and has no path on the filesystem.

3.  **Download and Decrypt to Memory**: The component downloads the encrypted G-code file from the URL provided by the marketplace. It decrypts the content on-the-fly and streams the plaintext G-code directly into the in-memory file. At no point is the decrypted content written to a physical disk.

4.  **File Descriptor Duplication**: A crucial step for interoperability with Klipper is duplicating the memory file's descriptor using `os.dup()`. This creates a second, independent file descriptor pointing to the same in-memory file. The original descriptor is managed by the Python process, while the duplicated one is kept open and passed to a Klipper extension.

5.  **Virtual File Announcement**: The plugin announces a new virtual file to Moonraker's `FileManager` (e.g., `_lmnt_encrypted_print.gcode`). This makes the print job visible in UIs like Mainsail and Fluidd, allowing for standard print controls (pause, cancel).

6.  **Printing with Klipper**: The plugin instructs Klipper to print the file using the standard `SDCARD_PRINT_FILE` command, referencing the virtual filename. A small Klipper extension (`encrypted_file_bridge.py`) intercepts this, looks up the duplicated file descriptor, and hands it to Klipper's `virtual_sdcard` module.

7.  **Native Klipper Streaming**: Klipper reads from the file descriptor as if it were a normal file on a physical SD card. Because the descriptor points to our in-memory file, Klipper streams the G-code directly from RAM, ensuring a secure and efficient print process.

This architecture provides the highest level of security by ensuring decrypted G-code never touches the disk, while seamlessly integrating with Klipper's native printing and UI functionalities.


## License

[Add license information]
