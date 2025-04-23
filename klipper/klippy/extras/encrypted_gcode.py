# Encrypted GCode provider for Klipper
#
# Copyright (C) 2025 Jeff <your@email.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import os
import logging
from cryptography.fernet import Fernet
import io
import re

class EncryptedGCodeProvider:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.print_stats = self.printer.load_object(config, 'print_stats')
        
        # Provider state
        self.filename = None
        self.file_position = 0
        self.file_size = 0
        self._decrypted_buffer = None
        self._last_reported_filament = 0.0
        self.start_print_time = None
        
        # Initialize metadata
        self._metadata = {
            'current_layer': 0,
            'layer_count': 0,
            'filament_used': 0.0,
            'print_duration': 0.0
        }
        
        # Required by virtual_sdcard
        self.is_streaming = False
        
        # Get encryption key from config
        self.encryption_key = config.get('encryption_key', 'WuDd4y2dnS8rP7hqm2a1XUgZaP9M6qI1CmJQptbbgqo=')
        try:
            # Ensure key is properly padded
            self.encryption_key += '=' * (-len(self.encryption_key) % 4)
            # Convert to bytes and validate
            self.key = self.encryption_key.encode('utf-8')
            self.fernet = Fernet(self.key)
        except Exception as e:
            raise config.error(f"Invalid encryption key: {str(e)}")
            
        # Set up gcodes directory path
        self.gcodes_path = os.path.expanduser("~/printer_data/gcodes")
        if not os.path.exists(self.gcodes_path):
            try:
                os.makedirs(self.gcodes_path)
            except Exception as e:
                raise config.error(f"Failed to create gcodes directory: {str(e)}")
        
    def get_stats(self, eventtime):
        """Return print stats for this G-code provider."""
        if self.filename is None:
            return False, ""
        return True, "SD printing byte %d/%d" % (
            self.file_position,
            self.file_size,
        )

    def get_status(self, eventtime):
        """Return the status of this G-code provider."""
        if not self.filename:
            return {
                "file_path": "",
                "progress": 0.0,
                "file_position": 0,
                "file_size": 0,
                "current_layer": 0,
                "layer_count": 0,
                "filament_used": 0.0,
                "print_duration": 0.0
            }
            
        progress = self.progress()
        status = {
            "file_path": self.filename,
            "progress": progress,
            "file_position": self.file_position,
            "file_size": self.file_size,
            "current_layer": self._metadata.get("current_layer", 0),
            "layer_count": self._metadata.get("layer_count", 0),
            "filament_used": self._metadata.get("filament_used", 0.0),
            "print_duration": self._metadata.get("print_duration", 0.0)
        }
        
        # Calculate estimated time remaining based on progress
        if progress > 0 and self.print_stats.state == 'printing':
            elapsed = self.print_stats.print_duration
            if elapsed > 0:
                total_est = elapsed / progress
                remaining = total_est - elapsed
                
                # Only update metadata, don't call _update_print_stats here
                # This prevents excessive status updates
                self._metadata['print_duration'] = total_est
                
                # Update the info dictionary directly without triggering a full update
                if hasattr(self.print_stats, 'info'):
                    self.print_stats.info['estimated_time'] = total_est
        
        return status

    def get_name(self):
        """Return the filename for this G-code provider."""
        if self.filename:
            # Convert absolute path to relative path from gcodes directory
            rel_path = os.path.relpath(self.filename, self.gcodes_path)
            return rel_path
        return ""

    def progress(self):
        """Return the print progress as a float between 0.0 and 1.0."""
        if self.file_size:
            return float(self.file_position) / self.file_size
        else:
            return 0.

    def reset(self):
        """Reset the G-code provider state - required by virtual_sdcard."""
        self.file_position = 0
        if self._decrypted_buffer:
            self._decrypted_buffer.seek(0)
        # Reset metadata
        self._metadata = {
            'current_layer': 0,
            'layer_count': 0,
            'filament_used': 0.0,
            'print_duration': 0.0
        }
        self._last_reported_filament = 0.0
        self.is_streaming = False
        self.start_print_time = None

    def set_file_position(self, pos):
        """Set the current file position."""
        self.file_position = pos
        if self._decrypted_buffer:
            self._decrypted_buffer.seek(pos)

    def get_file_position(self):
        """Return the current file position."""
        return self.file_position

    def is_active(self):
        """Return True if a print is currently active."""
        return self._decrypted_buffer is not None
        
    def set_filename(self, filename):
        """Set the filename and load the encrypted G-code."""
        if not filename:
            raise Exception("No filename provided")
            
        # If just filename provided, prepend gcodes path
        if not os.path.isabs(filename):
            filename = os.path.join(self.gcodes_path, filename)
            
        if not os.path.exists(filename):
            raise Exception(f"File not found: {filename}")
            
        self.filename = filename
        self.file_position = 0
        self._last_reported_filament = 0.0
        
        try:
            # Load and decrypt the file
            with open(filename, 'rb') as f:
                encrypted_data = f.read()
            decrypted_data = self._decrypt_gcode(encrypted_data)
            self._decrypted_buffer = io.StringIO(decrypted_data)
            self.file_size = len(decrypted_data)
            
            # Initialize print_stats
            self.print_stats.set_current_file(os.path.basename(filename))
            
            # Reset metadata
            self._metadata = {
                'current_layer': 0,
                'layer_count': 0,
                'filament_used': 0.0,
                'print_duration': 0.0
            }
            
            # Scan metadata
            logging.info("Starting metadata scan...")
            lines = self._decrypted_buffer.getvalue().splitlines()
            scan_lines = lines[:200] + lines[-200:] if len(lines) > 200 else lines
            
            # Thumbnail extraction variables
            in_thumbnail = False
            thumbnail_data = ""
            thumbnail_width = 0
            thumbnail_height = 0
            thumbnail_size = 0
            thumbnails = []
            
            for line in scan_lines:
                logging.info(f"Scanning metadata line: {line}")
                lower = line.lower()
                
                # Check for thumbnail begin markers
                if (line.startswith('; thumbnail begin') or 
                    line.startswith(';thumbnail begin')):
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
                if (line.startswith('; thumbnail end') or 
                    line.startswith(';thumbnail end')):
                    in_thumbnail = False
                    if thumbnail_width > 0 and thumbnail_height > 0:
                        # Format thumbnail data for Mainsail
                        thumbnails.append({
                            'width': thumbnail_width,
                            'height': thumbnail_height,
                            'size': thumbnail_size,
                            'data': thumbnail_data.strip(),
                            'relative_path': f'thumbs/{thumbnail_width}x{thumbnail_height}.png',
                            'format': 'png'
                        })
                        logging.info(f"Extracted thumbnail: {thumbnail_width}x{thumbnail_height}")
                    continue
                
                # Collect thumbnail data
                if in_thumbnail:
                    # Remove leading semicolon and whitespace
                    clean_line = line.lstrip(';').strip()
                    thumbnail_data += clean_line
                    continue
                
                # Layer count
                if (
                    'layer_count:' in lower or
                    'total layer number' in lower or
                    'total layers count' in lower
                ):
                    try:
                        match = re.search(r'(?:[:=\s])\s*([\d.]+)', line)
                        if match:
                            layer_count = int(float(match.group(1)))
                            self._metadata['layer_count'] = layer_count
                            # Update print_stats with layer count
                            self.print_stats.total_layer = layer_count
                            logging.info(f"Found layer_count: {layer_count}, updated print_stats")
                    except Exception as e:
                        logging.warning(f"Failed to parse LAYER_COUNT: {e}")
                        
                # Filament used - OrcaSlicer specific patterns
                elif (
                    'filament used' in lower or
                    'total filament' in lower or
                    'filament_length' in lower or  # OrcaSlicer format
                    'total_filament_used' in lower  # OrcaSlicer format
                ):
                    try:
                        # Try to find any number followed by a unit
                        match = re.search(r'([\d.]+)\s*(?:mm|g|m|cm)', line)
                        if match:
                            filament = float(match.group(1))
                            # Convert to mm if in other units
                            if 'm]' in line or 'meters' in lower:
                                filament *= 1000  # m to mm
                            elif 'cm' in line:
                                filament *= 10    # cm to mm
                            self._metadata['filament_used'] = filament
                            logging.info(f"Parsed FILAMENT_USED: {filament}")
                    except Exception as e:
                        logging.warning(f"Failed to parse FILAMENT_USED: {e}")
                        
                # Print duration - OrcaSlicer specific patterns
                elif (
                    'estimated printing time' in lower or
                    'estimated_time' in lower or  # OrcaSlicer format
                    'print_time' in lower or
                    'total_time' in lower
                ):
                    try:
                        duration = 0
                        # Try OrcaSlicer's detailed time format first
                        if 'd' in lower or 'h' in lower or 'm' in lower:
                            days = re.search(r'(\d+)d', lower)
                            hours = re.search(r'(\d+)h', lower)
                            mins = re.search(r'(\d+)m(?!m)', lower)  # negative lookahead to avoid matching 'mm'
                            secs = re.search(r'(\d+)s', lower)
                            
                            if days:
                                duration += int(days.group(1)) * 86400
                            if hours:
                                duration += int(hours.group(1)) * 3600
                            if mins:
                                duration += int(mins.group(1)) * 60
                            if secs:
                                duration += int(secs.group(1))
                                
                            if duration > 0:
                                self._metadata['print_duration'] = duration
                                logging.info(f"Parsed TIME (detailed): {duration}")
                        else:
                            # Try for plain seconds
                            match = re.search(r'(?:[:=\s])\s*([\d.]+)', line)
                            if match:
                                duration = float(match.group(1))
                                self._metadata['print_duration'] = duration
                                logging.info(f"Parsed TIME (seconds): {duration}")
                    except Exception as e:
                        logging.warning(f"Failed to parse TIME: {e}")
            logging.info(f"Final parsed metadata: {self._metadata}")
            
            # Store thumbnails in metadata
            if thumbnails:
                self._metadata['thumbnails'] = thumbnails
                logging.info(f"Added {len(thumbnails)} thumbnails to metadata")
            
            # Update print_stats with all metadata
            self._update_print_stats()
            
        except Exception as e:
            raise Exception(f"Failed to load encrypted G-code: {str(e)}")
            
    def _update_print_stats(self):
        """Update print_stats with current metadata."""
        try:
            gcode = self.printer.lookup_object('gcode')
            self.print_stats = self.printer.lookup_object('print_stats')
            
            # Calculate estimated time
            progress = self.progress()
            if progress > 0 and self.print_stats.state == 'printing':
                if self.start_print_time is None:
                    self.start_print_time = self.reactor.monotonic()
                elapsed = self.reactor.monotonic() - self.start_print_time
                if elapsed > 0:
                    total_est = elapsed / progress
                    self._metadata['print_duration'] = total_est
                    print_time = elapsed
                    total_time = total_est
                else:
                    print_time = 0
                    total_time = 0
            else:
                print_time = 0
                total_time = 0

            # Update all stats with a single command to reduce overhead
            cmd = (
                f"SET_PRINT_STATS_INFO"
                f" TOTAL_LAYER={self._metadata.get('layer_count', 0)}"
                f" CURRENT_LAYER={self._metadata.get('current_layer', 0)}"
            )
            
            # For filament, use the current value for FILAMENT_USED and total for FILAMENT_TOTAL
            filament_used = self._metadata.get('filament_used', 0.0)
            cmd += f" FILAMENT_USED={filament_used} FILAMENT_TOTAL={filament_used}"
            
            cmd += f" TOTAL_SIZE={self.file_size} FILE_POSITION={self.file_position}"
            
            # Only add time parameters if they're valid
            if print_time > 0:
                cmd += f" PRINT_TIME={print_time}"
            if total_time > 0:
                cmd += f" TOTAL_TIME={total_time}"
                
            gcode.run_script_from_command(cmd)
            
            # Set print_stats attributes directly for immediate update
            self.print_stats.total_layer = self._metadata.get('layer_count', 0)
            self.print_stats.current_layer = self._metadata.get('current_layer', 0)
            self.print_stats.file_position = self.file_position
            self.print_stats.file_size = self.file_size
            self.print_stats.filament_used = filament_used
            
            # Update info dictionary for Mainsail compatibility
            if hasattr(self.print_stats, 'info'):
                self.print_stats.info['total_layer'] = self._metadata.get('layer_count', 0)
                self.print_stats.info['total_layers'] = self._metadata.get('layer_count', 0)
                self.print_stats.info['current_layer'] = self._metadata.get('current_layer', 0)
                self.print_stats.info['current_layers'] = self._metadata.get('current_layer', 0)
                self.print_stats.info['filament_used'] = filament_used
                self.print_stats.info['filament_total'] = filament_used
                if total_time > 0:
                    self.print_stats.info['estimated_time'] = total_time
            
            # Force an immediate update but only log at info level for layer changes
            self.print_stats._update_stats(self.printer.get_reactor().monotonic())
            logging.info(f"Updated print_stats with: {self._metadata}, file_position={self.file_position}")
        except Exception as e:
            logging.error(f"Failed to update print_stats: {str(e)}")

    def _decrypt_gcode(self, encrypted_data):
        """Decrypt the G-code data."""
        try:
            return self.fernet.decrypt(encrypted_data).decode('utf-8')
        except Exception as e:
            raise Exception(f"Failed to decrypt G-code: {str(e)}")
            
    def get_gcode(self):
        """Generator to yield lines of G-code."""
        if not self._decrypted_buffer:
            return
            
        while True:
            line = self._decrypted_buffer.readline()
            if not line:
                break
                
            # Update file position
            self.file_position = self._decrypted_buffer.tell()
            
            # Track layer changes
            lower = line.lower()
            if (';layer:' in lower or 
                'layer_z' in lower or 
                ';z:' in lower or
                ';layer ' in lower or
                '; layer ' in lower or
                ';layer change' in lower or
                ';layer number' in lower or
                ';move to next layer' in lower or
                'move to z' in lower or
                ';layer_change' in lower or  # Add specific marker from user's G-code
                ';after_layer_change' in lower):  # Add specific marker from user's G-code
                try:
                    # Extract layer number directly if available
                    layer_match = re.search(r';layer:?\s*(\d+)', lower)
                    if layer_match:
                        current_layer = int(layer_match.group(1))
                    else:
                        # Try other layer number patterns
                        layer_num_match = re.search(r';layer (?:number|#)?\s*(\d+)', lower)
                        if layer_num_match:
                            current_layer = int(layer_num_match.group(1))
                        else:
                            # Try to extract Z height from various formats
                            z_match = re.search(r'(?:layer_z|z:|move to z|;z:)\s*([\d.]+)', lower)
                            if z_match:
                                z_height = float(z_match.group(1))
                                # Use z_height directly as layer number since many slicers use integer z values
                                # For 0.2mm layer height, divide by 0.2 to get layer number
                                layer_height = 0.2  # Default layer height, could be extracted from G-code
                                current_layer = int(round(z_height / layer_height))
                                
                    if current_layer >= 0:  # Sanity check
                        # Only update if the layer number is increasing
                        if current_layer > self._metadata.get('current_layer', 0):
                            # Update metadata
                            self._metadata['current_layer'] = current_layer
                            
                            # Update print_stats
                            self.print_stats.current_layer = current_layer
                            
                            # Also update the info field for Mainsail compatibility
                            if hasattr(self.print_stats, 'info'):
                                self.print_stats.info['current_layer'] = current_layer
                                self.print_stats.info['current_layers'] = current_layer
                            
                            # Update all stats on layer change
                            self._update_print_stats()
                            
                            # Send the command directly to ensure it takes effect
                            try:
                                gcode = self.printer.lookup_object('gcode')
                                # Use the SET_PRINT_STATS_INFO command which Mainsail recognizes
                                gcode.run_script_from_command(f"SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer}")
                                
                                # Also send a notification via M117 which will appear in the UI
                                gcode.run_script_from_command(f"M117 Layer {current_layer}/{self._metadata.get('layer_count', 0)}")
                                
                                # Log the layer change
                                logging.info(f"Layer changed: {current_layer}/{self._metadata.get('layer_count', 0)}")
                            except Exception as e:
                                logging.error(f"Error updating layer info: {str(e)}")
                except Exception as e:
                    logging.warning(f"Failed to parse layer change: {e}")
            
            # Track filament usage from extrusion moves
            if line.startswith('G1') and 'E' in line:
                try:
                    e_match = re.search(r'E([-+]?[\d.]+)', line)
                    if e_match:
                        e_value = float(e_match.group(1))
                        if e_value > 0:  # Only count positive extrusion
                            self._metadata['filament_used'] += e_value
                            # Only update filament usage in memory, don't send status updates
                            # Updates will be sent on layer changes instead
                            if hasattr(self.print_stats, 'info'):
                                self.print_stats.info['filament_used'] = self._metadata['filament_used']
                                self.print_stats.info['filament_total'] = self._metadata['filament_used']
                            self.print_stats.filament_used = self._metadata['filament_used']
                except Exception as e:
                    logging.warning(f"Failed to track filament usage: {e}")
            
            yield line
            
    def handle_shutdown(self):
        """Handle printer shutdown - required by virtual_sdcard."""
        self.reset()

    def start_print(self, filename):
        """Start a print with the given filename."""
        if self.is_streaming:
            raise self.printer.command_error("Already streaming a file")
        
        # Check if the file exists
        filepath = os.path.join(self.gcodes_path, filename)
        if not os.path.exists(filepath):
            raise self.printer.command_error(f"File {filename} not found")
        
        # Reset state
        self._reset_state()
        
        # Open the file and prepare for streaming
        self.filename = filename
        self.file_path = filepath
        
        try:
            # Read the encrypted file
            with open(filepath, 'rb') as f:
                encrypted_data = f.read()
            
            # Decrypt the file
            try:
                decrypted_data = self.fernet.decrypt(encrypted_data)
                self._decrypted_buffer = io.StringIO(decrypted_data.decode('utf-8'))
                self.file_size = len(decrypted_data)
            except Exception as e:
                raise self.printer.command_error(f"Failed to decrypt file: {str(e)}")
            
            # Set the start time
            self.start_print_time = self.reactor.monotonic()
            
            # Set streaming state
            self.is_streaming = True
            
            # Reset filament used to 0 at the start of print
            self._metadata['filament_used'] = 0.0
            
            # Inject SET_PRINT_STATS_INFO command at the beginning of the print
            # This is critical for Mainsail to display the layer count correctly
            layer_count = self._metadata.get('layer_count', 0)
            if layer_count > 0:
                self.print_stats.total_layer = layer_count
                self.print_stats.current_layer = 0
                # Also update the info field for Mainsail compatibility
                self.print_stats.info = {
                    'total_layer': layer_count,
                    'total_layers': layer_count,
                    'current_layer': 0,
                    'current_layers': 0,
                    'filament_used': 0.0,
                    'filament_total': self._metadata.get('filament_total', 0.0)
                }
                logging.info(f"Injected layer info at print start: 0/{layer_count}")
                
                # Send the command directly to ensure it takes effect
                gcode = self.printer.lookup_object('gcode')
                cmd = (
                    f"SET_PRINT_STATS_INFO"
                    f" TOTAL_LAYER={layer_count}"
                    f" CURRENT_LAYER=0"
                    f" FILAMENT_USED=0.0"
                )
                if 'filament_total' in self._metadata and self._metadata['filament_total'] > 0:
                    cmd += f" FILAMENT_TOTAL={self._metadata['filament_total']}"
                
                gcode.run_script_from_command(cmd)
            
            # Log the start of the print
            logging.info(f"Starting encrypted print: {filename}, size: {self.file_size} bytes")
            return True
        except Exception as e:
            self._reset_state()
            raise self.printer.command_error(f"Error starting print: {str(e)}")

def load_config(config):
    return EncryptedGCodeProvider(config)
