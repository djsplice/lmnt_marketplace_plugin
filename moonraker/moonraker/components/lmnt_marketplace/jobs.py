"""
LMNT Marketplace Jobs Module

Handles print job management for LMNT Marketplace integration:
- Job start and status updates
- Print state event handling
- Job queue management
"""

import os
import json
import logging
import asyncio
import aiohttp
import time
from datetime import datetime

class JobManager:
    """
    Manages print jobs for LMNT Marketplace
    
    Handles job start, status updates, print state event handling,
    and job queue management.
    """
    
    def __init__(self, integration):
        """Initialize the Job Manager"""
        self.integration = integration
        self.print_job_queue = []
        self.current_print_job = None
        self.print_job_started = False
        self.job_polling_task = None
        
        # References to other managers
        self.auth_manager = None
        self.crypto_manager = None
        self.gcode_manager = None
    
    def set_auth_manager(self, auth_manager):
        """Set the authentication manager reference"""
        self.auth_manager = auth_manager
        
    def set_crypto_manager(self, crypto_manager):
        """Set the crypto manager reference"""
        self.crypto_manager = crypto_manager
        
    def set_gcode_manager(self, gcode_manager):
        """Set the gcode manager reference"""
        self.gcode_manager = gcode_manager
    
    async def initialize(self, klippy_apis, http_client):
        """Initialize with Klippy APIs and HTTP client"""
        self.klippy_apis = klippy_apis
        self.http_client = http_client
        
        # Start job polling
        self.setup_job_polling()
    
    def register_endpoints(self, register_endpoint):
        """Register HTTP endpoints for job management"""
        # Job status endpoint
        register_endpoint(
            "/lmnt/job_status", 
            ["GET"], 
            self._handle_job_status,
            transports=["http"]
        )
        
        # Manual job start endpoint
        register_endpoint(
            "/lmnt/start_job", 
            ["POST"], 
            self._handle_start_job,
            transports=["http"]
        )
    
    def setup_job_polling(self):
        """Set up periodic polling for print jobs"""
        # Cancel any existing polling task
        if self.job_polling_task:
            self.job_polling_task.cancel()
        
        # Start polling task
        self.job_polling_task = asyncio.create_task(self._poll_for_jobs_loop())
        logging.info("Job polling started")
    
    async def _poll_for_jobs_loop(self):
        """Continuously poll for new print jobs"""
        poll_interval = 60  # seconds
        
        while True:
            try:
                # Only poll if we have a valid printer token
                if self.integration.auth_manager.printer_token:
                    await self._poll_for_jobs()
                
                # Wait for next poll
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                logging.info("Job polling cancelled")
                break
            except Exception as e:
                logging.error(f"Error in job polling: {str(e)}")
                await asyncio.sleep(poll_interval)
    
    async def _poll_for_jobs(self):
        """Poll the marketplace for new print jobs"""
        if not self.integration.auth_manager.printer_token:
            logging.error("Cannot poll for jobs: No printer token available")
            return
        
        if not self.integration.auth_manager.printer_id:
            logging.error("Cannot poll for jobs: No printer ID available")
            return
        
        # Check if printer is busy
        if self.current_print_job:
            logging.debug("Skipping job poll: Printer is busy with current job")
            return
        
        jobs_url = f"{self.integration.marketplace_url}/api/{self.integration.api_version}/printer-jobs"
        
        try:
            headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
            
            async with self.http_client.get(jobs_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    jobs = data.get('jobs', [])
                    
                    if jobs:
                        logging.info(f"Found {len(jobs)} pending jobs")
                        await self._process_pending_jobs(jobs)
                    else:
                        logging.debug("No pending jobs found")
                else:
                    error_text = await response.text()
                    logging.error(f"Job polling failed with status {response.status}: {error_text}")
        except Exception as e:
            logging.error(f"Error polling for jobs: {str(e)}")
    
    async def _process_pending_jobs(self, jobs):
        """Process pending print jobs from the marketplace"""
        # Add new jobs to queue
        for job in jobs:
            job_id = job.get('id')
            
            # Check if job is already in queue
            if job_id and not any(j.get('id') == job_id for j in self.print_job_queue):
                self.print_job_queue.append(job)
                logging.info(f"Added job {job_id} to queue")
        
        # Process next job if printer is ready
        if not self.current_print_job and self.print_job_queue:
            await self._process_next_job()
    
    async def _process_next_job(self):
        """Process the next job in the queue"""
        if not self.print_job_queue:
            logging.debug("No jobs in queue to process")
            return
        
        # Check if printer is ready
        is_ready = await self._check_printer_ready()
        if not is_ready:
            logging.info("Printer not ready for next job")
            return
        
        # Get next job from queue
        job = self.print_job_queue.pop(0)
        job_id = job.get('id')
        
        if not job_id:
            logging.error("Invalid job: missing job ID")
            return
        
        logging.info(f"Processing job {job_id}")
        
        # Set as current job
        self.current_print_job = job
        
        # Update job status to processing
        await self._update_job_status(job_id, 'processing', 'Starting job')
        
        # Download and decrypt GCode
        encrypted_filepath = await self._download_gcode(job)
        
        if not encrypted_filepath:
            logging.error(f"Failed to download GCode for job {job_id}")
            await self._update_job_status(job_id, 'failed', 'Failed to download GCode')
            self.current_print_job = None
            return
        
        # Start printing
        success = await self._start_print(job, encrypted_filepath)
        
        if not success:
            logging.error(f"Failed to start print for job {job_id}")
            await self._update_job_status(job_id, 'failed', 'Failed to start print')
            self.current_print_job = None
    
    async def _check_printer_ready(self):
        """Check if printer is ready for a new print job"""
        try:
            result = await self.klippy_apis.query_objects({'objects': {'print_stats': None}})
            if result.get('print_stats', {}).get('state', '') in ('printing', 'paused'):
                logging.info("Printer is busy (printing or paused)")
                return False
            
            # Check if printer is connected and ready
            printer_info = await self.klippy_apis.get_printer_info()
            if printer_info.get('state', '') != 'ready':
                logging.info(f"Printer not ready: {printer_info.get('state', '')}")
                return False
            
            return True
        except Exception as e:
            logging.error(f"Error checking printer readiness: {str(e)}")
            return False
    
    async def _download_gcode(self, job):
        """
        Download and save encrypted GCode for a job
        
        Args:
            job (dict): Job information including ID and GCode URL
            
        Returns:
            str: Path to saved encrypted GCode file
            None: If download failed
        """
        job_id = job.get('id')
        gcode_url = job.get('gcode_url')
        
        if not job_id or not gcode_url:
            logging.error(f"Invalid job data: missing ID or GCode URL")
            return None
        
        # Create filename for encrypted GCode
        encrypted_filename = f"job_{job_id}.gcode.enc"
        encrypted_filepath = os.path.join(self.integration.encrypted_path, encrypted_filename)
        
        try:
            # Download encrypted GCode
            headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
            
            async with self.http_client.get(gcode_url, headers=headers) as response:
                if response.status == 200:
                    # Save encrypted GCode to file
                    with open(encrypted_filepath, 'wb') as f:
                        f.write(await response.read())
                    
                    logging.info(f"Downloaded encrypted GCode for job {job_id}: {encrypted_filepath}")
                    return encrypted_filepath
                else:
                    error_text = await response.text()
                    logging.error(f"GCode download failed with status {response.status}: {error_text}")
        except Exception as e:
            logging.error(f"Error downloading GCode for job {job_id}: {str(e)}")
        
        return None
    
    async def _start_print(self, job, encrypted_filepath):
        """
        Start printing a job
        
        Args:
            job (dict): Job information
            encrypted_filepath (str): Path to encrypted GCode file
            
        Returns:
            bool: True if print started successfully, False otherwise
        """
        job_id = job.get('id')
        
        if not job_id or not encrypted_filepath:
            logging.error("Cannot start print: Missing job ID or encrypted file path")
            return False
        
        try:
            # Check if printer is ready
            is_ready = await self._check_printer_ready()
            if not is_ready:
                logging.error(f"Cannot start print for job {job_id}: Printer not ready")
                return False
            
            # Home the printer if needed
            try:
                await self.klippy_apis.run_gcode("G28")
                logging.info("Homed printer before starting print")
            except Exception as e:
                logging.error(f"Error homing printer: {str(e)}")
                return False
            
            # Stream decrypted GCode to Klipper
            metadata = await self.integration.gcode_manager.stream_decrypted_gcode(
                encrypted_filepath, job_id)
            
            if not metadata:
                logging.error(f"Failed to stream GCode for job {job_id}")
                return False
            
            # Save metadata
            self.integration.gcode_manager.save_metadata(job_id)
            
            # Update job status to printing
            await self._update_job_status(job_id, 'printing', 'Print started')
            
            # Start monitoring print progress
            asyncio.create_task(self._monitor_print_progress(job))
            
            logging.info(f"Started print for job {job_id}")
            return True
            
        except Exception as e:
            logging.error(f"Error starting print for job {job_id}: {str(e)}")
            return False
    
    async def _update_job_status(self, job_id, status, message=None):
        """
        Update job status in the marketplace
        
        Args:
            job_id (str): Job ID
            status (str): New status ('processing', 'printing', 'completed', 'failed', 'cancelled')
            message (str, optional): Status message
            
        Returns:
            bool: True if status update was successful, False otherwise
        """
        if not job_id:
            logging.error("Cannot update job status: Missing job ID")
            return False
        
        if not self.integration.auth_manager.printer_token:
            logging.error("Cannot update job status: No printer token available")
            return False
        
        update_url = f"{self.integration.marketplace_url}/api/{self.integration.api_version}/job-status/{job_id}"
        
        try:
            headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
            payload = {"status": status}
            
            if message:
                payload["message"] = message
            
            async with self.http_client.post(update_url, headers=headers, json=payload) as response:
                if response.status == 200:
                    logging.info(f"Updated job {job_id} status to {status}")
                    return True
                else:
                    error_text = await response.text()
                    logging.error(f"Job status update failed with status {response.status}: {error_text}")
        except Exception as e:
            logging.error(f"Error updating job status for {job_id}: {str(e)}")
        
        return False
    
    async def _monitor_print_progress(self, job):
        """
        Monitor print progress and update status
        
        Args:
            job (dict): Job information
        """
        job_id = job.get('id')
        
        if not job_id:
            logging.error("Cannot monitor print progress: Missing job ID")
            return
        
        try:
            # Wait for print to start
            await asyncio.sleep(5)
            
            # Get metadata for layer count
            metadata = self.integration.gcode_manager.current_metadata
            total_layers = metadata.get('layer_count', 0)
            
            # Monitor until print is complete or failed
            while True:
                try:
                    # Query print stats
                    result = await self.klippy_apis.query_objects({'objects': {'print_stats': None}})
                    stats = result.get('print_stats', {})
                    state = stats.get('state', '')
                    
                    # Get progress information
                    progress = stats.get('progress', 0) * 100  # Convert to percentage
                    current_layer = int(total_layers * stats.get('progress', 0)) if total_layers > 0 else 0
                    duration = stats.get('print_duration', 0)
                    
                    # Update job progress
                    await self._update_job_progress(job_id, progress, current_layer, total_layers, duration)
                    
                    # Check if print is complete or failed
                    if state == 'complete':
                        await self._update_job_status(job_id, 'completed', 'Print completed successfully')
                        self.current_print_job = None
                        break
                    elif state in ('error', 'cancelled'):
                        await self._update_job_status(job_id, 'failed', f'Print {state}')
                        self.current_print_job = None
                        break
                    elif state not in ('printing', 'paused'):
                        logging.info(f"Print state changed to {state}, continuing to monitor")
                    
                except Exception as e:
                    logging.error(f"Error querying print stats: {str(e)}")
                
                # Wait before next update
                await asyncio.sleep(10)
        
        except asyncio.CancelledError:
            logging.info(f"Print monitoring for job {job_id} cancelled")
        except Exception as e:
            logging.error(f"Error monitoring print progress for job {job_id}: {str(e)}")
            
            # Ensure job status is updated even on error
            if self.current_print_job and self.current_print_job.get('id') == job_id:
                await self._update_job_status(job_id, 'failed', f'Error monitoring print: {str(e)}')
                self.current_print_job = None
    
    async def _update_job_progress(self, job_id, progress, current_layer, total_layers, duration):
        """
        Update job progress in the marketplace
        
        Args:
            job_id (str): Job ID
            progress (float): Print progress percentage (0-100)
            current_layer (int): Current layer being printed
            total_layers (int): Total number of layers
            duration (float): Print duration in seconds
            
        Returns:
            bool: True if progress update was successful, False otherwise
        """
        if not job_id:
            return False
        
        if not self.integration.auth_manager.printer_token:
            return False
        
        # Only update every 5% or every 2 minutes to avoid excessive API calls
        if hasattr(self, '_last_progress') and hasattr(self, '_last_progress_time'):
            if (progress - self._last_progress < 5 and 
                    time.time() - self._last_progress_time < 120):
                return True
        
        # Store current progress and time
        self._last_progress = progress
        self._last_progress_time = time.time()
        
        update_url = f"{self.integration.marketplace_url}/api/{self.integration.api_version}/job-progress/{job_id}"
        
        try:
            headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
            payload = {
                "progress": progress,
                "current_layer": current_layer,
                "total_layers": total_layers,
                "duration": duration
            }
            
            async with self.http_client.post(update_url, headers=headers, json=payload) as response:
                if response.status == 200:
                    logging.debug(f"Updated job {job_id} progress to {progress:.1f}% (layer {current_layer}/{total_layers})")
                    return True
                else:
                    # Only log errors occasionally to avoid log spam
                    if progress % 20 < 5:  # Log errors at 0%, 20%, 40%, 60%, 80%
                        error_text = await response.text()
                        logging.error(f"Job progress update failed with status {response.status}: {error_text}")
        except Exception as e:
            # Only log errors occasionally to avoid log spam
            if progress % 20 < 5:  # Log errors at 0%, 20%, 40%, 60%, 80%
                logging.error(f"Error updating job progress for {job_id}: {str(e)}")
        
        return False
    
    async def _handle_job_status(self, web_request):
        """
        Handle HTTP request for job status
        """
        if self.current_print_job:
            job_id = self.current_print_job.get('id')
            
            # Get print stats
            try:
                result = await self.klippy_apis.query_objects({'objects': {'print_stats': None}})
                stats = result.get('print_stats', {})
                state = stats.get('state', '')
                progress = stats.get('progress', 0) * 100  # Convert to percentage
                
                # Get metadata
                metadata = self.integration.gcode_manager.current_metadata
                total_layers = metadata.get('layer_count', 0)
                current_layer = int(total_layers * stats.get('progress', 0)) if total_layers > 0 else 0
                
                return {
                    "job_id": job_id,
                    "status": "printing" if state in ('printing', 'paused') else state,
                    "progress": progress,
                    "current_layer": current_layer,
                    "total_layers": total_layers,
                    "state": state,
                    "metadata": metadata
                }
            except Exception as e:
                logging.error(f"Error getting job status: {str(e)}")
                return {"error": str(e)}
        else:
            # Check if printer is ready for a new job
            is_ready = await self._check_printer_ready()
            return {
                "status": "idle",
                "ready": is_ready,
                "queue_length": len(self.print_job_queue)
            }
    
    async def _handle_start_job(self, web_request):
        """
        Handle HTTP request to manually start a job
        """
        try:
            # Extract job data from request
            job_data = await web_request.get_json_data()
            job_id = job_data.get('job_id')
            gcode_url = job_data.get('gcode_url')
            
            if not job_id or not gcode_url:
                raise web_request.error(
                    "Missing job ID or GCode URL", 400)
            
            # Check if printer is busy
            if self.current_print_job:
                raise web_request.error(
                    "Printer is busy with another job", 409)
            
            # Create job object
            job = {
                "id": job_id,
                "gcode_url": gcode_url
            }
            
            # Set as current job
            self.current_print_job = job
            
            # Update job status to processing
            await self._update_job_status(job_id, 'processing', 'Starting job')
            
            # Download and decrypt GCode
            encrypted_filepath = await self._download_gcode(job)
            
            if not encrypted_filepath:
                self.current_print_job = None
                raise web_request.error(
                    "Failed to download GCode", 500)
            
            # Start printing in background task to avoid blocking response
            asyncio.create_task(self._start_print(job, encrypted_filepath))
            
            return {
                "status": "processing",
                "job_id": job_id,
                "message": "Job started successfully"
            }
            
        except Exception as e:
            logging.error(f"Error handling start job request: {str(e)}")
            raise web_request.error(str(e), 500)
