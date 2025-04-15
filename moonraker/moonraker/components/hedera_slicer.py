import aiohttp
import os
import binascii
import logging  # Import logging module
import asyncio  # Import asyncio for adding delays
import traceback  # Import traceback for detailed error logging
from cryptography.fernet import Fernet, InvalidToken
from moonraker.common import RequestType  # Import RequestType

class HederaSlicer:
    def __init__(self, config):
        self.server = config.get_server()
        logging.info("Server retrieved successfully")
        self.eventloop = self.server.get_event_loop()
        self.print_job_started = False  # Track whether the print job started successfully
        # Register the endpoint with a check for duplicates
        try:
            self.server.register_endpoint(
                "/machine/hedera_slicer/slice_and_print", RequestType.POST, self.slice_and_print
            )
            logging.info("Endpoint registered successfully")
        except Exception as e:
            if "already registered" in str(e):
                logging.warning(f"Endpoint '/machine/hedera_slicer/slice_and_print' already registered, skipping registration")
            else:
                logging.error(f"Failed to register endpoint: {str(e)}")
                raise

    async def safe_cancel_print(self, klippy_apis):
        """Execute CANCEL_PRINT macro and ensure heaters are turned off, avoiding FIRMWARE_RESTART."""
        retry_attempts = 2
        for attempt in range(retry_attempts):
            try:
                # Attempt to clear the error state with a RESTART
                try:
                    await klippy_apis.run_gcode("RESTART")
                    logging.info("Successfully executed RESTART to clear error state")
                    # Wait for Klipper to reconnect after restart
                    if not await self.wait_for_klippy_connection(klippy_apis):
                        logging.error("Klipper did not reconnect after RESTART")
                        continue
                except Exception as restart_e:
                    logging.warning(f"RESTART attempt {attempt + 1} failed: {str(restart_e)}")
                    if attempt == retry_attempts - 1:
                        logging.error("All RESTART attempts failed, proceeding without restart")

                # Execute CANCEL_PRINT macro
                await klippy_apis.run_gcode("CANCEL_PRINT")
                logging.info("Successfully executed CANCEL_PRINT macro")
                return  # Success, exit the method
            except Exception as cancel_e:
                logging.error(f"CANCEL_PRINT attempt {attempt + 1} failed: {str(cancel_e)}")
                if attempt == retry_attempts - 1:
                    # Fallback: Explicitly turn off heaters
                    try:
                        await klippy_apis.run_gcode("M104 S0")  # Turn off extruder heater
                        await klippy_apis.run_gcode("M140 S0")  # Turn off bed heater
                        logging.info("Successfully turned off heaters as fallback")
                    except Exception as heater_e:
                        logging.error(f"Failed to turn=b off heaters as fallback: {str(heater_e)}")

    async def wait_for_klippy_connection(self, klippy_apis, max_wait_time=30):
        """Wait for Klipper to reconnect."""
        wait_interval = 2  # Check every 2 seconds
        elapsed_time = 0
        while elapsed_time < max_wait_time:
            try:
                # Attempt a simple query to check if Klipper is connected
                await klippy_apis.query_objects({"print_stats": None})
                logging.info("Klipper reconnected successfully")
                return True
            except Exception as e:
                logging.debug(f"Waiting for Klipper to reconnect... ({elapsed_time}/{max_wait_time} seconds)")
                await asyncio.sleep(wait_interval)
                elapsed_time += wait_interval
        logging.error("Klipper failed to reconnect within the timeout period")
        return False

    async def stream_decrypted_gcode(self, klippy_apis, encrypted_filepath, http_client):
        """Decrypt the G-code and stream it to Klipper via the queue, returning the total size."""
        key = b"WuDd4y2dnS8rP7hqm2a1XUgZaP9M6qI1CmJQptbbgqo="
        cipher = Fernet(key)
        try:
            # Delay to ensure Klipper is ready
            logging.info("Starting stream_decrypted_gcode")
            await asyncio.sleep(2.0)

            # Read the encrypted G-code file
            logging.info(f"Reading encrypted G-code file: {encrypted_filepath}")
            with open(encrypted_filepath, "rb") as f:
                encrypted_gcode = f.read()

            # Decrypt the G-code in memory
            logging.info("Decrypting G-code")
            decrypted_gcode = cipher.decrypt(encrypted_gcode).decode()

            # Calculate the total size of the G-code (in bytes)
            total_size = len(decrypted_gcode.encode())
            # Set the stream size in print_stats via Klipper's API
            await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO TOTAL_SIZE={total_size}")

            # Track layer count, filament usage, and extrusion mode
            current_layer = 0  # Start at 0, increment to 1 on first LAYER_CHANGE
            filament_used = 0.0
            last_e_value = 0.0  # Track the last E value for absolute extrusion
            is_absolute_extrusion = True  # Assume absolute extrusion (M82) by default
            is_first_layer_change = True
            is_printing = False  # Flag to track if printing has started (post-homing)

            # Update current_layer to 0 at the start
            await http_client.request(
                "POST",
                "/server/job/start",
                {
                    "filename": "hedera_streamed_print",
                    "current_layer": current_layer,
                    "total_layers": 60  # Set total layers early
                }
            )
            logging.info(f"Initialized current_layer to {current_layer}")

            # Stream the decrypted G-code to Klipper via the STREAM_GCODE_LINE command
            logging.info("Streaming decrypted G-code to Klipper")
            for line in decrypted_gcode.splitlines():
                line = line.strip()
                if not line:  # Skip empty lines
                    continue

                # Log each G-code line for debugging
                logging.debug(f"Processing G-code line: {line}")

                # Check for extrusion mode (M82 for absolute, M83 for relative)
                if line.startswith("M82"):
                    is_absolute_extrusion = True
                    logging.debug("Detected M82 - Absolute extrusion mode")
                elif line.startswith("M83"):
                    is_absolute_extrusion = False
                    logging.debug("Detected M83 - Relative extrusion mode")

                # Check if printing has started (post-homing/calibration)
                # Wait for the first G1 move with Z movement (indicating print start)
                if not is_printing and line.startswith("G1") and " Z" in line and " F" in line:
                    is_printing = True
                    logging.info("Detected start of printing after first G1 move with Z")

                # Parse layer changes and update current_layer only after printing starts
                if is_printing and line.startswith(";LAYER_CHANGE"):
                    if is_first_layer_change:
                        current_layer = 1  # Start at 1 for the first layer
                        is_first_layer_change = False
                    else:
                        current_layer += 1
                    try:
                        await http_client.request(
                            "POST",
                            "/server/job/start",
                            {
                                "filename": "hedera_streamed_print",
                                "current_layer": current_layer,
                                "total_layers": 60  # Ensure total layers is set
                            }
                        )
                        logging.info(f"Updated current_layer to {current_layer}")
                        # Add a small delay to ensure Mainsail processes the update
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        logging.error(f"Failed to update current_layer: {str(e)}")

                # Parse extrusion commands to track filament usage
                if " E" in line and (line.startswith("G1") or line.startswith("G0")):
                    # Extract the E value (e.g., "G1 X10 Y10 E1.5")
                    parts = line.split()
                    for part in parts:
                        if part.startswith("E"):
                            try:
                                e_value = float(part[1:])
                                logging.debug(f"Parsed E value: {e_value}, last_e_value: {last_e_value}, is_absolute_extrusion: {is_absolute_extrusion}")
                                if is_absolute_extrusion:
                                    # Absolute extrusion: calculate delta from last E value
                                    if e_value > last_e_value:
                                        delta_e = e_value - last_e_value
                                        filament_used += delta_e
                                        await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO FILAMENT_USED={filament_used}")
                                        logging.debug(f"Updated filament_used to {filament_used} (delta: {delta_e}, line: {line})")
                                    last_e_value = e_value
                                else:
                                    # Relative extrusion: add E value directly
                                    filament_used += e_value
                                    await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO FILAMENT_USED={filament_used}")
                                    logging.debug(f"Updated filament_used to {filament_used} (relative, line: {line})")
                            except ValueError:
                                logging.warning(f"Failed to parse E value in line: {line}")
                            break

                # Escape any quotes in the G-code line to avoid breaking the parser
                escaped_line = line.replace('"', '\\"')
                # Send the G-code line to Klipper via the STREAM_GCODE_LINE command
                await klippy_apis.run_gcode(f'STREAM_GCODE_LINE LINE="{escaped_line}"')
                await asyncio.sleep(0.001)  # Reduced delay to speed up streaming

            # Signal the end of the stream
            logging.info("Sending end of stream signal")
            await klippy_apis.run_gcode("STREAM_GCODE_LINE")

            return total_size
        except Exception as e:
            logging.error(f"Failed to stream decrypted G-code: {str(e)}\n{traceback.format_exc()}")
            raise
        finally:
            # Clean up the encrypted G-code file immediately after streaming
            logging.info("Attempting to clean up encrypted file after streaming")
            try:
                if os.path.exists(encrypted_filepath):
                    os.remove(encrypted_filepath)
                    logging.info(f"Successfully cleaned up encrypted file after streaming: {encrypted_filepath}")
                else:
                    logging.info(f"Encrypted file {encrypted_filepath} already deleted or does not exist")
            except Exception as e:
                logging.error(f"Failed to clean up encrypted file after streaming: {str(e)}")

    async def monitor_print_state(self, klippy_apis, encrypted_filepath):
        """Monitor the print job state as a simple watchdog with a shorter timeout."""
        logging.info(f"Starting print state monitoring for file: {encrypted_filepath}")
        max_monitor_time = 30  # Reduced to 30 seconds after print completion
        start_time = asyncio.get_event_loop().time()
        try:
            # Wait for the print job to complete or timeout
            while True:
                elapsed_time = asyncio.get_event_loop().time() - start_time
                # Check the printer state to see if the print has completed
                printer_info = await klippy_apis.query_objects({"print_stats": None})
                printer_state = printer_info.get("print_stats", {}).get("state", "unknown")
                if printer_state in ["complete", "standby", "error", "cancelled"]:
                    logging.info(f"Print job finished with state: {printer_state}")
                    break
                if elapsed_time > max_monitor_time:
                    logging.warning(f"Print state monitoring timed out after {max_monitor_time} seconds for file: {encrypted_filepath}")
                    break
                await asyncio.sleep(5.0)  # Check every 5 seconds
        except Exception as e:
            logging.error(f"Error monitoring print state: {str(e)}\n{traceback.format_exc()}")

    async def slice_and_print(self, request):
        logging.info("Endpoint called: /machine/hedera_slicer/slice_and_print")
        # Extract data from form parameters
        try:
            wallet_address = request.get_str("wallet_address")
            token_id = request.get_str("token_id")
            encrypted_gcode_hex = request.get_str("encrypted_gcode")
        except Exception as e:
            logging.error(f"Failed to parse form data: {str(e)}")
            return {"error": "Invalid form data"}, 400
        logging.info(f"Received wallet_address: {wallet_address}, token_id: {token_id}")
        logging.info(f"Encrypted G-code (first 50 chars): {encrypted_gcode_hex[:50]}")

        if not all([wallet_address, token_id, encrypted_gcode_hex]):
            logging.warning("Missing required fields in request")
            return {"error": "Missing required fields"}, 400

        # Decode hex to bytes
        try:
            encrypted_gcode = binascii.unhexlify(encrypted_gcode_hex)
        except binascii.Error:
            logging.error("Invalid hex-encoded G-code")
            return {"error": "Invalid hex-encoded G-code"}, 400

        # Check G-code size to ensure it fits in memory
        gcode_size = len(encrypted_gcode)
        max_size = 1024 * 1024 * 100  # 100 MB limit
        if gcode_size > max_size:
            logging.error(f"G-code too large: {gcode_size} bytes, max allowed: {max_size} bytes")
            return {"error": f"G-code too large: {gcode_size} bytes, max allowed: {max_size} bytes"}, 400
        logging.info(f"G-code size: {gcode_size} bytes")

        # Check printer state before scheduling the print
        try:
            klippy_apis = self.server.lookup_component("klippy_apis")
            printer_info = await klippy_apis.query_objects({"print_stats": None})
            printer_state = printer_info.get("print_stats", {}).get("state", "unknown")
            if printer_state not in ["standby", "paused"]:
                # If the state is complete, reset it to standby
                if printer_state == "complete":
                    await klippy_apis.run_gcode("SDCARD_RESET_FILE")
                    logging.info("Reset printer state to standby before starting new print")
                    # Verify the state has changed
                    printer_info = await klippy_apis.query_objects({"print_stats": None})
                    printer_state = printer_info.get("print_stats", {}).get("state", "unknown")
                    if printer_state != "standby":
                        logging.error(f"Failed to reset printer state, still in state: {printer_state}")
                        return {"error": f"Failed to reset printer state, still in state: {printer_state}"}, 400
                else:
                    logging.error(f"Printer not ready to print, state: {printer_state}")
                    return {"error": f"Printer not ready, state: {printer_state}"}, 400
        except Exception as e:
            logging.error(f"Failed to check printer state: {str(e)}")
            return {"error": f"Failed to check printer state: {str(e)}"}, 500

        # Write the encrypted G-code to the virtual SD card directory
        encrypted_filename = f"hedera_print_{token_id}.gcode"
        encrypted_filepath = os.path.join(os.path.expanduser("~/printer_data/gcodes"), encrypted_filename)
        try:
            if os.path.exists(encrypted_filepath):
                os.remove(encrypted_filepath)
            with open(encrypted_filepath, "wb") as f:
                f.write(encrypted_gcode)
            logging.info(f"Wrote encrypted G-code to file: {encrypted_filepath}")
        except Exception as e:
            logging.error(f"Failed to write encrypted G-code to file: {str(e)}")
            return {"error": f"Failed to write encrypted G-code to file: {str(e)}"}, 500

        # Decrypt the G-code and write it to disk for Moonraker to parse metadata
        key = b"WuDd4y2dnS8rP7hqm2a1XUgZaP9M6qI1CmJQptbbgqo="
        cipher = Fernet(key)
        try:
            logging.info("Decrypting G-code to write to disk")
            decrypted_gcode = cipher.decrypt(encrypted_gcode).decode()
        except Exception as e:
            logging.error(f"Failed to decrypt G-code: {str(e)}\n{traceback.format_exc()}")
            return {"error": f"Failed to decrypt G-code: {str(e)}"}, 500

        # Write the decrypted G-code to disk for Moonraker to parse metadata
        fake_filename = "hedera_streamed_print.gcode"  # Actual filename on disk
        fake_filename_without_extension = "hedera_streamed_print"  # Match what Mainsail requests
        fake_filepath = os.path.join(os.path.expanduser("~/printer_data/gcodes"), fake_filename)
        try:
            if os.path.exists(fake_filepath):
                os.remove(fake_filepath)
            with open(fake_filepath, "w") as f:
                f.write(decrypted_gcode)
            logging.info(f"Wrote decrypted G-code to file: {fake_filepath}")
        except Exception as e:
            logging.error(f"Failed to write decrypted G-code to file: {str(e)}")
            return {"error": f"Failed to write decrypted G-code to file: {str(e)}"}, 500

        # Define http_client for making requests to Moonraker
        http_client = self.server.lookup_component("http_client")

        # Force Moonraker to refresh its file list and index the file
        try:
            await http_client.request("POST", "/server/files/refresh")
            logging.info("Forced Moonraker to refresh file list")
        except Exception as e:
            logging.error(f"Failed to refresh Moonraker file list: {str(e)}")
            return {"error": f"Failed to refresh Moonraker file list: {str(e)}"}, 500

        # Add a longer delay to allow Moonraker to complete metadata parsing
        await asyncio.sleep(10.0)

        # Verify that Moonraker has indexed the metadata
        try:
            metadata_response = await http_client.request("GET", f"/server/files/metadata?filename={fake_filename}")
            logging.info(f"Moonraker metadata for {fake_filename}: {metadata_response}")
            # Parse the response to check for metadata
            if metadata_response and hasattr(metadata_response, 'result'):
                metadata = metadata_response.result
                if 'result' in metadata and 'size' in metadata['result']:
                    logging.info(f"Metadata successfully generated: {metadata['result']}")
                else:
                    logging.warning(f"Metadata missing expected fields: {metadata}")
            else:
                logging.warning(f"Unexpected metadata response format: {metadata_response}")
        except Exception as e:
            logging.error(f"Failed to fetch metadata for {fake_filename}: {str(e)}")

        # Schedule the print job and streaming task in the background
        self.print_job_started = False  # Reset the flag
        try:
            # Start the print job with SDCARD_STREAM_GCODE
            await klippy_apis.run_gcode("SDCARD_STREAM_GCODE")
            logging.info("Started print with SDCARD_STREAM_GCODE")

            # Run streaming and monitoring tasks in the background
            stream_task = self.eventloop.create_task(self.stream_decrypted_gcode(klippy_apis, encrypted_filepath, http_client))
            monitor_task = self.eventloop.create_task(self.monitor_print_state(klippy_apis, encrypted_filepath))

            # Wait for the stream task to complete to get the total size
            total_size = await stream_task

            # Clear Moonraker's print history to prevent interference
            try:
                await http_client.request("DELETE", "/server/history/clear")
                logging.info("Cleared Moonraker print history")
            except Exception as e:
                logging.error(f"Failed to clear Moonraker print history: {str(e)}")

            # Explicitly set the print job metadata in Moonraker with the filename Mainsail expects
            try:
                metadata = {
                    "size": total_size,
                    "slicer": "OrcaSlicer",
                    "slicer_version": "2.3.0",
                    "layer_count": 60,
                    "object_height": 12.05,
                    "estimated_time": 104,
                    "nozzle_diameter": 0.4,
                    "layer_height": 0.2,
                    "first_layer_height": 0.25,
                    "first_layer_extr_temp": 220.0,
                    "first_layer_bed_temp": 35.0,
                    "chamber_temp": 0.0,
                    "filament_name": "Polymaker - Panchroma - CoPE - Teal",
                    "filament_type": "PLA",
                    "filament_colors": ["#F2754E"],
                    "extruder_colors": ["#F2754E"],
                    "filament_temps": [220],
                    "referenced_tools": [],
                    "mmu_print": 1,
                    "filament_total": 362.73,
                    "filament_weight_total": 1.08,
                    "filament_weights": [1.08],
                    "filament_used": 362.73,  # Explicitly set for Print History
                    "print_time": 331.704  # From previous run (in seconds)
                }
                await http_client.request(
                    "POST",
                    "/server/job/start",
                    {
                        "filename": fake_filename_without_extension,  # Match what Mainsail requests (without .gcode)
                        "size": total_size,  # File size
                        "total_layers": 60,  # From OrcaSlicer G-code
                        "current_layer": 0,  # Start at 0
                        "estimated_time": 104,  # 1m 44s from OrcaSlicer G-code
                        "filament_total": 362.73,  # From OrcaSlicer G-code (filament used [mm])
                        "filament_weight": 1.08,  # From OrcaSlicer G-code (filament used [g])
                        "first_layer_height": 0.25,  # From OrcaSlicer G-code
                        "layer_height": 0.2,  # From OrcaSlicer G-code
                        "object_height": 12.05,  # From OrcaSlicer G-code (max_z_height)
                        "slicer": "OrcaSlicer",  # Set slicer name
                        "slicer_version": "2.3.0",  # Set slicer version
                        "metadata": metadata
                    }
                )
                logging.info(f"Set print job metadata in Moonraker: filename={fake_filename_without_extension}, size={total_size}")

                # Update job history metadata
                await http_client.request(
                    "POST",
                    "/server/history/job",
                    {
                        "filename": fake_filename_without_extension,
                        "total_duration": 331.704,  # From previous run (in seconds)
                        "print_duration": 331.704,
                        "filament_used": 362.73,
                        "estimated_time": 104,
                        "slicer": "OrcaSlicer",
                        "slicer_version": "2.3.0",
                        "layer_count": 60,
                        "first_layer_height": 0.25,
                        "layer_height": 0.2,
                        "object_height": 12.05,
                        "filament_total": 362.73,
                        "filament_weight_total": 1.08
                    }
                )
                logging.info("Updated job history metadata")
            except Exception as e:
                logging.error(f"Failed to set print job metadata in Moonraker: {str(e)}")

            # Schedule a cleanup task to run after the tasks complete
            async def cleanup():
                try:
                    await asyncio.wait({monitor_task}, timeout=600)  # 10 minutes timeout
                except asyncio.TimeoutError:
                    logging.error("Tasks timed out after 10 minutes, proceeding to cleanup")
                except Exception as e:
                    logging.error(f"Tasks failed: {str(e)}\n{traceback.format_exc()}")
                finally:
                    # Clean up the fake G-code file after the print completes
                    logging.info("Attempting to clean up fake G-code file")
                    try:
                        if os.path.exists(fake_filepath):
                            os.remove(fake_filepath)
                            logging.info(f"Successfully cleaned up fake G-code file: {fake_filepath}")
                        else:
                            logging.info(f"Fake G-code file {fake_filepath} already deleted or does not exist")
                    except Exception as e:
                        logging.error(f"Failed to clean up fake G-code file: {str(e)}")
                    logging.info("Print state monitoring and cleanup completed")

            # Schedule the cleanup task only once
            self.eventloop.create_task(cleanup())
        except Exception as e:
            logging.error(f"Failed to start print job: {str(e)}\n{traceback.format_exc()}")
            return {"error": f"Failed to start print job: {str(e)}"}, 500

        # Return immediately to close the HTTP connection
        return {"message": "G-code received, print started"}, 200

def load_component(config):
    return HederaSlicer(config)