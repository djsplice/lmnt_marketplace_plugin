"""
LMNT Marketplace Integration for Moonraker
Main integration class that coordinates all marketplace components

This module serves as the main entry point for the LMNT Marketplace integration,
coordinating authentication, crypto operations, GCode handling, and job management.
"""

import os
import logging
import asyncio
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
        
        # Set up paths for tokens and data storage
        data_path = self.server.get_app_args()['data_path']
        self.tokens_path = os.path.join(data_path, "lmnt_marketplace", "tokens")
        
        # Create directories if they don't exist
        os.makedirs(self.tokens_path, exist_ok=True)
        
        # API endpoints
        self.marketplace_url = "https://api.lmnt.market"
        self.cws_url = "https://cws.lmnt.market"
        
        # Get event loop for scheduling tasks
        self.eventloop = self.server.get_event_loop()
        
        # Initialize managers
        self.auth_manager = auth.AuthManager(self)
        self.crypto_manager = crypto.CryptoManager(self)
        self.gcode_manager = gcode.GCodeManager(self)
        self.job_manager = jobs.JobManager(self)
        
        # Link managers to each other
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
        
        # Initialize managers with Klippy APIs
        await self.auth_manager.initialize(klippy_apis, None)
        await self.crypto_manager.initialize(klippy_apis, None)
        await self.gcode_manager.initialize(klippy_apis, None)
        await self.job_manager.initialize(klippy_apis, None)
        
        # Start background tasks
        self.server.register_event_loop_callback(self._background_tasks)
        
        logging.info("LMNT Marketplace Integration initialized with Klippy APIs")
    
    async def _background_tasks(self):
        """
        Start background tasks for the integration
        """
        # Start job checking task
        self.server.register_event_loop_callback(self.job_manager.job_check_task)
    
    async def handle_klippy_shutdown(self):
        """
        Handle Klippy shutdown event
        """
        self.klippy_apis = None
        
        # Notify managers
        await self.auth_manager.handle_klippy_shutdown()
        await self.crypto_manager.handle_klippy_shutdown()
        await self.gcode_manager.handle_klippy_shutdown()
        await self.job_manager.handle_klippy_shutdown()
