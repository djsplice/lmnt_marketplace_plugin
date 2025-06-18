# ~/klipper/klipper/extras/hedera_decrypt.py
import logging
from cryptography.fernet import Fernet
import time
import os

class HederaDecrypt:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.key = b"WuDd4y2dnS8rP7hqm2a1XUgZaP9M6qI1CmJQptbbgqo="
        self.cipher = Fernet(self.key)
        # Register a custom handler for HEDERA_PRINT_FILE
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command("HEDERA_PRINT_FILE", self.cmd_HEDERA_PRINT_FILE, desc="Decrypt and print Hedera G-code")

    def cmd_HEDERA_PRINT_FILE(self, gcmd):
        filename = gcmd.get("FILENAME", None)
        if filename is None:
            gcmd.error("FILENAME parameter is required")
            return

        # Get the virtual SD card directory and construct the full file path
        sdcard = self.printer.lookup_object("virtual_sdcard")
        sdpath = sdcard.sdcard_dirname  # Get the SD card directory (e.g., /home/jeff/printer_data/gcodes)
        filepath = os.path.join(sdpath, filename)
        if not os.path.exists(filepath):
            gcmd.error(f"File {filename} not found")
            return

        # Read the encrypted G-code file
        try:
            with open(filepath, "rb") as f:
                encrypted_gcode = f.read()
        except Exception as e:
            gcmd.error(f"Failed to read file {filename}: {str(e)}")
            return

        # Decrypt the G-code in memory
        try:
            decrypted_gcode = self.cipher.decrypt(encrypted_gcode).decode()
        except Exception as e:
            gcmd.error(f"Failed to decrypt G-code: {str(e)}")
            return

        # Stream the decrypted G-code to Klipper
        gcode = self.printer.lookup_object("gcode")
        try:
            gcmd.respond_info("Print Started")
            lines_processed = 0
            initialization_complete = False
            for line in decrypted_gcode.splitlines():
                line = line.strip()
                if line:  # Skip empty lines
                    try:
                        gcode.run_script_from_command(line)
                        lines_processed += 1
                        # Check for initialization commands (e.g., G28 for homing)
                        if not initialization_complete and ("G28" in line or "M190" in line or lines_processed > 50):
                            gcmd.respond_info("Print Initialization Complete")
                            initialization_complete = True
                    except Exception as e:
                        gcmd.error(f"Failed to send G-code command '{line}': {str(e)}")
                        # Stop processing further G-code lines and cancel the print
                        try:
                            gcode.run_script_from_command("CANCEL_PRINT")
                            gcmd.respond_info("Print canceled due to error")
                        except Exception as cancel_e:
                            gcmd.error(f"Failed to execute CANCEL_PRINT: {str(cancel_e)}")
                        # Fallback: Explicitly turn off heaters
                        try:
                            gcode.run_script_from_command("M104 S0")  # Turn off extruder heater
                            gcode.run_script_from_command("M140 S0")  # Turn off bed heater
                            gcmd.respond_info("Turned off heaters as fallback")
                        except Exception as heater_e:
                            gcmd.error(f"Failed to turn off heaters: {str(heater_e)}")
                        return  # Exit the loop on error
                    time.sleep(0.01)  # Add a 10ms delay between commands
            gcmd.respond_info("Print Complete")
            gcmd.respond_info("Done printing file")
        except Exception as e:
            gcmd.error(f"Failed to process G-code: {str(e)}")
            # Execute CANCEL_PRINT macro to ensure safe shutdown
            try:
                gcode.run_script_from_command("CANCEL_PRINT")
                gcmd.respond_info("Print canceled due to error")
            except Exception as cancel_e:
                gcmd.error(f"Failed to execute CANCEL_PRINT: {str(cancel_e)}")
            # Fallback: Explicitly turn off heaters
            try:
                gcode.run_script_from_command("M104 S0")  # Turn off extruder heater
                gcode.run_script_from_command("M140 S0")  # Turn off bed heater
                gcmd.respond_info("Turned off heaters as fallback")
            except Exception as heater_e:
                gcmd.error(f"Failed to turn off heaters: {str(heater_e)}")

def load_config(config):
    return HederaDecrypt(config)
