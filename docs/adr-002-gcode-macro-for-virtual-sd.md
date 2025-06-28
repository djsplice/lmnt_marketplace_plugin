# ADR-002: G-Code Macro for Virtual SD Card Command Interception

**Date**: 2025-06-26

**Status**: Accepted

## Context

The plugin requires a mechanism to intercept print jobs intended for Klipper's `virtual_sdcard` module. Specifically, it must differentiate between standard G-code files and encrypted files (prefixed with `virtual_`) passed from Moonraker. The initial approaches involved modifying Klipper's `virtual_sdcard.py` directly or attempting to monkey-patch its G-code command handlers at runtime.

These methods proved to be highly unreliable due to the complexities of Klipper's startup sequence and internal object initialization. Patches were frequently overwritten or applied incorrectly, leading to persistent `KeyError` exceptions and failed prints. The primary goal was to achieve this interception without modifying Klipper's core source files, ensuring stability and easy future updates.

## Decision

We decided to abandon all Python-level modifications and runtime patching in favor of a pure, Klipper-native G-code macro solution defined in `printer.cfg`.

This approach leverages Klipper's built-in G-code processing features:

1.  **`[gcode_macro SDCARD_PRINT_FILE]`**: A new macro is created that replaces the default `SDCARD_PRINT_FILE` command.
2.  **`rename_existing: BASE_SDCARD_PRINT_FILE`**: Inside the macro definition, the original command is renamed to `BASE_SDCARD_PRINT_FILE`. This is a critical feature that prevents registration conflicts and preserves access to the original command.
3.  **Conditional Logic**: The macro uses Jinja2 templating to inspect the `FILENAME` parameter. 
    - If the filename starts with `virtual_`, it calls the plugin's `SET_GCODE_FD` command to handle the encrypted print.
    - Otherwise, it passes the command and all its parameters (`{rawparams}`) to the renamed `BASE_SDCARD_PRINT_FILE` command, ensuring normal behavior for standard files.

## Consequences

### Positive

-   **Zero Core File Modifications**: `virtual_sdcard.py` was reverted to its 100% stock version. This completely eliminates the risk of conflicts with future Klipper updates.
-   **Enhanced Stability**: The solution is exceptionally robust, as it uses Klipper's intended configuration and command processing system, avoiding the unpredictable nature of runtime patching.
-   **Improved Maintainability**: The logic for command interception is now cleanly and transparently defined in the user's `printer.cfg`, where it is easy to find and understand.
-   **Complete Functionality**: The system now flawlessly handles both encrypted and standard G-code print jobs.

### Negative

-   Requires a specific configuration to be present in the user's `printer.cfg`. However, this is standard practice for Klipper plugins and is easily managed during installation.
