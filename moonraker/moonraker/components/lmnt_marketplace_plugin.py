# LMNT Marketplace Plugin for Moonraker
# Integrates 3D printers with the LMNT Marketplace for secure model printing
# This is a thin wrapper that loads the modular LMNT Marketplace integration

import logging
import asyncio
from moonraker.common import RequestType

# Import the modular integration
from .lmnt_marketplace import LmntMarketplaceIntegration

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
        
        # Initialize the modular integration
        self.integration = LmntMarketplaceIntegration(config, self.server)
        
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
