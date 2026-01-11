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
from .lmnt_marketplace.print_service import PrintJob, PrintResult

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
        self.print_service = None

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
            
            # Try to lookup the LMNT Marketplace component
            lmnt_component = await self._get_lmnt_component()
            if lmnt_component:
                self.lmnt_integration = lmnt_component.integration
                self.crypto_manager = self.lmnt_integration.crypto_manager
                self.print_service = self.lmnt_integration.print_service
                logging.info("[EncryptedPrint] All components successfully initialized.")
            else:
                logging.error("[EncryptedPrint] Failed to find LMNT Marketplace component after multiple attempts.")
        except Exception as e:
            logging.exception(
                "[EncryptedPrint] Failed to initialize components after Klippy ready")

        return False  # Return False to unregister this handler after it runs

    async def _get_lmnt_component(self):
        """
        Robustly find the LMNT Marketplace component with retries and broad scanning.
        """
        for attempt in range(1, 4):
            # 1. Try common names
            for name in ["lmnt_marketplace_plugin", "lmnt_marketplace"]:
                try:
                    comp = self.server.lookup_component(name)
                    logging.info(f"[EncryptedPrint] Found LMNT Marketplace component as: {name}")
                    return comp
                except Exception:
                    continue
            
            # 2. Brute force scan for anything "lmnt"
            try:
                for comp_name, comp_obj in self.server.components.items():
                    if "lmnt" in comp_name.lower() and comp_name != "encrypted_print":
                        logging.info(f"[EncryptedPrint] Found LMNT component via broad scan: {comp_name}")
                        return comp_obj
            except Exception as e:
                logging.warning(f"[EncryptedPrint] Broad scan failed: {e}")

            if attempt < 3:
                logging.info(f"[EncryptedPrint] Component not found, retrying in 0.5s (attempt {attempt}/3)...")
                await asyncio.sleep(0.5)
        
        # Final failure state: log what we DID find to help debug
        try:
            available = list(self.server.components.keys())
            logging.error(f"[EncryptedPrint] Could not find LMNT component. Available components: {available}")
        except Exception:
            pass
            
        return None

    async def handle_encrypted_print(self, web_request):
        try:
            # Just-in-time component lookup to prevent race conditions
            if self.print_service is None:
                if self.lmnt_integration is None:
                    lmnt_component = await self._get_lmnt_component()
                    if lmnt_component is None:
                        raise ServerError("Component (lmnt_marketplace_plugin or lmnt_marketplace) not found", 503)
                    self.lmnt_integration = lmnt_component.integration
                self.print_service = self.lmnt_integration.print_service
            
            # Ensure UnifiedPrintService dependencies are initialized (klippy_apis, file_manager)
            try:
                if getattr(self.print_service, 'klippy_apis', None) is None or getattr(self.print_service, 'file_manager', None) is None:
                    klippy_apis = self.server.lookup_component("klippy_apis")
                    file_manager = self.server.lookup_component("file_manager")
                    if klippy_apis is None:
                        raise ServerError("Klippy APIs not yet available", 503)
                    await self.print_service.initialize(klippy_apis, file_manager)
                    logging.info("[EncryptedPrint] UnifiedPrintService initialized with Klippy APIs and File Manager")
            except Exception as init_e:
                logging.warning(f"[EncryptedPrint] Initialization check failed: {init_e}")
                # Proceed; downstream retry may succeed shortly after
            
            # Use the correct WebRequest API method to get all arguments
            data = web_request.get_args()
            
            job_id = data.get("job_id")
            encrypted_gcode = base64.b64decode(data.get("encrypted_gcode"))
            gcode_dek_package = data.get("gcode_dek_package")
            gcode_iv_hex = data.get("gcode_iv_hex")
            filename = data.get("filename", f"encrypted_{job_id}.gcode")
            
            if not all([job_id, encrypted_gcode, gcode_dek_package, gcode_iv_hex]):
                raise ServerError("Missing required parameters", 400)
            
            logging.info(f"[EncryptedPrint] Received encrypted print job {job_id}, delegating to print service")
            
            # Create PrintJob and delegate to unified print service with small retry/backoff
            print_job = PrintJob(
                job_id=job_id,
                encrypted_data=encrypted_gcode,
                dek_package=gcode_dek_package,
                iv_hex=gcode_iv_hex,
                filename=filename,
                metadata=data.get('metadata', {})
            )

            last_error_msg = None
            for attempt in range(1, 4):  # up to 3 attempts
                try:
                    result = await self.print_service.start_encrypted_print(print_job)
                    if result and result.success:
                        return {
                            "status": "ok",
                            "message": f"Encrypted print for job {job_id} started successfully",
                            "metadata": result.metadata,
                            "layer_count": result.layer_count
                        }
                    else:
                        last_error_msg = (result.error_message if result else "Unknown error")
                        logging.warning(f"[EncryptedPrint] Attempt {attempt} failed to start print for job {job_id}: {last_error_msg}")
                except Exception as e:
                    last_error_msg = str(e)
                    logging.warning(f"[EncryptedPrint] Attempt {attempt} threw while starting print for job {job_id}: {last_error_msg}")

                # Backoff before next attempt (short, to cover startup race)
                await asyncio.sleep(0.5)

            # If all attempts failed, surface the last known error
            raise ServerError(f"Failed to start encrypted print: {last_error_msg}", 500)
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