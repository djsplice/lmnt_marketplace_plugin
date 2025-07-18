# Changelog

## [1.2.0] - 2025-07-16

### Fixed
- **Layer Progress Tracking**: Resolved critical issue where layer progress was not updating in Mainsail/Fluidd during encrypted prints. Root cause was missing `SET_PRINT_STATS_INFO` commands in OrcaSlicer machine profiles.
- **Native Klipper Integration**: Ensured encrypted prints now use Klipper's native print stats system instead of custom notifications, providing seamless UI integration.
- **Slicer Profile Updates**: Updated OrcaSlicer machine profiles to include proper `SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]` in start GCode and `SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}` in layer change GCode.
- **Just-in-Time Key Loading**: Fixed critical issue where encrypted prints would fail immediately after marketplace registration if Moonraker hadn't been restarted. Added automatic private key loading via AuthManager when needed, eliminating the need for manual plugin restarts.

### Improved
- **Print Status Monitoring**: Enhanced print status monitoring and reporting workflow documentation to reflect current native Klipper/Moonraker integration approach.
- **Code Cleanup**: Removed temporary layer progress injection workaround from encrypted print streaming, simplifying the codebase and relying on proper slicer profile configuration.
- **Debug Logging**: Cleaned up verbose debug logging that was used during troubleshooting, resulting in cleaner log output while maintaining essential monitoring information.

### Technical Details
- **Variable Syntax**: Confirmed proper OrcaSlicer variable syntax using `[total_layer_count]` (square brackets) for start GCode and `{layer_num + 1}` (curly braces) for layer change GCode.
- **Universal Benefit**: These slicer profile improvements benefit ALL prints (encrypted and normal), not just encrypted ones.
- **Production Ready**: Layer progress tracking system is now production-ready with native Klipper integration and clean logging.

## [1.1.0] - 2025-06-25
Git tag v0.2

### Added
- **Major Improvement**: Re-architected the encrypted print job interception logic to use a Klipper G-code macro instead of modifying or patching `virtual_sdcard.py`. This new method is 100% non-invasive, ensuring Klipper's core files remain stock. It provides a more stable, maintainable, and robust solution for both encrypted and clear-text printing.

### Fixed
- **Print Monitoring Stability**: Resolved a critical race condition where the print monitor would fail if Klippy was not immediately ready on startup. The monitor now reliably uses `klippy_apis` to query print status, removing the fragile dependency on the `printer` component.
- **Redundant Polling Loops**: Eliminated a `RuntimeError` caused by multiple, concurrent job polling loops. The polling mechanism is now initialized only once, ensuring stable background operation.
- **Stuck Print Jobs**: Corrected an issue where print jobs would remain in the "printing" state indefinitely if Klippy was restarted mid-print. The monitor now intelligently detects the state transition from `printing` to `standby` as a successful job completion.

### Improved
- **System Resilience**: The overall stability of the print job lifecycle has been significantly hardened against common operational issues like service restarts.
- **Code Simplification**: Refactored component interactions to be cleaner and more robust, reducing complexity and potential points of failure.

## [1.0.5] - 2025-06-12 
Git tag v0.1

### Fixed
- **G-code Streaming**: Resolved the 'Unknown command:"STREAM_GCODE_LINE"' error by sending raw G-code lines directly to Klipper, enabling successful printing.
- **Print Status Reporting**: Fixed a bug where print jobs were incorrectly marked as 'failure'. Status now correctly transitions from 'processing' to 'printing' and 'success'.
- **On-Printer Decryption**: Corrected a file-write error (`TypeError: a bytes-like object is required, not 'str'`) by ensuring the decrypted G-code was written to a temporary file in binary mode (`'wb'`).

### Changed
- **End-to-End Workflow**: The secure print workflow is now fully operational. The plugin can successfully poll for a job, download the encrypted G-code, decrypt it on the printer, and stream it to Klipper for printing.

## [1.0.4] - 2025-06-09
### Added
- Advanced component tests for comprehensive local testing of GCode and Job managers
- Extension methods for GCodeManager to support memory-efficient GCode streaming, metadata extraction, and thumbnail handling
- Extension methods for JobManager to support job queue management and status updates
- Dynamic test extension system for patching manager instances with test-specific methods
- Memory usage monitoring in tests to ensure efficient GCode processing

### Improved
- Enhanced error handling in GCode processing with proper exception propagation
- Implemented secure in-memory decryption without writing decrypted content to disk
- Added test mocks for crypto_manager and klippy_apis to enable local testing without external dependencies

## [1.0.3] - 2025-06-08
### Added
- LMNT Marketplace integration for secure printer token management
- Dedicated `/api/refresh-printer-token` endpoint for secure printer token refresh
- Printer-specific encryption key (PSEK) handling for secure G-code decryption
- Standalone test script (`test_marketplace_integration.py`) for testing marketplace integration
- Integration with Custodial Wallet Service (CWS) for key management

### Fixed
- Resolved indentation and syntax errors in the marketplace integration test script
- Improved error handling in HTTP requests with timeouts to prevent hanging
- Enhanced token refresh logic with better error handling

### Improved
- Added detailed logging for marketplace API interactions
- Implemented automatic token refresh mechanism with 30-day expiration
- Added JWT middleware for printer token validation
- Implemented command-line argument support for flexible testing scenarios
- Added simulation of GCode encryption/decryption using decrypted PSEKs

## [1.0.2] - 2025-04-22
### Improved
- Eliminated duplicate layer updates between Moonraker and Klipper for more reliable layer tracking
- Established Klipper as the single source of truth for layer information
- Reduced race conditions in print statistics tracking
- Maintained LCD display functionality while simplifying the update flow

### Fixed
- Resolved potential conflicts in layer tracking between Moonraker and Klipper components

## [1.0.1] - 2025-04-18
### Improved
- Enhanced `virtual_sdcard.py` to transparently support both encrypted and plaintext G-code files.
- Automatic detection and seamless switching between encrypted and regular G-code files.
- Improved logging and error messages for encrypted file handling and fallback scenarios.

### Fixed
- More robust error handling when encrypted file loading fails, with fallback to plaintext mode.

## [1.0.0] - 2025-04-16
Fully functional end-to-end, including layer count updates.

### Added

- Moonraker extension (hedera_slicer.py) for handling encrypted G-code files
- Modified Klipper components for secure G-code processing:
  - Enhanced virtual_sdcard.py for encrypted file operations
  - Updated print_stats.py for accurate print statistics
- Web API endpoint (/machine/hedera_slicer/slice_and_print) for slice-and-print operations
- Secure G-code streaming implementation with Fernet encryption
- Integration with Klipper's native print process
- Real-time print status and statistics tracking
- Documentation for installation and configuration

### Changed
- Removed deprecated hedera_decrypt.py Klipper extension
- Updated virtual_sdcard.py to handle encrypted files natively
- Improved print status tracking in print_stats.py

### Security
- Implemented secure G-code file handling with Fernet encryption
- Added secure cleanup of encrypted files after print completion
