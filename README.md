## About
This is code to support parsing and printing of encrypted gcode files posted from the `osx-local-app.py` slicer.

### Approaches
Primary goal is to reduce exposure/availability of unencrypted GCode, secondary goal to provide a klipper native experience for the end user (print status, stats, estiamtes, etc.)

Moonraker extension
I tried 3 approaches with a moonraker only solution
1. Process the inbound encrypted gcode file, decrypt in-memory and then used the klipper script_execute process to manually send the unencrypted gcode file to the printer
* This worked well and is viable, but requires a lot of custom handling and printer status checks that we would get for free using the native klipper print process. Things like printer state checks, job stats/history, job state emitted in logs (started, completed).

2. Create a named pipe (`hedera_slicer_pipe.py`), simulating a file in `printer_data/gcodes` where we would stream the processed/decrypted gcode from memory directly to the native klipper print process (virtual_sdcard integration)
* This crashed moonraker, likely because klippers native print process expects to have an actual file handle, not a FIFO named pipe. Abandonded

3. Combination of Moonraker extension and Klipper Extension
Klipper Extension (`hedera_decrypt.py`)
Defines a custom G-Code command `HEDERA_PRINT_FILE`
* Responsible for decrypting the G-code file and streaming the decrypted G-code commands directly to Klipper using gcode.run_script_from_command.
* Handles error detection (e.g., homing failures) and cleanup (e.g., canceling the print, turning off heaters).
* Sends UI messages (e.g., Print Started, Print Initialization Complete, Print Complete) via gcmd.respond_info.

Moonraker Extension (`hedera_slicer.py`)
A Moonraker component that exposes the /machine/hedera_slicer/slice_and_print endpoint to receive encrypted G-code from the web slicer.
* Writes the encrypted G-code to a file in the virtual_sdcard directory (e.g., /home/jeff/printer_data/gcodes/hedera_print_8.gcode).
* Schedules the print job by calling HEDERA_PRINT_FILE via klippy_apis.run_gcode.
* Monitors the print job using monitor_print_state, which listens for the "Print Initialization Complete" message via gcode_response events to confirm the print has started.
* Cleans up the encrypted G-code file after the print completes.

Currently using a combination of hedera_slicer.py, an updated version of virtual_sdcard.py and print_stats.py

## Trade-Offs:
Custom Klipper Command vs Standalone Moonraker plugin

Pros:
* Fine-Grained Control: The Klipper extension gives us direct control over G-code execution, allowing us to decrypt and stream commands securely without writing a decrypted file to disk.
* Security: Since the decrypted G-code is never written to disk, there’s less risk of exposing sensitive data.
* Custom Error Handling: We can implement custom error handling (e.g., stopping the print on specific errors) directly in the Klipper extension.

Cons:
* State Mismatch: Klipper’s `print_stats` state doesn’t transition to "printing", causing UI mismatches (e.g., "Busy" in Mainsail, "Standby" in Mobileraker) and requiring custom state monitoring in `hedera_slicer.py`.
* No Native Features: We can’t use Klipper’s native print logging, progress reporting, or error handling because we’re bypassing the `virtual_sdcard` module.
* Complex Monitoring: The Moonraker extension has to implement custom logic to monitor the print job (e.g., listening for `gcode_response` messages, estimating print duration), which adds complexity and potential points of failure.
* Error Handling Overhead: We have to handle errors (e.g., homing failures, Klipper disconnections) manually in both the Klipper and Moonraker extensions, duplicating effort that Klipper’s `virtual_sdcard` module would handle natively.
* Two-Component Architecture: Maintaining both a Klipper extension and a Moonraker extension adds complexity to the codebase and deployment process.


## Usage
1. Start the osx-local-app.py app
2. From a directory that has your slicing profiles curl:

```bash
➜  orca-exp-2 git:(main) ✗ pwd
/Users/jeff/Desktop/orca-exp-2

➜  orca-exp-2 git:(main) ✗ curl -X POST \
  -F "data={\"wallet_address\":\"0x82530DedCeaC04F2b1c8CB2e827f479E60dfe519\",\"token_id\":\"8\",\"contract_address\":\"0x385Dc80D9923338ed43D11a9A8a68B0f1F2EB413\",\"encryption_key\":\"d12591a164476f92fc0555d545bb474df96d1851b2491cac7fbd7dfb58769bc2\",\"uri\":\"hedera://0.0.1047\",\"print\":\"true\"}" \
  -F "machine_settings=@printer/ZeroG-0.4-UHF.json" \
  -F "process_settings=@process/0.20mm-draft.json" \
  -F "filament=@filament/Polymaker-Panchroma-CoPE-Teal.json" \
  http://localhost:5000/slice
{"message":"Sliced, encrypted, and sent to printer"}
```
3. This should slice the cube.stl and push encrypted gcode to the Mercury

## Configuration

### Moonraker plugin
To enable add the following to your `moonraker.conf`
```bash
# Testing Hedera Web Slicer
[hedera_slicer]
```

Expose endpoint in NGINX

```bash
$ sudo vi /etc/nginx/sites-available/mainsail

location /hedera_slicer/slice_and_print {
    proxy_pass http://apiserver/hedera_slicer/slice_and_print;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Scheme $scheme;
    # Explicitly allow POST
    limit_except POST {
        deny all;
    }
}

$sudo nginx -t
sudo systemctl reload nginx
```

Increase timeouts for Moonraker (not sure if this is necessary)
```bash
$ sudo vi /etc/nginx/sites-available/default

        # Increase timeouts for Moonraker
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;

$sudo nginx -t
sudo systemctl reload nginx
```

### Klipper Extension
* no longer in use
To enable, add the following to your `printer.cfg`
`[hedera_decrypt]`

## Debugging 
Get filtered logs from klippy.logs
`tail -n 7200  ~/printer_data/logs/klippy.log |  grep -v "Stats " | grep -v "Receive: " | grep -v "Sent " | grep -v "Received "  | grep Reset`

Get `hedera_slicer` logs from moonraker
`cat   ~/printer_data/logs/moonraker.log | grep --color=auto --color=auto "hedera_slicer"`

Query moonraker for metadata:
`curl -X GET "http://localhost:7125/server/files/metadata?filename=hedera_streamed_print.gcode"`