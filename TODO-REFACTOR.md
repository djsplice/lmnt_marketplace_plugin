# Encrypted G-code Plugin Refactoring TODO

This document outlines recommendations for future refactoring of the encrypted G-code plugin system to improve architecture, reduce redundancy, and clarify component responsibilities.

## Component Responsibility Boundaries

### 1. Clarify Component Roles

- [ ] **encrypted_gcode.py**: Focus solely on decryption and providing file-like access
  - Should handle encrypted file detection, decryption, and streaming
  - Should not duplicate print operation logic from virtual_sdcard.py
  - Consider implementing a standard file handler interface

- [ ] **virtual_sdcard.py**: Handle all print operation logic
  - Should work consistently for both encrypted and plaintext files
  - Should delegate file access to appropriate handler based on file type
  - Should be the single component responsible for print flow control

- [ ] **print_stats.py**: Single aggregator of print statistics
  - Should receive updates from a consistent source
  - Should be the only component that tracks print state, duration, etc.
  - Should expose a clear API for updating statistics

### 2. Standardize Data Flow

- [ ] Establish clear update paths for all statistics:
  - [ ] Layer information
  - [ ] File position/progress
  - [ ] Filament usage
  - [ ] Print duration
  - [ ] Print state changes

- [ ] Document which component is the "source of truth" for each data point
  - [ ] Create a data ownership matrix
  - [ ] Ensure all components respect these ownership boundaries

- [ ] Eliminate any remaining duplicate updates
  - [ ] Audit all calls to print_stats methods
  - [ ] Ensure only the responsible component updates each data point

## Architectural Improvements

### 1. Consider File Handler Interface

- [ ] Create a common interface for file handlers:
  ```python
  class GCodeFileHandler:
      def open(self, filename): pass
      def read(self, size): pass
      def seek(self, position): pass
      def close(self): pass
      def get_file_position(self): pass
      def get_file_size(self): pass
  ```

- [ ] Implement for both plaintext and encrypted files:
  - [ ] PlaintextGCodeHandler
  - [ ] EncryptedGCodeHandler

- [ ] Modify virtual_sdcard.py to use the appropriate handler

### 2. Metadata Extraction Standardization

- [ ] Standardize how metadata is extracted from files:
  - [ ] Layer information
  - [ ] Thumbnails
  - [ ] Print estimates
  - [ ] Filament usage

- [ ] Consider a common metadata extraction utility used by both file handlers

### 3. Event-Based Communication

- [ ] Use Klipper's event system more consistently:
  - [ ] Define clear events for layer changes, progress updates, etc.
  - [ ] Ensure components listen for relevant events rather than polling

## Security Considerations

- [ ] Review decryption process to ensure minimal exposure:
  - [ ] Verify decryption happens in small chunks
  - [ ] Ensure decrypted content is not cached unnecessarily
  - [ ] Add secure memory handling where possible

- [ ] Audit logging to ensure no sensitive data is exposed:
  - [ ] Remove any logging of decrypted content
  - [ ] Ensure keys are not logged

## Testing and Documentation

- [ ] Create test cases for both encrypted and plaintext files:
  - [ ] Verify identical behavior for both file types
  - [ ] Test edge cases (large files, corrupt files, etc.)

- [ ] Update documentation to reflect the refined architecture:
  - [ ] Component responsibilities
  - [ ] Data flow diagrams
  - [ ] Security considerations

## Implementation Strategy

1. Start with defining clear interfaces and responsibility boundaries
2. Implement changes to encrypted_gcode.py first
3. Modify virtual_sdcard.py to use the new interfaces
4. Update print_stats.py as needed
5. Test thoroughly with both file types
6. Update documentation

---

This refactoring will further improve the architecture of the encrypted G-code plugin system, reducing redundancy and potential conflicts while maintaining all functionality and security features.
