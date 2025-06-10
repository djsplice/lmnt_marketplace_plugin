# LMNT Marketplace Plugin for Moonraker
# Integrates 3D printers with the LMNT Marketplace for secure model printing
# This is a thin wrapper that loads the modular LMNT Marketplace integration

import logging
import os
import sys
import traceback
import json as jsonw

from moonraker.common import RequestType

# Import will be done in __init__ to avoid circular imports
# We'll import LmntMarketplaceIntegration dynamically

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
            # Use a relative import to avoid path manipulation
            # This avoids the circular import issue and module not found errors
            from .lmnt_marketplace import LmntMarketplaceIntegration
            
            # Initialize the modular integration
            self.integration = LmntMarketplaceIntegration(config, self.server)
            
            logging.info("Successfully imported LmntMarketplaceIntegration using relative import")
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
        
    async def close(self):
        """Called when Moonraker is shutting down"""
        logging.info("LMNT Marketplace Plugin shutting down")
        if hasattr(self, 'integration'):
            await self.integration.close()
    
    def _register_legacy_endpoints(self):
        """Register legacy endpoints for backward compatibility"""
        try:
            # User authentication and printer registration endpoints
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/user_login", 
                RequestType.POST, 
                self._handle_user_login,
                auth_required=False  # Bypass Moonraker's JWT validation
            )
            
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/register_printer", 
                RequestType.POST, 
                self._handle_register_printer,
                auth_required=False  # Bypass Moonraker's JWT validation
            )
            
            # Manual job check endpoint (for local UI use only)
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/check_jobs", 
                RequestType.POST, 
                self._handle_manual_check_jobs,
                auth_required=False  # Bypass Moonraker's JWT validation
            )
            
            # Status endpoint
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/status", 
                RequestType.GET, 
                self._handle_status,
                auth_required=False  # Bypass Moonraker's JWT validation
            )
            
            logging.info("Registered LMNT Marketplace legacy endpoints")
        except Exception as e:
            logging.error(f"Error registering legacy endpoints: {str(e)}")
    
    # Legacy endpoint handlers that delegate to the modular integration
    
    async def _handle_user_login(self, web_request):
        """Handle user login (legacy endpoint)"""
        try:
            # Parse the request arguments
            args = {}
            
            # Try to get arguments from the request
            for key in web_request.get_args():
                args[key] = web_request.get_str(key)
            
            # If no arguments found, try to parse JSON from the body
            if not args:
                try:
                    # Get the raw body data
                    body = web_request.get_body()
                    if body:
                        args = jsonw.loads(body)
                except Exception:
                    logging.exception("Error parsing JSON request")
                    raise self.server.error("Invalid JSON in request body", 400)
            
            username = args.get('username')
            password = args.get('password')
            
            if not username or not password:
                raise self.server.error("Missing username or password", 400)
            
            # Log the request details
            logging.info(f"Login request for user: {username}")
            logging.info(f"Using CWS URL: {self.integration.cws_url}")
            
            # Delegate to the auth manager
            result = await self.integration.auth_manager.login_user(username, password)
            return result
        except Exception as e:
            logging.error(f"Error during user login: {str(e)}")
            raise self.server.error(str(e), 500)
    
    async def _handle_register_printer(self, web_request):
        """Handle printer registration (legacy endpoint)"""
        try:
            # Parse the request arguments
            args = {}
            
            # Try to get arguments from the request
            for key in web_request.get_args():
                args[key] = web_request.get_str(key)
            
            # If no arguments found, try to parse JSON from the body
            if not args:
                try:
                    # Get the raw body data
                    body = web_request.get_body()
                    if body:
                        args = jsonw.loads(body)
                except Exception:
                    logging.exception("Error parsing JSON request")
                    raise self.server.error("Invalid JSON in request body", 400)
            
            user_token = args.get('user_token')
            printer_name = args.get('printer_name')
            manufacturer = args.get('manufacturer')
            model = args.get('model')
            
            # Check for Authorization header if user_token is not in body
            if not user_token:
                auth_header = web_request.headers.get('Authorization')
                if auth_header and auth_header.startswith('Bearer '):
                    user_token = auth_header[7:]  # Remove 'Bearer ' prefix
                    logging.info("Using token from Authorization header")
            
            if not user_token or not printer_name:
                raise self.server.error("Missing user token or printer name", 400)
            
            # Log registration request details
            logging.info(f"Registering printer: {printer_name}, Manufacturer: {manufacturer}, Model: {model}")
            
            # Delegate to the auth manager
            result = await self.integration.auth_manager.register_printer(
                user_token, printer_name, manufacturer, model)
            return result
        except Exception as e:
            logging.error(f"Error during printer registration: {str(e)}")
            raise self.server.error(str(e), 500)
    
    async def _handle_manual_check_jobs(self, web_request):
        """Handle manual job check (legacy endpoint)"""
        try:
            # Delegate to the job manager
            result = await self.integration.job_manager.check_for_jobs()
            return {"status": "success", "message": "Job check initiated"}
        except Exception as e:
            logging.error(f"Error initiating job check: {str(e)}")
            raise self.server.error(str(e), 500)
    
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
            raise self.server.error(str(e), 500)


def load_component(config):
    """Load the LMNT Marketplace Plugin component
    
    Args:
        config: component configuration
    
    Returns:
        LmntMarketplacePlugin: initialized plugin instance
    """
    return LmntMarketplacePlugin(config)
