# Encrypted G-code Plugin Refactoring TODO

This document outlines recommendations for future refactoring of the encrypted G-code plugin system to improve architecture, reduce redundancy, and clarify component responsibilities.

## Component Responsibility Boundaries

### 1. Clarify Component Roles

- [x] **`encrypted_print.py`**: Focus solely on decryption and providing file-like access via `memfd`.
  - [x] Handles encrypted file download, decryption, and in-memory streaming.
  - [x] Does not duplicate print operation logic.

- [x] **`virtual_sdcard.py`**: Handles all print operation logic, using the file descriptor provided by the `encrypted_file_bridge.py` Klipper extension.
  - [x] Works consistently for both standard and encrypted files (via the bridge).
  - [x] Remains the single component responsible for print flow control.

- [x] **`print_stats.py`**: Remains the single aggregator of print statistics, driven by Klipper's internal state.

### 2. Standardize Data Flow

- [x] Established clear update paths for all statistics by relying on Klipper as the single source of truth.
- [x] Eliminated all duplicate/manual updates to `print_stats` from the plugin components.

## Architectural Improvements

### 1. File Handler Interface

- [x] The `memfd` and `os.dup()` approach, combined with the `encrypted_file_bridge.py` Klipper extension, serves as a highly effective, low-level file handler interface that integrates directly with Klipper's existing `virtual_sdcard`.

### 2. Metadata Extraction Standardization

- [ ] Future work could standardize how metadata (thumbnails, etc.) is extracted and reported, but the core printing flow is now robust.

### 3. Event-Based Communication

- [x] The system now correctly uses Klipper's state changes (e.g., `printing` -> `standby`) as the primary events for determining job completion, removing fragile polling logic.

## Security Considerations

- [x] Review decryption process to ensure minimal exposure:
  - [x] Decryption happens on-the-fly into an in-memory file (`memfd`).
  - [x] Decrypted content is never written to disk.
  - [x] Secure memory handling is used via the `memfd` mechanism.

- [x] Audit logging to ensure no sensitive data is exposed:
  - [x] Removed verbose logging of sensitive data.

## Testing and Documentation

- [x] Extensive manual testing has been performed on encrypted files, including edge cases like Klippy restarts.
- [x] Documentation has been thoroughly updated to reflect the new, robust architecture:
  - [x] `README.md` updated with the correct data flow.
  - [x] `installation.md` simplified and corrected.
  - [x] `CHANGELOG.md` updated with the latest fixes.
