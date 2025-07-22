"""
LMNT Marketplace Unified Print Service

Consolidates encrypted print logic to eliminate multiple memfd allocations
and provide a single, efficient service for both jobs.py and encrypted_print.py
"""

import os
import re
import time
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

@dataclass
class PrintJob:
    """Represents a print job with all necessary data"""
    job_id: str
    encrypted_data: bytes
    dek_package: str
    iv_hex: str
    filename: str
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

@dataclass
class PrintResult:
    """Result of a print operation"""
    success: bool
    memfd: Optional[int] = None
    metadata: Dict[str, Any] = None
    error_message: Optional[str] = None
    layer_count: int = 0

class UnifiedPrintService:
    """
    Unified service for encrypted print operations
    
    Eliminates multiple memfd allocations by providing a single
    service that both jobs.py and encrypted_print.py can use.
    """
    
    def __init__(self, integration):
        self.integration = integration
        self.crypto_manager = integration.crypto_manager
        self.klippy_apis = None
        self.file_manager = None
        self.active_prints = {}  # job_id -> PrintResult
        
    async def initialize(self, klippy_apis, file_manager):
        """Initialize with required components"""
        self.klippy_apis = klippy_apis
        self.file_manager = file_manager
        
    async def start_encrypted_print(self, print_job: PrintJob) -> PrintResult:
        """
        Start an encrypted print job with single memfd allocation
        
        Args:
            print_job: PrintJob containing all necessary data
            
        Returns:
            PrintResult with success status and memfd if successful
        """
        job_id = print_job.job_id
        logging.info(f"[PrintService] Starting encrypted print for job {job_id}")
        
        try:
            # SINGLE ALLOCATION: Decrypt directly to memfd
            memfd = await self._decrypt_to_memfd(print_job)
            if memfd is None:
                return PrintResult(
                    success=False,
                    error_message=f"Failed to decrypt GCode for job {job_id}"
                )
            
            # Parse metadata using the same memfd (no duplication)
            metadata = await self._parse_metadata_from_memfd(memfd, print_job.metadata)
            
            # Extract layer count using the same memfd (no duplication)
            layer_count = await self._extract_layer_count_from_memfd(memfd)
            metadata['layer_count'] = layer_count
            
            # Start the print using the same memfd
            success = await self._start_klipper_print(memfd, print_job.filename, metadata)
            
            if success:
                result = PrintResult(
                    success=True,
                    memfd=memfd,
                    metadata=metadata,
                    layer_count=layer_count
                )
                self.active_prints[job_id] = result
                logging.info(f"[PrintService] Successfully started print for job {job_id}")
                return result
            else:
                # Clean up memfd if print start failed
                os.close(memfd)
                return PrintResult(
                    success=False,
                    error_message=f"Failed to start Klipper print for job {job_id}"
                )
                
        except Exception as e:
            logging.error(f"[PrintService] Error starting print for job {job_id}: {e}")
            return PrintResult(
                success=False,
                error_message=str(e)
            )
    
    async def start_print_with_decrypted_memfd(self, job_id: str, decrypted_memfd: int, filename: str) -> PrintResult:
        """
        Start a print job with pre-decrypted memfd data
        
        Args:
            job_id: Job identifier
            decrypted_memfd: File descriptor containing decrypted GCode
            filename: Virtual filename for the print
            
        Returns:
            PrintResult with success status
        """
        logging.info(f"[PrintService] Starting print with pre-decrypted memfd for job {job_id}")
        
        try:
            # Parse metadata using the decrypted memfd
            metadata = await self._parse_metadata_from_memfd(decrypted_memfd, {})
            
            # Extract layer count using the decrypted memfd
            layer_count = await self._extract_layer_count_from_memfd(decrypted_memfd)
            metadata['layer_count'] = layer_count
            
            # Start the print using the decrypted memfd
            success = await self._start_klipper_print(decrypted_memfd, filename, metadata)
            
            if success:
                result = PrintResult(
                    success=True,
                    memfd=decrypted_memfd,
                    metadata=metadata,
                    layer_count=layer_count
                )
                self.active_prints[job_id] = result
                logging.info(f"[PrintService] Successfully started print for job {job_id}")
                return result
            else:
                return PrintResult(
                    success=False,
                    error_message=f"Failed to start Klipper print for job {job_id}"
                )
                
        except Exception as e:
            logging.error(f"[PrintService] Error starting print with decrypted memfd for job {job_id}: {e}")
            return PrintResult(
                success=False,
                error_message=str(e)
            )
    
    async def _decrypt_to_memfd(self, print_job: PrintJob) -> Optional[int]:
        """
        Decrypt encrypted GCode directly to memfd (single allocation)
        
        Returns:
            memfd file descriptor or None if failed
        """
        try:
            # Decrypt DEK first
            decrypted_dek = await self.crypto_manager.decrypt_dek(print_job.dek_package)
            if decrypted_dek is None:
                logging.error(f"[PrintService] Failed to decrypt DEK for job {print_job.job_id}")
                return None
            
            # Decrypt GCode directly to memfd (reuse existing crypto manager method)
            memfd = await self.crypto_manager.decrypt_gcode_bytes_to_memory(
                print_job.encrypted_data,
                decrypted_dek,
                print_job.iv_hex,
                print_job.job_id
            )
            
            if memfd is None:
                logging.error(f"[PrintService] Failed to decrypt GCode to memfd for job {print_job.job_id}")
                return None
                
            logging.info(f"[PrintService] Successfully decrypted job {print_job.job_id} to memfd {memfd}")
            return memfd
            
        except Exception as e:
            logging.error(f"[PrintService] Decryption error for job {print_job.job_id}: {e}")
            return None
    
    async def _parse_metadata_from_memfd(self, memfd: int, existing_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse metadata from memfd using seek operations (no duplication)
        
        Args:
            memfd: File descriptor to read from
            existing_metadata: Any existing metadata to merge
            
        Returns:
            Combined metadata dictionary
        """
        metadata = existing_metadata.copy()
        
        try:
            # Save current position
            current_pos = os.lseek(memfd, 0, os.SEEK_CUR)
            
            # Read first 1MB for metadata parsing
            os.lseek(memfd, 0, os.SEEK_SET)
            content_bytes = os.read(memfd, 1024 * 1024)
            content = content_bytes.decode('utf-8', errors='ignore')
            
            # Restore position
            os.lseek(memfd, current_pos, os.SEEK_SET)
            
            # Parse basic metadata from GCode content
            # Extract estimated print time if available
            time_match = re.search(r';\s*estimated printing time.*?=\s*([\d.]+)\s*h', content, re.IGNORECASE)
            if time_match:
                metadata['estimated_time'] = float(time_match.group(1)) * 3600  # Convert hours to seconds
            
            # Extract filament usage if available
            filament_match = re.search(r';\s*filament used.*?=\s*([\d.]+)\s*mm', content, re.IGNORECASE)
            if filament_match:
                metadata['filament_total'] = float(filament_match.group(1))
            
            logging.info(f"[PrintService] Parsed metadata: {metadata}")
            return metadata
            
        except Exception as e:
            logging.error(f"[PrintService] Error parsing metadata: {e}")
            return metadata
    
    async def _extract_layer_count_from_memfd(self, memfd: int) -> int:
        """
        Extract layer count from memfd using seek operations (no duplication)
        
        Args:
            memfd: File descriptor to read from
            
        Returns:
            Layer count or 0 if not found
        """
        try:
            # Save current position
            current_pos = os.lseek(memfd, 0, os.SEEK_CUR)
            
            # Read header and footer sections for layer count
            os.lseek(memfd, 0, os.SEEK_SET)
            header_content = os.read(memfd, 200 * 1024).decode('utf-8', errors='ignore')  # First 200KB
            
            # Try to read footer (last 800KB)
            try:
                file_size = os.lseek(memfd, 0, os.SEEK_END)
                footer_start = max(0, file_size - 800 * 1024)
                os.lseek(memfd, footer_start, os.SEEK_SET)
                footer_content = os.read(memfd, 800 * 1024).decode('utf-8', errors='ignore')
            except:
                footer_content = ""
            
            # Restore position
            os.lseek(memfd, current_pos, os.SEEK_SET)
            
            # Search for layer count patterns
            content_to_search = header_content + "\n" + footer_content
            layer_patterns = [
                ';LAYER_COUNT:',
                '; layer_count =',
                '; total layers =',
                '; total layers count =',
                ';Total layers:',
            ]
            
            for line in content_to_search.split('\n'):
                line_upper = line.upper()
                for pattern in layer_patterns:
                    if pattern.upper() in line_upper:
                        try:
                            if ':' in line:
                                layer_count = int(line.split(':')[-1].strip())
                            elif '=' in line:
                                layer_count = int(line.split('=')[-1].strip())
                            logging.info(f"[PrintService] Found layer count: {layer_count}")
                            return layer_count
                        except (ValueError, IndexError):
                            continue
            
            logging.warning("[PrintService] No layer count found in GCode")
            return 0
            
        except Exception as e:
            logging.error(f"[PrintService] Error extracting layer count: {e}")
            return 0
    
    async def _start_klipper_print(self, memfd: int, filename: str, metadata: Dict[str, Any]) -> bool:
        """
        Start print in Klipper using the memfd
        
        Args:
            memfd: File descriptor containing decrypted GCode
            filename: Virtual filename for the print
            metadata: Print metadata
            
        Returns:
            True if print started successfully
        """
        try:
            if not self.klippy_apis:
                logging.error("[PrintService] No Klipper APIs available")
                return False
            
            # Rewind memfd for Klipper
            os.lseek(memfd, 0, os.SEEK_SET)
            
            # Get Moonraker PID for registration
            moonraker_pid = os.getpid()
            virtual_filename = f"virtual_{filename}"
            
            # 1. Register the encrypted file with Klipper
            register_cmd = f'REGISTER_ENCRYPTED_FILE FILENAME="{virtual_filename}" PID={moonraker_pid} FD={memfd}'
            if metadata.get('layer_count', 0) > 0:
                register_cmd += f' LAYER_COUNT={metadata["layer_count"]}'
            
            await self.klippy_apis.run_gcode(register_cmd)
            logging.info(f"[PrintService] Registered encrypted file: {virtual_filename}")
            
            # 2. Save metadata to file manager
            if self.file_manager:
                gcode_metadata = self.file_manager.get_metadata_storage()
                gcode_metadata.insert(virtual_filename, metadata)
                
                # Announce file creation to UI
                self.file_manager._sched_changed_event("create", "gcodes", virtual_filename, immediate=True)
                logging.info(f"[PrintService] Saved metadata and announced file: {virtual_filename}")
            
            # 3. Set up print metadata in Klipper
            if metadata.get('layer_count', 0) > 0:
                await self.klippy_apis.run_gcode(
                    f"SET_PRINT_STATS_INFO TOTAL_LAYER={metadata['layer_count']}"
                )
            
            # 4. Start the print
            await self.klippy_apis.run_gcode(
                f"SDCARD_PRINT_FILE FILENAME={virtual_filename}"
            )
            
            logging.info(f"[PrintService] Successfully started Klipper print: {virtual_filename}")
            return True
            
        except Exception as e:
            logging.error(f"[PrintService] Error starting Klipper print: {e}")
            return False
    
    def cleanup_print(self, job_id: str):
        """Clean up resources for a completed print job"""
        if job_id in self.active_prints:
            result = self.active_prints.pop(job_id)
            if result.memfd:
                try:
                    os.close(result.memfd)
                    logging.info(f"[PrintService] Cleaned up memfd for job {job_id}")
                except OSError as e:
                    logging.warning(f"[PrintService] Error closing memfd for job {job_id}: {e}")
    
    def get_active_prints(self) -> Dict[str, PrintResult]:
        """Get all active print jobs"""
        return self.active_prints.copy()
