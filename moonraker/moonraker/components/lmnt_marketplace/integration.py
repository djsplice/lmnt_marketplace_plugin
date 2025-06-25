"""
LMNT Marketplace Integration for Moonraker
Main integration class that coordinates all marketplace components

This module serves as the main entry point for the LMNT Marketplace integration,
coordinating authentication, crypto operations, GCode handling, and job management.
"""

import os
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta

# Import submodules
from . import auth
from . import crypto
from . import gcode
from . import jobs

class LmntMarketplaceIntegration:
    """
    Main integration class for LMNT Marketplace
    
    This class coordinates all the components of the LMNT Marketplace integration,
    including authentication, crypto operations, GCode handling, and job management.
    """
    
    def __init__(self, config, server):
        """
        Initialize the LMNT Marketplace integration
        
        Args:
            config: Configuration object from Moonraker
            server: Moonraker server instance
        """
        self.server = server
        self.config = config
        self.klippy_apis = None
        self.api_version = "1.0.0"
        
        # Set up paths for tokens, keys, and data storage
        data_path = self.server.get_app_args()['data_path']
        lmnt_data_path = os.path.join(data_path, "lmnt_marketplace")
        self.tokens_path = os.path.join(lmnt_data_path, "tokens")
        self.keys_path = os.path.join(lmnt_data_path, "keys")
        self.encrypted_path = os.path.join(lmnt_data_path, "encrypted")
        self.metadata_path = os.path.join(lmnt_data_path, "metadata")
        self.thumbnails_path = os.path.join(lmnt_data_path, "thumbnails")
        
        # Create directories if they don't exist
        os.makedirs(self.tokens_path, exist_ok=True)
        os.makedirs(self.keys_path, exist_ok=True)
        os.makedirs(self.encrypted_path, exist_ok=True)
        os.makedirs(self.metadata_path, exist_ok=True)
        os.makedirs(self.thumbnails_path, exist_ok=True)
        
        logging.info(f"LMNT data paths: tokens={self.tokens_path}, keys={self.keys_path}, encrypted={self.encrypted_path}, metadata={self.metadata_path}, thumbnails={self.thumbnails_path}")
        
        # API endpoints
        # Use configurable endpoints with defaults
        self.marketplace_url = self.config.get('marketplace_url', "https://api.lmnt.market")
        self.cws_url = self.config.get('cws_url', "https://cws.lmnt.market")
        
        # Debug mode for verbose logging (default: False)
        self.debug_mode = self.config.getboolean('debug_mode', False)
        
        # Development mode for testing features (default: False)
        self.development_mode = self.config.getboolean('development_mode', False)
        
        # Log the configured endpoints
        logging.info(f"LMNT Marketplace API URL: {self.marketplace_url}")
        logging.info(f"LMNT CWS URL: {self.cws_url}")
        logging.info(f"Debug mode: {self.debug_mode}")
        logging.info(f"Development mode: {self.development_mode}")
        
        # Configure logging level
        if self.debug_mode:
            logging.info("Debug mode enabled - sensitive information may be logged")
        else:
            logging.info("Debug mode disabled - sensitive information will be redacted")
        
        # Get event loop for scheduling tasks
        self.eventloop = self.server.get_event_loop()
        
        # Initialize managers
        self.auth_manager = auth.AuthManager(self)
        self.crypto_manager = crypto.CryptoManager(self)
        self.gcode_manager = gcode.GCodeManager(self)
        self.job_manager = jobs.JobManager(self)
        
        # Link managers to each other
        # Pass the DLT private key from AuthManager to CryptoManager, if it was loaded
        if self.auth_manager.dlt_private_key:
            logging.info("LmntMarketplaceIntegration: Passing DLT private key from AuthManager to CryptoManager.")
            self.crypto_manager.dlt_private_key_ed25519 = self.auth_manager.dlt_private_key
        else:
            logging.warning("LmntMarketplaceIntegration: No DLT private key was loaded by AuthManager, so it cannot be passed to CryptoManager.")

        self.job_manager.set_auth_manager(self.auth_manager)
        self.job_manager.set_crypto_manager(self.crypto_manager)
        self.job_manager.set_gcode_manager(self.gcode_manager)
        
        logging.info("LMNT Marketplace Integration initialized")
    
    async def initialize(self, klippy_apis):
        """
        Initialize the integration with Klippy APIs
        
        Args:
            klippy_apis: Klippy APIs component from Moonraker
        """
        self.klippy_apis = klippy_apis
        
        # Create HTTP client for API calls
        self.http_client = aiohttp.ClientSession()
        logging.info("Created HTTP client for API calls")
        
        # Initialize managers with Klippy APIs and HTTP client
        await self.auth_manager.initialize(klippy_apis, self.http_client)
        await self.crypto_manager.initialize(klippy_apis, self.http_client)
        await self.gcode_manager.initialize(klippy_apis, self.http_client)
        await self.job_manager.initialize(klippy_apis, self.http_client)
        
        logging.info("LMNT Marketplace Integration initialized with Klippy APIs")
    
    async def on_klippy_ready(self, klippy_apis):
        pass
    
    async def handle_klippy_shutdown(self):
        """
        Handle Klippy shutdown event
        """
        logging.info("LMNT Marketplace: Handling Klippy shutdown")
        self.klippy_apis = None
        
        # Notify managers
        await self.auth_manager.handle_klippy_shutdown()
        await self.crypto_manager.handle_klippy_shutdown()
        await self.gcode_manager.handle_klippy_shutdown()
        await self.job_manager.handle_klippy_shutdown()
    
    async def close(self):
        """
        Close the integration and release resources
        """
        # Close all managers first
        if hasattr(self, 'auth_manager'):
            await self.auth_manager.close()
            
        # Close HTTP client last
        if hasattr(self, 'http_client') and self.http_client is not None:
            await self.http_client.close()
            logging.info("Closed HTTP client")
            
        logging.info("LMNT Marketplace Integration closed")
