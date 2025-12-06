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
from tornado.websocket import websocket_connect

# Import print service data classes
from .print_service import PrintJob, PrintResult

# Using Tornado's native WebSocket client for async compatibility

class JobManager:
    """
    Manages print jobs for LMNT Marketplace
    
    Handles job start, status updates, print state event handling,
    and job queue management.
    """
    
    def __init__(self, integration):
        """Initialize the Job Manager"""
        self.integration = integration
        self.server = self.integration.server
        self.print_job_queue = []
        self.current_print_job = None
        self.print_job_started = False
        self.job_polling_task = None
        
        # Polling rate limiting state
        self.last_poll_time = 0
        self.consecutive_poll_errors = 0
        self.MIN_POLL_INTERVAL = 5.0  # Minimum seconds between polls
        self.MAX_BACKOFF = 300.0      # Maximum backoff in seconds (5 minutes)
        
        # References to other managers
        self.http_client = None
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
        """Set up periodic polling and Firebase listening for print jobs"""
        logging.info("LMNT JOB POLLING: setup_job_polling method called")
        
        # Cancel any existing polling task
        if self.job_polling_task:
            self.job_polling_task.cancel()
            logging.info("Previous job polling task cancelled")
            
        # Cancel any existing firebase listener task
        if hasattr(self, 'firebase_listener_task') and self.firebase_listener_task:
            self.firebase_listener_task.cancel()
            logging.info("Previous firebase listener task cancelled")
        
        # Get poll interval from config (already parsed in integration)
        poll_interval = self.integration.check_interval
        
        if poll_interval > 0:
            logging.info(f"Setting up job polling with interval of {poll_interval} seconds")
            # Start polling task (fallback)
            try:
                self.job_polling_task = asyncio.create_task(self._poll_for_jobs_loop(poll_interval))
                logging.info("LMNT JOB POLLING: Polling task created successfully")
            except Exception as e:
                logging.error(f"LMNT JOB POLLING: Failed to create polling task: {str(e)}")
                import traceback
                logging.error(f"LMNT JOB POLLING: {traceback.format_exc()}")
        else:
            logging.info(f"LMNT JOB POLLING: Polling disabled (interval={poll_interval})")
            
        # Start Firebase listener task
        try:
            self.firebase_listener_task = asyncio.create_task(self._listen_to_firebase())
            logging.info("LMNT JOB POLLING: Firebase listener task created successfully")
        except Exception as e:
            logging.error(f"LMNT JOB POLLING: Failed to create firebase listener task: {str(e)}")
            import traceback
            logging.error(f"LMNT JOB POLLING: {traceback.format_exc()}")
        
        logging.info("Job polling and listening started")

    async def _listen_to_firebase(self):
        """Listen to Firebase Realtime Database for job signals"""
        logging.info("LMNT FIREBASE: _listen_to_firebase started")
        
        while True:
            try:
                printer_id = self.integration.auth_manager.printer_id
                if not printer_id:
                    logging.info("LMNT FIREBASE: No printer ID yet, waiting...")
                    await asyncio.sleep(5)
                    continue
                
                # Use the configured project ID
                project_id = self.integration.firebase_project_id
                # Construct the URL for the printer's queue
                url = f"https://{project_id}-default-rtdb.firebaseio.com/printers/{printer_id}/queue.json"
                
                logging.info(f"LMNT FIREBASE: Connecting to {url}")
                
                headers = {'Accept': 'text/event-stream'}
                
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=None)
                
                async with self.http_client.get(url, headers=headers, timeout=timeout) as response:
                    logging.info(f"LMNT FIREBASE: Connected with status {response.status}")
                    
                    if response.status == 200:
                        async for line in response.content:
                            if not line:
                                continue
                                
                            line = line.decode('utf-8').strip()
                            if not line:
                                continue
                                
                            # logging.debug(f"LMNT FIREBASE: Received: {line}")
                            
                            if line.startswith("event: put") or line.startswith("event: patch"):
                                logging.info("LMNT FIREBASE: Received update signal, triggering poll")
                                # Trigger a poll immediately
                                asyncio.create_task(self._poll_for_jobs())
                            elif line.startswith("data: "):
                                # We could parse the data, but we just treat any data as a signal to poll
                                pass
                                
                    elif response.status == 401:
                        logging.error("LMNT FIREBASE: Unauthorized (401). Check security rules.")
                        await asyncio.sleep(60) # Wait longer before retry
                    else:
                        logging.error(f"LMNT FIREBASE: Connection failed with status {response.status}")
                        await asyncio.sleep(10)
                        
            except asyncio.CancelledError:
                logging.info("LMNT FIREBASE: Listener cancelled")
                break
            except Exception as e:
                logging.error(f"LMNT FIREBASE: Error in listener: {str(e)}")
                await asyncio.sleep(10)

    
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
                
                # Only poll if we have a present (and not expired) printer token
                if self.integration.auth_manager.printer_token:
                    # Proactively verify the token's expiry using AuthManager helpers
                    try:
                        token = self.integration.auth_manager.printer_token
                        expiry = self.integration.auth_manager._get_token_expiry_from_jwt(token)
                        now = self.integration.auth_manager._get_timezone_aware_now()
                        
                        if expiry is None:
                            logging.warning("LMNT JOB POLLING: Printer token present but expiry unknown; invoking refresh check")
                            # Ask auth manager to evaluate and schedule appropriate action
                            self.integration.auth_manager.check_token_refresh()
                            # Proceed with a single poll attempt; server will reject if invalid
                        else:
                            cmp = self.integration.auth_manager._safe_datetime_comparison(expiry, now)
                            if cmp is None or cmp <= 0:
                                logging.warning(
                                    f"LMNT JOB POLLING: Printer token present but expired (exp={expiry}); skipping poll and triggering re-registration"
                                )
                                # Trigger expired-token handling and skip this poll cycle
                                asyncio.create_task(self.integration.auth_manager._handle_expired_token())
                                logging.info(f"LMNT JOB POLLING: Waiting {poll_interval} seconds until next job poll")
                                await asyncio.sleep(poll_interval)
                                continue
                            else:
                                # Token is valid - call check_token_refresh() to trigger proactive renewal
                                # if token is approaching expiration (within 7 days or 80% of lifetime)
                                self.integration.auth_manager.check_token_refresh()
                    except Exception as e:
                        logging.warning(f"LMNT JOB POLLING: Pre-poll token check failed: {e}")

                    logging.info(f"LMNT JOB POLLING: Polling for jobs with present printer token")
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
        # Rate limiting check
        now = time.time()
        
        # Calculate backoff duration: MIN_POLL_INTERVAL + (2^errors * 1s)
        backoff_duration = self.MIN_POLL_INTERVAL
        if self.consecutive_poll_errors > 0:
            exponential_backoff = min(self.MAX_BACKOFF, 2 ** self.consecutive_poll_errors)
            backoff_duration += exponential_backoff
            
        time_since_last_poll = now - self.last_poll_time
        
        if time_since_last_poll < backoff_duration:
            logging.info(f"LMNT JOB POLLING: Skipping poll due to rate limiting/backoff. "
                         f"Last poll was {time_since_last_poll:.1f}s ago, required wait: {backoff_duration:.1f}s "
                         f"(Errors: {self.consecutive_poll_errors})")
            return

        logging.info("LMNT JOB POLLING: _poll_for_jobs method called")
        self.last_poll_time = now
        
        # Check if we have a valid printer token and ID
        printer_id = self.integration.auth_manager.printer_id
        if not printer_id:
            logging.error("LMNT JOB POLLING: Cannot poll for jobs - no printer ID available")
            return
        
        # Get the API endpoint URL
        api_url = f"{self.integration.marketplace_url}/api/printer-agent/poll-print-queue"
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
            logging.debug(f"LMNT JOB POLLING: HTTP client connector info: {self.http_client.connector}")
            
            # Record the start time for timing the request
            start_time = time.time()
            
            # Make the API request using shared HTTP client
            async with self.http_client.get(api_url, headers=headers) as response:
                logging.debug(f"LMNT JOB POLLING: Response object created successfully")
                # Calculate the response time
                response_time = int((time.time() - start_time) * 1000)  # Convert to milliseconds
                
                # Log the response status
                logging.info(f"LMNT JOB POLLING: Response received in {response_time}ms with status: {response.status}")
                
                # Handle different response statuses
                if response.status == 200:
                    # Success! Reset error counter
                    self.consecutive_poll_errors = 0
                    
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
                    # Token is invalid or expired. The printer may need to be re-registered.
                    # Increment error counter
                    self.consecutive_poll_errors += 1
                    
                    error_text = await response.text()
                    logging.error(f"LMNT JOB POLLING: Received 401 Unauthorized. The printer token is invalid and may need to be re-registered. Details: {error_text}")
                    # Trigger expired-token handling so the system can refresh or re-register automatically
                    try:
                        await self.integration.auth_manager._handle_expired_token()
                    except Exception as e:
                        logging.error(f"LMNT JOB POLLING: Error invoking expired-token handler: {e}")
                    
                else:
                    # Log other error responses
                    # Increment error counter
                    self.consecutive_poll_errors += 1
                    
                    error_text = await response.text()
                    logging.error(f"LMNT JOB POLLING: Job polling failed with status {response.status}: {error_text}")
                        
        except aiohttp.ClientConnectorError as e:
            self.consecutive_poll_errors += 1
            logging.error(f"LMNT JOB POLLING: Connection error while polling for jobs: {str(e)}")
        except aiohttp.ClientError as e:
            self.consecutive_poll_errors += 1
            logging.error(f"LMNT JOB POLLING: HTTP client error while polling for jobs: {str(e)}")
        except Exception as e:
            self.consecutive_poll_errors += 1
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
            
            # Check actual printer state to see if it's really busy
            try:
                if self.klippy_apis:
                    result = await self.klippy_apis.query_objects({'print_stats': None})
                    if result and 'print_stats' in result:
                        printer_state = result['print_stats'].get('state', 'unknown')
                        logging.info(f"LMNT PROCESS: Current printer state: {printer_state}, current job: {job_id}")
                        
                        # If printer is idle/standby, clear the stale job reference
                        if printer_state in ['idle', 'standby', 'ready']:
                            logging.info(f"LMNT PROCESS: Printer is {printer_state}, clearing stale job reference for {job_id}")
                            self.current_print_job = None
                            self.job_start_time = None
                        elif printer_state in ['printing', 'paused']:
                            # Printer is actually busy
                            if hasattr(self, 'job_start_time') and self.job_start_time:
                                elapsed = time.time() - self.job_start_time
                                if elapsed > 300:  # 5 minutes
                                    logging.warning(f"LMNT PROCESS: Job {job_id} has been processing for {elapsed:.1f} seconds without completion")
                                    logging.warning(f"LMNT PROCESS: Resetting stuck job {job_id}")
                                    self.current_print_job = None
                                    self.job_start_time = None
                                else:
                                    logging.info(f"LMNT PROCESS: Cannot process next job - printer is busy with job {job_id} for {elapsed:.1f} seconds")
                                    return
                            else:
                                logging.info(f"LMNT PROCESS: Cannot process next job - printer is busy with job {job_id}")
                                return
            except Exception as e:
                logging.error(f"LMNT PROCESS: Error checking printer state: {e}")
                # If we can't check state, fall back to time-based logic
                if hasattr(self, 'job_start_time') and self.job_start_time:
                    elapsed = time.time() - self.job_start_time
                    if elapsed > 300:  # 5 minutes
                        logging.warning(f"LMNT PROCESS: Resetting potentially stuck job {job_id} after {elapsed:.1f} seconds")
                        self.current_print_job = None
                        self.job_start_time = None
        
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
        
        # Cancel firebase listener task
        if hasattr(self, 'firebase_listener_task') and self.firebase_listener_task and not self.firebase_listener_task.done():
            self.firebase_listener_task.cancel()
            try:
                await self.firebase_listener_task
            except asyncio.CancelledError:
                logging.info("Firebase listener task cancelled due to Klippy shutdown")
            except Exception as e:
                logging.error(f"Error cancelling firebase listener task: {str(e)}")
        
        # Reset state
        self.job_polling_task = None
        self.firebase_listener_task = None
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
        
        # Stream encrypted GCode directly to memory (never touches disk)
        logging.info(f"LMNT PROCESS: Streaming encrypted GCode for job {job_id}")
        mem_fd = await self._stream_encrypted_gcode_to_memfd(job)
        if not mem_fd:
            logging.error(f"LMNT PROCESS: Failed to stream encrypted GCode for job {job_id}")
            await self._update_job_status(job_id, "failed", "Failed to stream encrypted GCode")
            self.current_print_job = None
            return
        
        logging.info(f"LMNT PROCESS: Starting print for job {job_id}")
        success = await self._start_print(job, mem_fd)
        # Note: mem_fd is closed by _start_print() via os.fdopen(), so no need to close it here
        if not success:
            logging.error(f"LMNT PROCESS: Failed to start print for job {job_id}")
            await self._update_job_status(job_id, "failed", "Failed to start print")
            self.current_print_job = None
            return
        logging.info(f"LMNT PROCESS: Print started for job {job_id}")
        self.print_job_started = True
    
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
                # The correct endpoint is /api/printer-agent/download-gcode with print_job_id parameter
                download_url = f"{self.integration.marketplace_url}/api/printer-agent/download-gcode?print_job_id={job_id}"
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
                    if download_url != f"{self.integration.marketplace_url}/api/printer-agent/download-gcode?print_job_id={job_id}":
                        logging.info(f"LMNT DOWNLOAD: Direct download failed, trying API proxy")
                        proxy_url = f"{self.integration.marketplace_url}/api/printer-agent/download-gcode?print_job_id={job_id}"
                        
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
    
    async def _stream_encrypted_gcode_to_memfd(self, job):
        """
        Stream encrypted GCode directly from API to memfd without disk storage
        
        Args:
            job (dict): Job information including ID and URL
            
        Returns:
            int: File descriptor of memfd containing encrypted GCode
            None: If streaming failed
        """
        job_id = job.get('id')
        gcode_url = job.get('gcode_url')
        
        logging.info(f"LMNT STREAM: Starting secure stream for job {job_id}")
        
        if not job_id:
            logging.error(f"LMNT STREAM: Invalid job data: missing ID")
            return None
        
        try:
            # Get download URL (same logic as _download_gcode)
            if not gcode_url:
                logging.info(f"LMNT STREAM: Fetching gcode_url from job details")
                job_details_url = f"{self.integration.marketplace_url}/api/get-print-job?print_job_id={job_id}"
                headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
                
                async with self.http_client.get(job_details_url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        gcode_url = data.get('gcode_file_url')
                        if not gcode_url:
                            logging.error("LMNT STREAM: No gcode_file_url found in job details")
                            return None
                    else:
                        error_text = await response.text()
                        logging.error(f"LMNT STREAM: Failed to get job details: {error_text}")
                        return None
            
            # Determine download URL (same logic as _download_gcode)
            if "storage.googleapis.com" in gcode_url:
                download_url = f"{self.integration.marketplace_url}/api/printer-agent/download-gcode?print_job_id={job_id}"
            else:
                download_url = gcode_url
            
            # Stream encrypted data directly to memfd
            headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
            
            start_time = time.time()
            async with self.http_client.get(download_url, headers=headers) as response:
                elapsed_ms = int((time.time() - start_time) * 1000)
                logging.info(f"LMNT STREAM: Response received in {elapsed_ms}ms with status: {response.status}")
                
                if response.status == 200:
                    # Read encrypted content and save to memfd
                    encrypted_data = await response.read()
                    content_size = len(encrypted_data)
                    logging.info(f"LMNT STREAM: Streamed {content_size} bytes of encrypted GCode to memory")
                    
                    # Create memfd and write encrypted data
                    memfd = os.memfd_create(f"encrypted_gcode_{job_id}", 0)
                    os.write(memfd, encrypted_data)
                    os.lseek(memfd, 0, os.SEEK_SET)  # Reset to beginning for reading
                    
                    logging.info(f"LMNT STREAM: Successfully saved encrypted job {job_id} to memfd")
                    return memfd
                else:
                    error_text = await response.text()
                    logging.error(f"LMNT STREAM: Stream failed with status {response.status}: {error_text}")
                    return None
                    
        except Exception as e:
            logging.error(f"LMNT STREAM: Error streaming job {job_id}: {str(e)}")
            return None

    async def _start_print(self, job, encrypted_memfd):
        start_time = time.time()
        job_id = job.get('id')
        try:
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
            memfd_file = os.fdopen(encrypted_memfd, 'rb')
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
                elapsed = time.time() - start_time
                response_text = await response.text()
                logging.info(
                    f"LMNT PRINT: Encrypted print endpoint response for job {job_id} - "
                    f"Status: {response.status}, Elapsed: {elapsed:.2f}s, Body: {response_text}"
                )
                
                if response.status == 200:
                    try:
                        response_data = json.loads(response_text)
                        # Handle nested result structure: {"result": {"status": "ok", ...}}
                        result = response_data.get('result', response_data)
                        if result.get('status') == 'ok':
                            logging.info(
                                f"LMNT PRINT: Successfully started job {job_id} "
                                f"in {elapsed:.2f}s, beginning progress monitoring."
                            )
                            # Start monitoring for marketplace status reporting
                            asyncio.create_task(self._monitor_print_progress(job_id))
                            return True
                        else:
                            logging.error(f"LMNT PRINT: Encrypted print endpoint returned error: {response_data}")
                            await self._update_job_status(job_id, "failed", f"Print start error: {response_data}")
                            self.current_print_job = None
                            self.print_job_started = False
                            return False
                    except json.JSONDecodeError as e:
                        logging.error(f"LMNT PRINT: Failed to parse response JSON after {elapsed:.2f}s: {e}")
                        await self._update_job_status(job_id, "failed", f"Invalid response format: {response_text}")
                        self.current_print_job = None
                        self.print_job_started = False
                        return False
                else:
                    logging.error(
                        f"LMNT PRINT: Failed to start job {job_id} - Status: {response.status}, "
                        f"Elapsed: {elapsed:.2f}s, Response: {response_text}"
                    )
                    await self._update_job_status(job_id, "failed", f"HTTP {response.status}: {response_text}")
                    self.current_print_job = None
                    self.print_job_started = False
                    return False

            return True

        except Exception as e:
            elapsed = time.time() - start_time
            logging.error(
                f"LMNT PRINT: Error starting print for job {job_id or job.get('id', 'unknown')} "
                f"after {elapsed:.2f}s: {e!r}"
            )
            import traceback
            logging.error(f"LMNT PRINT: Exception traceback for job {job_id}: {traceback.format_exc()}")
            if job_id:
                await self._update_job_status(job_id, "failed", f"Print start error after {elapsed:.2f}s: {e!r}")
            self.current_print_job = None
            self.print_job_started = False
            return False

    async def _monitor_print_progress(self, job_id):
        """Monitor print progress using simple API polling"""
        logging.info(f"LMNT MONITOR: Starting print progress monitoring for job {job_id}")
        
        last_state = None
        last_report_time = time.time()
        consecutive_errors = 0
        max_errors = 5
        
        while self.current_print_job and self.current_print_job.get('id') == job_id:
            try:
                # Query print stats directly from Klipper
                if not self.klippy_apis:
                    logging.error(f"LMNT MONITOR: No Klippy APIs available for job {job_id}")
                    break
                    
                result = await self.klippy_apis.query_objects({'print_stats': None})
                if not result or 'print_stats' not in result:
                    consecutive_errors += 1
                    if consecutive_errors >= max_errors:
                        logging.error(f"LMNT MONITOR: Too many API errors, stopping monitoring for job {job_id}")
                        break
                    await asyncio.sleep(10)
                    continue
                
                consecutive_errors = 0  # Reset error count on success
                print_stats = result['print_stats']
                state = print_stats.get('state', 'unknown')
                filament_used = print_stats.get('filament_used', 0.0)
                print_duration = print_stats.get('print_duration', 0.0)
                total_duration = print_stats.get('total_duration', 0.0)
                filename = print_stats.get('filename', '')
                
                # Calculate progress percentage
                progress_pct = 0.0
                if total_duration > 0:
                    progress_pct = (print_duration / total_duration) * 100
                
                # Only update marketplace on state changes, or periodic heartbeat
                current_time = time.time()
                should_report = False
                
                if last_state != state:
                    logging.info(f"LMNT MONITOR: Job {job_id} state changed: {last_state} -> {state}")
                    should_report = True
                elif current_time - last_report_time > 30: # 30s heartbeat
                    logging.info(f"LMNT MONITOR: sending heartbeat for job {job_id} at {progress_pct:.1f}%")
                    should_report = True
                
                if should_report:
                    last_report_time = current_time
                    
                    if state == 'printing':
                         # If it's just a heartbeat, message provides context
                         msg = "Print started" if last_state != 'printing' else f"Printing: {progress_pct:.1f}%"
                         await self._update_job_status(job_id, 'printing', msg)
                    elif state == 'paused':
                        await self._update_job_status(job_id, 'paused', f"Print paused at {progress_pct:.1f}%")
                    elif state == 'complete':
                        logging.info(f"LMNT MONITOR: Print job {job_id} completed successfully")
                        stats = {
                            'filament_used': filament_used,
                            'print_duration': print_duration,
                            'total_duration': total_duration
                        }
                        logging.info(f"LMNT MONITOR: Collected stats for {job_id}: {stats}")
                        await self._update_job_status(job_id, 'completed', "Print completed successfully", stats=stats)
                        self.current_print_job = None
                        break
                    elif state in ['error', 'cancelled']:
                        logging.warning(f"LMNT MONITOR: Print job {job_id} failed with state: {state}")
                        await self._update_job_status(job_id, 'failed', f"Print {state}")
                        self.current_print_job = None
                        break
                    elif state == 'idle' and last_state in ['printing', 'paused']:
                        # Print finished but we missed the complete state
                        logging.info(f"LMNT MONITOR: Print job {job_id} appears to have completed (idle after printing)")
                        stats = {
                            'filament_used': filament_used,
                            'print_duration': print_duration,
                            'total_duration': total_duration
                        }
                        logging.info(f"LMNT MONITOR: Collected stats for {job_id} (idle fallback): {stats}")
                        await self._update_job_status(job_id, 'completed', "Print completed", stats=stats)
                        self.current_print_job = None
                        break
                    
                    last_state = state
                
                # Wait before next check
                await asyncio.sleep(10)
                
            except Exception as e:
                consecutive_errors += 1
                logging.error(f"LMNT MONITOR: Error monitoring job {job_id}: {e}")
                if consecutive_errors >= max_errors:
                    logging.error(f"LMNT MONITOR: Too many consecutive errors, stopping monitoring for job {job_id}")
                    break
                await asyncio.sleep(10)
        
        logging.info(f"LMNT MONITOR: Stopped monitoring job {job_id}")
    
    async def _fallback_status_check(self, job_id):
        """Fallback status check when WebSocket monitoring fails"""
        try:
            logging.info(f"LMNT MONITOR: Performing fallback status check for job {job_id}")
            # Try to get current printer status via Klippy APIs
            if self.klippy_apis:
                result = await self.klippy_apis.query_objects({'print_stats': None})
                if result and 'print_stats' in result:
                    state = result['print_stats'].get('state', 'unknown')
                    progress = result['print_stats'].get('progress', 0) * 100
                    logging.info(f"LMNT MONITOR: Fallback status - Job {job_id}: {state} at {progress:.1f}%")
                    
                    if state == 'complete':
                        await self._update_job_status(job_id, 'completed', "Print completed (fallback check)")
                        self.current_print_job = None
                    elif state in ['error', 'cancelled']:
                        await self._update_job_status(job_id, 'failed', f"Print {state} (fallback check)")
                        self.current_print_job = None
                    else:
                        await self._update_job_status(job_id, 'printing', f"Progress: {progress:.1f}% (fallback check)")
        except Exception as e:
            logging.error(f"LMNT MONITOR: Fallback status check failed for job {job_id}: {e}")
    
    async def _update_job_status(self, job_id, status, message=None, stats=None):
        """
        Update job status in the marketplace
        
        Args:
            job_id (str): Job ID
            status (str): New status ('processing', 'printing', 'completed', 'failed', 'cancelled')
            status (str): New status ('processing', 'printing', 'completed', 'failed', 'cancelled')
            message (str, optional): Status message
            stats (dict, optional): Print statistics (filament_used, print_duration, total_duration)
            
        Returns:
            bool: True if status update was successful, False otherwise
        """
        if not job_id:
            logging.error("Cannot update job status: Missing job ID")
            return False
        
        if not self.integration.auth_manager.printer_token:
            logging.error("Cannot update job status: No printer token available")
            return False
        
        update_url = f"{self.integration.marketplace_url}/api/printer-agent/{self.integration.api_version}/job-status/{job_id}"
        
        try:
            headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
            
            # Map plugin status to API-compliant status
            api_status = status
            if status == 'completed':
                api_status = 'success'
            elif status in ['failed', 'cancelled']:
                api_status = 'failure'
            elif status == 'printing': # Printing is a form of processing
                api_status = 'printing'
            # 'processing' maps to 'processing'
            
            payload = {"status": api_status}
            
            # Add stats if provided
            if stats:
                logging.info(f"Adding stats to payload for {job_id}: {stats}")
                payload.update(stats)
            else:
                logging.warning(f"No stats provided for {job_id}")
            
            if message:
                payload["message"] = message
            
            logging.info(f"Sending job update payload for {job_id}: {payload}")
            
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
    
    async def _report_print_status(self, job, status, error_message=None):
        """Report the final print status back to the marketplace."""
        if not job:
            logging.error("Cannot report print status: Missing job details")
            return

        job_id = job.get('id')
        purchase_id = job.get('purchase_id')
        user_id = job.get('user_id')

        if not all([job_id, purchase_id, user_id]):
            logging.error(
                f"Cannot report print status for job {job_id}: "
                "Missing id, purchase_id, or user_id."
            )
            return

        if not self.integration.auth_manager.printer_token:
            logging.error(
                f"Cannot report print status for job {job_id}: "
                "No printer token available"
            )
            return

        report_url = f"{self.integration.marketplace_url}/api/report-print-status"
        payload = {
            "user_id": user_id,
            "purchase_id": purchase_id,
            "print_job_id": job_id,
            "status": status,
        }
        if status == 'failure' and error_message:
            payload['error'] = error_message

        try:
            headers = {
                "Authorization": f"Bearer {self.integration.auth_manager.printer_token}"
            }
            logging.info(f"Reporting print status for job {job_id}: {payload}")
            async with self.http_client.post(
                report_url, headers=headers, json=payload
            ) as response:
                if response.status == 200:
                    logging.info(
                        f"Successfully reported print status for job {job_id} as {status}"
                    )
                else:
                    error_text = await response.text()
                    logging.error(
                        f"Failed to report print status for job {job_id}. "
                        f"Status: {response.status}, Response: {error_text}"
                    )
        except Exception as e:
            logging.error(
                f"Exception while reporting print status for job {job_id}: {str(e)}"
            )
    

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
            await self._update_job_status(job_id, 'printing', 'Starting job')
            
            # Download and decrypt GCode
            encrypted_filepath = await self._download_gcode(job)
            
            if not encrypted_filepath:
                self.current_print_job = None
                raise web_request.error(
                    "Failed to download GCode", 500)
            
            # Start printing in background task to avoid blocking response
            asyncio.create_task(self._start_print(job, encrypted_filepath))
            
            return {
                "status": "printing",
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