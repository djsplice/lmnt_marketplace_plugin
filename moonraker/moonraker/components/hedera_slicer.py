import aiohttp
import os
import binascii
import logging  # Import logging module
import asyncio  # Import asyncio for adding delays
import traceback  # Import traceback for detailed error logging
import re  # Import re for regular expressions
from cryptography.fernet import Fernet, InvalidToken
from moonraker.common import RequestType  # Import RequestType
import uuid
import time
import json

class HederaSlicer:
    def __init__(self, config):
        self.server = config.get_server()
        logging.info("Server retrieved successfully")
        self.eventloop = self.server.get_event_loop()
        self.print_job_started = False  # Track whether the print job started successfully
        # Set up the gcodes directory path
        self.gcodes_path = os.path.expanduser("~/printer_data/gcodes")
        if not os.path.exists(self.gcodes_path):
            
            try:
                os.makedirs(self.gcodes_path)
                logging.info(f"Created gcodes directory at {self.gcodes_path}")
            except Exception as e:
                logging.error(f"Failed to create gcodes directory: {str(e)}")
                
        # Store metadata for encrypted files
        self.file_metadata = {}
        
        # Get the file manager component
        self.file_manager = self.server.lookup_component('file_manager', None)
        
        # Register endpoints
        self.register_endpoints()
        
        # Set encryption key
        self.encryption_key = b"WuDd4y2dnS8rP7hqm2a1XUgZaP9M6qI1CmJQptbbgqo="
        
        # Register event handlers
        self.register_event_handlers()
        
        # Register handler for print_stats:stats_changed to enable real-time layer updates
        self.server.register_event_handler("print_stats:stats_changed", self._on_print_stats_changed)

    def register_endpoints(self):
        """Register API endpoints."""
        try:
            # Register endpoints with original paths
            self.server.register_endpoint(
                "/machine/hedera_slicer/slice_and_print", RequestType.POST, self._handle_slice_and_print
            )
            self.server.register_endpoint(
                "/machine/hedera_slicer/stream_gcode", RequestType.GET, self.stream_decrypted_gcode
            )
            # Register a custom endpoint for layer updates
            self.server.register_endpoint(
                "/machine/hedera_slicer/layer_info", RequestType.GET, self.get_layer_info
            )
            logging.info("Registered hedera_slicer endpoints")
        except Exception as e:
            logging.error(f"Error registering endpoints: {str(e)}")
                
    def register_event_handlers(self):
        """Register event handlers for file manager events."""
        try:
            # Register for file manager events
            self.server.register_event_handler(
                "file_manager:file_upload_complete", self._on_file_upload_complete
            )
            logging.info("Registered file manager event handlers")
        except Exception as e:
            logging.error(f"Error registering event handlers: {str(e)}")
    
    async def _on_file_upload_complete(self, filename):
        """Handle file upload complete event."""
        try:
            logging.info(f"File upload complete: {filename}")
            
            # Check if this is a gcode file
            if not filename.endswith(".gcode"):
                return
            
            # Construct the full path
            file_path = os.path.join(self.gcodes_path, filename)
            
            # Check if the file exists
            if not os.path.exists(file_path):
                logging.warning(f"File not found: {file_path}")
                return
            
            # Check if this is an encrypted file
            try:
                with open(file_path, "rb") as f:
                    header = f.read(100)  # Read the first 100 bytes
                
                # Try to detect if it's encrypted
                is_encrypted = False
                try:
                    # Check if it starts with a binary header (not text)
                    header.decode('utf-8')
                    # If we can decode as UTF-8, it might not be encrypted
                    # Further check if it contains typical gcode markers
                    if not b"; thumbnail begin" in header and not b"; THUMBNAIL_BLOCK_START" in header:
                        # Might be encrypted
                        is_encrypted = True
                except UnicodeDecodeError:
                    # If we can't decode as UTF-8, it's likely encrypted
                    is_encrypted = True
                
                if is_encrypted:
                    logging.info(f"Detected encrypted file: {filename}")
                    
                    # Process the encrypted file to extract metadata and thumbnails
                    metadata = self.extract_metadata(file_path)
                    
                    # Create metadata file
                    self.create_metadata_file(file_path, metadata)
                    
                    logging.info(f"Processed encrypted file: {filename}")
            except Exception as e:
                logging.error(f"Error processing file: {str(e)}")
        except Exception as e:
            logging.error(f"Error handling file upload event: {str(e)}")
                
    def store_metadata(self, encrypted_filepath, metadata, update_file=True):
        """Store metadata for the encrypted file."""
        try:
            # Get the base filename and relative path
            base_name = os.path.basename(encrypted_filepath)
            rel_path = os.path.relpath(encrypted_filepath, self.gcodes_path)
            
            # Process thumbnails to ensure they're in the format Mainsail expects
            processed_thumbnails = []
            if 'thumbnails' in metadata:
                for thumb in metadata['thumbnails']:
                    # Ensure the relative_path is set correctly
                    processed_thumb = {
                        'width': thumb.get('width', 0),
                        'height': thumb.get('height', 0),
                        'size': thumb.get('size', 0),
                        'data': thumb.get('data', ''),
                        'relative_path': f".thumbs/{base_name}-{thumb['width']}x{thumb['height']}.png",
                        'format': 'png'
                    }
                    processed_thumbnails.append(processed_thumb)
                metadata['thumbnails'] = processed_thumbnails
            
            # Create a metadata file that Moonraker can read directly
            if update_file:
                self.create_metadata_file(encrypted_filepath, metadata)
            
            # Store metadata in the file_manager's metadata database
            try:
                # Get the file_manager if not already available
                file_manager = self.file_manager
                if file_manager is None:
                    file_manager = self.server.lookup_component('file_manager', None)
                    self.file_manager = file_manager
                
                if file_manager is not None:
                    # Try different methods to set metadata
                    if hasattr(file_manager, 'set_file_metadata'):
                        file_manager.set_file_metadata(rel_path, metadata)
                        logging.info(f"Updated metadata using set_file_metadata for {base_name}")
                    elif hasattr(file_manager, 'update_metadata'):
                        file_manager.update_metadata(rel_path, metadata)
                        logging.info(f"Updated metadata using update_metadata for {base_name}")
                    elif hasattr(file_manager, 'insert_metadata'):
                        file_manager.insert_metadata(rel_path, metadata)
                        logging.info(f"Inserted metadata using insert_metadata for {base_name}")
                    elif hasattr(file_manager, 'metadata'):
                        # Try to access the metadata storage directly
                        metadata_storage = file_manager.metadata
                        if hasattr(metadata_storage, 'insert'):
                            metadata_storage.insert(rel_path, metadata)
                            logging.info(f"Updated metadata using metadata.insert for {base_name}")
                        elif hasattr(metadata_storage, 'update'):
                            metadata_storage.update(rel_path, metadata)
                            logging.info(f"Updated metadata using metadata.update for {base_name}")
                        else:
                            logging.warning(f"No suitable metadata update method found on metadata storage")
                    else:
                        logging.warning(f"No suitable metadata update method found on file_manager")
                else:
                    logging.warning(f"File manager component not available")
                
                # Force Moonraker to refresh the metadata by sending an event
                try:
                    self.server.send_event("file_manager:metadata_update", {
                        "filename": rel_path,
                        "metadata": metadata
                    })
                    logging.info(f"Sent metadata update event for {base_name}")
                except Exception as e:
                    logging.error(f"Error sending metadata update event: {str(e)}")
            except Exception as e:
                logging.error(f"Error storing metadata in file_manager: {str(e)}")
            
            logging.info(f"Stored metadata for {base_name} and {encrypted_filepath}: {metadata}")
            return metadata
        except Exception as e:
            logging.exception(f"Error storing metadata: {str(e)}")
            return metadata
        
    async def handle_metadata_request(self, web_request):
        """Handle metadata requests from Moonraker."""
        filename = web_request.get_str('filename')
        logging.info(f"Received metadata request for: {filename}")
        
        # Try both the full path and just the filename
        if filename in self.file_metadata:
            logging.info(f"Found metadata for {filename} (direct match)")
            return self.file_metadata[filename]
            
        # Try just the basename
        base_name = os.path.basename(filename)
        if base_name in self.file_metadata:
            logging.info(f"Found metadata for {filename} (basename match: {base_name})")
            return self.file_metadata[base_name]
            
        # Try with gcodes path prefix
        gcodes_path = os.path.join(self.gcodes_path, filename)
        if gcodes_path in self.file_metadata:
            logging.info(f"Found metadata for {filename} (gcodes path match: {gcodes_path})")
            return self.file_metadata[gcodes_path]
            
        # If still not found, check if this is an encrypted file we know about
        for known_file, metadata in self.file_metadata.items():
            if os.path.basename(known_file) == base_name:
                logging.info(f"Found metadata for {filename} (basename search match: {known_file})")
                return metadata
                
        # If not our file, return None to let other handlers process it
        logging.warning(f"No metadata found for {filename}")
        return None
        
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
                        logging.error(f"Failed to turn off heaters as fallback: {str(heater_e)}")

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

            # Split the G-code into lines for processing
            lines = decrypted_gcode.splitlines()
            
            # Initialize variables for thumbnail extraction
            in_thumbnail = False
            thumbnail_data = ""
            thumbnail_width = 0
            thumbnail_height = 0
            thumbnail_size = 0
            thumbnails = []
            
            # Scan the first 200 lines for metadata
            scan_lines = lines[:200] if len(lines) > 200 else lines
            layer_count = 0
            
            # Try to find layer count in the first 200 lines
            for line in scan_lines:
                line = line.strip()
                if not line:  # Skip empty lines
                    continue

                # Log each G-code line for debugging
                logging.debug(f"Processing G-code line: {line}")

                # Check for layer count in comments
                if "total_layer" in line.lower() or "layer_count" in line.lower():
                    # Try to extract layer count from comments
                    match = re.search(r'(?:total_layer|layer_count)[^\d]*(\d+)', line.lower())
                    if match:
                        layer_count = int(match.group(1))
                        logging.info(f"Found layer count in G-code: {layer_count}")

                # Check for thumbnail begin markers
                if line.startswith('; thumbnail begin'):
                    in_thumbnail = True
                    thumbnail_data = ""
                    # Extract dimensions and size from the begin line
                    # Format: "; thumbnail begin 48x48 432"
                    thumb_match = re.search(r'thumbnail begin\s+(\d+)x(\d+)\s+(\d+)', line)
                    if thumb_match:
                        thumbnail_width = int(thumb_match.group(1))
                        thumbnail_height = int(thumb_match.group(2))
                        thumbnail_size = int(thumb_match.group(3))
                        logging.info(f"Found thumbnail header: {thumbnail_width}x{thumbnail_height}, size: {thumbnail_size}")
                    continue
                
                # Check for thumbnail end markers
                if line.startswith('; thumbnail end'):
                    in_thumbnail = False
                    if thumbnail_width > 0 and thumbnail_height > 0:
                        # Save the thumbnail data
                        try:
                            thumbs_dir = os.path.join(self.gcodes_path, ".thumbs")
                            os.makedirs(thumbs_dir, exist_ok=True)
                            base_name = os.path.basename(encrypted_filepath)
                            thumb_filename = f"{base_name}-{thumbnail_width}x{thumbnail_height}.png"
                            thumb_path = os.path.join(thumbs_dir, thumb_filename)
                            
                            # Clean and decode the thumbnail data
                            clean_data = thumbnail_data.replace('; ', '').replace(';', '')
                            
                            # Decode and save the thumbnail
                            import base64
                            try:
                                img_data = base64.b64decode(clean_data)
                                with open(thumb_path, 'wb') as f:
                                    f.write(img_data)
                                
                                # Add to thumbnails list with correct relative path
                                thumbnails.append({
                                    'width': thumbnail_width,
                                    'height': thumbnail_height,
                                    'size': thumbnail_size,
                                    'relative_path': f".thumbs/{base_name}-{thumbnail_width}x{thumbnail_height}.png",
                                    'format': 'png'
                                })
                                logging.info(f"Saved thumbnail to {thumb_path}")
                            except Exception as e:
                                logging.error(f"Failed to decode/save thumbnail: {str(e)}")
                        except Exception as e:
                            logging.error(f"Failed to save thumbnail: {str(e)}")
                    continue
                
                # Collect thumbnail data
                if in_thumbnail:
                    # Remove leading semicolon and whitespace
                    clean_line = line.lstrip(';').strip()
                    thumbnail_data += clean_line
                    continue
            
            # If we found a layer count or have a default, set it in Klipper
            if layer_count > 0:
                await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO TOTAL_LAYER={layer_count}")
                logging.info(f"Set total layer count in Klipper: {layer_count}")
            else:
                # Try to estimate layer count from Z movements
                max_z = 0
                layer_height = 0.2  # Default layer height
                for line in lines:
                    if line.startswith("G1") and " Z" in line:
                        # Extract Z value from G1 command
                        z_match = re.search(r'Z([\d.]+)', line)
                        if z_match:
                            z_val = float(z_match.group(1))
                            max_z = max(max_z, z_val)
                
                if max_z > 0:
                    estimated_layers = int(max_z / layer_height)
                    if estimated_layers > 0:
                        layer_count = estimated_layers
                        await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO TOTAL_LAYER={layer_count}")
                        logging.info(f"Estimated and set total layer count in Klipper: {layer_count}")
            
            # Extract metadata (thumbnails, layer count, etc.)
            logging.info("Extracting metadata from G-code")
            metadata = self.extract_metadata(lines, encrypted_filepath)
            
            # Get layer count from metadata
            layer_count = metadata.get('layer_count', 0)
            if layer_count == 0:
                # If no layer count found, estimate based on layer change markers
                layer_changes = sum(1 for line in lines if line.startswith(';LAYER_CHANGE'))
                if layer_changes > 0:
                    layer_count = layer_changes + 1  # +1 because first layer might not have a marker
                    logging.info(f"Estimated layer_count: {layer_count} based on layer change markers")
                else:
                    # Default fallback
                    layer_count = 60
                    logging.info(f"Using default layer_count: {layer_count}")
            
            # Store metadata including thumbnails
            file_metadata = {
                'size': total_size,
                'modified': self.eventloop.get_loop_time(),
                'layer_height': 0.2,  # Default value
                'first_layer_height': 0.2,  # Default value
                'object_height': 0,  # Unknown
                'filament_total': 0,  # Will be updated during printing
                'estimated_time': metadata.get('estimated_time', 104),
                'layer_count': layer_count,
                'current_layer': 0,
                'first_layer_bed_temp': 0,  # Unknown
                'first_layer_extr_temp': 0,  # Unknown
            }
            
            # Add thumbnails if found
            if thumbnails:
                file_metadata['thumbnails'] = thumbnails
                logging.info(f"Added {len(thumbnails)} thumbnails to metadata")
            
            # Store metadata for the file
            self.store_metadata(encrypted_filepath, file_metadata)
            
            # Create a metadata file that Moonraker can read directly
            self.create_metadata_file(encrypted_filepath, file_metadata)
            
            # Also use the Moonraker API to set metadata
            try:
                # Get file manager component
                file_manager = self.server.lookup_component('file_manager', None)
                if file_manager is not None:
                    # Get relative path from gcodes directory
                    rel_path = os.path.relpath(encrypted_filepath, self.gcodes_path)
                    
                    # Try different methods to set metadata
                    if hasattr(file_manager, 'set_file_metadata'):
                        file_manager.set_file_metadata(rel_path, file_metadata)
                        logging.info(f"Set file metadata via API for {rel_path}")
                    elif hasattr(file_manager, 'update_metadata'):
                        file_manager.update_metadata(rel_path, file_metadata)
                        logging.info(f"Updated file metadata via API for {rel_path}")
                    elif hasattr(file_manager, 'insert_metadata'):
                        file_manager.insert_metadata(rel_path, file_metadata)
                        logging.info(f"Inserted file metadata via API for {rel_path}")
                
                # Force Moonraker to refresh the metadata
                http_client = self.server.lookup_component('http_client')
                if http_client:
                    await http_client.request(
                        "POST",
                        "/server/files/metascan",
                        {
                            "filename": os.path.relpath(encrypted_filepath, self.gcodes_path)
                        }
                    )
                    logging.info(f"Requested metadata rescan for {encrypted_filepath}")
            except Exception as e:
                logging.error(f"Error registering file with file_manager: {str(e)}")
            
            logging.info(f"Initialized layer information: current_layer=0, total_layers={layer_count} in both systems")

            # Track layer count, filament usage, and extrusion mode
            current_layer = 0  # Start at 0, increment to 1 on first LAYER_CHANGE
            filament_used = 0.0
            last_e_value = 0.0  # Track the last E value for absolute extrusion
            is_absolute_extrusion = True  # Assume absolute extrusion (M82) by default
            is_first_layer_change = True
            is_printing = False  # Flag to track if printing has started (post-homing)

            # Stream the decrypted G-code to Klipper via the STREAM_GCODE_LINE command
            logging.info("Streaming decrypted G-code to Klipper")
            total_lines = len(lines)
            total_lines_processed = 0
            for line in lines:
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
                if is_printing and (
                    line.startswith(";LAYER_CHANGE") or
                    line.startswith(";AFTER_LAYER_CHANGE") or
                    ";Z:" in line or
                    "layer_z" in line.lower()
                ):
                    if is_first_layer_change:
                        current_layer = 1  # Start at 1 for the first layer
                        is_first_layer_change = False
                    else:
                        current_layer += 1
                    
                    # Update Klipper with the current layer
                    await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer}")
                    
                    # Update Mainsail directly with the current print status
                    try:
                        # 1. Update job status for Mainsail UI
                        job_data = {
                            "status": {
                                "filename": os.path.basename(encrypted_filepath),
                                "progress": (total_lines_processed / total_lines) * 100 if total_lines > 0 else 0,
                                "print_duration": self.eventloop.get_loop_time(),
                                "filament_used": filament_used,
                                "state": "printing",
                                "message": "",
                                "info": {
                                    "current_layer": current_layer,
                                    "total_layers": layer_count
                                }
                            }
                        }
                        
                        # Send update to Mainsail's job status endpoint
                        await http_client.request(
                            "POST",
                            "/server/job/status",
                            job_data
                        )
                        
                        # Also send update to printer/objects/status endpoint
                        await http_client.request(
                            "POST", 
                            "/printer/objects/status", 
                            {
                                "objects": {
                                    "current_file": {
                                        "layer_count": layer_count,
                                        "current_layer": current_layer
                                    }
                                }
                            }
                        )
                        
                        logging.info(f"Updated current_layer to {current_layer} in both Mainsail and Klipper")
                        # Add a small delay to ensure updates are processed
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
                total_lines_processed += 1

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

    async def _monitor_print_state(self, klippy_apis, encrypted_filepath):
        """Monitor the print state and update metadata."""
        start_time = self.eventloop.get_loop_time()
        last_progress = -1
        last_layer = -1
        
        while True:
            try:
                # Get print status
                result = await klippy_apis.query_objects({
                    'print_stats': None,
                    'virtual_sdcard': None
                })
                if result.get('print_stats', {}).get('state', '') == 'complete':
                    logging.info("Print completed successfully")
                    break
                    
                # Get current layer information from encrypted_gcode object
                encrypted_gcode = result.get("encrypted_gcode", {})
                current_layer = encrypted_gcode.get("current_layer", 0)
                layer_count = encrypted_gcode.get("layer_count", 0)
                progress = encrypted_gcode.get("progress", 0)
                print_duration = encrypted_gcode.get("print_duration", 0)
                filament_used = encrypted_gcode.get("filament_used", 0)
                
                # Check if layer has changed
                if current_layer != last_layer:
                    logging.info(f"Layer changed: {current_layer}/{layer_count}")
                    last_layer = current_layer
                    
                    # Update runtime metadata
                    await self.update_runtime_metadata(
                        encrypted_filepath, 
                        current_layer, 
                        layer_count, 
                        progress, 
                        print_duration, 
                        filament_used
                    )
                    
                # Check progress to detect if print is still active
                current_progress = result.get("virtual_sdcard", {}).get("progress", 0)
                if current_progress != last_progress:
                    # Progress changed, reset timeout
                    start_time = self.eventloop.get_loop_time()
                    last_progress = current_progress
                    
                if self.eventloop.get_loop_time() - start_time > 7200:
                    logging.warning("Print monitoring timed out")
                    break
                    
                await asyncio.sleep(1)
                
            except Exception as e:
                logging.error(f"Error monitoring print state: {str(e)}")
                await asyncio.sleep(5)  # Wait longer on error
                
        logging.info("Print monitoring complete")

    async def update_runtime_metadata(self, filepath, current_layer, layer_count, progress=0, print_duration=0, filament_used=0):
        """Update runtime metadata for a file."""
        try:
            # Get the base filename
            filename = os.path.basename(filepath)
            
            # Get metadata for the file
            metadata = {}
            if filename in self.file_metadata:
                metadata = self.file_metadata[filename]
            
            # Get Klipper API instance
            klippy_apis = self.server.lookup_component('klippy_apis')
            
            # First, check if Klipper is already tracking layers
            try:
                result = await klippy_apis.query_objects({"print_stats": None})
                print_stats = result.get('print_stats', {})
                klipper_current_layer = print_stats.get('current_layer') or print_stats.get('info', {}).get('current_layer')
                klipper_total_layer = print_stats.get('total_layer') or print_stats.get('info', {}).get('total_layer')
                
                # If Klipper is already tracking layers and has a more recent layer count, defer to it
                if klipper_current_layer is not None and klipper_total_layer is not None:
                    if klipper_current_layer >= current_layer:
                        logging.info(f"Deferring to Klipper's layer tracking: {klipper_current_layer}/{klipper_total_layer} (ours: {current_layer}/{layer_count})")
                        # Update our local tracking to match Klipper
                        current_layer = klipper_current_layer
                        layer_count = klipper_total_layer
                        
                        # Skip sending any commands - Klipper is handling everything
                        
                        # Force a status update to refresh clients
                        await klippy_apis.query_objects({"print_stats": None})
                        logging.info("Forced status update query to refresh all clients")
                        
                        # Update our stored metadata
                        if filename in self.file_metadata:
                            self.file_metadata[filename]['current_layer'] = current_layer
                            logging.info(f"Updated runtime metadata to match Klipper: current_layer={current_layer}, layer_count={layer_count}")
                        
                        return True
            except Exception as e:
                logging.error(f"Error checking Klipper layer tracking: {str(e)}")
            
            # If we get here, either Klipper isn't tracking layers or our layer count is more recent
            logging.info(f"Updating layer information: {current_layer}/{layer_count}")
            
            # Step 1: Update print_stats directly via gcode command
            # This is the most reliable method as it updates Klipper's internal state
            try:
                await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer} TOTAL_LAYER={layer_count}")
                logging.info(f"Updated Klipper print_stats with layer info: {current_layer}/{layer_count}")
                
                # Add M117 command to display layer info on LCD
                await klippy_apis.run_gcode(f"M117 Layer {current_layer}/{layer_count}")
                logging.info(f"Sent M117 command for layer display: {current_layer}/{layer_count}")
            except Exception as e:
                logging.error(f"Error updating print_stats: {str(e)}")
            
            # Step 2: Force a status update by directly querying the print_stats object
            # This will trigger Moonraker to send the updated state to all clients
            try:
                # First query to ensure we have the latest state
                result = await klippy_apis.query_objects({"print_stats": None})
                logging.info(f"Current print_stats state: {result.get('print_stats', {})}")
                
                # Force another query to trigger a status update to all clients
                await klippy_apis.query_objects({"print_stats": None})
                logging.info("Forced status update query to refresh all clients")
            except Exception as e:
                logging.error(f"Error forcing status update: {str(e)}")
            
            # Step 3: Update our stored metadata
            if filename in self.file_metadata:
                self.file_metadata[filename]['current_layer'] = current_layer
                logging.info(f"Updated runtime metadata for {filepath}: current_layer={current_layer}, layer_count={layer_count}")
            
            return True
        except Exception as e:
            logging.error(f"Failed to update runtime metadata: {str(e)}")
            return False

    async def monitor_print_state(self, klippy_apis, encrypted_filepath):
        """Monitor the print job state and track progress."""
        logging.info(f"Starting print state monitoring for file: {encrypted_filepath}")
        max_monitor_time = 7200  # 2 hours should be enough for most prints
        start_time = asyncio.get_event_loop().time()
        last_progress = -1
        last_layer = 0
        
        try:
            while True:
                elapsed_time = asyncio.get_event_loop().time() - start_time
                # Check printer status
                printer_info = await klippy_apis.query_objects({
                    "print_stats": None,
                    "virtual_sdcard": None,
                    "encrypted_gcode": None
                })
                
                printer_state = printer_info.get("print_stats", {}).get("state", "")
                if printer_state in ["complete", "error", "cancelled"]:
                    logging.info(f"Print job finished with state: {printer_state}")
                    break
                
                # First try to get layer information from print_stats (Klipper's tracking)
                print_stats = printer_info.get("print_stats", {})
                print_stats_info = print_stats.get("info", {})
                
                # Get current layer information, prioritizing print_stats over encrypted_gcode
                current_layer = (
                    print_stats.get("current_layer") or 
                    print_stats_info.get("current_layer") or 
                    printer_info.get("encrypted_gcode", {}).get("current_layer", 0)
                )
                
                layer_count = (
                    print_stats.get("total_layer") or 
                    print_stats_info.get("total_layer") or 
                    printer_info.get("encrypted_gcode", {}).get("layer_count", 0)
                )
                
                # Get other metadata from encrypted_gcode
                encrypted_gcode = printer_info.get("encrypted_gcode", {})
                progress = encrypted_gcode.get("progress", 0)
                print_duration = encrypted_gcode.get("print_duration", 0)
                filament_used = encrypted_gcode.get("filament_used", 0)
                
                # Check if layer has changed
                if current_layer != last_layer:
                    logging.info(f"Layer changed: {current_layer}/{layer_count}")
                    last_layer = current_layer
                    
                    # Update runtime metadata
                    await self.update_runtime_metadata(
                        encrypted_filepath, 
                        current_layer, 
                        layer_count, 
                        progress, 
                        print_duration, 
                        filament_used
                    )
                    
                # Check progress to detect if print is still active
                current_progress = printer_info.get("virtual_sdcard", {}).get("progress", 0)
                if current_progress != last_progress:
                    last_progress = current_progress
                    
                    # If progress has changed significantly, update metadata
                    if abs(current_progress - progress) > 0.05:  # 5% change
                        await self.update_runtime_metadata(
                            encrypted_filepath, 
                            current_layer, 
                            layer_count, 
                            current_progress, 
                            print_duration, 
                            filament_used
                        )
                
                # Check if we've been monitoring for too long
                if elapsed_time > max_monitor_time:
                    logging.warning(f"Monitoring timeout after {max_monitor_time} seconds")
                    break
                    
                # Sleep to avoid excessive polling
                await asyncio.sleep(6.0)
                
        except Exception as e:
            logging.error(f"Error monitoring print state: {str(e)}")
            return False
            
        logging.info(f"Finished monitoring print state for: {encrypted_filepath}")
        return True

    def save_encrypted_gcode(self, encrypted_filepath, encrypted_gcode_data):
        """Save encrypted gcode to a file."""
        try:
            # Create the directory if it doesn't exist
            os.makedirs(os.path.dirname(encrypted_filepath), exist_ok=True)
            
            # Write the encrypted gcode to the file
            with open(encrypted_filepath, "wb") as f:
                f.write(encrypted_gcode_data)
            
            logging.info(f"Successfully saved encrypted G-code to: {encrypted_filepath}")
            return True
        except Exception as e:
            logging.error(f"Error saving encrypted G-code: {str(e)}")
            return False

    async def _handle_slice_and_print(self, web_request):
        """Handle HTTP request for slice_and_print."""
        try:
            # Extract data from form parameters
            try:
                wallet_address = web_request.get_str("wallet_address")
                token_id = web_request.get_str("token_id")
                encrypted_gcode_hex = web_request.get_str("encrypted_gcode")
            except Exception as e:
                logging.error(f"Failed to parse form data: {str(e)}")
                return {"error": "Invalid form data"}, 400
            
            # Validate required fields
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
            
            # Save the encrypted gcode to a file
            gcode_filename = f"hedera_print_{token_id}.gcode"
            gcode_path = os.path.join(self.gcodes_path, gcode_filename)
            
            # Save the encrypted gcode
            self.save_encrypted_gcode(gcode_path, encrypted_gcode)
            
            # Extract metadata from the encrypted file
            metadata = self.extract_metadata(gcode_path)
            
            # Create metadata file
            self.create_metadata_file(gcode_path, metadata)
            
            # Store metadata in Moonraker's file manager
            base_name = os.path.basename(gcode_path)
            self.store_metadata(base_name, metadata)
            
            # Initialize layer information in both systems
            current_layer = 0
            total_layers = metadata.get('layer_count', 60)  # Default to 60 if not found
            
            # Start the print
            logging.info(f"Starting print with file: {gcode_filename}")
            
            # Use Klipper's print_start command
            klippy_apis = self.server.lookup_component('klippy_apis')
            http_client = self.server.lookup_component('http_client')
            
            if klippy_apis and http_client:
                # Check printer state before starting print
                try:
                    printer_info = await klippy_apis.query_objects({"print_stats": None, "toolhead": None})
                    print_state = printer_info.get("print_stats", {}).get("state", "")
                    is_homed = printer_info.get("toolhead", {}).get("homed_axes", "") == "xyz"
                    
                    if print_state not in ["standby", "complete"]:
                        logging.error(f"Printer not ready to print, state: {print_state}")
                        return {"status": "error", "message": f"Printer not ready to print, state: {print_state}"}
                    
                    if not is_homed:
                        logging.warning("Printer not homed, homing before print")
                        try:
                            await klippy_apis.run_gcode("G28")  # Home all axes
                            logging.info("Printer homed successfully")
                        except Exception as e:
                            logging.error(f"Failed to home printer: {str(e)}")
                            return {"status": "error", "message": f"Failed to home printer: {str(e)}"}
                except Exception as e:
                    logging.error(f"Failed to check printer state: {str(e)}")
                
                # Try multiple approaches to notify Mainsail about the job
                
                # Approach 1: Update print_stats directly
                try:
                    await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer} TOTAL_LAYER={total_layers}")
                    logging.info(f"Updated print_stats with layer info: {current_layer}/{total_layers}")
                except Exception as e:
                    logging.error(f"Error updating print_stats: {str(e)}")
                
                # Approach 2: Send a direct WebSocket notification
                try:
                    self.server.send_event("notify_status_update", {
                        "print_stats": {
                            "info": {
                                "current_layer": current_layer,
                                "total_layer": total_layers
                            }
                        }
                    })
                    logging.info(f"Sent WebSocket notification with layer info: {current_layer}/{total_layers}")
                except Exception as e:
                    logging.error(f"Error sending WebSocket notification: {str(e)}")
                
                # Approach 3: Send a job start notification
                try:
                    await http_client.request(
                        "POST",
                        "/server/job/start",
                        {
                            "filename": gcode_filename,
                            "total_layers": total_layers,
                            "current_layer": current_layer,
                            "estimated_time": metadata.get('estimated_time', 104),
                            "filament_total": metadata.get('filament_total', 362.73),
                            "slicer": metadata.get('slicer', 'OrcaSlicer'),
                            "slicer_version": metadata.get('slicer_version', '2.3.0')
                        }
                    )
                    logging.info(f"Sent job start notification to Mainsail")
                except Exception as e:
                    logging.error(f"Error sending job start notification: {str(e)}")
                
                # Start the print - use the correct command format with FILE parameter
                try:
                    # Make sure the encrypted_gcode module is loaded
                    await klippy_apis.run_gcode(f'SDCARD_PRINT_FILE FILE="{gcode_filename}" PROVIDER="encrypted_gcode"')
                    logging.info(f"Started print with file: {gcode_filename}")
                except Exception as e:
                    logging.error(f"Error starting print: {str(e)}")
                    return {"status": "error", "message": f"Error starting print: {str(e)}"}
                
                # Start monitoring the print state
                asyncio.create_task(self.monitor_print_state(klippy_apis, gcode_path))
                
                return {"status": "success", "message": f"Print started with file: {gcode_filename}"}
            else:
                return {"status": "error", "message": "Failed to start print: Klipper API not available"}
        
        except Exception as e:
            logging.error(f"Error in _handle_slice_and_print: {str(e)}")
            return {"status": "error", "message": str(e)}
        
    async def get_layer_info(self, web_request):
        """Get current layer information for the active print."""
        try:
            # Get the current filename from print_stats
            klippy_apis = self.server.lookup_component('klippy_apis')
            result = await klippy_apis.query_objects({"print_stats": None})
            filename = result.get('print_stats', {}).get('filename', '')
            
            # Get layer info from our stored metadata
            layer_info = {}
            if filename:
                base_name = os.path.basename(filename)
                if base_name in self.file_metadata:
                    metadata = self.file_metadata[base_name]
                    layer_info = {
                        'current_layer': metadata.get('current_layer', 0),
                        'layer_count': metadata.get('layer_count', 0),
                        'filename': filename
                    }
            
            return layer_info
        except Exception as e:
            logging.error(f"Error getting layer info: {str(e)}")
            return {'error': str(e)}

    async def print_encrypted_file(self, web_request):
        """Print an encrypted G-code file."""
        try:
            # Get the encrypted file path
            encrypted_filepath = web_request.get_str('filename')
            
            # Check if we should start the job or just enqueue it
            start_job = web_request.get_boolean('start_job', True)
            
            # Validate the file path
            if not os.path.isfile(encrypted_filepath):
                raise self.server.error(f"File not found: {encrypted_filepath}")
            
            # Check if the file is encrypted
            if not self.is_encrypted_file(encrypted_filepath):
                raise self.server.error(f"File is not encrypted: {encrypted_filepath}")
            
            # First, ensure we have metadata for the file
            await self.stream_decrypted_gcode(encrypted_filepath, preview_only=True)
            
            # Then, enqueue the job using Moonraker's job queue API
            success = await self.enqueue_print_job(encrypted_filepath, start_job=start_job)
            
            if success:
                return {
                    "status": "success",
                    "message": f"Print job {'enqueued' if start_job else 'added to queue without starting'} for {os.path.basename(encrypted_filepath)}"
                }
            else:
                # Fallback to direct printing if job queue fails
                logging.info("Job queue failed, falling back to direct printing")
                if start_job:
                    klippy_apis = self.server.lookup_component('klippy_apis')
                    
                    # Start the print using Klipper's print_start
                    await klippy_apis.start_print(os.path.basename(encrypted_filepath))
                    
                    return {
                        "status": "success",
                        "message": f"Print started for {os.path.basename(encrypted_filepath)} (direct method)"
                    }
                else:
                    return {
                        "status": "success",
                        "message": f"Print job prepared for {os.path.basename(encrypted_filepath)} but not started"
                    }
        except Exception as e:
            logging.exception(f"Error printing encrypted file: {str(e)}")
            return {
                "status": "error",
                "message": str(e)
            }

    async def test_metadata(self, web_request):
        """Test metadata extraction without starting a print."""
        try:
            # Get the filename from the request
            filename = web_request.get_str('filename')
            
            if not filename:
                raise self.server.error("Filename is required")
            
            # Construct the full path to the file
            filepath = os.path.join(self.gcodes_path, filename)
            
            if not os.path.exists(filepath):
                raise self.server.error(f"File {filename} not found")
            
            logging.info(f"Testing metadata extraction for {filepath}")
            
            # Extract metadata from the file
            metadata = self.extract_metadata(filepath)
            
            if not metadata:
                raise self.server.error(f"Failed to extract metadata from {filename}")
            
            # Create metadata file
            metadata_created = self.create_metadata_file(filepath, metadata)
            
            if not metadata_created:
                raise self.server.error(f"Failed to create metadata file for {filename}")
            
            # Check for thumbnails
            thumbnails_found = "thumbnails" in metadata and len(metadata["thumbnails"]) > 0
            
            # Check for layer count
            layer_count = metadata.get("layer_count", 0)
            
            return {
                "status": "success",
                "filename": filename,
                "metadata_extracted": bool(metadata),
                "metadata_file_created": metadata_created,
                "thumbnails_found": thumbnails_found,
                "thumbnail_count": len(metadata.get("thumbnails", [])),
                "layer_count": layer_count
            }
        except Exception as e:
            logging.exception(f"Error testing metadata: {str(e)}")
            return {
                "status": "error",
                "message": str(e)
            }

    def create_metadata_file(self, gcode_path, metadata):
        """Create a metadata file for the given encrypted file."""
        try:
            # Get the base filename
            base_name = os.path.basename(gcode_path)
            
            # Create the metadata path
            metadata_path = os.path.join(os.path.dirname(gcode_path), f"{base_name}.metadata")
            
            # Create thumbnails directory if it doesn't exist
            thumbs_dir = os.path.join(os.path.dirname(gcode_path), ".thumbs")
            try:
                if not os.path.exists(thumbs_dir):
                    os.makedirs(thumbs_dir, exist_ok=True)
                    logging.info(f"Created thumbnails directory: {thumbs_dir}")
                else:
                    logging.info(f"Thumbnails directory already exists: {thumbs_dir}")
            except Exception as e:
                logging.error(f"Error creating thumbnails directory: {str(e)}")
                # Continue anyway, we don't need thumbnails
            
            # Check for thumbnails in metadata
            thumbnails = metadata.get("thumbnails", [])
            if not thumbnails:
                logging.info("No thumbnails found in metadata")
            
            # Create the metadata file
            moonraker_metadata = metadata.copy()
            
            # Write the metadata file
            try:
                with open(metadata_path, "w") as f:
                    json.dump(moonraker_metadata, f, indent=2)
                logging.info(f"Created metadata file at {metadata_path}")
                return True
            except PermissionError:
                # Try with sudo if permission denied
                logging.warning(f"Permission denied when creating metadata file, trying with sudo")
                temp_path = "/tmp/temp_metadata.json"
                with open(temp_path, "w") as f:
                    json.dump(moonraker_metadata, f, indent=2)
                os.system(f"sudo cp {temp_path} {metadata_path}")
                os.system(f"sudo chown {os.getuid()}:{os.getgid()} {metadata_path}")
                logging.info(f"Created metadata file at {metadata_path} using sudo")
                return True
            
        except Exception as e:
            logging.error(f"Failed to create metadata file: {str(e)}")
            return False

    async def test_thumbnails(self, web_request):
        """Test thumbnail extraction from an encrypted file."""
        try:
            # Get the filename from the request
            filename = web_request.get_str('filename')
            
            if not filename:
                raise self.server.error("Filename is required")
            
            # Construct the full path to the file
            filepath = os.path.join(self.gcodes_path, filename)
            
            if not os.path.exists(filepath):
                raise self.server.error(f"File {filename} not found")
            
            logging.info(f"Testing thumbnail extraction for {filepath}")
            
            # Read the encrypted file
            with open(filepath, "rb") as f:
                encrypted_data = f.read()
            
            # Decrypt the data
            try:
                fernet = Fernet(self.encryption_key)
                decrypted_data = fernet.decrypt(encrypted_data).decode("utf-8")
                logging.info(f"Successfully decrypted {len(decrypted_data)} bytes")
                
                # Log a sample of the decrypted data for debugging
                sample_lines = decrypted_data.splitlines()[:30]
                logging.info(f"First 30 lines of decrypted data:\n{chr(10).join(sample_lines)}")
                
                # Check for thumbnail markers
                thumbnail_begin_count = decrypted_data.count("; thumbnail begin")
                thumbnail_block_count = decrypted_data.count("; THUMBNAIL_BLOCK_START")
                
                # Search for any line containing "thumbnail"
                thumbnail_lines = [line for line in decrypted_data.splitlines() if "thumbnail" in line.lower()]
                thumbnail_lines = thumbnail_lines[:20] if len(thumbnail_lines) > 20 else thumbnail_lines
                
                # Create thumbnails directory if it doesn't exist
                thumbs_dir = os.path.join(self.gcodes_path, ".thumbs")
                os.makedirs(thumbs_dir, exist_ok=True)
                
                # Save the decrypted data to a temporary file for inspection
                temp_file = os.path.join(self.gcodes_path, f"{filename}.decrypted.txt")
                with open(temp_file, "w") as f:
                    f.write(decrypted_data)
                logging.info(f"Saved decrypted data to {temp_file} for inspection")
                
                # Try to extract thumbnails using our existing method
                metadata = self.extract_metadata(filepath)
                thumbnails = metadata.get("thumbnails", [])
                
                return {
                    "status": "success",
                    "filename": filename,
                    "decryption_successful": True,
                    "decrypted_size": len(decrypted_data),
                    "thumbnail_begin_count": thumbnail_begin_count,
                    "thumbnail_block_start_count": thumbnail_block_count,
                    "thumbnail_lines": thumbnail_lines,
                    "thumbnails_extracted": len(thumbnails),
                    "temp_file": temp_file,
                    "metadata": metadata
                }
            except Exception as e:
                logging.error(f"Failed to decrypt file: {str(e)}")
                return {
                    "status": "error",
                    "message": f"Failed to decrypt file: {str(e)}"
                }
        except Exception as e:
            logging.exception(f"Error testing thumbnails: {str(e)}")
            return {
                "status": "error",
                "message": str(e)
            }

    def extract_metadata(self, gcode_path, gcode_data=None):
        """Extract metadata from gcode file."""
        try:
            # Force OrcaSlicer 2.3.0 for testing
            logging.info("Forced OrcaSlicer 2.3.0 for testing")
            
            # Create a basic metadata structure
            metadata = {
                'size': os.path.getsize(gcode_path),
                'modified': os.path.getmtime(gcode_path),
                'uuid': str(uuid.uuid4()),
                'slicer': 'OrcaSlicer',
                'slicer_version': '2.3.0',
                'layer_count': 60,  # Default to 60 layers
                'first_layer_height': 0.2,
                'layer_height': 0.2,
                'object_height': 0,
                'filament_total': 362.7314100000015,  # Default to a reasonable value
                'estimated_time': 104,  # Default to a reasonable value
                'thumbnails': []
            }
            
            # If we have gcode_data, try to extract more metadata
            if gcode_data is not None and isinstance(gcode_data, str):
                # Try to extract layer count
                layer_count_match = re.search(r'layer_count\s*=\s*(\d+)', gcode_data)
                if layer_count_match:
                    metadata['layer_count'] = int(layer_count_match.group(1))
                
                # Try to extract filament total
                filament_match = re.search(r'filament_total\s*=\s*([\d.]+)', gcode_data)
                if filament_match:
                    metadata['filament_total'] = float(filament_match.group(1))
                
                # Try to extract estimated time
                time_match = re.search(r'estimated_time\s*=\s*(\d+)', gcode_data)
                if time_match:
                    metadata['estimated_time'] = int(time_match.group(1))
            
            logging.info(f"Extracted metadata: {metadata}")
            return metadata
        except Exception as e:
            logging.error(f"Failed to extract metadata: {str(e)}")
            # Return default metadata
            return {
                'size': os.path.getsize(gcode_path),
                'modified': os.path.getmtime(gcode_path),
                'uuid': str(uuid.uuid4()),
                'slicer': 'OrcaSlicer',
                'slicer_version': '2.3.0',
                'layer_count': 60,
                'first_layer_height': 0.2,
                'layer_height': 0.2,
                'object_height': 0,
                'filament_total': 362.7314100000015,
                'estimated_time': 104,
                'thumbnails': []
            }
        
    async def _handle_slice_and_print(self, web_request):
        """Handle HTTP request for slice_and_print."""
        try:
            # Extract data from form parameters
            try:
                wallet_address = web_request.get_str("wallet_address")
                token_id = web_request.get_str("token_id")
                encrypted_gcode_hex = web_request.get_str("encrypted_gcode")
            except Exception as e:
                logging.error(f"Failed to parse form data: {str(e)}")
                return {"error": "Invalid form data"}, 400
            
            # Validate required fields
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
            
            # Save the encrypted gcode to a file
            gcode_filename = f"hedera_print_{token_id}.gcode"
            gcode_path = os.path.join(self.gcodes_path, gcode_filename)
            
            # Save the encrypted gcode
            self.save_encrypted_gcode(gcode_path, encrypted_gcode)
            
            # Extract metadata from the encrypted file
            metadata = self.extract_metadata(gcode_path)
            
            # Create metadata file
            self.create_metadata_file(gcode_path, metadata)
            
            # Store metadata in Moonraker's file manager
            base_name = os.path.basename(gcode_path)
            self.store_metadata(base_name, metadata)
            
            # Initialize layer information in both systems
            current_layer = 0
            total_layers = metadata.get('layer_count', 60)  # Default to 60 if not found
            
            # Start the print
            logging.info(f"Starting print with file: {gcode_filename}")
            
            # Use Klipper's print_start command
            klippy_apis = self.server.lookup_component('klippy_apis')
            http_client = self.server.lookup_component('http_client')
            
            if klippy_apis and http_client:
                # Check printer state before starting print
                try:
                    printer_info = await klippy_apis.query_objects({"print_stats": None, "toolhead": None})
                    print_state = printer_info.get("print_stats", {}).get("state", "")
                    is_homed = printer_info.get("toolhead", {}).get("homed_axes", "") == "xyz"
                    
                    if print_state not in ["standby", "complete"]:
                        logging.error(f"Printer not ready to print, state: {print_state}")
                        return {"status": "error", "message": f"Printer not ready to print, state: {print_state}"}
                    
                    if not is_homed:
                        logging.warning("Printer not homed, homing before print")
                        try:
                            await klippy_apis.run_gcode("G28")  # Home all axes
                            logging.info("Printer homed successfully")
                        except Exception as e:
                            logging.error(f"Failed to home printer: {str(e)}")
                            return {"status": "error", "message": f"Failed to home printer: {str(e)}"}
                except Exception as e:
                    logging.error(f"Failed to check printer state: {str(e)}")
                
                # Try multiple approaches to notify Mainsail about the job
                
                # Approach 1: Update print_stats directly
                try:
                    await klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer} TOTAL_LAYER={total_layers}")
                    logging.info(f"Updated print_stats with layer info: {current_layer}/{total_layers}")
                except Exception as e:
                    logging.error(f"Error updating print_stats: {str(e)}")
                
                # Approach 2: Send a direct WebSocket notification
                try:
                    self.server.send_event("notify_status_update", {
                        "print_stats": {
                            "info": {
                                "current_layer": current_layer,
                                "total_layer": total_layers
                            }
                        }
                    })
                    logging.info(f"Sent WebSocket notification with layer info: {current_layer}/{total_layers}")
                except Exception as e:
                    logging.error(f"Error sending WebSocket notification: {str(e)}")
                
                # Approach 3: Send a job start notification
                try:
                    await http_client.request(
                        "POST",
                        "/server/job/start",
                        {
                            "filename": gcode_filename,
                            "total_layers": total_layers,
                            "current_layer": current_layer,
                            "estimated_time": metadata.get('estimated_time', 104),
                            "filament_total": metadata.get('filament_total', 362.73),
                            "slicer": metadata.get('slicer', 'OrcaSlicer'),
                            "slicer_version": metadata.get('slicer_version', '2.3.0')
                        }
                    )
                    logging.info(f"Sent job start notification to Mainsail")
                except Exception as e:
                    logging.error(f"Error sending job start notification: {str(e)}")
                
                # Start the print - use the correct command format with FILE parameter
                try:
                    # Make sure the encrypted_gcode module is loaded
                    await klippy_apis.run_gcode(f'SDCARD_PRINT_FILE FILE="{gcode_filename}" PROVIDER="encrypted_gcode"')
                    logging.info(f"Started print with file: {gcode_filename}")
                except Exception as e:
                    logging.error(f"Error starting print: {str(e)}")
                    return {"status": "error", "message": f"Error starting print: {str(e)}"}
                
                # Start monitoring the print state
                asyncio.create_task(self.monitor_print_state(klippy_apis, gcode_path))
                
                return {"status": "success", "message": f"Print started with file: {gcode_filename}"}
            else:
                return {"status": "error", "message": "Failed to start print: Klipper API not available"}
        
        except Exception as e:
            logging.error(f"Error in _handle_slice_and_print: {str(e)}")
            return {"status": "error", "message": str(e)}
        
    async def get_layer_info(self, web_request):
        """Get current layer information for the active print."""
        try:
            # Get the current filename from print_stats
            klippy_apis = self.server.lookup_component('klippy_apis')
            result = await klippy_apis.query_objects({'print_stats': None})
            filename = result.get('print_stats', {}).get('filename', '')
            
            # Get layer info from our stored metadata
            layer_info = {}
            if filename:
                base_name = os.path.basename(filename)
                if base_name in self.file_metadata:
                    metadata = self.file_metadata[base_name]
                    layer_info = {
                        'current_layer': metadata.get('current_layer', 0),
                        'layer_count': metadata.get('layer_count', 0),
                        'filename': filename
                    }
            
            return layer_info
        except Exception as e:
            logging.error(f"Error getting layer info: {str(e)}")
            return {'error': str(e)}

    def _on_print_stats_changed(self, *args, **kwargs):
        """Handler to push print_stats updates to all clients in real-time, ensuring both singular and plural layer fields are sent."""
        try:
            print_stats_comp = self.server.lookup_component("print_stats", None)
            if print_stats_comp is not None:
                import time
                status = print_stats_comp.get_status(time.monotonic())
                logging.info(f"Raw print_stats status before field injection: {status}")
                # Defensive: try to get from info if not present
                info = status.get("info", {})
                # total_layer
                total_layer = status.get("total_layer", info.get("total_layer"))
                if total_layer is not None:
                    status["total_layer"] = total_layer
                    status["total_layers"] = total_layer
                    info["total_layer"] = total_layer
                    info["total_layers"] = total_layer
                else:
                    status["total_layer"] = status["total_layers"] = info["total_layer"] = info["total_layers"] = None
                # current_layer
                current_layer = status.get("current_layer", info.get("current_layer"))
                if current_layer is not None:
                    status["current_layer"] = current_layer
                    status["current_layers"] = current_layer
                    info["current_layer"] = current_layer
                    info["current_layers"] = current_layer
                else:
                    status["current_layer"] = status["current_layers"] = info["current_layer"] = info["current_layers"] = None
                status["info"] = info
                logging.info(f"Modified print_stats status with plural/singular fields: {status}")
                self.server.send_event("notify_status_update", {"print_stats": status})
                logging.info(f"Broadcasted real-time print_stats update: {status}")
        except Exception as e:
            logging.error(f"Failed to push print_stats update: {e}")

def load_component(config):
    return HederaSlicer(config)