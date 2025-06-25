# ADR-001: Secure File Handoff for Encrypted Printing

**Date**: 2025-06-24

**Status**: Accepted

## Context

The core requirement for the encrypted printing feature is to print a G-code file that has been decrypted in memory, without ever writing the cleartext G-code to disk. This presents a challenge, as the printing process is managed by Klipper, while the decryption and job management are handled by Moonraker—two separate processes.

A mechanism is needed to securely and efficiently pass the decrypted G-code data from the Moonraker process to the Klipper process for printing.

## Decision

We decided to use a combination of Linux `memfd` (in-memory file descriptors) and a custom Klipper extension (`EncryptedFileBridge`) to facilitate the handoff. This approach leverages the `/proc` filesystem to allow one process to access a file descriptor belonging to another, creating a secure and direct pipe for the data.

The process is as follows:

1.  **Decryption in Moonraker**: The `encrypted_print` component in Moonraker decrypts the G-code into an in-memory file created with `memfd_create()`. This returns a file descriptor (an integer), which is a handle to the in-memory data.

2.  **G-code Command Registration**: Moonraker gets its own Process ID (`os.getpid()`) and sends a custom G-code command to Klipper:
    `REGISTER_ENCRYPTED_FILE FILENAME="..." PID=<moonraker_pid> FD=<memfd_number>`

3.  **File Handoff in Klipper**: Klipper's `EncryptedFileBridge` extension receives this command.
    - It constructs a path into the `/proc` filesystem (e.g., `/proc/12345/fd/67`). This special path is a direct link to the in-memory file that is still open in the Moonraker process.
    - Klipper calls `os.open()` on this path. The Linux kernel grants Klipper its *own* file descriptor that points to the exact same underlying in-memory data.

4.  **Critical Post-Processing**: Immediately after acquiring its file descriptor, the `EncryptedFileBridge` performs two critical actions:
    - It **rewinds the file pointer** to the beginning using `os.lseek(fd, 0, os.SEEK_SET)`. This is necessary because the decryption process in Moonraker leaves the pointer at the end of the file.
    - It **wraps the raw file descriptor** in a proper Python file object using `os.fdopen(fd, 'r')`. This is the most critical step, as the rest of Klipper's print system (`virtual_sdcard`) expects a file-like object with methods like `.read()` and `.seek()`, not a raw integer.

5.  **Printing**: The `virtual_sdcard` component receives the `SDCARD_PRINT_FILE` command. It requests the file handle from the `EncryptedFileBridge`, receives the file object, and streams the G-code from it as if it were a normal file on disk. The file object is consumed on use and closed by `virtual_sdcard` when the print is complete.

## Consequences

**Positive**:
-   **High Security**: Cleartext G-code is never written to persistent storage, fulfilling the primary security requirement.
-   **Efficiency**: The handoff is a direct memory-to-memory transfer managed by the kernel, with no unnecessary data copies.
-   **Robustness**: Leverages standard, well-understood Linux kernel features (`/proc` filesystem) for inter-process communication.

**Negative**:
-   **Complexity**: The mechanism is more complex than a simple file-read operation and requires careful management of file descriptors and process state.
-   **Platform Dependency**: This solution is specific to Linux-based systems that support the `/proc` filesystem and `memfd`.
-   **Custom Extension**: Requires a custom Klipper extension, which adds to the maintenance overhead.
