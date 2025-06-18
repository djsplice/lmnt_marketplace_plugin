# ~/moonraker/moonraker/components/hedera_slicer.py
import aiohttp
import os
import binascii
import logging  # Import logging module
import asyncio  # Import asyncio for adding delays
from cryptography.fernet import Fernet, InvalidToken
from moonraker.common import RequestType  # Import RequestType

class HederaSlicer:
    def __init__(self, config):
        self.server = config.get_server()
        logging.info("Server retrieved successfully")
        self.key = b"WuDd4y2dnS8rP7hqm2a1XUgZaP9M6qI1CmJQptbbgqo="
        logging.info("Key set successfully")
        self.cipher = Fernet(self.key)
        logging.info("Cipher initialized successfully")
        self.eventloop = self.server.get_event_loop()
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
                    await asyncio.sleep(1.0)  # Wait for Klipper to restart
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
                        logging.error(f"Failed to turn off heaters as fallback: {str(heater_e)}")

    async def stream_gcode_to_klipper(self, gcode_lines):
        """Background task to stream G-code lines to Klipper with simulated native experience."""
        klippy_apis = self.server.lookup_component("klippy_apis")
        try:
            # Simulate the "printing" state by sending a custom message
            await klippy_apis.run_gcode("M118 Print Started")
            logging.info("Sent M118 Print Started to simulate printing state")

            for i, line in enumerate(gcode_lines):
                # Check printer state every 500 lines (reduced frequency)
                if i % 500 == 0:
                    try:
                        printer_info = await klippy_apis.query_objects({"print_stats": None})
                        printer_state = printer_info.get("print_stats", {}).get("state", "unknown")
                        if printer_state in ["error", "canceled"]:
                            logging.error(f"Print job failed or canceled, state: {printer_state}")
                            # Execute CANCEL_PRINT macro to ensure safe shutdown
                            await self.safe_cancel_print(klippy_apis)
                            return
                    except Exception as e:
                        logging.error(f"Failed to check printer state during streaming: {str(e)}")
                        # Stop streaming if we can't check the state (e.g., Klipper disconnected)
                        await self.safe_cancel_print(klippy_apis)
                        return

                line = line.strip()
                if line:  # Skip empty lines
                    try:
                        await klippy_apis.run_gcode(line)
                        # Removed the delay to increase streaming speed
                        # await asyncio.sleep(0.001)  # Small delay between commands
                    except Exception as e:
                        logging.error(f"Failed to send G-code line {i}: {str(e)}")
                        # Query the printer state to log the exact reason for the failure
                        try:
                            printer_info = await klippy_apis.query_objects({"print_stats": None})
                            printer_state = printer_info.get("print_stats", {}).get("state", "unknown")
                            logging.error(f"Printer state after error: {printer_state}")
                        except Exception as state_e:
                            logging.error(f"Failed to query printer state after error: {str(state_e)}")
                        # Execute CANCEL_PRINT macro to ensure safe shutdown
                        await self.safe_cancel_print(klippy_apis)
                        # Stop streaming for any G-code error
                        return
            # Successfully streamed all G-code lines
            logging.info(f"Streamed {len(gcode_lines)} G-code lines to Klipper")
            # Simulate the "complete" state and "Done printing file" message
            await klippy_apis.run_gcode("M118 Print Complete")
            await klippy_apis.run_gcode("M117 Done printing file")
            logging.info("Sent M118 Print Complete and M117 Done printing file to simulate completion")
        except Exception as e:
            logging.error(f"Background streaming failed: {str(e)}")
            # Log the final printer state on failure
            try:
                printer_info = await klippy_apis.query_objects({"print_stats": None})
                printer_state = printer_info.get("print_stats", {}).get("state", "unknown")
                logging.error(f"Final printer state on failure: {printer_state}")
            except Exception as state_e:
                logging.error(f"Failed to query final printer state: {str(state_e)}")
            # Execute CANCEL_PRINT macro to ensure safe shutdown
            await self.safe_cancel_print(klippy_apis)

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

        # Decrypt G-code
        try:
            decrypted_gcode = self.cipher.decrypt(encrypted_gcode).decode()
        except InvalidToken:
            logging.error("Decryption failed: Invalid key or corrupted G-code")
            return {"error": "Decryption failed"}, 400

        # Check G-code size to ensure it fits in memory
        gcode_size = len(decrypted_gcode)
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
                logging.error(f"Printer not ready to print, state: {printer_state}")
                return {"error": f"Printer not ready, state: {printer_state}"}, 400
        except Exception as e:
            logging.error(f"Failed to check printer state: {str(e)}")
            return {"error": f"Failed to check printer state: {str(e)}"}, 500

        # Schedule the streaming as a background task
        gcode_lines = decrypted_gcode.splitlines()
        self.eventloop.register_callback(self.stream_gcode_to_klipper, gcode_lines)
        logging.info("Scheduled G-code streaming in the background")

        # Return immediately to close the HTTP connection
        return {"message": "G-code received, streaming scheduled"}, 200

def load_component(config):
    return HederaSlicer(config)
