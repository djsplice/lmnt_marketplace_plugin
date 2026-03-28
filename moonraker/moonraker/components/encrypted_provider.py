import os
import logging

class EncryptedProvider:
    """
    A file-like provider for encrypted GCode files stored in memfd.
    This class wraps a memfd file descriptor to provide the necessary
    interface for Klipper's virtual_sdcard to read GCode lines.
    """
    def __init__(self, memfd_fd, metadata=None):
        """
        Initialize the provider with a memfd file descriptor.
        
        Args:
            memfd_fd (int): File descriptor of the memfd containing decrypted GCode.
            metadata (dict, optional): Metadata about the GCode file (e.g., total layers).
        """
        # Duplicate the memfd to ensure independent access
        self.memfd_fd = os.dup(memfd_fd)
        self.file_obj = os.fdopen(self.memfd_fd, 'r')
        self.metadata = metadata or {}
        self._calculate_size()
        logging.info(f"EncryptedProvider initialized with file size: {self.size}")
    
    def _calculate_size(self):
        """Determine the total size of the memfd content."""
        current_pos = self.file_obj.tell()
        self.file_obj.seek(0, 2)  # Seek to end
        self.size = self.file_obj.tell()
        self.file_obj.seek(current_pos)  # Restore position
    
    def readline(self):
        """Read a line from the memfd."""
        line = self.file_obj.readline()
        return line
    
    def seek(self, pos, whence=0):
        """Seek to a position in the memfd."""
        return self.file_obj.seek(pos, whence)
    
    def tell(self):
        """Get current position in the memfd."""
        return self.file_obj.tell()
    
    def get_file_size(self):
        """Return the total size of the file."""
        return self.size
    
    def close(self):
        """Close the memfd file object."""
        self.file_obj.close()
        logging.info("EncryptedProvider closed")
