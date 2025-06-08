# LMNT Marketplace Plugin for Moonraker
# Integrates 3D printers with the LMNT Marketplace for secure model printing
import os
import json
import logging
import asyncio
import aiohttp
import binascii
import time
import uuid
import re
import base64
from cryptography.fernet import Fernet, InvalidToken
from datetime import datetime, timedelta
from moonraker.common import RequestType

# Import Google Cloud PubSub client library
try:
    from google.cloud import pubsub_v1
    PUBSUB_AVAILABLE = True
except ImportError:
    PUBSUB_AVAILABLE = False

class LmntMarketplacePlugin:
    """
    LMNT Marketplace Plugin for Moonraker
    
    This plugin provides integration between 3D printers running Klipper/Moonraker
    and the LMNT Marketplace. It handles printer registration, authentication,
    secure file transfer, and print job management.
    """
    
    def __init__(self, config):
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        
        # Configuration
        self.marketplace_url = config.get('marketplace_url', 
                                         'https://api.lmnt.market')
        self.cws_url = config.get('cws_url', 'https://cws.lmnt.market')
        self.api_version = config.get('api_version', 'v1')
        self.debug = config.getboolean('debug', False)
        
        # Setup logging
        self.setup_logging()
        logging.info("LMNT Marketplace Plugin initialized")
        
        # Set up directories
        self.setup_directories()
        
        # Initialize components
        self.file_manager = self.server.lookup_component('file_manager', None)
        self.klippy_apis = None
        self.http_client = None
        
        # State variables
        self.printer_registered = False
        self.printer_token = None
        self.token_expiry = None
        self.user_token = None  # Temporary storage for user JWT during registration
        self.printer_id = None
        self.print_job_queue = []
        self.current_print_job = None
        self.print_job_started = False
        
        # PubSub related attributes
        self.pubsub_subscription_name = None
        self.pubsub_subscriber = None
        self.pubsub_future = None
        self.pubsub_thread = None
        self.pubsub_executor = None
        
        # Register endpoints and event handlers
        self.register_endpoints()
        self.register_event_handlers()
        
        # Schedule token refresh check and job polling
        self.eventloop.register_callback(self.check_token_refresh)
        self.setup_job_polling()
    
    def setup_logging(self):
        """Configure logging for the plugin"""
        log_level = logging.DEBUG if self.debug else logging.INFO
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    def setup_directories(self):
        """Set up necessary directories for the plugin"""
        
        # Create secure storage directory
        home = os.path.expanduser("~")
        self.secure_storage_path = os.path.join(home, "printer_data", "lmnt_marketplace")
        os.makedirs(self.secure_storage_path, exist_ok=True)
        
        # Create directory for downloaded gcode files
        self.gcodes_path = os.path.join(home, "printer_data", "gcodes", "lmnt_marketplace")
        os.makedirs(self.gcodes_path, exist_ok=True)
        
        # Create directory for keys
        self.keys_path = os.path.join(self.secure_storage_path, "keys")
        os.makedirs(self.keys_path, exist_ok=True)
        
        logging.info(f"Created directories: {self.secure_storage_path}, {self.gcodes_path}, {self.keys_path}")
    
    def register_endpoints(self):
        """Register API endpoints for the plugin"""
        try:
            # Local UI endpoints for configuration and manual actions
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/status", 
                RequestType.GET, 
                self._handle_local_status
            )
            
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/config", 
                RequestType.GET, 
                self._handle_get_config
            )
            
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/config", 
                RequestType.POST, 
                self._handle_set_config
            )
            
            # User authentication and printer registration endpoints
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/user_login", 
                RequestType.POST, 
                self._handle_user_login
            )
            
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/register_printer", 
                RequestType.POST, 
                self._handle_register_printer
            )
            
            # Manual job check endpoint (for local UI use only)
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/check_jobs", 
                RequestType.POST, 
                self._handle_manual_check_jobs
            )
            
            logging.info("Registered LMNT Marketplace local endpoints")
        except Exception as e:
            logging.error(f"Error registering endpoints: {str(e)}")
            
    def setup_job_polling(self):
        """Set up periodic polling for print jobs"""
        # Start polling for jobs every 60 seconds
        self.eventloop.register_callback(self._start_job_handling)
        
    async def _start_job_handling(self):
        """Start job handling using PubSub or polling as fallback"""
        # Check if we have a valid printer token first
        if not self.printer_token:
            self.logger.info("No printer token available, job handling not started")
            return
            
        # Try to set up PubSub subscription if available
        if PUBSUB_AVAILABLE and await self._setup_pubsub_subscription():
            self.logger.info("PubSub subscription active for print jobs")
        else:
            # Fall back to polling if PubSub is not available or setup failed
            self.logger.info("Using polling fallback for print jobs")
            if self.job_poll_task is None:
                self.job_poll_task = self.server.get_event_loop().register_callback(
                    self._poll_for_jobs
                )
                # Schedule the first poll
                await self._poll_for_jobs()
    
    async def _setup_pubsub_subscription(self):
        """Set up Google Cloud PubSub subscription for print jobs"""
        if not PUBSUB_AVAILABLE:
            self.logger.warning("Google Cloud PubSub library not available")
            return False
            
        try:
            # Get printer ID from token claims
            printer_id = self._get_printer_id_from_token()
            if not printer_id:
                self.logger.error("Cannot set up PubSub: No printer ID available")
                return False
                
            # Set up subscription name based on printer ID
            self.pubsub_subscription_name = f"projects/{self.get_gcp_project_id()}/subscriptions/printer-{printer_id}-jobs"
            
            # Create subscriber in a separate thread to avoid blocking the event loop
            self.pubsub_executor = ThreadPoolExecutor(max_workers=1)
            self.pubsub_thread = threading.Thread(
                target=self._run_pubsub_subscriber,
                daemon=True
            )
            self.pubsub_thread.start()
            
            return True
        except Exception as e:
            self.logger.exception(f"Failed to set up PubSub subscription: {e}")
            return False
    
    def _run_pubsub_subscriber(self):
        """Run the PubSub subscriber in a separate thread"""
        try:
            subscriber = pubsub_v1.SubscriberClient()
            subscription_path = self.pubsub_subscription_name
            
            def callback(message):
                try:
                    # Parse the message data
                    data = json.loads(message.data.decode('utf-8'))
                    self.logger.info(f"Received print job from PubSub: {data}")
                    
                    # Schedule job processing in the event loop
                    asyncio.run_coroutine_threadsafe(
                        self._handle_pubsub_message(data),
                        self.server.get_event_loop().get_loop()
                    )
                    
                    # Acknowledge the message
                    message.ack()
                except Exception as e:
                    self.logger.exception(f"Error processing PubSub message: {e}")
                    # Negative acknowledgment to retry
                    message.nack()
            
            # Subscribe to the subscription
            self.logger.info(f"Subscribing to {subscription_path}")
            streaming_pull_future = subscriber.subscribe(
                subscription_path, callback=callback
            )
            self.pubsub_future = streaming_pull_future
            
            # Keep the thread alive
            try:
                streaming_pull_future.result()
            except Exception as e:
                streaming_pull_future.cancel()
                self.logger.exception(f"PubSub subscription failed: {e}")
        except Exception as e:
            self.logger.exception(f"Error in PubSub subscriber thread: {e}")
    
    async def _handle_pubsub_message(self, data):
        """Process a job notification received from PubSub"""
        try:
            # Extract job information
            job_id = data.get('print_job_id') or data.get('job_id')
            if not job_id:
                self.logger.error("Received PubSub message without job_id")
                return
                
            # Create a job object with all available data
            job = {
                'id': job_id,
                'purchase_id': data.get('purchase_id'),
                'printer_id': data.get('printer_id'),
                'user_id': data.get('user_id'),
                'gcode_file_url': data.get('gcode_file_url')
            }
                
            # Add job to queue and process
            self.logger.info(f"Adding job {job_id} from PubSub to queue")
            self.print_job_queue.append(job)
            await self._process_pending_jobs()
        except Exception as e:
            self.logger.exception(f"Error handling PubSub message: {e}")
    
    def _get_printer_id_from_token(self):
        """Extract printer ID from the JWT token"""
        if not self.printer_token:
            return None
            
        try:
            # JWT tokens have three parts separated by dots
            parts = self.printer_token.split('.')
            if len(parts) != 3:
                return None
                
            # Decode the payload (middle part)
            import base64
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + '==').decode('utf-8'))
            
            # Extract printer ID from claims
            return payload.get('printer_id') or payload.get('sub')
        except Exception as e:
            logging.exception(f"Error extracting printer ID from token: {e}")
            return None
    
    def get_gcp_project_id(self):
        """Get the Google Cloud project ID"""
        # Try to get from environment variable
        return os.environ.get('GOOGLE_CLOUD_PROJECT', 'lmnt-marketplace')
    
    async def _poll_for_jobs(self):
        """Poll the marketplace for new print jobs"""
        if not self.printer_registered or not self.printer_token:
            logging.debug("Cannot poll for jobs: Printer not registered")
            # Reschedule next poll
            self.eventloop.delay_callback(60., self._poll_for_jobs)
            return
        
        try:
            url = f"{self.marketplace_url}/api/printer-jobs"
            headers = {
                'Authorization': f'Bearer {self.printer_token}',
                'Content-Type': 'application/json'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        jobs = data.get('jobs', [])
                        
                        if jobs:
                            logging.info(f"Found {len(jobs)} pending print jobs")
                            # Process new jobs
                            await self._process_pending_jobs(jobs)
                        else:
                            logging.debug("No pending print jobs found")
                    else:
                        error_text = await response.text()
                        logging.error(f"Failed to poll for jobs: {response.status} - {error_text}")
        except Exception as e:
            logging.error(f"Error polling for jobs: {str(e)}")
        
        # Reschedule next poll
        self.eventloop.delay_callback(60., self._poll_for_jobs)
    
    async def _process_pending_jobs(self, jobs):
        """Process pending print jobs from the marketplace"""
        for job in jobs:
            job_id = job.get('id')
            purchase_id = job.get('purchase_id')
            model_id = job.get('model_id')
            status = job.get('status')
            
            # Check if job is already in queue
            if any(j.get('id') == job_id for j in self.print_job_queue):
                logging.debug(f"Job {job_id} already in queue, skipping")
                continue
            
            # Check if job is already being processed
            if self.current_print_job and self.current_print_job.get('id') == job_id:
                logging.debug(f"Job {job_id} is current job, skipping")
                continue
            
            # Only add new jobs with 'pending' status
            if status == 'pending':
                logging.info(f"Adding job {job_id} to queue")
                self.print_job_queue.append(job)
                
                # Update job status to 'queued'
                await self._update_job_status(job_id, 'queued')
        
        # If no current job and queue has jobs, start processing
        if not self.current_print_job and self.print_job_queue:
            await self._process_next_job()
    
    async def _process_next_job(self):
        """Process the next job in the queue"""
        if not self.print_job_queue:
            logging.debug("No jobs in queue to process")
            return
        
        # Check printer status before starting
        if not await self._check_printer_ready():
            logging.warning("Printer not ready, delaying job processing")
            # Schedule retry in 60 seconds
            self.eventloop.delay_callback(60., self._process_next_job)
            return
        
        # Get next job
        job = self.print_job_queue.pop(0)
        self.current_print_job = job
        job_id = job.get('id')
        purchase_id = job.get('purchase_id')
        
        logging.info(f"Processing job {job_id} for purchase {purchase_id}")
        
        # Update job status to 'processing'
        await self._update_job_status(job_id, 'processing')
        
        # Download encrypted gcode
        success = await self._download_gcode(job)
        
        if success:
            # Start the print
            await self._start_print(job)
        else:
            # Failed to download, mark job as failed
            await self._update_job_status(job_id, 'failed', 'Failed to download gcode')
            self.current_print_job = None
            
            # Process next job if available
            if self.print_job_queue:
                await self._process_next_job()
    
    async def _check_printer_ready(self):
        """Check if printer is ready for a new print job"""
        if not self.klippy_apis:
            logging.error("Klippy APIs not available")
            return False
        
        try:
            printer_info = await self.klippy_apis.query_objects({"print_stats": None, "toolhead": None})
            print_state = printer_info.get("print_stats", {}).get("state", "")
            is_homed = printer_info.get("toolhead", {}).get("homed_axes", "") == "xyz"
            
            # Printer is ready if it's in standby or complete state
            ready = print_state in ["standby", "complete"]
            
            if not ready:
                logging.warning(f"Printer not ready, state: {print_state}")
            
            return ready
        except Exception as e:
            logging.error(f"Error checking printer status: {str(e)}")
            return False
    
    # PSEK is generated server-side by the marketplace API
    
    async def _save_encrypted_psek(self, encrypted_psek):
        """Save the encrypted PSEK received from the server
        
        According to ADR-003, the kek_id field in the printer registration response
        actually contains the encrypted PSEK (encrypted by the Master Printer KEK).
        """
        try:
            psek_path = os.path.join(self.keys_path, "kek_id")
            with open(psek_path, 'w') as f:
                f.write(encrypted_psek)
            logging.info(f"Saved encrypted PSEK to {psek_path}")
            return True
        except Exception as e:
            logging.error(f"Error saving encrypted PSEK: {str(e)}")
            return False
    
    async def _get_decryption_key(self):
        """Get the decryption key for GCode files by decrypting the PSEK via CWS"""
        try:
            # 1. Read the encrypted PSEK (kek_id) received during registration
            kek_id_path = os.path.join(self.keys_path, "kek_id")
            if not os.path.exists(kek_id_path):
                logging.error("No encrypted PSEK found")
                return None
                
            with open(kek_id_path, 'r') as f:
                encrypted_psek = f.read()
            
            # 2. Call CWS to decrypt the PSEK
            # According to ADR-003, the kek_id field actually contains the encrypted PSEK
            # We need to send this to CWS for decryption using the Master Printer KEK
            
            # In production environment:
            if not self.debug:
                try:
                    # Call the CWS decrypt-data endpoint
                    cws_url = os.environ.get('CWS_URL', 'https://cws.lmnt.market')
                    url = f"{cws_url}/ops/decrypt-data"
                    
                    headers = {
                        'Authorization': f'Bearer {self.printer_token}',
                        'Content-Type': 'application/json'
                    }
                    
                    payload = {
                        'encrypted_data': encrypted_psek  # Send the encrypted PSEK for decryption
                    }
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json=payload, headers=headers) as response:
                            if response.status == 200:
                                response_data = await response.json()
                                decrypted_psek = response_data.get('decrypted_data')
                                
                                if decrypted_psek:
                                    # Convert from base64 if needed
                                    import base64
                                    if not decrypted_psek.startswith(b'b'):
                                        decrypted_psek = base64.b64decode(decrypted_psek)
                                    
                                    logging.info("Successfully decrypted PSEK via CWS")
                                    return decrypted_psek
                                else:
                                    logging.error("CWS response missing decrypted_data")
                            else:
                                error_text = await response.text()
                                logging.error(f"CWS decryption failed: {response.status} - {error_text}")
                except Exception as e:
                    logging.error(f"Error calling CWS decrypt endpoint: {str(e)}")
            
            # For testing/debug environment, use a simulated key
            logging.warning("Using dummy PSEK for testing - in production would use CWS decryption")
            return Fernet.generate_key()
        except Exception as e:
            logging.error(f"Error getting decryption key: {str(e)}")
            return None
    
    async def _decrypt_gcode(self, encrypted_data, job_id):
        """Decrypt GCode data using PSEK"""
        try:
            # Get the decryption key
            key = await self._get_decryption_key()
            if not key:
                logging.error(f"No decryption key available for job {job_id}")
                return None
            
            # Create Fernet cipher with the key
            cipher = Fernet(key)
            
            # Decrypt the data
            try:
                decrypted_data = cipher.decrypt(encrypted_data)
                logging.info(f"Successfully decrypted GCode for job {job_id}")
                return decrypted_data
            except InvalidToken:
                logging.error(f"Invalid token or corrupted data for job {job_id}")
                return None
        except Exception as e:
            logging.error(f"Error decrypting GCode: {str(e)}")
            return None
    
    async def _download_gcode(self, job):
        """Download and decrypt gcode for a job"""
        job_id = job.get('id')
        purchase_id = job.get('purchase_id')
        gcode_file_url = job.get('gcode_file_url')
        
        try:
            # If we have a direct GCP bucket URL, use it
            if gcode_file_url:
                download_url = gcode_file_url
                headers = {
                    'Authorization': f'Bearer {self.printer_token}'
                }
            else:
                # Otherwise get download URL from marketplace API
                url = f"{self.marketplace_url}/api/printer-jobs/{job_id}/gcode"
                headers = {
                    'Authorization': f'Bearer {self.printer_token}',
                    'Content-Type': 'application/json'
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            response_data = await response.json()
                            download_url = response_data.get('download_url')
                            if not download_url:
                                logging.error(f"No download URL provided for job {job_id}")
                                return False
                        else:
                            error_text = await response.text()
                            logging.error(f"Failed to get download URL: {response.status} - {error_text}")
                            return False
            
            # Download the encrypted gcode file
            logging.info(f"Downloading encrypted gcode from {download_url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, headers=headers) as response:
                    if response.status == 200:
                        # Get the encrypted gcode data
                        encrypted_gcode = await response.read()
                        
                        # Decrypt the gcode using PSEK
                        decrypted_gcode = await self._decrypt_gcode(encrypted_gcode, job_id)
                        if not decrypted_gcode:
                            logging.error(f"Failed to decrypt gcode for job {job_id}")
                            return False
                        
                        # Save decrypted gcode to file
                        filename = f"lmnt_print_{job_id}.gcode"
                        filepath = os.path.join(self.gcodes_path, filename)
                        
                        with open(filepath, 'wb') as f:
                            f.write(decrypted_gcode)
                        
                        # Store file path in job
                        job['filepath'] = filepath
                        job['filename'] = filename
                        
                        logging.info(f"Downloaded and decrypted gcode for job {job_id} to {filepath}")
                        return True
                    else:
                        error_text = await response.text()
                        logging.error(f"Failed to download gcode: {response.status} - {error_text}")
                        return False
        except Exception as e:
            logging.error(f"Error downloading/decrypting gcode: {str(e)}")
            return False
    
    async def _start_print(self, job):
        """Start printing a job"""
        job_id = job.get('id')
        filepath = job.get('filepath')
        filename = job.get('filename')
        
        if not filepath or not os.path.exists(filepath):
            logging.error(f"File not found for job {job_id}: {filepath}")
            await self._update_job_status(job_id, 'failed', 'File not found')
            self.current_print_job = None
            return False
        
        try:
            # Home the printer if needed
            try:
                printer_info = await self.klippy_apis.query_objects({"toolhead": None})
                is_homed = printer_info.get("toolhead", {}).get("homed_axes", "") == "xyz"
                
                if not is_homed:
                    logging.info("Homing printer before print")
                    await self.klippy_apis.run_gcode("G28")
            except Exception as e:
                logging.error(f"Error homing printer: {str(e)}")
                # Continue anyway, the print might still work
            
            # Start the print
            logging.info(f"Starting print for job {job_id} with file {filename}")
            
            # Use the SDCARD_PRINT_FILE command with the lmnt_marketplace provider
            await self.klippy_apis.run_gcode(f'SDCARD_PRINT_FILE FILE="{filename}" PROVIDER="lmnt_marketplace"')
            
            # Update job status to 'printing'
            await self._update_job_status(job_id, 'printing')
            
            # Start monitoring print progress
            self.print_job_started = True
            self.eventloop.create_task(self._monitor_print_progress(job))
            
            return True
        except Exception as e:
            logging.error(f"Error starting print: {str(e)}")
            await self._update_job_status(job_id, 'failed', f'Failed to start print: {str(e)}')
            self.current_print_job = None
            return False
    
    async def _update_job_status(self, job_id, status, message=None):
        """Update job status in the marketplace"""
        if not self.printer_token:
            logging.error("Cannot update job status: No printer token available")
            return False
        
        try:
            url = f"{self.marketplace_url}/api/printer-jobs/{job_id}/status"
            headers = {
                'Authorization': f'Bearer {self.printer_token}',
                'Content-Type': 'application/json'
            }
            
            data = {'status': status}
            if message:
                data['message'] = message
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, headers=headers) as response:
                    if response.status == 200:
                        logging.info(f"Updated job {job_id} status to {status}")
                        return True
                    else:
                        error_text = await response.text()
                        logging.error(f"Failed to update job status: {response.status} - {error_text}")
                        return False
        except Exception as e:
            logging.error(f"Error updating job status: {str(e)}")
            return False
    
    async def _monitor_print_progress(self, job):
        """Monitor print progress and update status"""
        job_id = job.get('id')
        
        try:
            while self.print_job_started:
                # Get current print stats
                printer_info = await self.klippy_apis.query_objects({"print_stats": None})
                print_stats = printer_info.get("print_stats", {})
                state = print_stats.get("state", "")
                
                # Check if print is still active
                if state not in ["printing", "paused"]:
                    logging.info(f"Print job {job_id} finished with state {state}")
                    
                    # Update job status based on final state
                    if state == "complete":
                        await self._update_job_status(job_id, 'completed')
                    elif state == "error":
                        await self._update_job_status(job_id, 'failed', 'Print error')
                    else:
                        await self._update_job_status(job_id, 'cancelled')
                    
                    # Reset current job
                    self.print_job_started = False
                    self.current_print_job = None
                    
                    # Process next job if available
                    if self.print_job_queue:
                        await self._process_next_job()
                    
                    return
                
                # Get progress information
                progress = print_stats.get("progress", 0)
                filename = print_stats.get("filename", "")
                print_duration = print_stats.get("print_duration", 0)
                filament_used = print_stats.get("filament_used", 0)
                
                # Get layer information if available
                current_layer = 0
                total_layers = 0
                
                if hasattr(print_stats, "info"):
                    info = print_stats.get("info", {})
                    current_layer = info.get("current_layer", 0)
                    total_layers = info.get("total_layer", 0)
                
                # Update job progress
                await self._update_job_progress(job_id, progress, current_layer, total_layers, print_duration)
                
                # Wait before next update
                await asyncio.sleep(10)
        except Exception as e:
            logging.error(f"Error monitoring print progress: {str(e)}")
            # Try to update job as failed
            await self._update_job_status(job_id, 'failed', f'Monitoring error: {str(e)}')
            self.print_job_started = False
            self.current_print_job = None
    
    async def _update_job_progress(self, job_id, progress, current_layer, total_layers, duration):
        """Update job progress in the marketplace"""
        if not self.printer_token:
            return False
        
        try:
            url = f"{self.marketplace_url}/api/printer-jobs/{job_id}/progress"
            headers = {
                'Authorization': f'Bearer {self.printer_token}',
                'Content-Type': 'application/json'
            }
            
            data = {
                'progress': progress,
                'current_layer': current_layer,
                'total_layers': total_layers,
                'duration': duration
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, headers=headers) as response:
                    if response.status == 200:
                        logging.debug(f"Updated job {job_id} progress: {progress:.1%}")
                        return True
                    else:
                        error_text = await response.text()
                        logging.error(f"Failed to update job progress: {response.status} - {error_text}")
                        return False
        except Exception as e:
            logging.error(f"Error updating job progress: {str(e)}")
            return False
    
    async def load_printer_token(self):
        """Load saved printer token from secure storage"""
        token_file = os.path.join(self.secure_storage_path, "printer_token.json")
        
        if not os.path.exists(token_file):
            logging.info("No saved printer token found")
            return False
        
        try:
            with open(token_file, 'r') as f:
                token_data = json.load(f)
            
            self.printer_token = token_data.get('token')
            expiry_str = token_data.get('expiry')
            
            if expiry_str:
                expiry = datetime.fromisoformat(expiry_str)
                self.token_expiry = expiry
                
                # Check if token is still valid
                if datetime.now() < expiry:
                    self.printer_registered = True
                    logging.info("Loaded valid printer token")
                    return True
                else:
                    logging.info("Loaded printer token is expired")
                    return False
            else:
                logging.warning("Token data missing expiry information")
                return False
        except Exception as e:
            logging.error(f"Error loading printer token: {str(e)}")
            return False
    
    async def save_printer_token(self, token, expiry):
        """Save printer token to secure storage"""
        token_file = os.path.join(self.secure_storage_path, "printer_token.json")
        
        try:
            # Convert expiry to ISO format string if it's a datetime
            if isinstance(expiry, datetime):
                expiry_str = expiry.isoformat()
            else:
                expiry_str = expiry
                
            token_data = {
                'token': token,
                'expiry': expiry_str,
                'saved_at': datetime.now().isoformat()
            }
            
            with open(token_file, 'w') as f:
                json.dump(token_data, f)
                
            logging.info("Saved printer token to secure storage")
            return True
        except Exception as e:
            logging.error(f"Error saving printer token: {str(e)}")
            return False
    
    async def check_token_refresh(self):
        """Check if token needs to be refreshed and schedule refresh if needed"""
        if not self.printer_token or not self.token_expiry:
            return
            
        # Check if token is expired or close to expiry (within 1 day)
        now = datetime.now()
        refresh_threshold = now + timedelta(days=1)
        
        if self.token_expiry <= refresh_threshold:
            logging.info("Printer token needs refresh, refreshing...")
            success = await self.refresh_printer_token()
            
            if not success:
                logging.error("Failed to refresh printer token")
                
                # If token is actually expired (not just close to expiry), mark as unregistered
                if self.token_expiry <= now:
                    logging.warning("Printer token expired and refresh failed, marking as unregistered")
                    self.printer_registered = False
        
        # Schedule next check in 1 hour
        self.eventloop.delay_callback(60 * 60, self.check_token_refresh)
    
    async def refresh_printer_token(self):
        """Refresh the printer token with the marketplace
        
        Uses the newly created /api/refresh-printer-token endpoint which is specifically
        designed for printer token refresh. This endpoint validates the current printer token
        and issues a new one with extended expiration.
        """
        if not self.printer_token:
            logging.error("No printer token to refresh")
            return False
            
        try:
            # Call marketplace token refresh endpoint
            url = f"{self.marketplace_url}/api/refresh-printer-token"
            headers = {
                'Authorization': f'Bearer {self.printer_token}',
                'Content-Type': 'application/json'
            }
            
            logging.info(f"Refreshing printer token via {url}")
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        new_token = data.get('token')
                        
                        # Handle different possible response formats
                        expiry = data.get('expiry') or data.get('expires_at')
                        
                        if new_token:
                            # If expiry is provided, use it; otherwise default to 30 days
                            if expiry:
                                try:
                                    # Try to parse as ISO format
                                    expiry_dt = datetime.fromisoformat(expiry)
                                except (ValueError, TypeError):
                                    # If it's a timestamp or other format, default to 30 days
                                    expiry_dt = datetime.now() + timedelta(days=30)
                            else:
                                expiry_dt = datetime.now() + timedelta(days=30)
                            
                            # Update token and expiry
                            self.printer_token = new_token
                            self.token_expiry = expiry_dt
                            
                            # Save to secure storage
                            await self.save_printer_token()
                            
                            logging.info(f"Printer token refreshed successfully, new expiry: {expiry_dt}")
                            return True
                        else:
                            logging.error("Token refresh response missing token")
                            return False
                    else:
                        error_text = await response.text()
                        logging.error(f"Token refresh failed: {response.status} - {error_text}")
                        return False
        except Exception as e:
            logging.error(f"Error refreshing printer token: {str(e)}")
            return False
    
    async def _handle_user_login(self, web_request):
        """Handle user login to the CWS and obtain user JWT"""
        try:
            # Extract login credentials
            username = web_request.get_str('username')
            password = web_request.get_str('password')
            
            if not username or not password:
                return {'status': 'error', 'message': 'Username and password are required'}, 400
            
            # Build login payload
            payload = {
                'username': username,
                'password': password
            }
            
            # Send login request to CWS
            url = f"{self.marketplace_url}/auth/login"
            headers = {'Content-Type': 'application/json'}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        user_token = data.get('token')
                        
                        if user_token:
                            # Store user token temporarily (in memory only, not on disk)
                            self.user_token = user_token
                            
                            logging.info(f"User {username} logged in successfully")
                            return {
                                'status': 'success', 
                                'message': 'Login successful', 
                                'token': user_token
                            }
                        else:
                            logging.error("Login response missing token")
                            return {'status': 'error', 'message': 'Invalid login response'}, 500
                    else:
                        error_text = await response.text()
                        logging.error(f"Login failed: {response.status} - {error_text}")
                        return {'status': 'error', 'message': f"Login failed: {error_text}"}, response.status
        except Exception as e:
            logging.error(f"Error during user login: {str(e)}")
            return {'status': 'error', 'message': str(e)}, 500
    
    async def _handle_register_printer(self, web_request):
        """Handle printer registration with marketplace"""
        if self.printer_registered:
            return {'status': 'error', 'message': 'Printer already registered'}, 400
        
        try:
            # Get user token from request
            user_token = web_request.get_str('user_token')
            if not user_token:
                return {'status': 'error', 'message': 'User token required'}, 400
            
            # Get printer info
            printer_name = web_request.get_str('printer_name', 'My 3D Printer')
            manufacturer = web_request.get_str('manufacturer', 'Unknown')
            model = web_request.get_str('model', 'Unknown')
            
            # Prepare registration data
            data = {
                'printer_name': printer_name,
                'manufacturer': manufacturer,
                'model': model
            }
            
            # Send registration request to marketplace
            url = f"{self.marketplace_url}/api/register-printer"
            headers = {
                'Authorization': f'Bearer {user_token}',
                'Content-Type': 'application/json'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, headers=headers) as response:
                    if response.status == 201:
                        # Registration successful
                        response_data = await response.json()
                        
                        # Extract and store printer token
                        self.printer_token = response_data.get('token')
                        
                        # Parse expiry if provided, otherwise default to 30 days
                        expiry = response_data.get('expiry') or response_data.get('expires_at')
                        if expiry:
                            try:
                                self.token_expiry = datetime.fromisoformat(expiry)
                            except (ValueError, TypeError):
                                self.token_expiry = datetime.now() + timedelta(days=30)
                        else:
                            self.token_expiry = datetime.now() + timedelta(days=30)
                        
                        # Save token to secure storage
                        await self.save_printer_token()
                        
                        # Save the printer ID
                        self.printer_id = response_data.get('id')
                        
                        # Save the encrypted PSEK (kek_id) returned from the server
                        kek_id = response_data.get('kek_id')
                        if kek_id:
                            await self._save_encrypted_psek(kek_id)
                            logging.info("Saved encrypted PSEK (kek_id) from server")
                        else:
                            logging.warning("No kek_id received from server")
                        
                        # Mark as registered
                        self.printer_registered = True
                        
                        # Start job handling now that we're registered
                        self.setup_job_polling()
                        
                        return {'status': 'success', 'message': 'Printer registered successfully'}
                    else:
                        error_text = await response.text()
                        logging.error(f"Registration failed: {response.status} - {error_text}")
                        return {'status': 'error', 'message': f"Registration failed: {error_text}"}, response.status
        except Exception as e:
            logging.error(f"Error during printer registration: {str(e)}")
            return {'status': 'error', 'message': str(e)}, 500
    
    async def _handle_manual_register(self, web_request):
        """Handle printer registration with the LMNT Marketplace"""
        try:
            # Extract registration data
            user_token = web_request.get_str('user_token')
            printer_name = web_request.get_str('printer_name')
            manufacturer = web_request.get_str('manufacturer', None)
            model = web_request.get_str('model', None)
            
            if not user_token or not printer_name:
                return {'status': 'error', 'message': 'Missing required fields'}, 400
            
            # Prepare registration data
            registration_data = {
                'printer_name': printer_name
            }
            
            if manufacturer:
                registration_data['manufacturer'] = manufacturer
            
            if model:
                registration_data['model'] = model
            
            # Get printer info for additional data
            try:
                if self.klippy_apis:
                    printer_info = await self.klippy_apis.query_objects({'configfile': None})
                    config = printer_info.get('configfile', {}).get('config', {})
                    
                    # Add printer details from config if available
                    if not manufacturer and 'printer' in config:
                        registration_data['manufacturer'] = config['printer'].get('manufacturer', 'Unknown')
                    
                    if not model and 'printer' in config:
                        registration_data['model'] = config['printer'].get('model', 'Unknown')
            except Exception as e:
                logging.warning(f"Could not get printer config: {str(e)}")
            
            # Register with marketplace
            url = f"{self.marketplace_url}/api/{self.api_version}/register-printer"
            headers = {
                'Authorization': f'Bearer {user_token}',
                'Content-Type': 'application/json'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, 
                    json=registration_data,
                    headers=headers
                ) as response:
                    response_data = await response.json()
                    
                    if response.status == 200 or response.status == 201:
                        # Extract printer token and details
                        printer_token = response_data.get('printer_token')
                        printer_id = response_data.get('id')
                        token_expires = response_data.get('token_expires')
                        
                        if printer_token and token_expires:
                            # Parse expiry timestamp
                            try:
                                expiry = datetime.fromisoformat(token_expires)
                            except ValueError:
                                # Try parsing as timestamp
                                expiry = datetime.fromtimestamp(float(token_expires))
                            
                            # Save the token
                            self.printer_token = printer_token
                            self.token_expiry = expiry
                            self.printer_registered = True
                            await self.save_printer_token(printer_token, expiry)
                            
                            # Return success response
                            return {
                                'status': 'success',
                                'message': 'Printer registered successfully',
                                'printer_id': printer_id,
                                'token_expires': token_expires
                            }
                        else:
                            return {
                                'status': 'error',
                                'message': 'Registration response missing token information'
                            }, 500
                    else:
                        error_message = response_data.get('message', 'Unknown error')
                        return {
                            'status': 'error',
                            'message': f'Registration failed: {error_message}'
                        }, response.status
        except Exception as e:
            logging.exception(f"Error in printer registration: {str(e)}")
            return {'status': 'error', 'message': str(e)}, 500
