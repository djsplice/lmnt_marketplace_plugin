#!/bin/bash
# Script to fix circular import issues in the LMNT Marketplace Plugin

set -e

echo "LMNT Marketplace Plugin Circular Import Fix"
echo "=========================================="

# Define directories
PLUGIN_NAME="lmnt-marketplace"
PLUGIN_DIR="${HOME}/${PLUGIN_NAME}"
MOONRAKER_DIR="${HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"
MARKETPLACE_DIR="${COMPONENT_DIR}/lmnt_marketplace"

# Check if the plugin directory exists
if [ ! -d "${PLUGIN_DIR}" ]; then
    echo "ERROR: Plugin directory not found at ${PLUGIN_DIR}"
    echo "Please run the installation script first."
    exit 1
fi

# Create integration.py file
echo "Creating integration.py file..."
cat > "${PLUGIN_DIR}/component/lmnt_marketplace/integration.py" << 'EOF'
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
        
        # Initialize managers
        self.auth_manager = auth.LmntAuthManager(config, server)
        self.crypto_manager = crypto.LmntCryptoManager(config, server)
        self.gcode_manager = gcode.LmntGcodeManager(config, server)
        self.job_manager = jobs.LmntJobManager(config, server)
        
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
        await self.auth_manager.initialize(klippy_apis)
        await self.crypto_manager.initialize(klippy_apis)
        await self.gcode_manager.initialize(klippy_apis)
        await self.job_manager.initialize(klippy_apis)
        
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
EOF

# Update __init__.py file
echo "Updating __init__.py file..."
cat > "${PLUGIN_DIR}/component/lmnt_marketplace/__init__.py" << 'EOF'
"""
LMNT Marketplace Integration for Moonraker
Main module coordinating all marketplace components

This module serves as the main entry point for the LMNT Marketplace integration,
coordinating authentication, crypto operations, GCode handling, and job management.
"""

# Import the integration class from the integration module
from .integration import LmntMarketplaceIntegration

# Export the integration class
__all__ = ['LmntMarketplaceIntegration']
EOF

# Update lmnt_marketplace_plugin.py
echo "Updating lmnt_marketplace_plugin.py..."
cat > "${PLUGIN_DIR}/component/lmnt_marketplace_plugin.py" << 'EOF'
# LMNT Marketplace Plugin for Moonraker
# Integrates 3D printers with the LMNT Marketplace for secure model printing
# This is a thin wrapper that loads the modular LMNT Marketplace integration

import logging
import os
import sys
import traceback

from moonraker.common import RequestType

