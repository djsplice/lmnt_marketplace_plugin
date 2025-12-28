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
                # Begin streaming to Klipper
                start_time = time.time()
                line_count = 0
                metadata = {}
                
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    line_count += 1
                    
                    if line_count % 1000 == 0:
                        elapsed = time.time() - start_time
                        rate = line_count / elapsed if elapsed > 0 else 0
                        logging.info(f"Streamed {line_count} lines{job_info} ({rate:.1f} lines/sec)")
                    
                    if not metadata:
                        metadata = await self._extract_metadata_from_line(line, line_count)
                    
                    await self.klippy_apis.run_gcode(line)
                
                # End of streaming is implicit when G-code lines run out.
                # Log completion
                elapsed = time.time() - start_time
                rate = line_count / elapsed if elapsed > 0 else 0
                logging.info(f"Completed streaming {line_count} lines{job_info} in {elapsed:.1f}s ({rate:.1f} lines/sec)")
                
                # Return metadata
                return metadata
                
        except Exception as e:
            logging.error(f"Error streaming decrypted GCode{job_info}: {str(e)}")
            return None

    async def stream_decrypted_gcode_from_stream(self, stream, job_id=None):
        """
        Stream decrypted GCode from a provided stream object line-by-line to Klipper
        
        Args:
            stream: A file-like object or stream containing decrypted GCode
            job_id (str, optional): Job ID for tracking and logging
            
        Returns:
            dict: Metadata extracted from the GCode
            None: If streaming failed
        """
        self.current_job_id = job_id
        job_info = f" for job {job_id}" if job_id else ""
        
        try:
            logging.info(f"Starting to stream GCode from provided stream{job_info}")
            
            # Begin streaming to Klipper
            start_time = time.time()
            line_count = 0
            metadata = {}
            
            while True:
                line = stream.readline()
                if not line:
                    break
                
                decoded_line = line.decode('utf-8').strip()
                if not decoded_line:
                    continue
                
                line_count += 1
                
                if line_count % 1000 == 0:
                    elapsed = time.time() - start_time
                    rate = line_count / elapsed if elapsed > 0 else 0
                    logging.info(f"Streamed {line_count} lines{job_info} ({rate:.1f} lines/sec)")
                
                if not metadata:
                    metadata = await self._extract_metadata_from_line(decoded_line, line_count)
                
                await self.klippy_apis.run_gcode(decoded_line)
            
            # End of streaming is implicit when G-code lines run out.
            # Log completion
            elapsed = time.time() - start_time
            rate = line_count / elapsed if elapsed > 0 else 0
            logging.info(f"Completed streaming {line_count} lines{job_info} in {elapsed:.1f}s ({rate:.1f} lines/sec)")
            
            # Return metadata
            return metadata
            
        except Exception as e:
            logging.error(f"Error streaming decrypted GCode from stream{job_info}: {str(e)}")
            return None

    def parse_gcode_metadata(self, content_chunk):
        """
        Parse metadata from a chunk of GCode text
        
        Args:
            content_chunk (str): Decrypted GCode text chunk
            
        Returns:
            dict: Extracted metadata
        """
        metadata = {}
        # Split into lines
        lines = content_chunk.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Use basic line extraction but accumulate results
            line_metadata = self._extract_metadata_from_line_sync(line, 0)
            
            # Update metadata with found values (ignore defaults)
            for key, value in line_metadata.items():
                if key in ['thumbnails', 'timestamp', 'job_id']: 
                    continue
                    
                if isinstance(value, (int, float)) and value > 0:
                    metadata[key] = value
                elif isinstance(value, str) and value:
                    metadata[key] = value
                    
        return metadata

    def _extract_metadata_from_line_sync(self, line, line_count):
        """
        Synchronous version of _extract_metadata_from_line to reuse logic
        without async overhead in tight loops
        """
        return self._do_extract_metadata(line, line_count)

    def _do_extract_metadata(self, line, line_count):
        """
        Internal method to extract metadata from a single line
        """
        metadata = {
            'job_id': self.current_job_id,
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
        
        # Regular expressions for metadata extraction - made case insensitive and more flexible for OrcaSlicer format
        layer_count_pattern = re.compile(r';\s*(?:total layer number|total layers|LAYER_COUNT|LAYERCOUNT|LAYERS)\s*[:=\s]\s*(\d+)', re.IGNORECASE)
        time_pattern = re.compile(r';\s*(?:TIME|ESTIMATED_TIME|PRINT_TIME)\s*[:=\s]\s*(\d+)', re.IGNORECASE)
        time_hms_pattern = re.compile(r';\s*estimated printing time\s*=\s*(?:(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?)', re.IGNORECASE)
        filament_pattern = re.compile(r';\s*(?:FILAMENT_USED|FILAMENT|filament used|total filament used)\s*(?:\[mm\]|\[cm3\]|\[g\]|)\s*[:=\s]\s*([\d\.]+)(?:m|cm|mm|g)?', re.IGNORECASE)
        first_layer_height_pattern = re.compile(r';\s*(?:FIRST_LAYER_HEIGHT|FIRST_LAYER|first layer height|first layer extrusion width|first layer thickness)\s*[:=\s]\s*([\d\.]+)(?:mm)?', re.IGNORECASE)
        layer_height_pattern = re.compile(r';\s*(?:LAYER_HEIGHT|HEIGHT_PER_LAYER|layer height|perimeters extrusion width)\s*[:=\s]\s*([\d\.]+)(?:mm)?', re.IGNORECASE)
        object_height_pattern = re.compile(r';\s*(?:OBJECT_HEIGHT|MODEL_HEIGHT|TOTAL_HEIGHT|max_z_height)\s*[:=\s]\s*([\d\.]+)(?:mm)?', re.IGNORECASE)
        nozzle_diameter_pattern = re.compile(r';\s*(?:NOZZLE_DIAMETER|NOZZLE_SIZE|nozzle diameter|external perimeters extrusion width)\s*[:=\s]\s*([\d\.]+)(?:mm)?', re.IGNORECASE)
        filament_type_pattern = re.compile(r';\s*(?:FILAMENT_TYPE|FILA_TYPE|MATERIAL|filament type)\s*[:=\s]*(.+)', re.IGNORECASE)
        generated_by_pattern = re.compile(r';\s*(?:GENERATED_WITH|GENERATED_BY|SLICER|SOFTWARE|generated by)\s*[:=\s]*(.+)', re.IGNORECASE)
        
        # Extract metadata from the line
        if line.startswith(';'):
            # Layer count
            match = layer_count_pattern.search(line)
            if match:
                metadata['layer_count'] = int(match.group(1))
            
            # Estimated time
            match = time_pattern.search(line)
            if match:
                metadata['estimated_time'] = int(match.group(1))
            else:
                match = time_hms_pattern.search(line)
                if match:
                    hours = int(match.group(1)) if match.group(1) else 0
                    minutes = int(match.group(2)) if match.group(2) else 0
                    seconds = int(match.group(3)) if match.group(3) else 0
                    metadata['estimated_time'] = hours * 3600 + minutes * 60 + seconds
            
            # Filament used
            match = filament_pattern.search(line)
            if match:
                metadata['filament_used'] = float(match.group(1))
            
            # First layer height
            match = first_layer_height_pattern.search(line)
            if match:
                metadata['first_layer_height'] = float(match.group(1))
            
            # Layer height
            match = layer_height_pattern.search(line)
            if match:
                metadata['layer_height'] = float(match.group(1))
            
            # Object height
            match = object_height_pattern.search(line)
            if match:
                metadata['object_height'] = float(match.group(1))
            
            # Nozzle diameter
            match = nozzle_diameter_pattern.search(line)
            if match:
                metadata['nozzle_diameter'] = float(match.group(1))
            
            # Filament type
            match = filament_type_pattern.search(line)
            if match:
                metadata['filament_type'] = match.group(1).strip()
            
            # Generated by
            match = generated_by_pattern.search(line)
            if match:
                metadata['generated_by'] = match.group(1).strip()
        
        return metadata

    async def _extract_metadata_from_line(self, line, line_count):
        """
        Extract metadata from a single GCode line
        
        Args:
            line (str): GCode line
            line_count (int): Current line count
            
        Returns:
            dict: Extracted metadata
        """
        return self._do_extract_metadata(line, line_count)
    
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