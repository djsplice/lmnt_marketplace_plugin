# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, logging, io
import re

VALID_GCODE_EXTS = ["gcode", "g", "gco"]


DEFAULT_ERROR_GCODE = """
{% if 'heaters' in printer %}
   TURN_OFF_HEATERS
{% endif %}
"""


class VirtualSD:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler(
            "klippy:shutdown", self.handle_shutdown
        )
        # sdcard state
        sd = config.get("path")
        self.with_subdirs = config.getboolean("with_subdirs", False)
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        logging.info(f"Virtual SD card path: {self.sdcard_dirname}")
        self.current_file = None
        self.file_position = self.file_size = 0
        # Print Stat Tracking
        self.print_stats = self.printer.load_object(config, "print_stats")
        # Work timer
        self.reactor = self.printer.get_reactor()
        self.must_pause_work = self.cmd_from_sd = False
        self.next_file_position = 0
        self.work_timer = None
        # Error handling
        gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.on_error_gcode = gcode_macro.load_template(
            config, "on_error_gcode", DEFAULT_ERROR_GCODE
        )
        # Register commands
        self.gcode = self.printer.lookup_object("gcode")
        for cmd in ["M20", "M21", "M23", "M24", "M25", "M26", "M27"]:
            self.gcode.register_command(cmd, getattr(self, "cmd_" + cmd))
        for cmd in ["M28", "M29", "M30"]:
            self.gcode.register_command(cmd, self.cmd_error)
        self.gcode.register_command(
            "SDCARD_RESET_FILE",
            self.cmd_SDCARD_RESET_FILE,
            desc=self.cmd_SDCARD_RESET_FILE_help,
        )
        self.gcode.register_command(
            "SDCARD_PRINT_FILE",
            self.cmd_SDCARD_PRINT_FILE,
            desc=self.cmd_SDCARD_PRINT_FILE_help,
        )
        # Layer change tracking
        self.last_layer_update_time = 0
        self.pending_layer_updates = []
        self.layer_update_timer = None
        self.min_layer_update_interval = 0.5  # Minimum seconds between layer UI updates

    def handle_shutdown(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            try:
                readpos = max(self.file_position - 1024, 0)
                readcount = self.file_position - readpos
                self.current_file.seek(readpos)
                data = self.current_file.read(readcount + 128)
            except:
                logging.exception("virtual_sdcard shutdown read")
                return
            logging.info(
                "Virtual sdcard (%d): %s\nUpcoming (%d): %s",
                readpos,
                repr(data[:readcount]),
                self.file_position,
                repr(data[readcount:]),
            )

    def stats(self, eventtime):
        if self.work_timer is None:
            return False, ""
        return True, "sd_pos=%d" % (self.file_position,)

    def get_file_list(self, check_subdirs=False):
        if check_subdirs:
            logging.info(f"Checking subdirectories in {self.sdcard_dirname}")
            flist = []
            for root, dirs, files in os.walk(
                self.sdcard_dirname, followlinks=True
            ):
                for name in files:
                    ext = os.path.splitext(name)[1]
                    if ext.lower()[1:] in VALID_GCODE_EXTS:
                        rel_path = os.path.relpath(os.path.join(root, name), self.sdcard_dirname)
                        size = os.path.getsize(os.path.join(root, name))
                        flist.append((rel_path, size))
            return flist
        else:
            logging.info(f"Listing files in {self.sdcard_dirname}")
            flist = []
            try:
                for fname in os.listdir(self.sdcard_dirname):
                    root, ext = os.path.splitext(fname)
                    if ext.lower()[1:] in VALID_GCODE_EXTS:
                        fpath = os.path.join(self.sdcard_dirname, fname)
                        size = os.path.getsize(fpath)
                        flist.append((fname, size))
            except Exception as e:
                logging.exception(f"Error listing files in {self.sdcard_dirname}: {str(e)}")
            return flist

    def get_status(self, eventtime):
        progress = self.progress()
        is_active = self.is_active()
        file_path = self.file_path()
        
        # Get print_stats for additional info
        try:
            print_stats = self.printer.lookup_object('print_stats')
            # Update file_size in print_stats
            print_stats.file_size = self.file_size
            # Force a status update
            print_stats.set_position(self.file_position, self.file_size)
        except Exception as e:
            logging.exception(f"Error updating print_stats: {e}")
            
        return {
            'file_position': self.file_position,
            'file_size': self.file_size,
            'progress': progress,
            'is_active': is_active,
            'file_path': file_path,
        }

    def file_path(self):
        if self.current_file:
            # Handle the case where current_file is an EncryptedGCodeProvider
            if hasattr(self.current_file, 'name'):
                return self.current_file.name
            elif hasattr(self.current_file, 'filename'):
                return self.current_file.filename
            elif hasattr(self.current_file, 'get_name'):
                return self.current_file.get_name()
        return None

    def progress(self):
        if self.file_size:
            return float(self.file_position) / self.file_size
        else:
            return 0.0

    def is_active(self):
        return self.work_timer is not None

    def do_pause(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            while self.work_timer is not None and not self.cmd_from_sd:
                self.reactor.pause(self.reactor.monotonic() + 0.001)

    def do_resume(self):
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        self.must_pause_work = False
        self.work_timer = self.reactor.register_timer(
            self.work_handler, self.reactor.NOW
        )

    def do_cancel(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
            self.print_stats.note_cancel()
        self.file_position = self.file_size = 0

    # G-Code commands
    def cmd_error(self, gcmd):
        raise gcmd.error("SD write not supported")

    def _reset_file(self):
        if self.current_file is not None:
            self.do_pause()
            self.current_file.close()
            self.current_file = None
        self.file_position = self.file_size = 0
        self.next_file_position = 0
        self.must_pause_work = False
        self.cmd_from_sd = False
        # Reset layer tracking
        self.pending_layer_updates = []
        self.last_layer_update_time = 0
        if self.layer_update_timer is not None:
            self.reactor.unregister_timer(self.layer_update_timer)
            self.layer_update_timer = None
        self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")

    cmd_SDCARD_RESET_FILE_help = (
        "Clears a loaded SD File. Stops the print if necessary"
    )

    def cmd_SDCARD_RESET_FILE(self, gcmd):
        if self.cmd_from_sd:
            raise gcmd.error("SDCARD_RESET_FILE cannot be run from the sdcard")
        self._reset_file()

    cmd_SDCARD_PRINT_FILE_help = (
        "Loads a SD file and starts the print.  May "
        "include files in subdirectories."
    )

    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        # Accept both FILE and FILENAME for backward compatibility
        filename = gcmd.get("FILENAME", None)
        if filename is None:
            filename = gcmd.get("FILE", "")
        if not filename:
            raise gcmd.error("SD print requires FILENAME parameter")
        
        # Reset print_stats
        print_stats = self.printer.lookup_object('print_stats')
        if hasattr(print_stats, 'current_layer'):
            print_stats.current_layer = 0
        if hasattr(print_stats, 'total_layer'):
            print_stats.total_layer = 0
        if hasattr(print_stats, 'info'):
            print_stats.info['current_layer'] = 0
            print_stats.info['total_layer'] = 0
            print_stats.info['total_layers'] = 0
                
        # Try to detect total layer count from file
        full_path = os.path.join(self.sdcard_dirname, filename)
        if os.path.exists(full_path):
            try:
                self._detect_total_layers(full_path)
                
                # Also scan for filament usage estimate
                self._detect_filament_usage(full_path)
            except Exception as e:
                logging.warning(f"Failed to detect total layers: {e}")
                
            # Also check for metadata file for encrypted gcode
            metadata_path = full_path + ".metadata"
            if os.path.exists(metadata_path):
                try:
                    self._load_metadata(metadata_path)
                except Exception as e:
                    logging.warning(f"Failed to load metadata: {e}")
        
        # Load file and start print
        self._load_file(gcmd, filename, check_subdirs=True)
        self.do_resume()

    def _load_metadata(self, metadata_path):
        """Load metadata from encrypted gcode metadata file"""
        try:
            import json
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
                
            # Extract layer information
            if 'layer_count' in metadata:
                total_layers = int(metadata['layer_count'])
                if total_layers > 0:
                    # Update print_stats with the detected total
                    print_stats = self.printer.lookup_object('print_stats')
                    print_stats.total_layer = total_layers
                    if hasattr(print_stats, 'info'):
                        print_stats.info['total_layer'] = total_layers
                        # Also set the alternate key for compatibility
                        print_stats.info['total_layers'] = total_layers
                    logging.info(f"Loaded total layers from metadata: {total_layers}")
                    
                    # Send explicit G-code command to update Klipper's internal state
                    gcode = self.printer.lookup_object('gcode')
                    gcode.run_script_from_command(f"SET_PRINT_STATS_INFO TOTAL_LAYER={total_layers}")
                    
                    # Force a status update to ensure UI gets the latest info
                    eventtime = self.reactor.monotonic()
                    self.printer.send_event("virtual_sdcard:layer_count_update", eventtime, total_layers)
                    
            # Extract other metadata like filament used, estimated time, etc.
            if 'filament_total' in metadata:
                filament_total = float(metadata['filament_total'])
                print_stats = self.printer.lookup_object('print_stats')
                if hasattr(print_stats, 'info'):
                    print_stats.info['filament_total'] = filament_total
                    
                # Send explicit G-code command to update Klipper's internal state
                gcode = self.printer.lookup_object('gcode')
                gcode.run_script_from_command(f"SET_PRINT_STATS_INFO FILAMENT_TOTAL={filament_total}")
                    
            if 'estimated_time' in metadata:
                estimated_time = float(metadata['estimated_time'])
                print_stats = self.printer.lookup_object('print_stats')
                if hasattr(print_stats, 'info'):
                    print_stats.info['estimated_time'] = estimated_time
                    
                # Send explicit G-code command to update Klipper's internal state
                gcode = self.printer.lookup_object('gcode')
                gcode.run_script_from_command(f"SET_PRINT_STATS_INFO ESTIMATED_TIME={estimated_time}")
        except Exception as e:
            logging.warning(f"Error loading metadata: {e}")

    def cmd_M20(self, gcmd):
        # List SD card
        files = self.get_file_list(self.with_subdirs)
        gcmd.respond_raw("Begin file list")
        for fname, fsize in files:
            gcmd.respond_raw("%s %d" % (fname, fsize))
        gcmd.respond_raw("End file list")

    def cmd_M21(self, gcmd):
        # Initialize SD card
        gcmd.respond_raw("SD card ok")

    def cmd_M23(self, gcmd):
        # Select SD file
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        filename = gcmd.get_raw_command_parameters().strip()
        if filename.startswith("/"):
            filename = filename[1:]
        self._load_file(gcmd, filename, self.with_subdirs)

    def _load_file(self, gcmd, filename, check_subdirs=False):
        # First check if this is an encrypted G-code file
        try:
            encrypted_gcode = self.printer.lookup_object('encrypted_gcode')
            if encrypted_gcode is not None:
                logging.info(f"Attempting to load as encrypted G-code: {filename}")
                try:
                    encrypted_gcode.set_filename(filename)
                    # If we get here, it's an encrypted file
                    logging.info(f"Successfully loaded encrypted G-code file: {filename}")
                    self.current_file = encrypted_gcode
                    self.file_position = 0
                    self.file_size = getattr(encrypted_gcode, 'file_size', 0)
                    logging.info(f"Encrypted file size: {self.file_size}")
                    
                    # Set the filename in print_stats
                    print_stats = self.printer.lookup_object('print_stats')
                    print_stats.set_current_file(filename)
                    
                    # Update file size in print_stats
                    print_stats.file_size = self.file_size
                    print_stats.set_position(0, self.file_size)
                    
                    # Check for metadata file for encrypted gcode
                    metadata_path = os.path.join(self.sdcard_dirname, filename + ".metadata")
                    if os.path.exists(metadata_path):
                        try:
                            self._load_metadata(metadata_path)
                        except Exception as e:
                            logging.warning(f"Failed to load metadata: {e}")
                    
                    # Force a status update to ensure UI gets the latest info
                    self.printer.send_event("virtual_sdcard:load_file")
                    gcmd.respond_raw("File opened: %s Size: %d" % (filename, self.file_size))
                    gcmd.respond_raw("File selected")
                    return
                except Exception as e:
                    logging.error(f"Error loading encrypted file: {str(e)}")
                    logging.debug(f"Not an encrypted file: {str(e)}")
                    # Continue with normal file loading
        except Exception as e:
            logging.debug(f"encrypted_gcode module not available: {str(e)}")
            # Continue with normal file loading
        
        # Standard file loading for plaintext G-code
        try:
            # Log directory contents for debugging
            logging.info(f"Current SD card directory: {self.sdcard_dirname}")
            logging.info(f"Looking for file: {filename}")
            
            # Get a list of all files in the directory
            files = self.get_file_list(check_subdirs)
            
            # Log available files for debugging
            logging.info(f"Available files: {[f[0] for f in files]}")
            
            flist = [f[0] for f in files]
            files_by_lower = {fname.lower(): fname for fname, fsize in files}
            
            # Try to find the file (case insensitive)
            found_file = None
            if filename in flist:
                found_file = filename
                logging.info(f"Found exact match: {found_file}")
            elif filename.lower() in files_by_lower:
                found_file = files_by_lower[filename.lower()]
                logging.info(f"Found case-insensitive match: {found_file}")
                
            if found_file is None:
                # Try direct path
                filepath = os.path.join(self.sdcard_dirname, filename)
                logging.info(f"Checking direct path: {filepath}")
                if os.path.isfile(filepath):
                    found_file = filename
                    logging.info(f"Found at direct path: {found_file}")
                else:
                    # Try subdirectories if enabled
                    if check_subdirs:
                        logging.info("Searching subdirectories...")
                        path_match = []
                        for root, dirs, files in os.walk(
                            self.sdcard_dirname, followlinks=True
                        ):
                            for name in files:
                                if name == filename:
                                    fname = os.path.join(root, name)
                                    ext = os.path.splitext(fname)[1]
                                    if ext.lower()[1:] in VALID_GCODE_EXTS:
                                        path_match.append(fname)
                                        logging.info(f"Found in subdirectory: {fname}")
                        if path_match:
                            if len(path_match) > 1:
                                logging.warning(f"Multiple matches found: {path_match}")
                                raise gcmd.error(
                                    "Ambiguous filename: %s matches: %s"
                                    % (filename, path_match)
                                )
                            filepath = path_match[0]
                            found_file = os.path.relpath(filepath, self.sdcard_dirname)
                            logging.info(f"Using file from subdirectory: {found_file}")
            
            if found_file is None:
                logging.error(f"Unable to find file: {filename}")
                raise gcmd.error(f"Unable to open file '{filename}'")
                
            # Open the file
            filepath = os.path.join(self.sdcard_dirname, found_file)
            logging.info(f"Opening file at path: {filepath}")
            self.current_file = open(filepath, 'rb')
            self.file_position = 0
            self.file_size = os.path.getsize(filepath)
            
            # Update print_stats with file information
            print_stats = self.printer.lookup_object('print_stats')
            print_stats.set_current_file(found_file)
            
            # Update file size in print_stats
            print_stats.file_size = self.file_size
            print_stats.set_position(0, self.file_size)
            
            # Try to detect total layer count
            try:
                self._detect_total_layers(filepath)
                
                # Also scan for filament usage estimate
                self._detect_filament_usage(filepath)
            except Exception as e:
                logging.warning(f"Failed to detect file metadata: {e}")
            
            logging.info(
                "sd_card: Loaded '%s' file: %s\n"
                % (filename, filepath)
            )
            gcmd.respond_raw("File opened: %s Size: %d" % (filename, self.file_size))
            gcmd.respond_raw("File selected")
            
            # Force a status update to ensure UI gets the latest info
            self.printer.send_event("virtual_sdcard:load_file")
        except Exception as e:
            logging.exception(f"virtual_sdcard: Unable to open file: {str(e)}")
            raise gcmd.error("Unable to open file")
            
    def _detect_filament_usage(self, filename):
        try:
            filament_total = 0.0
            estimated_time = 0.0
            total_layers = 0
            
            logging.info(f"Scanning file for metadata: {filename}")
            
            # First try to find the time estimate from the filename itself
            # Format like: rabbit_0.2mm_PLA_4m32s.gcode
            try:
                base_filename = os.path.basename(filename)
                logging.info(f"Checking filename for time estimate: {base_filename}")
                
                # Look for time pattern like 4m32s or 1h5m
                time_match = re.search(r'_(\d+)h(\d+)m\.gcode$|_(\d+)m(\d+)s\.gcode$|_(\d+)h(\d+)m(\d+)s\.gcode$', base_filename)
                if time_match:
                    logging.info(f"Found time in filename: {time_match.group(0)}")
                    # Check which pattern matched
                    if time_match.group(1) is not None and time_match.group(2) is not None:
                        # Format: _1h5m.gcode
                        hours = int(time_match.group(1))
                        minutes = int(time_match.group(2))
                        estimated_time = hours * 3600 + minutes * 60
                        logging.info(f"Parsed time from filename: {hours}h{minutes}m = {estimated_time}s")
                    elif time_match.group(3) is not None and time_match.group(4) is not None:
                        # Format: _4m32s.gcode
                        minutes = int(time_match.group(3))
                        seconds = int(time_match.group(4))
                        estimated_time = minutes * 60 + seconds
                        logging.info(f"Parsed time from filename: {minutes}m{seconds}s = {estimated_time}s")
                    elif time_match.group(5) is not None and time_match.group(6) is not None and time_match.group(7) is not None:
                        # Format: _1h5m30s.gcode
                        hours = int(time_match.group(5))
                        minutes = int(time_match.group(6))
                        seconds = int(time_match.group(7))
                        estimated_time = hours * 3600 + minutes * 60 + seconds
                        logging.info(f"Parsed time from filename: {hours}h{minutes}m{seconds}s = {estimated_time}s")
            except Exception as e:
                logging.warning(f"Error parsing filename for time: {e}")
            
            # Direct check for filament usage in the last 1000 lines
            try:
                from collections import deque
                last_lines = deque(maxlen=1000)
                with open(filename, 'r') as f:
                    for line in f:
                        last_lines.append(line)
                
                # Check for various filament patterns in the last lines
                for i, line in enumerate(last_lines):
                    lower = line.lower()
                    # PrusaSlicer format
                    if 'filament used [mm]' in lower:
                        match = re.search(r';\s*filament used \[mm\]\s*=\s*([\d.]+)', lower)
                        if match:
                            filament_mm = float(match.group(1))
                            filament_total = filament_mm
                            logging.info(f"Detected filament usage (PrusaSlicer mm): {filament_mm}")
                            break
                    # Cura format
                    elif ';filament used=' in lower or ';filament used:' in lower:
                        match = re.search(r';\s*filament used[=:]\s*([\d.]+)', lower)
                        if match:
                            filament_total = float(match.group(1))
                            logging.info(f"Detected filament usage (Cura): {filament_total}")
                            break
                    # SuperSlicer format
                    elif '; filament used [cm3]' in lower:
                        match = re.search(r';\s*filament used \[cm3\]\s*=\s*([\d.]+)', lower)
                        if match:
                            filament_cm3 = float(match.group(1))
                            # Store as cm3, UI can convert as needed
                            filament_total = filament_cm3
                            logging.info(f"Detected filament usage (SuperSlicer cm3): {filament_cm3}")
                            break
                    # Look for other common formats
                    elif ';total filament' in lower:
                        match = re.search(r';\s*total filament.*?[=:]\s*([\d.]+)', lower)
                        if match:
                            filament_total = float(match.group(1))
                            logging.info(f"Detected total filament: {filament_total}")
                            break
                    elif 'filament length' in lower:
                        match = re.search(r';\s*filament length.*?[=:]\s*([\d.]+)', lower)
                        if match:
                            filament_total = float(match.group(1))
                            logging.info(f"Detected filament length: {filament_total}")
                            break
            except Exception as e:
                logging.warning(f"Error in direct filament check: {e}")
            
            # Function to process a line and extract metadata
            def process_line(line, filament_total, estimated_time, total_layers):
                lower = line.lower()
                
                # Log the first few lines for debugging
                if len(lower) < 100:  # Only log reasonably sized lines
                    logging.debug(f"Scanning line: {lower}")
                
                # Check for filament usage
                if ';filament used' in lower:
                    match = re.search(r';\s*filament used \[mm\]\s*=\s*([\d.]+)', lower)
                    if match:
                        filament_mm = float(match.group(1))
                        filament_total = filament_mm
                    else:
                        match = re.search(r';\s*filament used[=:]\s*([\d.]+)\s*m', lower)
                        if match:
                            filament_m = float(match.group(1))
                            filament_total = filament_m * 1000.0
                        else:
                            match = re.search(r';\s*filament used[=:]\s*([\d.]+)\s*mm', lower)
                            if match:
                                filament_total = float(match.group(1))
                elif ';filament_total' in lower:
                    match = re.search(r';\s*filament_total[=:]\s*([\d.]+)', lower)
                    if match:
                        filament_total = float(match.group(1))
                elif ';filament used:' in lower:
                    match = re.search(r';\s*filament used:\s*([\d.]+)m', lower)
                    if match:
                        filament_m = float(match.group(1))
                        filament_total = filament_m * 1000.0
                elif '; total filament used' in lower:
                    match = re.search(r';\s*total filament used \[mm\]\s*=\s*([\d.]+)', lower)
                    if match:
                        filament_total = float(match.group(1))
                elif ';   filament length:' in lower:
                    match = re.search(r';\s*filament length:\s*([\d.]+)\s*mm', lower)
                    if match:
                        filament_total = float(match.group(1))
                # Check for time estimates
                elif ';TIME:' in lower:
                    match = re.search(r';TIME:\s*(\d+)', lower)
                    if match:
                        # Cura format, time in seconds
                        estimated_time = int(match.group(1))
                        logging.info(f"Detected estimated time (Cura): {estimated_time}s")
                elif '; estimated printing time' in lower:
                    # PrusaSlicer format
                    try:
                        # Extract the time string
                        time_str = lower.split('estimated printing time')[1].strip()
                        if '=' in time_str:
                            time_str = time_str.split('=')[1].strip()
                        if ':' in time_str:
                            time_str = time_str.split(':')[1].strip()
                        
                        # Parse the time components
                        hours = 0
                        minutes = 0
                        seconds = 0
                        
                        if 'h' in time_str:
                            hours_part = time_str.split('h')[0].strip()
                            hours = int(hours_part)
                            time_str = time_str.split('h')[1].strip()
                        if 'm' in time_str:
                            minutes_part = time_str.split('m')[0].strip()
                            minutes = int(minutes_part)
                            time_str = time_str.split('m')[1].strip()
                        if 's' in time_str:
                            seconds_part = time_str.split('s')[0].strip()
                            seconds = int(seconds_part)
                        
                        estimated_time = hours * 3600 + minutes * 60 + seconds
                        logging.info(f"Detected estimated time (PrusaSlicer): {estimated_time}s")
                    except Exception as e:
                        logging.warning(f"Error parsing PrusaSlicer time: {e}")
                # Cura format
                elif ';time' in lower and len(lower.strip()) < 15:  # Avoid matching other time-related comments
                    match = re.search(r';time[=:]\s*(\d+)', lower)
                    if match:
                        # Cura stores time in seconds
                        estimated_time = int(match.group(1))
                        logging.info(f"Detected estimated time (Cura): {estimated_time}s")
                
                # Check for layer information
                elif ';LAYER_COUNT:' in lower:
                    match = re.search(r';LAYER_COUNT:\s*(\d+)', lower)
                    if match:
                        total_layers = int(match.group(1))
                        logging.info(f"Detected total layers (Cura): {total_layers}")
                elif '; total layers count = ' in lower:
                    match = re.search(r'; total layers count = (\d+)', lower)
                    if match:
                        total_layers = int(match.group(1))
                        logging.info(f"Detected total layers (PrusaSlicer): {total_layers}")
                
                return filament_total, estimated_time, total_layers
            
            # Process the entire file
            with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    filament_total, estimated_time, total_layers = process_line(
                        line, filament_total, estimated_time, total_layers)
            
            # Update print_stats with the detected values
            if filament_total > 0 or estimated_time > 0 or total_layers > 0:
                self._update_print_stats_with_metadata(filament_total, estimated_time, total_layers)
        except Exception as e:
            logging.warning(f"Error detecting filament usage: {e}")

    def _update_print_stats_with_metadata(self, filament_total, estimated_time, total_layers):
        print_stats = self.printer.lookup_object('print_stats')
        
        # Update the info dictionary for Mainsail compatibility
        if hasattr(print_stats, 'info'):
            if filament_total > 0:
                # Set filament_used to 0 initially, filament_total to the total value
                print_stats.info['filament_used'] = 0.0
                print_stats.info['filament_total'] = filament_total
                logging.info(f"Set info['filament_used']=0.0 and info['filament_total']={filament_total}")
            
            if estimated_time > 0:
                print_stats.info['estimated_time'] = estimated_time
                logging.info(f"Set info['estimated_time'] to {estimated_time}")
            
            if total_layers > 0:
                print_stats.info['total_layer'] = total_layers
                print_stats.info['total_layers'] = total_layers
                logging.info(f"Set info['total_layer'] to {total_layers}")
        
        # Directly set the values in print_stats
        if filament_total > 0:
            # Set filament_used to 0 initially
            print_stats.filament_used = 0.0
            logging.info(f"Directly set filament_used to 0.0 (total will be {filament_total})")
            
        if estimated_time > 0:
            print_stats.total_duration = estimated_time
            logging.info(f"Directly set total_duration to {estimated_time}")
            
        if total_layers > 0:
            print_stats.total_layer = total_layers
            logging.info(f"Directly set total_layer to {total_layers}")
        
        # Build a SET_PRINT_STATS_INFO command with all the metadata
        cmd = "SET_PRINT_STATS_INFO"
        if filament_total > 0:
            cmd += f" FILAMENT_USED=0.0 FILAMENT_TOTAL={filament_total}"
        if estimated_time > 0:
            cmd += f" TOTAL_TIME={estimated_time}"
        if total_layers > 0:
            cmd += f" TOTAL_LAYER={total_layers}"
        
        # Send the command
        try:
            gcode = self.printer.lookup_object('gcode')
            gcode.run_script_from_command(cmd)
            logging.info(f"Sent print stats update: {cmd}")
        except Exception as e:
            logging.error(f"Error sending print stats update: {e}")
        
        # Force a status update to ensure UI gets the latest info
        eventtime = self.reactor.monotonic()
        print_stats._update_stats(eventtime)
        self.printer.send_event("virtual_sdcard:metadata_update", eventtime)
        
        # Log the current state for debugging
        logging.info(f"Current print_stats state: filament_used={print_stats.filament_used}, total_duration={print_stats.total_duration}, total_layer={print_stats.total_layer}")
        if hasattr(print_stats, 'info'):
            logging.info(f"Current print_stats.info: {print_stats.info}")

    def _update_print_stats(self):
        try:
            progress = self.progress()
            print_stats = self.printer.lookup_object('print_stats')
            
            if progress > 0 and hasattr(print_stats, 'info') and 'filament_total' in print_stats.info:
                filament_total = print_stats.info['filament_total']
                if filament_total > 0:
                    # Calculate filament used based on progress
                    filament_used = filament_total * progress
                    current_filament_used = print_stats.filament_used
                    
                    # Only update if the change is significant (more than 1mm)
                    if abs(filament_used - current_filament_used) > 1.0:
                        current_time = self.reactor.monotonic()
                        last_update = getattr(self, '_last_filament_update_time', 0)
                        
                        # Limit updates to once every 5 seconds
                        if (current_time - last_update) > 5.0:
                            # Update print_stats directly
                            print_stats.filament_used = filament_used
                            print_stats.info['filament_used'] = filament_used
                            
                            # Send command to update filament used
                            cmd = f"SET_PRINT_STATS_INFO FILAMENT_USED={filament_used}"
                            gcode = self.printer.lookup_object('gcode')
                            gcode.run_script_from_command(cmd)
                            
                            # Log the update
                            logging.info(f"Updated filament_used to {filament_used:.2f} (progress: {progress:.2f})")
                            
                            # Remember when we last updated
                            self._last_filament_update_time = current_time
        except Exception as e:
            logging.exception(f"Error updating print_stats: {e}")

    def cmd_M24(self, gcmd):
        # Start/resume SD print
        self.do_resume()

    def cmd_M25(self, gcmd):
        # Pause SD print
        self.do_pause()

    def cmd_M26(self, gcmd):
        # Set SD position
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        pos = gcmd.get_int("S", minval=0)
        self.file_position = pos

    def cmd_M27(self, gcmd):
        # Report SD print status
        if self.current_file is None:
            gcmd.respond_raw("Not SD printing.")
            return
        gcmd.respond_raw(
            "SD printing byte %d/%d" % (self.file_position, self.file_size)
        )

    def get_file_position(self):
        return self.next_file_position

    def set_file_position(self, pos):
        self.next_file_position = pos

    def is_cmd_from_sd(self):
        return self.cmd_from_sd

    def detect_and_update_layer(self, line):
        lower = line.lower()
        # Same markers as encrypted_gcode.py
        if (';layer:' in lower or 
            'layer_z' in lower or 
            ';z:' in lower or
            ';layer ' in lower or
            '; layer ' in lower or
            ';layer change' in lower or
            ';layer number' in lower or
            ';move to next layer' in lower or
            'move to z' in lower or
            ';layer_change' in lower or
            ';after_layer_change' in lower):
            current_layer = None
            try:
                import re
                layer_match = re.search(r';layer:?\s*(\d+)', lower)
                if layer_match:
                    current_layer = int(layer_match.group(1))
                else:
                    layer_num_match = re.search(r';layer (?:number|#)?\s*(\d+)', lower)
                    if layer_num_match:
                        current_layer = int(layer_num_match.group(1))
                    else:
                        z_match = re.search(r'(?:layer_z|z:|move to z|;z:)\s*([\d.]+)', lower)
                        if z_match:
                            z_height = float(z_match.group(1))
                            layer_height = 0.2  # Default, could be improved
                            current_layer = int(round(z_height / layer_height))
                if current_layer is not None and current_layer >= 0:
                    if current_layer > getattr(self.print_stats, 'current_layer', 0):
                        # Update print_stats with the new layer
                        self.print_stats.current_layer = current_layer
                        if hasattr(self.print_stats, 'info'):
                            self.print_stats.info['current_layer'] = current_layer
                        
                        # Log the layer change
                        logging.info(f"Layer changed: {current_layer}/{getattr(self.print_stats, 'total_layer', 0)}")
                        
                        # Queue this layer update to be sent to UI at appropriate intervals
                        self.pending_layer_updates.append(current_layer)
                        
                        # Start the layer update timer if it's not already running
                        if self.layer_update_timer is None:
                            self.layer_update_timer = self.reactor.register_timer(
                                self.layer_update_handler, self.reactor.monotonic())
            except Exception as e:
                logging.warning(f"Failed to parse layer change: {e}")

    def layer_update_handler(self, eventtime):
        # This handler ensures UI updates for layer changes happen at a reasonable rate
        # without affecting print quality or G-code processing speed
        if not self.pending_layer_updates:
            self.layer_update_timer = None
            return self.reactor.NEVER
            
        now = self.reactor.monotonic()
        time_since_last_update = now - self.last_layer_update_time
        
        if time_since_last_update < self.min_layer_update_interval:
            # Not enough time has passed since the last update
            return now + (self.min_layer_update_interval - time_since_last_update)
            
        # Get the latest layer update (in case multiple layers were detected in quick succession)
        current_layer = self.pending_layer_updates[-1]
        self.pending_layer_updates = []
        
        try:
            gcode = self.printer.lookup_object('gcode')
            # This will update Klipper's internal state and trigger a notification
            gcode.run_script_from_command(f"SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer}")
            
            # Display on LCD
            gcode.run_script_from_command(f"M117 Layer {current_layer}/{getattr(self.print_stats, 'total_layer', 0)}")
            
            # Force a status update to be sent to clients
            self.printer.send_event("virtual_sdcard:layer_change", eventtime, current_layer)
            
            # Record the time of this update
            self.last_layer_update_time = now
        except Exception as e:
            logging.error(f"Error sending layer update to UI: {str(e)}")
            
        # Check if there are more updates pending
        if self.pending_layer_updates:
            return now + self.min_layer_update_interval
        else:
            self.layer_update_timer = None
            return self.reactor.NEVER

    def _start_print(self, filename, print_stats):
        self._reset_file()
        self.print_stats = print_stats
        try:
            # Check if file exists
            if not os.path.exists(filename):
                # Check if file exists in sdcard_dirname
                full_path = os.path.join(self.sdcard_dirname, filename)
                if not os.path.exists(full_path):
                    raise self.gcode.error(
                        "Unable to open file '%s'" % (filename)
                    )
                filename = full_path
            
            # Reset print_stats layer info
            if hasattr(self.print_stats, 'current_layer'):
                self.print_stats.current_layer = 0
            if hasattr(self.print_stats, 'total_layer'):
                self.print_stats.total_layer = 0
            if hasattr(self.print_stats, 'info'):
                self.print_stats.info['current_layer'] = 0
                self.print_stats.info['current_layers'] = 0
                self.print_stats.info['total_layer'] = 0
                self.print_stats.info['total_layers'] = 0
                
            # Try to detect total layer count from file
            try:
                self._detect_total_layers(filename)
            except Exception as e:
                logging.warning(f"Failed to detect total layers: {e}")
                
            # Try to detect filament usage and estimated time
            try:
                self._detect_filament_usage(filename)
            except Exception as e:
                logging.warning(f"Failed to detect filament usage: {e}")
                
            # Open file and start print
            self.current_file = open(filename, 'rb')
            self.file_position = 0
            self.file_size = os.path.getsize(filename)
            self.print_stats.set_current_file(filename)
            self.work_timer = self.reactor.register_timer(
                self.work_handler, self.reactor.NOW
            )
        except Exception:
            logging.exception("virtual_sdcard: Unable to open file")
            raise self.gcode.error("Unable to open file")
            
    def _detect_total_layers(self, filename):
        """Scan the file to detect total layer count"""
        max_layer = 0
        try:
            with open(filename, 'r') as f:
                for line in f:
                    lower = line.lower()
                    if ';layer:' in lower:
                        layer_match = re.search(r';layer:?\s*(\d+)', lower)
                        if layer_match:
                            layer = int(layer_match.group(1))
                            max_layer = max(max_layer, layer)
                    elif ';total layers:' in lower:
                        total_match = re.search(r';total layers:?\s*(\d+)', lower)
                        if total_match:
                            total = int(total_match.group(1))
                            if total > 0:
                                # Found explicit total, use it
                                max_layer = total
                                break
                    # Check for other common layer count metadata formats
                    elif ';layer_count:' in lower:
                        count_match = re.search(r';layer_count:?\s*(\d+)', lower)
                        if count_match:
                            count = int(count_match.group(1))
                            if count > 0:
                                max_layer = count
                                break
                    elif ';total_layer_count' in lower:
                        count_match = re.search(r';total_layer_count:?\s*(\d+)', lower)
                        if count_match:
                            count = int(count_match.group(1))
                            if count > 0:
                                max_layer = count
                                break
                    # PrusaSlicer/SuperSlicer format
                    elif '; total layers count = ' in lower:
                        count_match = re.search(r'; total layers count = (\d+)', lower)
                        if count_match:
                            count = int(count_match.group(1))
                            if count > 0:
                                max_layer = count
                                break
                    # Cura format
                    elif ';layer_count' in lower:
                        count_match = re.search(r';layer_count:(\d+)', lower)
                        if count_match:
                            count = int(count_match.group(1))
                            if count > 0:
                                max_layer = count
                                break
            
            if max_layer > 0:
                # Update print_stats with the detected total
                print_stats = self.printer.lookup_object('print_stats')
                print_stats.total_layer = max_layer
                if hasattr(print_stats, 'info'):
                    print_stats.info['total_layer'] = max_layer
                    # Also set the alternate key for compatibility
                    print_stats.info['total_layers'] = max_layer
                logging.info(f"Detected total layers: {max_layer}")
                
                # Send explicit G-code command to update Klipper's internal state
                gcode = self.printer.lookup_object('gcode')
                gcode.run_script_from_command(f"SET_PRINT_STATS_INFO TOTAL_LAYER={max_layer}")
                
                # Force a status update to ensure UI gets the latest info
                eventtime = self.reactor.monotonic()
                self.printer.send_event("virtual_sdcard:layer_count_update", eventtime, max_layer)
        except Exception as e:
            logging.warning(f"Error detecting total layers: {e}")

    # Background work timer
    def work_handler(self, eventtime):
        logging.info("Starting SD card print (position %d)", self.file_position)
        logging.info(f"Current file type: {type(self.current_file)}")
        self.reactor.unregister_timer(self.work_timer)
        
        # Check if we're using a custom provider (like EncryptedGCodeProvider)
        is_custom_provider = hasattr(self.current_file, 'get_gcode') and not hasattr(self.current_file, 'seek')
        
        if not is_custom_provider:
            try:
                logging.info(f"Seeking to position {self.file_position}")
                self.current_file.seek(self.file_position)
            except Exception as e:
                logging.exception(f"virtual_sdcard seek error: {str(e)}")
                self.work_timer = None
                return self.reactor.NEVER
                
        self.print_stats.note_start()
        gcode_mutex = self.gcode.get_mutex()
        partial_input = ""
        lines = []
        error_message = None
        
        # For custom providers, get the generator directly
        gcode_generator = None
        if is_custom_provider:
            logging.info("Using custom provider's get_gcode method (with batching)")
            try:
                gcode_generator = self.current_file.get_gcode()
            except Exception as e:
                logging.exception(f"Error getting gcode generator: {str(e)}")
                self.work_timer = None
                return self.reactor.NEVER
        
        BATCH_SIZE = 50  # Reduced batch size for encrypted gcode to ensure smoother UI updates
        last_update_time = self.reactor.monotonic()
        
        while not self.must_pause_work:
            # Update filament usage based on progress every 5 seconds
            # But only for standard files, not custom providers like encrypted_gcode
            current_time = self.reactor.monotonic()
            if current_time - last_update_time > 5.0 and not is_custom_provider:
                try:
                    self._update_print_stats()
                    last_update_time = current_time
                except Exception as e:
                    logging.warning(f"Error updating print stats: {e}")
                    
            if is_custom_provider:
                # Batch lines from the generator
                if not lines:
                    try:
                        lines = [next(gcode_generator, None) for _ in range(BATCH_SIZE)]
                        lines = [l for l in lines if l is not None]
                        if not lines:
                            # End of generator
                            logging.info("End of custom provider generator")
                            self.current_file = None
                            logging.info("Finished SD card print")
                            self.gcode.respond_raw("Done printing file")
                            break
                        lines.reverse()  # To pop from the end like standard path
                    except Exception as e:
                        logging.exception(f"Error batching lines from custom provider: {str(e)}")
                        error_message = str(e)
                        break
                # Pause if any other request is pending in the gcode class
                if gcode_mutex.test():
                    self.reactor.pause(self.reactor.monotonic() + 0.100)
                    continue
                # Dispatch command
                self.cmd_from_sd = True
                line = lines.pop()
                logging.debug(f"Processing line from custom provider: {line}")
                self.detect_and_update_layer(line)
                try:
                    self.gcode.run_script(line)
                except self.gcode.error as e:
                    error_message = str(e)
                    logging.error(f"G-code error: {error_message}")
                    try:
                        self.gcode.run_script(self.on_error_gcode.render())
                    except Exception as e:
                        logging.exception(f"virtual_sdcard on_error error: {str(e)}")
                    break
                except Exception as e:
                    error_message = str(e)
                    logging.exception(f"virtual_sdcard dispatch error: {str(e)}")
                    break
                self.cmd_from_sd = False
                # Update position for progress tracking (use bytes, not lines)
                self.file_position += len(line.encode('utf-8')) + 1  # +1 for newline
            else:
                # Standard file processing
                if not lines:
                    # Read more data
                    try:
                        logging.info("Reading data from file")
                        data = self.current_file.read(8192)
                        logging.info(f"Read {len(data) if data else 0} bytes")
                    except Exception as e:
                        logging.exception(f"virtual_sdcard read error: {str(e)}")
                        error_message = str(e)
                        break
                    if not data:
                        # End of file
                        logging.info("End of file reached")
                        self.current_file.close()
                        self.current_file = None
                        logging.info("Finished SD card print")
                        self.gcode.respond_raw("Done printing file")
                        break
                    
                    # Convert binary data to text
                    try:
                        text_data = data.decode('utf-8', 'replace')
                        lines = text_data.split("\n")
                        lines[0] = partial_input + lines[0]
                        partial_input = lines.pop()
                        lines.reverse()
                    except Exception as e:
                        logging.exception(f"Error processing file data: {str(e)}")
                        error_message = str(e)
                        break
                        
                    self.reactor.pause(self.reactor.NOW)
                    continue
                # Pause if any other request is pending in the gcode class
                if gcode_mutex.test():
                    self.reactor.pause(self.reactor.monotonic() + 0.100)
                    continue
                # Dispatch command
                self.cmd_from_sd = True
                line = lines.pop()
                logging.debug(f"Processing line: {line}")
                self.detect_and_update_layer(line)
                
                # Calculate next file position
                line_bytes = line.encode('utf-8')
                next_file_position = self.file_position + len(line_bytes) + 1  # +1 for newline
                
                self.next_file_position = next_file_position
                try:
                    self.gcode.run_script(line)
                except self.gcode.error as e:
                    error_message = str(e)
                    logging.error(f"G-code error: {error_message}")
                    try:
                        self.gcode.run_script(self.on_error_gcode.render())
                    except Exception as e:
                        logging.exception(f"virtual_sdcard on_error error: {str(e)}")
                    break
                except Exception as e:
                    error_message = str(e)
                    logging.exception(f"virtual_sdcard dispatch error: {str(e)}")
                    break
                self.cmd_from_sd = False
                self.file_position = self.next_file_position
                # Do we need to skip around?
                if self.next_file_position != next_file_position:
                    try:
                        self.current_file.seek(self.file_position)
                    except Exception as e:
                        logging.exception(f"virtual_sdcard seek error: {str(e)}")
                        error_message = str(e)
                        self.work_timer = None
                        return self.reactor.NEVER
                    lines = []
                    partial_input = ""
                    
        logging.info("Exiting SD card print (position %d)", self.file_position)
        self.work_timer = None
        self.cmd_from_sd = False
        if error_message is not None:
            self.print_stats.note_error(error_message)
        elif self.current_file is not None:
            self.print_stats.note_pause()
        else:
            self.print_stats.note_complete()
        return self.reactor.NEVER

def load_config(config):
    return VirtualSD(config)