class LmntMarketplacePlugin:
    """
    LMNT Marketplace Plugin for Moonraker
    
    This plugin provides integration between 3D printers running Klipper/Moonraker
    and the LMNT Marketplace. It handles printer registration, authentication,
    secure file transfer, and print job management.
    
    This is a thin wrapper around the modular LmntMarketplaceIntegration class.
    """
    
    def __init__(self, config):
        self.server = config.get_server()
        self.klippy_apis = None
        logging.info("Initializing LMNT Marketplace Plugin (modular version)")
        
        try:
            # Get the directory of this file
            current_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Add the lmnt_marketplace directory to the Python path
            marketplace_dir = os.path.join(current_dir, "lmnt_marketplace")
            if marketplace_dir not in sys.path:
                sys.path.insert(0, marketplace_dir)
            
            # Import the integration class directly
            # This avoids the circular import issue
            from lmnt_marketplace.integration import LmntMarketplaceIntegration
            
            # Initialize the modular integration
            self.integration = LmntMarketplaceIntegration(config, self.server)
            
            logging.info(f"Successfully imported LmntMarketplaceIntegration from {marketplace_dir}")
        except Exception as e:
            logging.error(f"Error importing LmntMarketplaceIntegration: {str(e)}")
            logging.error(f"Traceback: {traceback.format_exc()}")
            raise
        
        # Register server components
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_klippy_shutdown)
        
        # Register legacy endpoints for backward compatibility
        self._register_legacy_endpoints()
        
        logging.info("LMNT Marketplace Plugin initialized successfully")
    
    async def _handle_klippy_ready(self):
        """Called when Klippy reports ready"""
        # Get Klippy APIs
        self.klippy_apis = self.server.lookup_component('klippy_apis')
        
        # Initialize the integration with Klippy APIs
        await self.integration.initialize(self.klippy_apis)
    
    async def _handle_klippy_shutdown(self):
        """Called when Klippy reports shutdown"""
        self.klippy_apis = None
        await self.integration.handle_klippy_shutdown()
    
    def _register_legacy_endpoints(self):
        """Register legacy endpoints for backward compatibility"""
        try:
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
            
            # Status endpoint
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/status", 
                RequestType.GET, 
                self._handle_status
            )
            
            logging.info("Registered LMNT Marketplace legacy endpoints")
        except Exception as e:
            logging.error(f"Error registering legacy endpoints: {str(e)}")
    
    # Legacy endpoint handlers that delegate to the modular integration
    
    async def _handle_user_login(self, web_request):
        """Handle user login (legacy endpoint)"""
        try:
            # Extract login credentials
            login_data = await web_request.get_json_data()
            username = login_data.get('username')
            password = login_data.get('password')
            
            if not username or not password:
                raise web_request.error("Missing username or password", 400)
            
            # Delegate to the auth manager
            result = await self.integration.auth_manager.login_user(username, password)
            return result
        except Exception as e:
            logging.error(f"Error during user login: {str(e)}")
            raise web_request.error(str(e), 500)
    
    async def _handle_register_printer(self, web_request):
        """Handle printer registration (legacy endpoint)"""
        try:
            # Extract registration data
            reg_data = await web_request.get_json_data()
            user_token = reg_data.get('user_token')
            printer_name = reg_data.get('printer_name')
            
            if not user_token or not printer_name:
                raise web_request.error("Missing user token or printer name", 400)
            
            # Delegate to the auth manager
            result = await self.integration.auth_manager.register_printer(user_token, printer_name)
            return result
        except Exception as e:
            logging.error(f"Error during printer registration: {str(e)}")
            raise web_request.error(str(e), 500)
    
    async def _handle_manual_check_jobs(self, web_request):
        """Handle manual job check (legacy endpoint)"""
        try:
            # Delegate to the job manager
            result = await self.integration.job_manager.check_for_jobs()
            return {"status": "success", "jobs": result}
        except Exception as e:
            logging.error(f"Error checking for jobs: {str(e)}")
            raise web_request.error(str(e), 500)
    
    async def _handle_status(self, web_request):
        """Handle status request (legacy endpoint)"""
        try:
            # Get status from various managers
            auth_status = self.integration.auth_manager.get_status()
            job_status = await self.integration.job_manager.get_status()
            
            # Combine status information
            status = {
                "auth": auth_status,
                "jobs": job_status,
                "version": self.integration.api_version
            }
            
            return status
        except Exception as e:
            logging.error(f"Error getting status: {str(e)}")
            raise web_request.error(str(e), 500)


def load_component(config):
    """Load the LMNT Marketplace Plugin component
    
    Args:
        config: component configuration
    
    Returns:
        LmntMarketplacePlugin: initialized plugin instance
    """
    return LmntMarketplacePlugin(config)
EOF

# Check Python cache files and remove them
echo "Removing Python cache files..."
find "${COMPONENT_DIR}" -name "*.pyc" -delete 2>/dev/null || true
find "${COMPONENT_DIR}" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "Restarting Moonraker..."
sudo systemctl restart moonraker

echo "Waiting for Moonraker to start..."
sleep 5

echo "Checking Moonraker logs for errors..."
tail -n 20 ~/printer_data/logs/moonraker.log

echo "Fix completed. If you still see errors, please check the logs for more details."
