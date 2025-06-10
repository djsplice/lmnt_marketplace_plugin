"""
LMNT Marketplace Integration for Moonraker
Main module coordinating all marketplace components

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
    
    Coordinates all marketplace components including authentication, 
    crypto operations, GCode handling, and job management.
    """
    
    def __init__(self, config, server):
        """Initialize the LMNT Marketplace Integration"""
        self.server = server
        self.eventloop = server.get_event_loop()
        
        # Configuration
        self.marketplace_url = config.get('marketplace_url', 'https://api.lmnt.market')
        self.cws_url = config.get('cws_url', 'https://cws.lmnt.market')
        self.api_version = config.get('api_version', 'v1')
        self.debug = config.getboolean('debug', False)
        
        # Set up directories
        self.base_path = os.path.expanduser("~/.lmnt_marketplace")
        self.encrypted_path = os.path.join(self.base_path, "encrypted")
        self.keys_path = os.path.join(self.base_path, "keys")
        self.tokens_path = os.path.join(self.base_path, "tokens")
        self.thumbnails_path = os.path.join(self.base_path, "thumbnails")
        
        self._ensure_directories_exist()
        
        # Initialize components
        self.file_manager = server.lookup_component('file_manager', None)
        self.klippy_apis = None
        self.http_client = None
        
        # Initialize submodules
        self.auth_manager = auth.AuthManager(self)
        self.crypto_manager = crypto.CryptoManager(self)
        self.gcode_manager = gcode.GCodeManager(self)
        self.job_manager = jobs.JobManager(self)
        
        # Register event handlers for print state changes
        self.register_event_handlers()
        
        logging.info("LMNT Marketplace Integration initialized")
    
    def _ensure_directories_exist(self):
        """Ensure all required directories exist"""
        os.makedirs(self.base_path, exist_ok=True)
        os.makedirs(self.encrypted_path, exist_ok=True)
        os.makedirs(self.keys_path, exist_ok=True)
        os.makedirs(self.tokens_path, exist_ok=True)
        os.makedirs(self.thumbnails_path, exist_ok=True)
    
    def register_endpoints(self, register_endpoint):
        """Register HTTP endpoints for the integration"""
        # Register auth endpoints
        self.auth_manager.register_endpoints(register_endpoint)
        
        # Register job endpoints
        self.job_manager.register_endpoints(register_endpoint)
    
    def register_event_handlers(self):
        """Register event handlers for print state changes"""
        # Will be implemented when server is available
        pass
    
    async def initialize(self, klippy_apis, http_client):
        """Initialize with Klippy APIs and HTTP client"""
        self.klippy_apis = klippy_apis
        self.http_client = http_client
        
        # Initialize submodules with APIs
        await self.auth_manager.initialize(klippy_apis, http_client)
        await self.crypto_manager.initialize(klippy_apis, http_client)
        await self.gcode_manager.initialize(klippy_apis, http_client)
        await self.job_manager.initialize(klippy_apis, http_client)
        
        # Schedule token refresh check
        self.eventloop.register_callback(self.auth_manager.check_token_refresh)
