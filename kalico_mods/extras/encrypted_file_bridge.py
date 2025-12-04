# Klipper extension to bridge memfd from Moonraker
import logging
import os

class EncryptedFileBridge:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.registered_files = {}
        self.metadata = {}  # Store metadata for each registered file
        self.gcode.register_command(
            'REGISTER_ENCRYPTED_FILE', self.cmd_REGISTER_ENCRYPTED_FILE,
            desc=self.cmd_REGISTER_ENCRYPTED_FILE_help)
        logging.info("EncryptedFileBridge initialized")

    cmd_REGISTER_ENCRYPTED_FILE_help = "Register an encrypted file from a memfd with metadata"
    def cmd_REGISTER_ENCRYPTED_FILE(self, gcmd):
        filename = gcmd.get('FILENAME')
        pid = gcmd.get_int('PID')
        fd = gcmd.get_int('FD')
        
        # Optional metadata parameters
        layer_count = gcmd.get_int('LAYER_COUNT', None)
        filament_total = gcmd.get_float('FILAMENT_TOTAL', None)
        estimated_time = gcmd.get_int('ESTIMATED_TIME', None)
        
        if not all([filename, pid, fd]):
            raise gcmd.error("REGISTER_ENCRYPTED_FILE requires FILENAME, PID, and FD")

        proc_path = f"/proc/{pid}/fd/{fd}"
        logging.info(f"[EncryptedFileBridge] Attempting to open memfd via: {proc_path}")
        
        try:
            # This is the key step: Klipper opens its own fd to the memfd
            klipper_fd = os.open(proc_path, os.O_RDONLY)
            # CRITICAL: Rewind the file descriptor to the beginning.
            os.lseek(klipper_fd, 0, os.SEEK_SET)
            logging.info(f"[EncryptedFileBridge] Successfully opened {proc_path}, new Klipper fd: {klipper_fd}")
        except Exception as e:
            logging.error(f"[EncryptedFileBridge] Failed to open memfd from path {proc_path}: {e}")
            raise gcmd.error(f"Failed to open memfd: {e}")

        # Clean up any old file object for the same filename to prevent leaks
        if filename in self.registered_files:
            old_file = self.registered_files.pop(filename)
            try:
                old_file.close()
                logging.info(f"[EncryptedFileBridge] Closed stale file object for {filename}")
            except Exception as e:
                logging.warning(f"[EncryptedFileBridge] Could not close stale file object for {filename}: {e}")

        # Wrap the fd in a file object, as expected by virtual_sdcard
        klipper_file_obj = os.fdopen(klipper_fd, 'r')
        self.registered_files[filename] = klipper_file_obj
        
        # Store metadata for this file
        file_metadata = {}
        if layer_count is not None:
            file_metadata['total_layers'] = layer_count
            file_metadata['layer_count'] = layer_count  # Alternative key
        if filament_total is not None:
            file_metadata['filament_total'] = filament_total
        if estimated_time is not None:
            file_metadata['estimated_time'] = estimated_time
        
        self.metadata[filename] = file_metadata
        logging.info(f"[EncryptedFileBridge] Stored metadata for {filename}: {file_metadata}")
        
        gcmd.respond_info(f"Registered encrypted file '{filename}' with fd {klipper_fd} and metadata: {file_metadata}")

    def get_file_handle(self, filename):
        if filename not in self.registered_files:
            return None
        
        # Pop the file object to consume it. virtual_sdcard will be responsible for closing it.
        klipper_file_obj = self.registered_files.pop(filename)
        logging.info(f"[EncryptedFileBridge] Providing file handle for '{filename}'")
        return klipper_file_obj
    
    def get_file_metadata(self, filename):
        """Get metadata for a registered file."""
        return self.metadata.get(filename, {})

    def handle_shutdown(self):
        # Clean up all registered file descriptors on shutdown
        logging.info("[EncryptedFileBridge] Shutting down, closing all open fds.")
        for filename, fd in self.registered_files.items():
            try:
                os.close(fd)
                logging.info(f"[EncryptedFileBridge] Closed fd {fd} for {filename}")
            except Exception as e:
                logging.warning(f"[EncryptedFileBridge] Could not close fd {fd} on shutdown: {e}")
        self.registered_files.clear()

def load_config(config):
    return EncryptedFileBridge(config)
