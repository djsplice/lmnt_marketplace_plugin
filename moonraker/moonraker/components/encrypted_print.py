import io
import re
import json
import time
import asyncio
import base64
import os
import logging
from aiohttp import web
from typing import Optional
from moonraker.common import UserInfo
from moonraker.utils.exceptions import ServerError
import traceback

def load_component(config):
    return EncryptedPrint(config)

class EncryptedPrint:
    def __init__(self, config):
        self.server = config.get_server()
        self.lmnt_integration = None
        self.file_manager = self.server.lookup_component("file_manager")
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.database = self.server.lookup_component("database")
        
        # Note: print_stats is a Klipper object, not a Moonraker component
        # We interact with it via klippy_apis.query_objects() and GCode commands
        
        # Try to get the LMNT integration on startup
        try:
            lmnt_component = self.server.lookup_component("lmnt_marketplace_plugin")
            # The actual integration object is stored in the integration attribute
            self.lmnt_integration = lmnt_component.integration
            logging.info("[EncryptedPrint] Successfully found LMNT Marketplace Plugin on startup")
        except ServerError:
            logging.warning("[EncryptedPrint] LMNT Marketplace Plugin not found on initial attempt, will retry during component_init")
        
        # Register for server initialization completion
        self.server.register_event_handler("server:ready", self.handle_server_ready)
        
        # Register our endpoint
        self.server.register_endpoint(
            "/server/encrypted/print",
            ["POST"],
            self.handle_encrypted_print,
            transports=["http"]
        )
        logging.info("[EncryptedPrint] Registered /server/encrypted/print endpoint")

    async def handle_server_ready(self):
        """Called when the server is fully initialized"""
        if not self.lmnt_integration:
            try:
                lmnt_component = self.server.lookup_component("lmnt_marketplace_plugin")
                # The actual integration object is stored in the integration attribute
                self.lmnt_integration = lmnt_component.integration
                logging.info("[EncryptedPrint] Successfully found LMNT Marketplace Plugin after server ready")
            except ServerError:
                logging.error("[EncryptedPrint] Failed to find LMNT Marketplace Plugin even after server ready")
        
        return False  # Return False to unregister this handler after it runs

    async def handle_encrypted_print(self, web_request):
        try:
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
            filename = data.get("filename", f"virtual_{job_id}_{int(time.time())}.gcode")
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
            
            # Start streaming in background task to avoid blocking the HTTP response
            async def stream_gcode_task():
                memfd_stream = None
                try:
                    logging.info(f"[EncryptedPrint] Background task starting, memfd={decrypted_memfd}")
                    
                    # Verify the memfd is still valid
                    try:
                        # Check if we can seek on the memfd (this will fail if it's closed)
                        current_pos = os.lseek(decrypted_memfd, 0, os.SEEK_CUR)
                        logging.info(f"[EncryptedPrint] Memfd {decrypted_memfd} is valid, current position: {current_pos}")
                    except OSError as e:
                        logging.error(f"[EncryptedPrint] Memfd {decrypted_memfd} is invalid: {e}")
                        raise
                    
                    # Duplicate the memfd to avoid ownership issues with os.fdopen()
                    dup_memfd = os.dup(decrypted_memfd)
                    logging.info(f"[EncryptedPrint] Duplicated memfd {decrypted_memfd} to {dup_memfd}")
                    
                    # Create file object from duplicated memfd
                    memfd_stream = os.fdopen(dup_memfd, "rb")
                    logging.info(f"[EncryptedPrint] Created file stream from duplicated memfd {dup_memfd}")
                    
                    # Initialize print tracking with Moonraker and Klipper integration
                    virtual_path = f"gcodes/{filename}"
                    
                    # 1. Notify file_manager of the virtual file (for UI tracking)
                    # Note: We schedule a file change notification instead of calling non-existent methods
                    self.file_manager._sched_changed_event("create", "gcodes", virtual_path, immediate=True)
                    logging.info(f"[EncryptedPrint] Notified file_manager of virtual file: {virtual_path}")
                    
                    # 2. Set print metadata first (before starting print)
                    metadata_cmd = f"SET_PRINT_STATS_INFO FILENAME={filename}"
                    if 'metadata' in data and isinstance(data['metadata'], dict):
                        if 'estimated_time' in data['metadata']:
                            # Convert to integer seconds for Klipper
                            estimated_seconds = int(float(data['metadata']['estimated_time']))
                            metadata_cmd += f" TOTAL_TIME={estimated_seconds}"
                        if 'layer_count' in data['metadata']:
                            metadata_cmd += f" TOTAL_LAYER={data['metadata']['layer_count']}"
                    
                    await self.klippy_apis.run_gcode(metadata_cmd)
                    logging.info(f"[EncryptedPrint] Set print metadata: {metadata_cmd}")
                    
                    # 3. Start the print
                    # Removed the explicit PRINT_START command, instead let the GCode streaming trigger the print state change
                    
                    # 4. Set up job manager for proper status tracking
                    if hasattr(self.lmnt_integration, 'job_manager') and self.lmnt_integration.job_manager:
                        job_manager = self.lmnt_integration.job_manager
                        if job_manager.current_print_job and job_manager.current_print_job.get('id') == job_id:
                            logging.info(f"[EncryptedPrint] Job {job_id} already managed by JobManager, updating status to printing")
                            await job_manager._update_job_status(job_id, "printing", "Starting GCode streaming")
                        else:
                            # Set this as the current print job for proper status tracking
                            job_data = {
                                'id': job_id,
                                'filename': filename,
                                'metadata': data.get('metadata', {}),
                                'started_at': time.time()
                            }
                            job_manager.current_print_job = job_data
                            job_manager.print_job_started = True
                            
                            # Update status to printing before streaming starts
                            await job_manager._update_job_status(job_id, "printing", "Starting GCode streaming")
                            
                            # Start print progress monitoring in background
                            asyncio.create_task(job_manager._monitor_print_progress(job_id))
                    
                    # Stream the decrypted GCode to Klipper
                    success = await self.lmnt_integration.gcode_manager.stream_decrypted_gcode_from_stream(
                        memfd_stream, job_id
                    )
                    
                    if not success:
                        logging.error(f"[EncryptedPrint] Failed to stream GCode for job {job_id}")
                        if hasattr(self.lmnt_integration, 'job_manager') and self.lmnt_integration.job_manager:
                            await self.lmnt_integration.job_manager._update_job_status(job_id, "failed", "GCode streaming failed")
                        return
                    
                    # Use metadata from the request data
                    metadata = data.get('metadata', {})
                    self.lmnt_integration.gcode_manager.current_metadata = metadata
                    logging.info(f"[EncryptedPrint] Successfully streamed job {job_id} to printer")
                    
                    # Update job status to indicate streaming completed successfully
                    if hasattr(self.lmnt_integration, 'job_manager') and self.lmnt_integration.job_manager:
                        await self.lmnt_integration.job_manager._update_job_status(job_id, "printing", "GCode streaming completed, print in progress")
                
                except Exception as e:
                    logging.error(f"[EncryptedPrint] Error in background streaming task for job {job_id}: {str(e)}")
                    if hasattr(self.lmnt_integration, 'job_manager') and self.lmnt_integration.job_manager:
                        await self.lmnt_integration.job_manager._update_job_status(job_id, "failed", f"Streaming error: {str(e)}")
                finally:
                    # Ensure stream is closed (this will also close the duplicated memfd)
                    if memfd_stream is not None:
                        try:
                            memfd_stream.close()
                            logging.info(f"[EncryptedPrint] Closed file stream (and duplicated memfd)")
                        except Exception as e:
                            logging.warning(f"[EncryptedPrint] Error closing file stream: {e}")
                    
                    # Close the original memfd
                    try:
                        # Check if the original memfd is still valid before closing
                        os.lseek(decrypted_memfd, 0, os.SEEK_CUR)
                        os.close(decrypted_memfd)
                        logging.info(f"[EncryptedPrint] Closed original memfd {decrypted_memfd}")
                    except OSError as e:
                        if e.errno == 9:  # Bad file descriptor
                            logging.info(f"[EncryptedPrint] Original memfd {decrypted_memfd} already closed")
                        else:
                            logging.warning(f"[EncryptedPrint] Error closing original memfd {decrypted_memfd}: {e}")
                    except Exception as e:
                        logging.warning(f"[EncryptedPrint] Error closing original memfd {decrypted_memfd}: {e}")
            
            # Start the streaming task in background
            asyncio.create_task(stream_gcode_task())
            
            # Return immediately to prevent HTTP timeout
            return {"status": "success", "job_id": job_id, "message": "Print job initiated, streaming in progress"}
        except Exception as e:
            job_id = data.get("job_id", "unknown") if 'data' in locals() and data else "unknown"
            logging.error(f"[EncryptedPrint] Error processing job {job_id}: {str(e)}")
            logging.error(f"[EncryptedPrint] Traceback: {traceback.format_exc()}")
            raise ServerError(f"Failed to process encrypted print job: {str(e)}", 500)

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