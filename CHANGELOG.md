# Changelog

## [1.1.3] - 2025-12-04

### Fixed
- **Stats Reporting**: Fixed a bug where collected stats were not being included in the API payload, resulting in missing data on the server.

## [1.1.2] - 2025-12-04

### Fixed
- **Stats Collection**: Fixed missing stats collection when print job transitions directly from `printing` to `idle` (skipping `complete` state).

## [1.1.1] - 2025-12-04

### Changed
- **Update Manager**: Added `info_tags` to `install.sh` to display release notes and channel information in Mainsail/Fluidd.

## [1.1.0] - 2025-12-04

### Added
- **Print Stats Collection**: Now collects `filament_used`, `print_duration`, and `total_duration` for detailed analytics.
- **Enhanced Reporting**: Sends detailed print statistics to the marketplace API upon job completion.

### Fixed
- **Plugin Stability**: Fixed a crash in the print monitoring loop (`NameError`) that caused jobs to get stuck in "Processing".
- **Token Synchronization**: Improved reliability of printer authentication and token refresh.

## [1.0.0] - 2025-12-03

### Initial Release
- **Secure Printing**: End-to-end encrypted G-code printing with on-device decryption.
- **Marketplace Integration**: Seamless pairing and job management with the LMNT Marketplace.
- **Native Klipper Support**: Works with standard Klipper web interfaces (Mainsail, Fluidd) and uses native print stats.
- **Simplified Configuration**: Easy setup with automatic update manager integration.
