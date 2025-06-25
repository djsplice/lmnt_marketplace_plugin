import io
import re
import json
import time
import asyncio
import base64
import os
import sys
import logging
from aiohttp import web
from typing import Optional
from moonraker.common import UserInfo
from moonraker.utils.exceptions import ServerError
import traceback

# Ensure the current directory is in the Python path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Import our custom EncryptedProvider
try:
    from encrypted_provider import EncryptedProvider
except ImportError:
    logging.warning("[EncryptedPrint] Could not import EncryptedProvider, falling back to direct streaming")
    EncryptedProvider = None

def load_component(config):
    return EncryptedPrint(config)

class EncryptedPrint:
    def __init__(self, config):
        self.server = config.get_server()
        self.config = config
        self.lmnt_integration = None
        self.klippy_apis = None
        self.database = None
        self.file_manager = None
        self.moonraker_pid = os.getpid()
        self.active_memfds = {}

        # Component lookups are deferred to _handle_klippy_ready to avoid load order issues
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready)

        # Register handler to clean up memfds on job completion
        self.server.register_event_handler(
            "job_state:job_state_changed", self._handle_job_state_change)
        
        # Register our endpoint
        self.server.register_endpoint(
            "/server/encrypted/print",
            ["POST"],
            self.handle_encrypted_print,
            transports=["http"]
        )
        logging.info("[EncryptedPrint] Registered /server/encrypted/print endpoint")

    async def _handle_klippy_ready(self):
        """Called when Klippy is connected and ready"""
        logging.info("[EncryptedPrint] Klippy ready, looking up components...")
        try:
            self.klippy_apis = self.server.lookup_component("klippy_apis")
            self.database = self.server.lookup_component("database")
            self.file_manager = self.server.lookup_component("file_manager")
            lmnt_component = self.server.lookup_component("lmnt_marketplace_plugin")
            self.lmnt_integration = lmnt_component.integration
            logging.info("[EncryptedPrint] All components successfully initialized.")
        except Exception as e:
            logging.exception(
                "[EncryptedPrint] Failed to initialize components after Klippy ready")

        return False  # Return False to unregister this handler after it runs

    async def handle_encrypted_print(self, web_request):
        try:
            # Check if components are initialized, wait if necessary to resolve startup race condition
            if not all([self.klippy_apis, self.database, self.lmnt_integration, self.file_manager]):
                logging.warning("[EncryptedPrint] Components not ready, waiting 1s for server to initialize...")
                await asyncio.sleep(1)
                if not all([self.klippy_apis, self.database, self.lmnt_integration, self.file_manager]):
                    logging.error("[EncryptedPrint] Component still not ready after delay, aborting print request.")
                    raise ServerError("EncryptedPrint component not fully initialized", 503)

            # Use the correct WebRequest API method to get all arguments
            data = web_request.get_args()
            logging.info(f"[EncryptedPrint] Successfully extracted request data using web_request.get_args()")
            
            # Try to get the LMNT integration if not already available
            if not self.lmnt_integration:
                try:
                    lmnt_component = self.server.lookup_component("lmnt_marketplace_plugin")
                    # The actual integration object is stored in the integration attribute
                    self.lmnt_integration = lmnt_component.integration
                    logging.info("[EncryptedPrint] Successfully found LMNT Marketplace Plugin during request")
                except ServerError:
                    # Debug: List available components
                    available_components = list(self.server.components.keys()) if hasattr(self.server, 'components') else []
                    logging.error(f"[EncryptedPrint] LMNT Marketplace Plugin not found. Available components: {available_components}")
                    raise ServerError("LMNT Marketplace Plugin not available", 503)
            
            job_id = data.get("job_id")
            encrypted_gcode = base64.b64decode(data.get("encrypted_gcode"))
            gcode_dek_package = data.get("gcode_dek_package")
            gcode_iv_hex = data.get("gcode_iv_hex")
            filename = data.get("filename", f"encrypted_{job_id}.gcode")
            if not all([job_id, encrypted_gcode, gcode_dek_package, gcode_iv_hex]):
                raise ServerError("Missing required parameters", 400)
            
            logging.info(f"[EncryptedPrint] Received encrypted print job {job_id}, decrypting in memory")
            
            # Decrypt in memory without any filesystem operations
            if not hasattr(self.lmnt_integration, 'crypto_manager') or not self.lmnt_integration.crypto_manager:
                # Debug: Show available attributes
                available_attrs = [attr for attr in dir(self.lmnt_integration) if not attr.startswith('_')]
                logging.error(f"[EncryptedPrint] Crypto manager not found. Available attributes: {available_attrs}")
                raise ServerError("Crypto manager not available", 503)
            
            crypto_manager = self.lmnt_integration.crypto_manager
            
            gcode_dek_bytes = await crypto_manager.decrypt_dek(gcode_dek_package)
            if not gcode_dek_bytes:
                raise ServerError("Failed to decrypt DEK", 400)
            
            # Decrypt GCode directly from bytes to memory
            decrypted_memfd = await crypto_manager.decrypt_gcode_bytes_to_memory(
                encrypted_gcode, gcode_dek_bytes, gcode_iv_hex, job_id
            )
            if decrypted_memfd is None:
                raise ServerError(f"Failed to decrypt GCode for job {job_id}", 400)
            
            logging.info(f"[EncryptedPrint] Received decrypted memfd {decrypted_memfd} for job {job_id}")
            
            # Extract metadata from request data
            metadata = data.get('metadata', {})
            
            # Update metadata with any additional parsed data if needed
            await self._parse_metadata_from_memfd(decrypted_memfd, metadata)

            # CRITICAL: Rewind the file descriptor after parsing metadata.
            # The parser leaves the file pointer at the end of its read.
            os.lseek(decrypted_memfd, 0, os.SEEK_SET)

            # 1. Register the file descriptor with Klipper's bridge
            filename = f"virtual_{job_id}_{int(time.time())}.gcode"
            memfd_fileno = decrypted_memfd

            # Track the memfd so we can close it on job completion
            self.active_memfds[filename] = memfd_fileno
            register_gcode = f'REGISTER_ENCRYPTED_FILE FILENAME="{filename}" PID={self.moonraker_pid} FD={memfd_fileno}'
            try:
                await self.klippy_apis.run_gcode(register_gcode)
                logging.info(f"[EncryptedPrint] Successfully sent REGISTER_ENCRYPTED_FILE for {filename}")
            except Exception as e:
                logging.error(f"[EncryptedPrint] Error registering encrypted file with Klipper: {e}")
                # Important: Close the memfd if registration fails to prevent leaks
                os.close(memfd_fileno)
                raise ServerError(f"Failed to register encrypted file with Klipper: {e}")
            
            # 2. Save metadata. This must be done first, unconditionally, to prime the
            # cache and prevent a race condition where the UI requests metadata
            # before it has been saved.
            if self.file_manager:
                gcode_metadata = self.file_manager.get_metadata_storage()
                gcode_metadata.insert(filename, metadata)
                logging.info(f"[EncryptedPrint] Successfully saved metadata for {filename}")

                # 3. Announce the virtual file's existence to the UI.
                # Now that the metadata is saved, the UI can safely query it.
                self.file_manager._sched_changed_event("create", "gcodes", filename, immediate=True)
                logging.info(f"[EncryptedPrint] Notified file manager of new virtual file: {filename}")

            # 4. Start the print using the registered file. Now, when the UI is notified,
            # the file entry will exist, and the metadata will be ready and waiting.
            print_gcode = f'SDCARD_PRINT_FILE FILENAME={filename}'
            try:
                await self.klippy_apis.run_gcode(print_gcode)
                logging.info(f"[EncryptedPrint] Successfully sent SDCARD_PRINT_FILE for {filename}")
            except Exception as e:
                logging.error(f"[EncryptedPrint] Error starting print for encrypted file: {e}")
                # The bridge will clean up the fd on the Klipper side, so we don't close it here
                raise ServerError(f"Failed to start print for encrypted file: {e}")

            # NOTE: We intentionally do not close `decrypted_memfd` here.
            # Its ownership has been passed to the Klipper process, which will manage its lifecycle.

            return {"status": "ok", "message": f"Encrypted print for job {job_id} started via Klipper bridge"}
        except Exception as e:
            job_id = data.get("job_id", "unknown") if 'data' in locals() and data else "unknown"
            logging.error(f"[EncryptedPrint] Error processing job {job_id}: {str(e)}")
            logging.error(f"[EncryptedPrint] Traceback: {traceback.format_exc()}")
            raise ServerError(f"Failed to process encrypted print job: {str(e)}", 500)

    def _handle_job_state_change(self, event_data):
        job = event_data.get('job', {})
        job_state = job.get('state')
        filename = job.get('filename')

        if job_state in ['complete', 'error', 'cancelled']:
            if filename and filename in self.active_memfds:
                fd_to_close = self.active_memfds.pop(filename)
                logging.info(f"[EncryptedPrint] Job '{filename}' ended with state '{job_state}'. Attempting to clean up memfd: {fd_to_close}.")

                if not isinstance(fd_to_close, int) or fd_to_close < 0:
                    logging.warning(f"[EncryptedPrint] Invalid file descriptor value '{fd_to_close}' found for job '{filename}'. Skipping close.")
                    return

                try:
                    # THE DEFINITIVE FIX: Defensively check if the fd is still a valid, seekable file.
                    # An lseek on a closed fd or an unsupported fd (like a socket) will raise an OSError.
                    os.lseek(fd_to_close, 0, os.SEEK_CUR)

                    # If seek succeeds, it's our memfd. Now we can safely close it.
                    os.close(fd_to_close)
                    logging.info(f"[EncryptedPrint] Successfully closed memfd {fd_to_close} for job {filename}.")
                except OSError as e:
                    # This is the expected outcome if the fd was already closed or has been reused for a non-seekable resource like a socket.
                    logging.warning(f"[EncryptedPrint] Did not close fd {fd_to_close} for job {filename}. It was likely invalid, already closed, or a repurposed socket. Error: {e}")

    async def _parse_metadata_from_memfd(self, memfd_fd, existing_metadata):
        """
        Parse metadata like total layers or filament usage from the decrypted GCode in memfd.
        """
        dup_fd = -1  # Initialize with invalid FD
        try:
            # Duplicate the memfd to avoid interfering with the main stream
            dup_fd = os.dup(memfd_fd)
            with os.fdopen(dup_fd, 'r') as f:
                # Seek to start
                f.seek(0)
                content_sample = f.read(1024 * 1024)  # Read first 1MB for metadata
                # Implement or call a metadata parser (e.g., from lmnt_marketplace components)
                if self.lmnt_integration and hasattr(self.lmnt_integration, 'gcode_metadata_parser'):
                    parser = self.lmnt_integration.gcode_metadata_parser
                    if parser:
                        parsed_data = parser.parse_gcode_metadata(content_sample)
                        existing_metadata.update(parsed_data)
        except Exception as e:
            logging.warning(f"[EncryptedPrint] Failed to parse metadata: {str(e)}")
        return existing_metadata

    async def stream_gcode(self, stream, filename, job_id):
        try:
            self.lmnt_integration.integration.gcode_manager.current_job_id = job_id
            total_lines = 0
            current_line = 0
            metadata = {}
            chunk_size = 4096  # 4KB chunks
            buffer = ""

            # Count lines for progress
            content = stream.read().decode("utf-8")
            total_lines = sum(1 for _ in io.StringIO(content))
            stream.seek(0)

            await self.server.send_notification("notify_status_update", {
                "print_stats": {
                    "progress": 0.0,
                    "state": "printing",
                    "filename": filename,
                    "current_line": 0,
                    "total_lines": total_lines
                }
            })

            async for chunk in self.read_in_chunks(stream, chunk_size):
                buffer += chunk.decode("utf-8")
                lines = buffer.split("\n")
                buffer = lines[-1]
                for line in lines[:-1]:
                    if line.strip():
                        if not metadata:
                            metadata = await self.lmnt_integration.integration.gcode_manager._extract_metadata_from_line(line, current_line + 1)
                        await self.klippy_apis.run_gcode(line)
                        current_line += 1
                        progress = current_line / total_lines
                        await self.server.send_notification("notify_status_update", {
                            "print_stats": {
                                "progress": progress,
                                "state": "printing",
                                "filename": filename,
                                "current_line": current_line,
                                "total_lines": total_lines
                            }
                        })
            if buffer.strip():
                if not metadata:
                    metadata = await self.lmnt_integration.integration.gcode_manager._extract_metadata_from_line(buffer, current_line + 1)
                await self.klippy_apis.run_gcode(buffer)
                current_line += 1
                progress = current_line / total_lines
                await self.server.send_notification("notify_status_update", {
                    "print_stats": {
                        "progress": progress,
                        "state": "printing",
                        "filename": filename,
                        "current_line": current_line,
                        "total_lines": total_lines
                    }
                })

            await self.server.send_notification("notify_status_update", {
                "print_stats": {
                    "progress": 1.0,
                    "state": "completed",
                    "filename": filename
                }
            })
            await self.lmnt_integration.integration.job_manager._update_job_status(job_id, "completed", "Print job completed")
            return metadata
        except Exception as e:
            logging.error(f"EncryptedPrint: Error streaming job {job_id}: {str(e)}")
            await self.server.send_notification("notify_status_update", {
                "print_stats": {
                    "progress": 0.0,
                    "state": "error",
                    "filename": filename
                }
            })
            await self.lmnt_integration.integration.job_manager._update_job_status(job_id, "failed", f"Print streaming error: {str(e)}")
            return None
        finally:
            try:
                stream.close()
            except Exception as e:
                logging.error(f"EncryptedPrint: Error closing stream for job {job_id}: {str(e)}")

    async def read_in_chunks(self, stream, chunk_size):
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            yield chunk