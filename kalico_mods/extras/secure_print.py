# klippy/extras/secure_print.py
import os
import logging
import io

class SecurePrintGCodeProvider:
    """Custom G-code provider for in-memory file descriptors"""
    def __init__(self, file_handle, filename, file_size, metadata=None):
        self.printer = None  # Set by VirtualSD
        self.reactor = None  # Set by VirtualSD
        self.file_handle = file_handle
        self.filename = filename
        self.file_size = file_size
        self.file_position = 0
        self.next_file_position = 0
        self.metadata = metadata or {}

    def handle_shutdown(self):
        if self.file_handle is not None:
            try:
                readpos = max(self.file_position - 1024, 0)
                readcount = self.file_position - readpos
                self.file_handle.seek(readpos)
                data = self.file_handle.read(readcount + 128)
            except:
                logging.exception("secure_print shutdown read")
                return
            logging.info(
                "Secure print (%d): %s\nUpcoming (%d): %s",
                readpos, repr(data[:readcount]), self.file_position, repr(data[readcount:])
            )

    def get_stats(self, eventtime):
        return True, "secure_sd_pos=%d" % (self.file_position,)

    def get_status(self, eventtime):
        return {
            "file_path": self.filename,
            "progress": float(self.file_position) / self.file_size if self.file_size else 0.0,
            "file_position": self.file_position,
            "file_size": self.file_size,
        }

    def read(self, size):
        """Proxy read to the underlying file handle."""
        if self.file_handle is None:
            return b''
        return self.file_handle.read(size)

    def is_file_complete(self):
        """Check if printing is complete based on tracked position."""
        if self.file_size == 0:
            return True
        return self.file_position >= self.file_size

    def is_active(self):
        return self.file_handle is not None

    def get_name(self):
        return self.filename

    def reset(self):
        if self.file_handle is not None:
            self.file_handle.close()
            self.file_handle = None
            self.filename = ""
            self.file_position = self.file_size = 0
            self.next_file_position = 0

    def get_gcode(self):
        logging.info("Starting secure SD card print (position %d)", self.file_position)
        try:
            self.file_handle.seek(self.file_position)
        except:
            logging.exception("secure_print seek")
            return
        partial_input = ""
        lines = []
        while True:
            if not lines:
                try:
                    data = self.file_handle.read(8192)
                except:
                    logging.exception("secure_print read")
                    break
                if not data:
                    self.file_handle.close()
                    self.file_handle = None
                    logging.info("Finished secure SD card print")
                    break
                lines = data.split("\n")
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                yield ""
                continue
            line = lines.pop()
            yield line
            if self.next_file_position != self.file_position:
                self.file_position = self.next_file_position
                try:
                    self.file_handle.seek(self.file_position)
                    lines = []
                    partial_input = ""
                except:
                    logging.exception("secure_print seek")
                    return
            else:
                try:
                    new_pos = self.file_handle.tell()
                    self.file_position = new_pos
                    self.next_file_position = new_pos
                except:
                    logging.warning("secure_print: could not update position via tell()")
                    pass
        logging.info("Exiting secure SD card print (position %d)", self.file_position)

    def get_file_position(self):
        return self.next_file_position

    def set_file_position(self, pos):
        self.next_file_position = pos

class SecurePrint:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.virtual_sd = None
        self.encrypted_file_bridge = None
        self.print_stats = None
        self.printer.register_event_handler("klippy:connect", self.handle_connect)

        # Register custom G-code command
        self.gcode.register_command('SET_GCODE_FD', self.cmd_SET_GCODE_FD, desc="Set in-memory G-code file descriptor")

    def handle_connect(self):
        self.virtual_sd = self.printer.lookup_object('virtual_sdcard', None)
        self.encrypted_file_bridge = self.printer.lookup_object('encrypted_file_bridge', None)
        self.print_stats = self.printer.lookup_object('print_stats', None)

    def cmd_SET_GCODE_FD(self, gcmd):
        """Handle SET_GCODE_FD to set and start printing from a file descriptor"""
        if not self.virtual_sd:
            raise gcmd.error("virtual_sdcard not available")
        if not self.encrypted_file_bridge:
            raise gcmd.error("Encrypted file bridge not available")

        filename = gcmd.get('FILENAME', default='encrypted_gcode')
        file_handle = self.encrypted_file_bridge.get_file_handle(filename)
        if not file_handle:
            raise gcmd.error("No file handle provided")

        try:
            # Get file size and reset position
            file_handle.seek(0, os.SEEK_END)
            file_size = file_handle.tell()
            file_handle.seek(0)

            # Get metadata from encrypted_file_bridge for this specific file
            metadata = self.encrypted_file_bridge.get_file_metadata(filename) if self.encrypted_file_bridge else {}
            logging.info(f"[SecurePrint] Retrieved metadata for {filename}: {metadata}")

            # Create provider for in-memory file
            provider = SecurePrintGCodeProvider(file_handle, filename, file_size, metadata)
            provider.printer = self.printer
            provider.reactor = self.printer.get_reactor()

            # Update print_stats
            self.print_stats.set_current_file(filename)
            self.print_stats.file_size = file_size
            if metadata.get('total_layers'):
                self.print_stats.total_layer = metadata['total_layers']
                self.gcode.run_script_from_command(f"SET_PRINT_STATS_INFO TOTAL_LAYER={metadata['total_layers']}")
            if metadata.get('filament_total'):
                self.print_stats.filament_total = metadata['filament_total']
                self.gcode.run_script_from_command(f"SET_PRINT_STATS_INFO FILAMENT_TOTAL={metadata['filament_total']}")

            gcmd.respond_raw(f"File opened: {filename} Size: {file_size}")
            gcmd.respond_raw("File selected")

            # Delegate to virtual_sdcard for printing
            self.virtual_sd.print_with_gcode_provider(provider)
        except Exception as e:
            logging.exception("SET_GCODE_FD: Error setting file handle")
            raise gcmd.error(f"Failed to set G-code FD: {str(e)}")

def load_config(config):
    return SecurePrint(config)