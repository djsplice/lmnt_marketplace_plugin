"""
LMNT Marketplace Jobs Module

Handles print job management for LMNT Marketplace integration:
- Job start and status updates
- Print state event handling
- Job queue management
"""

import os
import time
import json
import logging
import asyncio
import aiohttp
import base64
from datetime import datetime

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    logging.warning("LMNT JOBS: WebSocket module not found, job control monitoring will be disabled")

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
        logging.info("LMNT JOB POLLING: setup_job_polling method called")
        
        # Cancel any existing polling task
        if self.job_polling_task:
            self.job_polling_task.cancel()
            logging.info("Previous job polling task cancelled")
        
        # Get poll interval from config or use default
        poll_interval = self.integration.config.getint('check_interval', 60)
        logging.info(f"Setting up job polling with interval of {poll_interval} seconds")
        
        # Start polling task
        try:
            self.job_polling_task = asyncio.create_task(self._poll_for_jobs_loop(poll_interval))
            logging.info("LMNT JOB POLLING: Task created successfully")
        except Exception as e:
            logging.error(f"LMNT JOB POLLING: Failed to create polling task: {str(e)}")
            import traceback
            logging.error(f"LMNT JOB POLLING: {traceback.format_exc()}")
        
        logging.info("Job polling started")
    
    async def _poll_for_jobs_loop(self, poll_interval=60):
        """Continuously poll for new print jobs
        
        Args:
            poll_interval: Interval in seconds between polls
        """
        logging.info(f"LMNT JOB POLLING: _poll_for_jobs_loop started with {poll_interval} second interval")
        
        # Log initial token status
        token_status = "available" if self.integration.auth_manager.printer_token else "not available"
        logging.info(f"LMNT JOB POLLING: Initial printer token status: {token_status}")
        
        poll_count = 0
        
        while True:
            try:
                poll_count += 1
                logging.info(f"LMNT JOB POLLING: Poll attempt #{poll_count}")
                
                # Only poll if we have a valid printer token
                if self.integration.auth_manager.printer_token:
                    logging.info(f"LMNT JOB POLLING: Polling for jobs with valid printer token")
                    await self._poll_for_jobs()
                else:
                    logging.warning(f"LMNT JOB POLLING: Skipping poll #{poll_count} - No printer token available")
                
                # Wait for next poll
                logging.info(f"LMNT JOB POLLING: Waiting {poll_interval} seconds until next job poll")
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                logging.info("LMNT JOB POLLING: Job polling cancelled")
                break
            except Exception as e:
                logging.error(f"LMNT JOB POLLING: Error in poll #{poll_count}: {str(e)}")
                import traceback
                logging.error(f"LMNT JOB POLLING: Exception traceback: {traceback.format_exc()}")
                await asyncio.sleep(poll_interval)
    
    async def _poll_for_jobs(self):
        """Poll for jobs from the LMNT Marketplace API"""
        logging.info("LMNT JOB POLLING: _poll_for_jobs method called")
        
        # Check if we have a valid printer token and ID
        printer_id = self.integration.auth_manager.printer_id
        if not printer_id:
            logging.error("LMNT JOB POLLING: Cannot poll for jobs - no printer ID available")
            return
        
        # Get the API endpoint URL
        api_url = f"{self.integration.marketplace_url}/api/poll-print-queue"
        logging.info(f"LMNT JOB POLLING: Polling for jobs at: {api_url} for printer ID: {printer_id}")
        
        # Get the printer token for authentication
        printer_token = self.integration.auth_manager.printer_token
        if not printer_token:
            logging.error("LMNT JOB POLLING: Cannot poll for jobs - no printer token available")
            return
        
        # Log the token (redacted in non-debug mode)
        token_for_log = printer_token if self.integration.debug_mode else f"{printer_token[:5]}..."
        logging.info(f"LMNT JOB POLLING: Sending job poll request with token: {token_for_log}")
        
        # Set up the request headers with authentication
        headers = {
            "Authorization": f"Bearer {printer_token}",
            "Content-Type": "application/json"
        }
        
        try:
            # Log the request details
            logging.info(f"LMNT JOB POLLING: Making GET request to {api_url}")
            
            # Record the start time for timing the request
            start_time = time.time()
            
            # Make the API request
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers) as response:
                    # Calculate the response time
                    response_time = int((time.time() - start_time) * 1000)  # Convert to milliseconds
                    
                    # Log the response status
                    logging.info(f"LMNT JOB POLLING: Response received in {response_time}ms with status: {response.status}")
                    
                    # Handle different response statuses
                    if response.status == 200:
                        # Parse the response JSON
                        data = await response.json()
                        logging.info(f"LMNT JOB POLLING: Received response: {data}")
                        
                        # Process the jobs data
                        if 'jobs' in data and data['jobs']:
                            job_count = len(data['jobs'])
                            logging.info(f"LMNT JOB POLLING: Found {job_count} pending jobs")
                            
                            # Process each job
                            for job in data['jobs']:
                                print_job_id = job.get('print_job_id')
                                if print_job_id:
                                    logging.info(f"LMNT JOB POLLING: Processing job {print_job_id}")
                                    # Transform the job format to match what _process_pending_jobs expects
                                    processed_job = {
                                        'id': print_job_id,
                                        'purchase_id': job.get('purchase_id'),
                                        'status': job.get('status'),
                                        'created_at': job.get('created_at'),
                                        # New fields for decryption
                                        'gcode_url': job.get('encrypted_gcode_download_url'), # This is the HTTP(S) URL for the encrypted G-code
                                        'gcode_dek_package': job.get('gcode_dek_encrypted_hex'), # This field now holds either DLT package or legacy hex
                                        'gcode_iv_hex': job.get('gcode_iv_hex'),
                                        'user_account_id': job.get('user_account_id'),
                                        'printer_kek_id': job.get('printer_kek_id')
                                    }
                                    logging.info(f"LMNT JOB POLLING: Job data: {processed_job}")
                                    if not processed_job.get('gcode_url'):
                                        logging.error(f"LMNT JOB POLLING: Missing encrypted_gcode_download_url for job {print_job_id}")
                                        continue
                                    # Essential fields for decryption are gcode_dek_package and gcode_iv_hex.
                                    # printer_kek_id is only used for the legacy PSEK path (if crypto_manager chooses that route).
                                    if not (processed_job.get('gcode_dek_package') and processed_job.get('gcode_iv_hex')):
                                        logging.error(f"LMNT JOB POLLING: Missing required crypto fields for job {print_job_id}: gcode_dek_package or gcode_iv_hex")
                                        continue
                                    # Add job to queue for processing
                                    await self._process_pending_jobs([processed_job])
                        else:
                            logging.info("LMNT JOB POLLING: No pending jobs found")
                        
                    elif response.status == 401:
                        # Token might be expired, try to refresh it
                        logging.warning("LMNT JOB POLLING: Received 401 Unauthorized, attempting to refresh token")
                        await self.integration.auth_manager.refresh_printer_token()
                        
                    else:
                        # Log other error responses
                        error_text = await response.text()
                        logging.error(f"LMNT JOB POLLING: Job polling failed with status {response.status}: {error_text}")
                        
        except aiohttp.ClientConnectorError as e:
            logging.error(f"LMNT JOB POLLING: Connection error while polling for jobs: {str(e)}")
        except aiohttp.ClientError as e:
            logging.error(f"LMNT JOB POLLING: HTTP client error while polling for jobs: {str(e)}")
        except Exception as e:
            logging.error(f"LMNT JOB POLLING: Unexpected error while polling for jobs: {str(e)}")
            import traceback
            logging.error(f"LMNT JOB POLLING: {traceback.format_exc()}")
            
            # Reset job state if an error occurred during processing
            if self.current_print_job:
                job_id = self.current_print_job.get('id')
                logging.warning(f"LMNT JOB POLLING: Resetting current job {job_id} due to error")
                self.current_print_job = None
                self.job_start_time = None
    
    async def _process_pending_jobs(self, jobs):
        """Process pending print jobs from the marketplace"""
        logging.info(f"LMNT PROCESS: Processing {len(jobs)} pending jobs")
        
        # Add new jobs to queue
        for job in jobs:
            job_id = job.get('id')
            
            # Check if job is already in queue
            if job_id and not any(j.get('id') == job_id for j in self.print_job_queue):
                self.print_job_queue.append(job)
                logging.info(f"LMNT PROCESS: Added job {job_id} to queue. Queue now has {len(self.print_job_queue)} jobs")
            else:
                logging.info(f"LMNT PROCESS: Job {job_id} already in queue or has invalid ID")
        
        # Check if we have a current print job
        if self.current_print_job:
            job_id = self.current_print_job.get('id')
            # Check if the job has been stuck for too long (more than 5 minutes)
            if hasattr(self, 'job_start_time') and self.job_start_time:
                elapsed = time.time() - self.job_start_time
                if elapsed > 300:  # 5 minutes
                    logging.warning(f"LMNT PROCESS: Job {job_id} has been processing for {elapsed:.1f} seconds without completion")
                    logging.warning(f"LMNT PROCESS: Resetting stuck job {job_id}")
                    self.current_print_job = None
                    self.job_start_time = None
                    # Continue processing
                else:
                    logging.info(f"LMNT PROCESS: Cannot process next job - printer is busy with job {job_id} for {elapsed:.1f} seconds")
                    return
            else:
                logging.info(f"LMNT PROCESS: Cannot process next job - printer is busy with job {job_id}")
                return
        
        # Check if we have jobs in the queue
        if not self.print_job_queue:
            logging.info(f"LMNT PROCESS: No jobs in queue to process")
            return
        
        # Process next job if printer is ready
        logging.info(f"LMNT PROCESS: Attempting to process next job from queue with {len(self.print_job_queue)} jobs")
        await self._process_next_job()
    
    async def handle_klippy_shutdown(self):
        """Handle Klippy shutdown event"""
        logging.info("LMNT Job Manager: Handling Klippy shutdown")
        
        # Cancel job polling task
        if self.job_polling_task and not self.job_polling_task.done():
            self.job_polling_task.cancel()
            try:
                await self.job_polling_task
            except asyncio.CancelledError:
                logging.info("Job polling task cancelled due to Klippy shutdown")
            except Exception as e:
                logging.error(f"Error cancelling job polling task: {str(e)}")
        
        # Reset state
        self.job_polling_task = None
        self.current_print_job = None
        self.print_job_started = False
        
        logging.info("LMNT Job Manager: Shutdown handling complete")
    
    async def _process_next_job(self):
        logging.info("LMNT PROCESS: _process_next_job called")
        if not self.print_job_queue:
            return
        logging.info("LMNT PROCESS: Checking if printer is ready")
        if not await self._check_printer_ready():
            logging.info("LMNT PROCESS: Printer not ready, postponing job processing")
            return
        logging.info("LMNT PROCESS: Printer is ready, proceeding with job")
        job = self.print_job_queue.pop(0)
        job_id = job.get('id')
        if not job_id:
            logging.error("LMNT PROCESS: No job ID provided")
            return
        if self.current_print_job and self.current_print_job.get('id') != job_id:
            logging.error(f"LMNT PROCESS: Another job {self.current_print_job.get('id')} is in progress")
            self.print_job_queue.insert(0, job)
            return
        logging.info(f"LMNT PROCESS: Processing job {job_id}")
        self.current_print_job = job
        await self._update_job_status(job_id, "processing")
        # Download encrypted GCode
        logging.info(f"LMNT PROCESS: Downloading encrypted GCode for job {job_id}")
        encrypted_gcode_path = await self._download_gcode(job)
        if not encrypted_gcode_path:
            logging.error(f"LMNT PROCESS: Failed to download GCode for job {job_id}")
            await self._update_job_status(job_id, "failed", "Failed to download GCode")
            self.current_print_job = None
            return
        logging.info(f"LMNT PROCESS: Sending encrypted GCode for job {job_id} to /server/encrypted/print endpoint")
        mem_fd = os.open(encrypted_gcode_path, os.O_RDONLY)
        success = await self._start_print(job, mem_fd)
        os.close(mem_fd)
        if not success:
            logging.error(f"LMNT PROCESS: Failed to start print for job {job_id}")
            await self._update_job_status(job_id, "failed", "Failed to start print")
            self.current_print_job = None
            return
        logging.info(f"LMNT PROCESS: Print started for job {job_id}")
        self.print_job_started = True
        # Optionally, clean up the encrypted file if it's no longer needed
        try:
            os.remove(encrypted_gcode_path)
            logging.info(f"LMNT PROCESS: Cleaned up encrypted file {encrypted_gcode_path}")
        except Exception as e:
            logging.warning(f"LMNT PROCESS: Failed to clean up encrypted file {encrypted_gcode_path}: {str(e)}")
    
    async def _check_printer_ready(self):
        """Check if printer is ready for a new print job"""
        logging.info("LMNT READY: Checking if printer is ready for printing")
        
        # For debugging purposes, assume printer is ready
        # Comment out this section once we've confirmed the job processing flow works
        logging.info("LMNT READY: DEVELOPMENT MODE - Assuming printer is ready for printing")
        return True
        
        # The code below is the proper implementation based on Moonraker documentation
        # Uncomment this once we've confirmed the job processing flow works
        '''
        try:
            # According to Moonraker docs, we should query webhooks, virtual_sdcard, and print_stats
            try:
                logging.info("LMNT READY: Querying printer objects to check status")
                result = await self.klippy_apis.query_objects({
                    'objects': {
                        'webhooks': None,
                        'virtual_sdcard': None,
                        'print_stats': None
                    }
                })
                
                # Check if we got a valid response
                if not result or 'status' not in result:
                    logging.info("LMNT READY: Failed to get printer status, printer not ready")
                    return False
                
                # Check if Klippy is ready
                webhooks = result.get('status', {}).get('webhooks', {})
                klippy_state = webhooks.get('state', '')
                logging.info(f"LMNT READY: Klippy state: '{klippy_state}'")
                
                if klippy_state != 'ready':
                    logging.info(f"LMNT READY: Klippy not ready: '{klippy_state}'")
                    return False
                
                # Check if printer is currently printing
                print_stats = result.get('status', {}).get('print_stats', {})
                print_state = print_stats.get('state', '')
                logging.info(f"LMNT READY: Current print_stats state: '{print_state}'")
                
                if print_state in ('printing', 'paused'):
                    logging.info("LMNT READY: Printer is busy (printing or paused)")
                    return False
                
                # If we got here, the printer is ready and not printing
                logging.info("LMNT READY: Printer is ready for printing")
                return True
            except Exception as e:
                logging.error(f"LMNT READY: Error checking printer status: {str(e)}")
                import traceback
                logging.error(f"LMNT READY: Exception traceback: {traceback.format_exc()}")
                return False
        except Exception as e:
            logging.error(f"LMNT READY: Error checking printer readiness: {str(e)}")
            import traceback
            logging.error(f"LMNT READY: Exception traceback: {traceback.format_exc()}")
            return False
        '''

    
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
        
        logging.info(f"LMNT DOWNLOAD: Starting download for job {job_id}")
        logging.info(f"LMNT DOWNLOAD: GCode URL: {gcode_url}")
        
        if not job_id:
            logging.error(f"LMNT DOWNLOAD: Invalid job data: missing ID")
            return None
        
        # Create directory for encrypted files if it doesn't exist
        if not os.path.exists(self.integration.encrypted_path):
            try:
                os.makedirs(self.integration.encrypted_path)
                logging.info(f"LMNT DOWNLOAD: Created directory for encrypted files: {self.integration.encrypted_path}")
            except Exception as e:
                logging.error(f"LMNT DOWNLOAD: Failed to create directory for encrypted files: {str(e)}")
                return None
        
        # Create filename for encrypted GCode
        encrypted_filename = f"job_{job_id}.gcode.enc"
        encrypted_filepath = os.path.join(self.integration.encrypted_path, encrypted_filename)
        
        try:
            # First try to get the job details with the file URL if we don't have it
            if not gcode_url:
                logging.info(f"LMNT DOWNLOAD: No direct gcode_url provided, fetching from job details")
                # Get the job details from the API
                job_details_url = f"{self.integration.marketplace_url}/api/get-print-job?print_job_id={job_id}"
                
                headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
                
                async with self.http_client.get(job_details_url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        gcode_url = data.get('gcode_file_url')
                        logging.info(f"LMNT DOWNLOAD: Retrieved gcode_file_url from job details: {gcode_url}")
                        
                        if not gcode_url:
                            logging.error("LMNT DOWNLOAD: No gcode_file_url found in job details")
                            return None
                    else:
                        error_text = await response.text()
                        logging.error(f"LMNT DOWNLOAD: Failed to get job details: {error_text}")
                        return None
            
            # If the URL is a GCS URL, we need to use a different approach
            if "storage.googleapis.com" in gcode_url:
                logging.info(f"LMNT DOWNLOAD: Detected GCS URL, using API proxy for download")
                # Use the API to proxy the download instead of direct GCS access
                # The correct endpoint is /api/print/download-gcode with print_job_id parameter
                download_url = f"{self.integration.marketplace_url}/api/print/download-gcode?print_job_id={job_id}"
                logging.info(f"LMNT DOWNLOAD: Using proxy download URL: {download_url}")
            else:
                download_url = gcode_url
                logging.info(f"LMNT DOWNLOAD: Using direct download URL: {download_url}")
            
            # Download encrypted GCode
            headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
            
            start_time = time.time()
            async with self.http_client.get(download_url, headers=headers) as response:
                elapsed_ms = int((time.time() - start_time) * 1000)
                logging.info(f"LMNT DOWNLOAD: Response received in {elapsed_ms}ms with status: {response.status}")
                
                if response.status == 200:
                    # Save encrypted GCode to file
                    content = await response.read()
                    content_size = len(content)
                    logging.info(f"LMNT DOWNLOAD: Downloaded {content_size} bytes of encrypted GCode")
                    
                    with open(encrypted_filepath, 'wb') as f:
                        f.write(content)
                    
                    logging.info(f"LMNT DOWNLOAD: Saved encrypted GCode to {encrypted_filepath}")
                    return encrypted_filepath
                else:
                    error_text = await response.text()
                    logging.error(f"LMNT DOWNLOAD: GCode download failed with status {response.status}: {error_text}")
                    
                    # If direct download failed, try using the API proxy
                    if download_url != f"{self.integration.marketplace_url}/api/print/download-gcode?print_job_id={job_id}":
                        logging.info(f"LMNT DOWNLOAD: Direct download failed, trying API proxy")
                        proxy_url = f"{self.integration.marketplace_url}/api/print/download-gcode?print_job_id={job_id}"
                        
                        try:
                            async with self.http_client.get(proxy_url, headers=headers) as proxy_response:
                                if proxy_response.status == 200:
                                    content = await proxy_response.read()
                                    content_size = len(content)
                                    logging.info(f"LMNT DOWNLOAD: Downloaded {content_size} bytes via API proxy")
                                    
                                    with open(encrypted_filepath, 'wb') as f:
                                        f.write(content)
                                    
                                    logging.info(f"LMNT DOWNLOAD: Saved encrypted GCode to {encrypted_filepath}")
                                    return encrypted_filepath
                                else:
                                    proxy_error = await proxy_response.text()
                                    logging.error(f"LMNT DOWNLOAD: API proxy download failed: {proxy_error}")
                        except Exception as e:
                            logging.error(f"LMNT DOWNLOAD: API proxy exception: {str(e)}")
                            
                        # If API proxy fails, try to get the job details to get the DEK and download directly from GCS
                        logging.info(f"LMNT DOWNLOAD: Trying to get job details to download directly")
                        job_details_url = f"{self.integration.marketplace_url}/api/get-print-job?print_job_id={job_id}"
                        
                        try:
                            async with self.http_client.get(job_details_url, headers=headers) as details_response:
                                if details_response.status == 200:
                                    job_details = await details_response.json()
                                    direct_url = job_details.get('gcode_file_url')
                                    
                                    if direct_url:
                                        logging.info(f"LMNT DOWNLOAD: Got direct URL from job details: {direct_url}")
                                        
                                        # Try direct download from GCS URL if possible
                                        if direct_url.startswith('https://storage.googleapis.com/'):
                                            async with self.http_client.get(direct_url) as direct_response:
                                                if direct_response.status == 200:
                                                    content = await direct_response.read()
                                                    content_size = len(content)
                                                    logging.info(f"LMNT DOWNLOAD: Downloaded {content_size} bytes directly from GCS")
                                                    
                                                    with open(encrypted_filepath, 'wb') as f:
                                                        f.write(content)
                                                    
                                                    logging.info(f"LMNT DOWNLOAD: Saved encrypted GCode to {encrypted_filepath}")
                                                    return encrypted_filepath
                                                else:
                                                    direct_error = await direct_response.text()
                                                    logging.error(f"LMNT DOWNLOAD: Direct GCS download failed: {direct_error}")
                                else:
                                    details_error = await details_response.text()
                                    logging.error(f"LMNT DOWNLOAD: Failed to get job details: {details_error}")
                        except Exception as e:
                            logging.error(f"LMNT DOWNLOAD: Job details exception: {str(e)}")
        except Exception as e:
            logging.error(f"LMNT DOWNLOAD: Error downloading GCode for job {job_id}: {str(e)}")
            import traceback
            logging.error(f"LMNT DOWNLOAD: Exception traceback: {traceback.format_exc()}")
        
        return None
    
    async def _start_print(self, job, mem_fd):
        try:
            job_id = job.get('id')
            if not job_id:
                logging.error("LMNT PRINT: No job ID provided")
                return False

            if self.current_print_job and self.current_print_job.get('id') != job_id:
                logging.error(f"LMNT PRINT: Another job {self.current_print_job.get('id')} is in progress")
                return False

            if not await self._check_printer_ready():
                logging.error("LMNT PRINT: Printer is not ready")
                return False

            self.current_print_job = job
            self.print_job_started = True
            
            # Read encrypted G-code from memfd
            memfd_file = os.fdopen(mem_fd, 'rb')
            encrypted_gcode = memfd_file.read()
            memfd_file.close()

            # Send to encrypted_print endpoint
            logging.info(f"LMNT PRINT: Sending job {job_id} to encrypted_print endpoint")
            url = "http://localhost:7125/server/encrypted/print"
            data = {
                "job_id": job_id,
                "encrypted_gcode": base64.b64encode(encrypted_gcode).decode("utf-8"),
                "gcode_dek_package": job.get("gcode_dek_package"),
                "gcode_iv_hex": job.get("gcode_iv_hex"),
                "filename": f"virtual_{job_id}_{int(time.time())}.gcode"
            }
            async with self.http_client.post(url, json=data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"LMNT PRINT: Failed to start job {job_id}: {error_text}")
                    await self._update_job_status(job_id, "failed", f"Print start error: {error_text}")
                    self.current_print_job = None
                    self.print_job_started = False
                    return False

            logging.info(f"LMNT PRINT: Successfully initiated print job {job_id}")
            return True

        except Exception as e:
            logging.error(f"LMNT PRINT: Error starting print for job {job.get('id', 'unknown')}: {str(e)}")
            if job.get('id'):
                await self._update_job_status(job.get('id'), "failed", f"Print start error: {str(e)}")
            self.current_print_job = None
            self.print_job_started = False
            return False

    async def monitor_job_control(self):
        if not WEBSOCKET_AVAILABLE:
            logging.error("LMNT MONITOR: WebSocket monitoring is disabled due to missing websocket module")
            return
        import json
        
        while True:  # Reconnection loop
            try:
                ws = websocket.WebSocket()
                ws.connect("ws://localhost:7125/websocket")
                ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "printer.objects.subscribe",
                    "params": {"objects": {"print_stats": ["state"]}},
                    "id": 2
                }))
                logging.info("LMNT MONITOR: Connected to WebSocket for print status monitoring")
                
                while True:
                    message = json.loads(ws.recv())
                    if "print_stats" in message.get("params", {}):
                        state = message["params"]["print_stats"].get("state")
                        job_id = self.current_print_job.get("id") if self.current_print_job else None
                        if job_id:
                            if state == "paused":
                                await self._update_job_status(job_id, "paused", "Print paused")
                            elif state == "printing":
                                await self._update_job_status(job_id, "printing", "Print resumed")
                            elif state == "complete":
                                await self._update_job_status(job_id, "completed", "Print completed successfully")
                                self.current_print_job = None
                                self.print_job_started = False
                                logging.info(f"LMNT MONITOR: Print job {job_id} completed")
                            elif state in ["error", "cancelled"]:
                                await self._update_job_status(job_id, "failed", f"Print {state}")
                                self.current_print_job = None
                                self.print_job_started = False
                                logging.info(f"LMNT MONITOR: Print job {job_id} {state}")
            except Exception as e:
                logging.error(f"LMNT MONITOR: Error in job control monitoring: {str(e)}")
                try:
                    ws.close()
                except:
                    pass
                # Wait before reconnecting
                await asyncio.sleep(5)
                logging.info("LMNT MONITOR: Attempting to reconnect to WebSocket...")
    
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
            
            # Map plugin status to API-compliant status
            api_status = status
            if status == 'completed':
                api_status = 'success'
            elif status in ['failed', 'cancelled']:
                api_status = 'failure'
            elif status == 'printing': # Printing is a form of processing
                api_status = 'processing'
            # 'processing' maps to 'processing'
            
            payload = {"status": api_status}
            
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
    
    async def _monitor_print_progress(self, job_id):
        """Monitor print progress and update job status"""
        logging.info(f"LMNT MONITOR: Starting print progress monitoring for job {job_id}")
        
        max_attempts = 3
        attempt = 0
        retry_delay = 2  # seconds
        monitor_interval = 10  # seconds between progress checks after initial connection
        
        try:
            # First, try to access the printer component
            printer_accessible = False
            server_available = hasattr(self, 'server')
            if not server_available:
                logging.warning(f"LMNT MONITOR: Server attribute not found on JobManager for job {job_id}. Skipping printer component check.")
            else:
                while attempt < max_attempts:
                    attempt += 1
                    try:
                        printer = self.server.lookup_component("printer", None)
                        if printer is None:
                            raise ValueError("Component (printer) not found")
                        
                        print_stats = printer.get_print_stats()
                        if print_stats is None:
                            raise ValueError("Object (print_stats) not found")
                        
                        logging.info(f"LMNT MONITOR: Successfully accessed printer component for job {job_id}")
                        printer_accessible = True
                        break
                    except Exception as e:
                        logging.error(f"LMNT MONITOR: Exception on attempt {attempt} to get printer/print_stats for job {job_id}: {str(e)}. Retrying in {retry_delay}s...")
                        if attempt < max_attempts:
                            await asyncio.sleep(retry_delay)
                        else:
                            logging.warning(f"LMNT MONITOR: Failed to get 'printer' component or 'print_stats' object after {max_attempts} attempts for job {job_id}. Falling back to alternative monitoring.")
        
            # If printer component is accessible, use it for monitoring
            if printer_accessible:
                while True:
                    try:
                        printer = self.server.lookup_component("printer")
                        print_stats = printer.get_print_stats()
                        state = print_stats.get("state", "unknown")
                        progress = print_stats.get("progress", 0.0)
                        logging.info(f"LMNT MONITOR: Print job {job_id} state: {state}, progress: {progress*100:.1f}%")
                        
                        # Update job status based on printer state
                        if state == "complete":
                            await self._update_job_status(job_id, "completed", "Print job completed")
                            self.current_print_job = None
                            logging.info(f"LMNT MONITOR: Print job {job_id} completed")
                            return
                        elif state in ["error", "cancelled"]:
                            await self._update_job_status(job_id, "failed", f"Print job {state}")
                            self.current_print_job = None
                            logging.error(f"LMNT MONITOR: Print job {job_id} failed with state: {state}")
                            return
                        
                        # Wait before checking again
                        await asyncio.sleep(monitor_interval)
                    except Exception as e:
                        logging.error(f"LMNT MONITOR: Error monitoring print progress for job {job_id}: {str(e)}")
                        await asyncio.sleep(monitor_interval)
            else:
                # Fallback to querying Klipper API directly for status if printer component is not accessible
                logging.info(f"LMNT MONITOR: Using fallback Klipper API query for job {job_id} monitoring")
                while True:
                    try:
                        # Check if klippy_apis has get_status method
                        if hasattr(self.klippy_apis, 'get_status'):
                            status_response = await self.klippy_apis.get_status()
                            if status_response and 'result' in status_response:
                                print_stats = status_response['result'].get('print_stats', {})
                                state = print_stats.get('state', 'unknown')
                                progress = print_stats.get('progress', 0.0)
                                logging.info(f"LMNT MONITOR: Fallback API - Print job {job_id} state: {state}, progress: {progress*100:.1f}%")
                                
                                if state == "complete":
                                    await self._update_job_status(job_id, "completed", "Print job completed")
                                    self.current_print_job = None
                                    logging.info(f"LMNT MONITOR: Print job {job_id} completed via fallback API")
                                    return
                                elif state in ["error", "cancelled"]:
                                    await self._update_job_status(job_id, "failed", f"Print job {state}")
                                    self.current_print_job = None
                                    logging.error(f"LMNT MONITOR: Print job {job_id} failed with state: {state} via fallback API")
                                    return
                            else:
                                logging.warning(f"LMNT MONITOR: Fallback API query failed or returned no status for job {job_id}")
                        else:
                            logging.warning(f"LMNT MONITOR: KlippyAPI does not have get_status method for job {job_id}. Cannot monitor progress.")
                            # Exit monitoring loop since no further monitoring is possible
                            return
                    
                        await asyncio.sleep(monitor_interval)
                    except Exception as e:
                        logging.error(f"LMNT MONITOR: Fallback API error monitoring print progress for job {job_id}: {str(e)}")
                        await asyncio.sleep(monitor_interval)
        except Exception as e:
            logging.error(f"LMNT MONITOR: Unexpected error in print progress monitoring for job {job_id}: {str(e)}")
            logging.error(f"LMNT MONITOR: Marking job {job_id} as failed due to unexpected monitoring error")
            await self._update_job_status(job_id, "failed", "Unexpected error in monitoring")
            self.current_print_job = None
    
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

    async def get_status(self):
        """
        Get the current job status
        
        Returns:
            dict: Job status information
        """
        status = {
            "current_job": self.current_print_job,
            "queue_length": len(self.print_job_queue),
            "job_started": self.print_job_started,
            "last_check": datetime.now().isoformat()
        }
        return status