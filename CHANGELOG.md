# Changelog

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
- Initial release of encrypted G-code plugin system
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
