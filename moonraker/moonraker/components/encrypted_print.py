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
        self.crypto_manager = None

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
            self.file_manager = self.server.lookup_component("file_manager")
            lmnt_component = self.server.lookup_component("lmnt_marketplace_plugin")
            self.lmnt_integration = lmnt_component.integration
            self.crypto_manager = self.lmnt_integration.crypto_manager
            logging.info("[EncryptedPrint] All components successfully initialized.")
        except Exception as e:
            logging.exception(
                "[EncryptedPrint] Failed to initialize components after Klippy ready")

        return False  # Return False to unregister this handler after it runs

    async def handle_encrypted_print(self, web_request):

        try:
            # Just-in-time component lookup to prevent race conditions
            if self.lmnt_integration is None:
                lmnt_component = self.server.lookup_component("lmnt_marketplace_plugin")
                self.lmnt_integration = lmnt_component.integration
                self.crypto_manager = self.lmnt_integration.crypto_manager
            if self.file_manager is None:
                self.file_manager = self.server.lookup_component("file_manager")
            if self.klippy_apis is None:
                self.klippy_apis = self.server.lookup_component("klippy_apis")
            if self.database is None:
                self.database = self.server.lookup_component("database")
            # Use the correct WebRequest API method to get all arguments
            data = web_request.get_args()
            
            job_id = data.get("job_id")
            encrypted_gcode = base64.b64decode(data.get("encrypted_gcode"))
            gcode_dek_package = data.get("gcode_dek_package")
            gcode_iv_hex = data.get("gcode_iv_hex")
            filename = data.get("filename", f"encrypted_{job_id}.gcode")
            if not all([job_id, encrypted_gcode, gcode_dek_package, gcode_iv_hex]):
                raise ServerError("Missing required parameters", 400)
            
            logging.info(f"[EncryptedPrint] Received encrypted print job {job_id}, decrypting in memory")
            
            # Ensure the crypto manager is available
            if not self.crypto_manager:
                raise ServerError("Crypto manager not available", 503)

            # Just-in-time key loading
            if not self.crypto_manager.is_dlt_private_key_loaded:
                logging.info("[EncryptedPrint] Private key not loaded. Attempting to load now via AuthManager...")
                # Ask the AuthManager to re-load the key. It will update the CryptoManager.
                if not self.lmnt_integration.auth_manager._load_dlt_private_key():
                    logging.error(f"[EncryptedPrint] Error processing job {job_id}: AuthManager failed to load private key on-demand.")
                    raise ServerError("Failed to load private key on-demand via AuthManager.", 500)
                else:
                    logging.info("[EncryptedPrint] AuthManager successfully loaded the private key.")

            # First, decrypt the Data Encryption Key (DEK)
            decrypted_dek = await self.crypto_manager.decrypt_dek(gcode_dek_package)
            if decrypted_dek is None:
                raise ServerError(f"Failed to decrypt DEK for job {job_id}", 500)

            # Now, decrypt the GCode using the decrypted DEK
            decrypted_memfd = await self.crypto_manager.decrypt_gcode_bytes_to_memory(
                encrypted_gcode, decrypted_dek, gcode_iv_hex, job_id
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
            
            # Extract layer count from decrypted GCode for proper progress tracking
            layer_count = await self._extract_layer_count_from_memfd(decrypted_memfd)
            
            # Add layer count to metadata for Mainsail UI
            if layer_count > 0:
                metadata['layer_count'] = layer_count
                metadata['object_height'] = metadata.get('object_height', 0)  # Ensure object_height exists
                logging.info(f"[EncryptedPrint] Added layer_count={layer_count} to metadata")
            
            # Rewind again after layer extraction
            os.lseek(decrypted_memfd, 0, os.SEEK_SET)

            # 1. Register the file descriptor with Klipper's bridge
            filename = f"virtual_{job_id}_{int(time.time())}.gcode"
            memfd_fileno = decrypted_memfd

            # Track the memfd so we can close it on job completion
            self.active_memfds[filename] = memfd_fileno
            
            # Build REGISTER_ENCRYPTED_FILE command with metadata
            register_gcode = f'REGISTER_ENCRYPTED_FILE FILENAME="{filename}" PID={self.moonraker_pid} FD={memfd_fileno}'
            if layer_count > 0:
                register_gcode += f' LAYER_COUNT={layer_count}'
            
            try:
                await self.klippy_apis.run_gcode(register_gcode)
                logging.info(f"[EncryptedPrint] Successfully sent REGISTER_ENCRYPTED_FILE for {filename} with {layer_count} layers")
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

            # 4. Set layer count in Klipper if available
            if layer_count > 0:
                try:
                    await self.klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO TOTAL_LAYER={layer_count}")
                    logging.info(f"[EncryptedPrint] Set TOTAL_LAYER={layer_count} in Klipper for {filename}")
                except Exception as e:
                    logging.warning(f"[EncryptedPrint] Failed to set layer count: {e}")
            
            # 5. Start the print using the registered file (modern SDCARD_PRINT_FILE approach)
            print_gcode = f'SDCARD_PRINT_FILE FILENAME={filename}'
            try:
                await self.klippy_apis.run_gcode(print_gcode)
                logging.info(f"[EncryptedPrint] Successfully sent SDCARD_PRINT_FILE for {filename} with {layer_count} layers")
            except Exception as e:
                logging.error(f"[EncryptedPrint] Error starting print for encrypted file: {e}")
                raise ServerError(f"Failed to start print for encrypted file: {e}")

            # NOTE: We intentionally do not close `decrypted_memfd` here.
            # Its ownership has been passed to the Klipper process, which will manage its lifecycle.

            return {"status": "ok", "message": f"Encrypted print for job {job_id} started via SDCARD_PRINT_FILE"}
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
                logging.info(f"[EncryptedPrint] Job '{filename}' ended with state '{job_state}'. Scheduling memfd cleanup: {fd_to_close}.")
                # Schedule cleanup with a small delay to avoid FD conflicts
                asyncio.create_task(self._delayed_memfd_cleanup(fd_to_close, filename))

    async def _delayed_memfd_cleanup(self, fd_to_close, filename):
        """Clean up memfd with a delay to avoid file descriptor conflicts."""
        try:
            # Wait a bit to let HTTP connections finish cleanup first
            await asyncio.sleep(1.0)
            
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
        except Exception as e:
            logging.error(f"[EncryptedPrint] Error in delayed memfd cleanup for {filename}: {e}")

    async def _extract_layer_count_from_memfd(self, memfd_fd):
        """Extract layer count from decrypted GCode in memfd using the proven working approach."""
        layer_count = 0
        try:
            # Read content from memfd (save current position)
            current_pos = os.lseek(memfd_fd, 0, os.SEEK_CUR)
            os.lseek(memfd_fd, 0, os.SEEK_SET)
            
            # Read first 1MB for layer detection (same as working streaming method)
            content_bytes = os.read(memfd_fd, 1024 * 1024)
            content = content_bytes.decode('utf-8', errors='ignore')
            
            # Restore original position
            os.lseek(memfd_fd, current_pos, os.SEEK_SET)
            
            # Split into lines for processing
            all_lines = content.split('\n')
            
            # Check metadata sections: OrcaSlicer puts metadata in first ~100 and last ~600 lines
            lines_to_check = all_lines[:200] + all_lines[-800:]  # First 200 (header) + last 800 (footer)
            
            # Try multiple layer count patterns
            layer_patterns = [
                ';LAYER_COUNT:',
                '; layer_count =',
                '; total layers =',
                '; total layers count =',
                ';Total layers:',
                '; LAYER_COUNT:',
                ';LAYER COUNT:'
            ]
            
            for line in lines_to_check:
                line_upper = line.upper()
                for pattern in layer_patterns:
                    if pattern.upper() in line_upper:
                        try:
                            # Extract number after colon or equals
                            if ':' in line:
                                layer_count = int(line.split(':')[-1].strip())
                            elif '=' in line:
                                layer_count = int(line.split('=')[-1].strip())
                            logging.info(f"[EncryptedPrint] Found layer count {layer_count} using pattern '{pattern}'")
                            return layer_count
                        except (ValueError, IndexError) as e:
                            logging.warning(f"[EncryptedPrint] Failed to parse layer count from line '{line.strip()}': {e}")
                if layer_count > 0:
                    break
            
            if layer_count == 0:
                logging.warning(f"[EncryptedPrint] No layer count found in GCode metadata")
                
        except Exception as e:
            logging.error(f"[EncryptedPrint] Error extracting layer count from memfd: {e}")
            logging.error(f"[EncryptedPrint] Traceback: {traceback.format_exc()}")
        
        return layer_count

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

            # Count lines and extract metadata for layer information
            content = stream.read().decode("utf-8")
            total_lines = sum(1 for _ in io.StringIO(content))
            stream.seek(0)
            
            # Extract layer count from GCode with multiple detection patterns
            layer_count = 0
            lines_to_check = content.split('\n')[:2000]  # Check first 2000 lines
            
            # Try multiple layer count patterns
            layer_patterns = [
                ';LAYER_COUNT:',
                '; layer_count =',
                '; total layers =',
                ';Total layers:',
                '; LAYER_COUNT:',
                ';LAYER COUNT:'
            ]
            
            for line in lines_to_check:
                line_upper = line.upper()
                for pattern in layer_patterns:
                    if pattern.upper() in line_upper:
                        try:
                            # Extract number after colon or equals
                            if ':' in line:
                                layer_count = int(line.split(':')[-1].strip())
                            elif '=' in line:
                                layer_count = int(line.split('=')[-1].strip())
                            logging.info(f"[EncryptedPrint] Found layer count {layer_count} using pattern '{pattern}'")
                            break
                        except (ValueError, IndexError) as e:
                            logging.warning(f"[EncryptedPrint] Failed to parse layer count from line '{line.strip()}': {e}")
                if layer_count > 0:
                    break
            
            # Set print stats info with layer count if available
            if layer_count > 0:
                await self.klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO TOTAL_LAYER={layer_count}")
                logging.info(f"[EncryptedPrint] Set TOTAL_LAYER={layer_count} in Klipper")
            else:
                logging.warning(f"[EncryptedPrint] No layer count found in GCode metadata for {filename}")
            
            logging.info(f"[EncryptedPrint] Starting GCode stream for {filename} with {total_lines} lines and {layer_count} layers")
            
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
                        # Let Klipper handle print stats naturally - no custom notifications
            if buffer.strip():
                if not metadata:
                    metadata = await self.lmnt_integration.integration.gcode_manager._extract_metadata_from_line(buffer, current_line + 1)
                
                # Check for layer change in final buffer
                if buffer.strip() == ";LAYER_CHANGE":
                    current_layer += 1
                    await self.klippy_apis.run_gcode(buffer)
                    await self.klippy_apis.run_gcode(f"SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer}")
                    logging.debug(f"[EncryptedPrint] Layer change detected: now on layer {current_layer} of {layer_count}")
                else:
                    await self.klippy_apis.run_gcode(buffer)
                
                current_line += 1
                # Let Klipper handle print stats naturally - no custom notifications

            # Let Klipper handle completion naturally - no custom notifications
            logging.info(f"[EncryptedPrint] Completed streaming {current_line} lines for job {job_id}")
            if layer_count > 0:
                logging.info(f"[EncryptedPrint] Print completed: {layer_count} total layers")
            # Job completion will be detected by the monitoring system
            return metadata
        except Exception as e:
            logging.error(f"EncryptedPrint: Error streaming job {job_id}: {str(e)}")
            # Let Klipper handle error state naturally
            logging.error(f"[EncryptedPrint] Streaming failed for job {job_id}: {str(e)}")
            # Error will be detected by the monitoring system
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