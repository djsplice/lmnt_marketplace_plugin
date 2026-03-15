# Changelog

## [1.2.2] - 2026-03-15
### Fixed
- **Printer Readiness:** Restored readiness checks using Moonraker `query_objects()` with legacy wrapper fallback to avoid false "printer not ready" on Kalico.

## [1.2.1] - 2026-03-15
### Fixed
- **Print Monitoring Compatibility:** Align `query_objects()` usage with Moonraker's canonical signature and add a legacy wrapper fallback to prevent “Invalid argument” errors on custom firmwares.
- **Mainline Klipper Support:** Added a fallback in `secure_print` to start prints when `virtual_sdcard` lacks Kalico's `gcode_provider` APIs.
- **Token Consistency:** Ensure printer ID is saved before writing `printer_token.json` and warn when token/ID mismatches are detected.

## [1.2.0] - 2026-03-13
### Added
- **Custom Firmware Support:** Full compatibility with non-standard Klipper environments like the Snapmaker U1 and `SnapmakerU1-Extended-Firmware`.
- **SysVinit Support:** `install.sh`, `update.sh`, `uninstall.sh`, and `switch_env.sh` now automatically detect `SysVinit` service managers and fallback from `systemctl` cleanly.
- **Isolated Virtual Environments:** The installer now creates an isolated Python `.venv` exclusively for the plugin inside its repository folder.
- **Permission Fallbacks:** Maintenance scripts gracefully prompt the user for manual root intervention if `sudo` is unavailable instead of crashing mid-execution.

### Fixed
- **ModuleNotFoundError:** Dynamically injects `sys.path` via `os.path.realpath(__file__)` during Moonraker bootstrapping to guarantee dependencies (`PyJWT`, `PyNaCl`) are resolvable even when Moonraker is running on the system python interpreter.

## [1.1.22] - 2026-01-11
- fix(critical): Add missing `http_client` attribute initialization in `LmntMarketplaceIntegration.__init__` to prevent `AttributeError` on startup.

## [1.1.21] - 2026-01-11
- fix(reliability): Ensure HTTP sessions are gracefully closed during re-initialization (e.g., after firmware restart) to prevent resource leaks.
- fix(reliability): Hardened component discovery in `encrypted_print.py` with retries and broad scanning to resolve intermittent lookup failures.

## [1.1.20] - 2026-01-11
- fix(reliability): Improve component lookup in `encrypted_print.py` to handle both `lmnt_marketplace_plugin` and `lmnt_marketplace` names, resolving intermittent "Component not found" errors after firmware restarts.

## [1.1.19] - 2026-01-11
- fix(reliability): Implement missing Klippy shutdown handlers to prevent `AttributeError` crash.
- fix(api): Correctly register plugin-specific endpoints (e.g., `/lmnt/job_status`) to resolve 404 errors.

## [1.1.18] - 2026-01-08
### Fixed
- **Polling Reliability**: Added 30-minute total timeout to Firebase connection to force periodic refresh and prevent "zombie" states.
- **Connection Diagnostics**: Added throttled logging of heartbeats to confirm connection health.
- **Timeout Logic**: Reduced read timeout to 60s (was 120s) to detect dropped connections faster.

## [1.1.17] - 2026-01-05
### Fixed
- **Connection Reliability**: Added read timeout (120s) to Firebase listener to prevent silent "zombie" connections that don't recover.
- **Poll Logic**: Fixed rate limiting logic to wait/sleep instead of dropping poll requests when triggered too quickly by multiple signals.

## [1.1.16] - 2025-12-28
### Fixed
- **OrcaSlicer Metadata**: Fixed regression where layer count and estimated time were not being correctly parsed from OrcaSlicer generated GCode files.
- **Metadata Parsing**: Refactored metadata extraction to scan both header and footer of GCode files for improved reliability.

## [1.1.15] - 2025-12-26
### Fixed
- **Print Progress**: Prioritize `virtual_sdcard` byte-based progress reporting (matches Mainsail/Flask) over inaccurate time-based estimates.

## [1.1.14] - 2025-12-26
### Fixed
- **Stability**: Hardened Firebase listener loop with auto-restart to prevent disconnection/crashes.
- **Error Handling**: Improved resiliency against backend polling errors (e.g. 500 status).

## [1.1.6] - 2025-12-04
### Fixed
- **CRITICAL**: Fixed API version mismatch causing 401 Unauthorized errors
- Reverted `api_version` to `1.0.0` to match backend API endpoint
- Added comment clarifying `api_version` is backend API version, not plugin version

## [1.1.5] - 2025-12-04
### Fixed
- **CRITICAL**: Fixed install script to symlink directly to repo instead of copying files
- This ensures updates via Moonraker's update manager are immediately active
- Removed unnecessary intermediate copy step that prevented updates from taking effect

## [1.1.4] - 2025-12-04
### Added
- Enhanced logging for print stats collection and transmission
- Detailed payload logging for debugging

### Fixed
- Removed duplicate stats update in payload construction
- Improved stats flow visibility for troubleshooting

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
