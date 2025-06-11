"""
LMNT Marketplace GCode Module

Handles secure GCode operations for LMNT Marketplace integration:
- Memory-only decryption of encrypted GCode files
- Secure streaming of GCode to Klipper
- Metadata extraction from GCode
- Thumbnail extraction and handling
"""

import os
import re
import json
import logging
import asyncio
import base64
import time
from datetime import datetime

class GCodeManager:
    """
    Manages GCode operations for LMNT Marketplace
    
    Handles secure decryption and streaming of GCode files,
    metadata extraction, and thumbnail handling.
    """
    
    def __init__(self, integration):
        """Initialize the GCode Manager"""
        self.integration = integration
        self.current_job_id = None
        self.current_metadata = {}
    
    async def initialize(self, klippy_apis, http_client):
        """Initialize with Klippy APIs and HTTP client"""
        self.klippy_apis = klippy_apis
        self.http_client = http_client
    
    async def stream_decrypted_gcode(self, decrypted_filepath, job_id=None):
        """
        Stream a decrypted GCode file line-by-line to Klipper
        
        Args:
            decrypted_filepath (str): Path to the decrypted GCode file
            job_id (str, optional): Job ID for tracking and logging
            
        Returns:
            dict: Metadata extracted from the GCode
            None: If streaming failed
        """
        self.current_job_id = job_id
        job_info = f" for job {job_id}" if job_id else ""
        
        try:
            logging.info(f"Starting to stream GCode from {decrypted_filepath}{job_info}")
            
            # Read decrypted file
            with open(decrypted_filepath, 'r', encoding='utf-8') as f:
                decrypted_gcode_content = f.read()
            
            decrypted_gcode = decrypted_gcode_content # Assign to the variable used by splitlines

            if not decrypted_gcode:
                logging.error(f"GCode file {decrypted_filepath} is empty or could not be read{job_info}")
                return None
            
            # Split into lines
            lines = decrypted_gcode.splitlines()
            
            # Extract metadata from GCode
            metadata = self._extract_metadata(lines, job_id)
            self.current_metadata = metadata
            
            # Extract and save thumbnails
            await self._extract_and_save_thumbnails(lines, job_id)
            
            # Prepare Klipper for streaming (CLEAR_STREAM_OUTPUT removed as it caused 'Unknown command')
            # await self.klippy_apis.run_gcode("CLEAR_STREAM_OUTPUT") 
            
            # Stream GCode line-by-line
            line_count = 0
            start_time = time.time()
            
            for line in lines:
                # Skip empty lines and comments
                if not line or line.strip().startswith(';'):
                    continue
                
                # Send the line to Klipper (removed double quote escaping)
                await self.klippy_apis.run_gcode(line)
                line_count += 1
                
                # Log progress periodically
                if line_count % 1000 == 0:
                    elapsed = time.time() - start_time
                    rate = line_count / elapsed if elapsed > 0 else 0
                    logging.info(f"Streamed {line_count} lines{job_info} ({rate:.1f} lines/sec)")
            
            # End of streaming is implicit when G-code lines run out.
            # Log completion
            elapsed = time.time() - start_time
            rate = line_count / elapsed if elapsed > 0 else 0
            logging.info(f"Completed streaming {line_count} lines{job_info} in {elapsed:.1f}s ({rate:.1f} lines/sec)")
            
            # Clear decryption key from memory
            self.integration.crypto_manager.clear_decryption_key()
            
            # Return metadata
            return metadata
            
        except Exception as e:
            logging.error(f"Error streaming decrypted GCode{job_info}: {str(e)}")
            # Clear decryption key from memory even on error
            self.integration.crypto_manager.clear_decryption_key()
            return None
    
    def _extract_metadata(self, gcode_lines, job_id=None):
        """
        Extract metadata from GCode lines
        
        Args:
            gcode_lines (list): List of GCode lines
            job_id (str, optional): Job ID for tracking and logging
            
        Returns:
            dict: Extracted metadata
        """
        metadata = {
            'job_id': job_id,
            'layer_count': 0,
            'estimated_time': 0,
            'filament_used': 0,
            'thumbnails': [],
            'first_layer_height': 0,
            'layer_height': 0,
            'object_height': 0,
            'nozzle_diameter': 0,
            'filament_type': '',
            'generated_by': '',
            'timestamp': datetime.now().isoformat()
        }
        
        # Regular expressions for metadata extraction
        layer_count_pattern = re.compile(r';LAYER_COUNT:(\d+)')
        time_pattern = re.compile(r';TIME:(\d+)')
        filament_pattern = re.compile(r';Filament used: (\d+\.\d+)m')
        first_layer_height_pattern = re.compile(r';FIRST_LAYER_HEIGHT:(\d+\.\d+)')
        layer_height_pattern = re.compile(r';LAYER_HEIGHT:(\d+\.\d+)')
        object_height_pattern = re.compile(r';OBJECT_HEIGHT:(\d+\.\d+)')
        nozzle_diameter_pattern = re.compile(r';NOZZLE_DIAMETER:(\d+\.\d+)')
        filament_type_pattern = re.compile(r';FILAMENT_TYPE:(.*)')
        generated_by_pattern = re.compile(r';Generated with (.*)')
        
        # Scan the first 1000 lines for metadata
        for i, line in enumerate(gcode_lines[:1000]):
            if i > 1000:  # Only check the first 1000 lines for metadata
                break
                
            if line.startswith(';'):
                # Layer count
                match = layer_count_pattern.search(line)
                if match:
                    metadata['layer_count'] = int(match.group(1))
                    continue
                
                # Estimated time
                match = time_pattern.search(line)
                if match:
                    metadata['estimated_time'] = int(match.group(1))
                    continue
                
                # Filament used
                match = filament_pattern.search(line)
                if match:
                    metadata['filament_used'] = float(match.group(1))
                    continue
                
                # First layer height
                match = first_layer_height_pattern.search(line)
                if match:
                    metadata['first_layer_height'] = float(match.group(1))
                    continue
                
                # Layer height
                match = layer_height_pattern.search(line)
                if match:
                    metadata['layer_height'] = float(match.group(1))
                    continue
                
                # Object height
                match = object_height_pattern.search(line)
                if match:
                    metadata['object_height'] = float(match.group(1))
                    continue
                
                # Nozzle diameter
                match = nozzle_diameter_pattern.search(line)
                if match:
                    metadata['nozzle_diameter'] = float(match.group(1))
                    continue
                
                # Filament type
                match = filament_type_pattern.search(line)
                if match:
                    metadata['filament_type'] = match.group(1).strip()
                    continue
                
                # Generated by
                match = generated_by_pattern.search(line)
                if match:
                    metadata['generated_by'] = match.group(1).strip()
                    continue
        
        return metadata
    
    async def _extract_and_save_thumbnails(self, gcode_lines, job_id=None):
        """
        Extract thumbnails from GCode and save them to disk
        
        Args:
            gcode_lines (list): List of GCode lines
            job_id (str, optional): Job ID for thumbnail filename
            
        Returns:
            list: List of saved thumbnail paths
        """
        thumbnail_paths = []
        job_prefix = f"{job_id}_" if job_id else ""
        
        # Find thumbnail sections
        i = 0
        while i < len(gcode_lines):
            line = gcode_lines[i]
            
            # Look for thumbnail begin marker
            if line.startswith('; thumbnail begin'):
                try:
                    # Parse thumbnail metadata
                    parts = line.split()
                    width = int(parts[3].split('=')[1])
                    height = int(parts[4].split('=')[1])
                    
                    # Collect base64 data
                    base64_data = ""
                    i += 1
                    while i < len(gcode_lines) and not gcode_lines[i].startswith('; thumbnail end'):
                        if gcode_lines[i].startswith(';'):
                            base64_data += gcode_lines[i][2:].strip()
                        i += 1
                    
                    # Decode and save the thumbnail
                    try:
                        image_data = base64.b64decode(base64_data)
                        filename = f"{job_prefix}thumbnail_{width}x{height}.png"
                        filepath = os.path.join(self.integration.thumbnails_path, filename)
                        
                        with open(filepath, 'wb') as f:
                            f.write(image_data)
                        
                        thumbnail_paths.append(filepath)
                        logging.info(f"Saved thumbnail: {filepath}")
                        
                        # Add to metadata
                        self.current_metadata['thumbnails'].append({
                            'width': width,
                            'height': height,
                            'path': filepath,
                            'filename': filename
                        })
                        
                    except Exception as e:
                        logging.error(f"Error saving thumbnail: {str(e)}")
                
                except Exception as e:
                    logging.error(f"Error parsing thumbnail: {str(e)}")
            
            i += 1
        
        return thumbnail_paths
    
    def save_metadata(self, job_id=None):
        """
        Save current job metadata to disk
        
        Args:
            job_id (str, optional): Job ID for metadata filename
            
        Returns:
            str: Path to saved metadata file
            None: If saving failed
        """
        if not job_id and not self.current_job_id:
            logging.error("Cannot save metadata: No job ID provided")
            return None
        
        job_id = job_id or self.current_job_id
        
        # Ensure metadata directory exists (handled by integration.py when it creates self.integration.metadata_path)
        metadata_file = os.path.join(self.integration.metadata_path, f"job_{job_id}_metadata.json")
        
        try:
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.current_metadata, f, indent=2)
            
            logging.info(f"Saved metadata for job {job_id}: {metadata_file}")
            return metadata_file
        except Exception as e:
            logging.error(f"Error saving metadata for job {job_id}: {str(e)}")
            return None

    def load_metadata(self, job_id):
        """
        Load job metadata from disk
        
        Args:
            job_id (str): Job ID for metadata filename
            
        Returns:
            dict: Loaded metadata
            None: If loading failed
        """
        if not job_id:
            logging.error("Cannot load metadata: No job ID provided")
            return None
        
        metadata_file = os.path.join(self.integration.metadata_path, f"job_{job_id}_metadata.json")
        
        try:
            if os.path.exists(metadata_file):
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                
                logging.info(f"Loaded metadata for job {job_id}: {metadata_file}")
                return metadata
            else:
                logging.warning(f"No metadata file found for job {job_id}: {metadata_file}")
                return None
        except Exception as e:
            logging.error(f"Error loading metadata for job {job_id}: {str(e)}")
            return None
