# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, logging, io

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
            flist = []
            for root, dirs, files in os.walk(
                self.sdcard_dirname, followlinks=True
            ):
                for name in files:
                    ext = name[name.rfind(".") + 1 :]
                    if ext not in VALID_GCODE_EXTS:
                        continue
                    full_path = os.path.join(root, name)
                    r_path = full_path[len(self.sdcard_dirname) + 1 :]
                    size = os.path.getsize(full_path)
                    flist.append((r_path, size))
            return sorted(flist, key=lambda f: f[0].lower())
        else:
            dname = self.sdcard_dirname
            try:
                filenames = os.listdir(self.sdcard_dirname)
                return [
                    (fname, os.path.getsize(os.path.join(dname, fname)))
                    for fname in sorted(filenames, key=str.lower)
                    if not fname.startswith(".")
                    and os.path.isfile((os.path.join(dname, fname)))
                ]
            except:
                logging.exception("virtual_sdcard get_file_list")
                raise self.gcode.error("Unable to get file list")

    def get_status(self, eventtime):
        return {
            "file_path": self.file_path(),
            "progress": self.progress(),
            "is_active": self.is_active(),
            "file_position": self.file_position,
            "file_size": self.file_size,
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
            filename = gcmd.get("FILE", None)
            if filename is None:
                raise gcmd.error("Missing FILENAME parameter")
        if filename[0] == "/":
            filename = filename[1:]
        self._load_file(gcmd, filename, check_subdirs=True)
        self.do_resume()

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
                    self.print_stats.set_current_file(filename)
                    self.printer.send_event("virtual_sdcard:load_file")
                    return
                except Exception as e:
                    logging.error(f"Error loading encrypted file: {str(e)}")
                    logging.debug(f"Not an encrypted file: {str(e)}")
                    # Continue with normal file loading
        except Exception as e:
            logging.debug(f"encrypted_gcode module not available: {str(e)}")
            # Continue with normal file loading
            
        # Standard file loading for plaintext G-code
        logging.info(f"Loading as plaintext G-code: {filename}")
        files = self.get_file_list(check_subdirs)
        flist = [f[0] for f in files]
        files_by_lower = {fname.lower(): fname for fname, fsize in files}
        fname = filename
        try:
            if fname not in flist:
                fname = files_by_lower[fname.lower()]
            fname = os.path.join(self.sdcard_dirname, fname)
            f = io.open(fname, "r", newline="")
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise gcmd.error("Unable to open file")
        gcmd.respond_raw("File opened: %s Size: %d" % (filename, fsize))
        gcmd.respond_raw("File selected")
        self.current_file = f
        self.file_position = 0
        self.file_size = fsize
        self.print_stats.set_current_file(filename)
        self.printer.send_event("virtual_sdcard:load_file")

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
        if is_custom_provider:
            logging.info("Using custom provider's get_gcode method (with batching)")
            try:
                gcode_generator = self.current_file.get_gcode()
            except Exception as e:
                logging.exception(f"Error getting gcode generator: {str(e)}")
                self.work_timer = None
                return self.reactor.NEVER
        
        BATCH_SIZE = 250  # Number of lines to buffer per batch for encrypted gcode
        
        def detect_and_update_layer(line):
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
                            self.print_stats.current_layer = current_layer
                            if hasattr(self.print_stats, 'info'):
                                self.print_stats.info['current_layer'] = current_layer
                            try:
                                gcode = self.printer.lookup_object('gcode')
                                gcode.run_script_from_command(f"SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer}")
                                gcode.run_script_from_command(f"M117 Layer {current_layer}/{getattr(self.print_stats, 'total_layer', 0)}")
                            except Exception as e:
                                logging.error(f"Error updating layer info: {str(e)}")
                except Exception as e:
                    logging.warning(f"Failed to parse layer change: {e}")

        while not self.must_pause_work:
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
                        break
                # Pause if any other request is pending in the gcode class
                if gcode_mutex.test():
                    self.reactor.pause(self.reactor.monotonic() + 0.100)
                    continue
                # Dispatch command
                self.cmd_from_sd = True
                line = lines.pop()
                logging.debug(f"Processing line from custom provider: {line}")
                detect_and_update_layer(line)
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
                        break
                    if not data:
                        # End of file
                        logging.info("End of file reached")
                        self.current_file.close()
                        self.current_file = None
                        logging.info("Finished SD card print")
                        self.gcode.respond_raw("Done printing file")
                        break
                    lines = data.split("\n")
                    lines[0] = partial_input + lines[0]
                    partial_input = lines.pop()
                    lines.reverse()
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
                detect_and_update_layer(line)
                if sys.version_info.major >= 3:
                    next_file_position = self.file_position + len(line.encode()) + 1
                else:
                    next_file_position = self.file_position + len(line) + 1
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