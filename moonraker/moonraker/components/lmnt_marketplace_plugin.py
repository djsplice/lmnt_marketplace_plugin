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
        logging.info("[LMNT Marketplace] Initializing LMNT Marketplace Plugin (modular version)")
        logging.info(f"[LMNT Marketplace] Configuration parameters: {config.get_options()}")
        
        # Register our custom klippy_connection component - commented out as klippy.py and klippy_connection.py mods are reverted
        # try:
        #     # Attempt to register the custom klippy_connection component
        #     # Use absolute import for the deployed environment
        #     from moonraker.components import klippy_connection
        #     self.server.register_component(klippy_connection.KlippyConnection, "klippy_connection")
        #     logging.info("[LMNT Marketplace] Successfully registered custom klippy_connection component")
        # except ImportError as e:
        #     logging.error(f"[LMNT Marketplace] Error registering custom klippy_connection component: {e}")
        #     logging.error(f"[LMNT Marketplace] Traceback: {traceback.format_exc()}")
        
        try:
            # Use a relative import to avoid path manipulation
            # This avoids the circular import issue and module not found errors
            from .lmnt_marketplace import LmntMarketplaceIntegration
            
            # Initialize the modular integration
            self.integration = LmntMarketplaceIntegration(config, self.server)
            
            logging.info("[LMNT Marketplace] Successfully imported LmntMarketplaceIntegration using relative import")
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error importing LmntMarketplaceIntegration: {str(e)}")
            logging.error(f"[LMNT Marketplace] Traceback: {traceback.format_exc()}")
            raise
        
        # Register server components
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready)
        logging.info("[LMNT Marketplace] Registered event handler for server:klippy_ready")
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_klippy_shutdown)
        
        # Register legacy endpoints for backward compatibility
        self._register_legacy_endpoints()
        
        logging.info("[LMNT Marketplace] LMNT Marketplace Plugin initialized successfully")
    
    async def _handle_klippy_ready(self):
        """Called when Klippy reports ready"""
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        logging.info("[LMNT Marketplace] Klippy APIs initialized after klippy_ready event")
        if hasattr(self.integration, 'on_klippy_ready'):
            await self.integration.on_klippy_ready(self.klippy_apis)
            logging.info("[LMNT Marketplace] Integration on_klippy_ready method called")
        else:
            logging.warning("[LMNT Marketplace] Integration does not have on_klippy_ready method")
        
        # Initialize the integration with Klippy APIs
        await self.integration.initialize(self.klippy_apis)
        
        # Only start job polling if not already running
        if not self.integration.job_manager.job_polling_task:
            logging.info("[LMNT Marketplace] LMNT Plugin: Explicitly starting job polling after Klippy ready")
            self.integration.job_manager.setup_job_polling()
            logging.info("[LMNT Marketplace] LMNT Plugin: Job polling setup completed")
        else:
            logging.info("[LMNT Marketplace] LMNT Plugin: Job polling already running, skipping setup")
    
    async def _handle_klippy_shutdown(self):
        """Called when Klippy reports shutdown"""
        self.klippy_apis = None
        await self.integration.handle_klippy_shutdown()
        
    async def close(self):
        """Called when Moonraker is shutting down"""
        logging.info("[LMNT Marketplace] LMNT Marketplace Plugin shutting down")
        if hasattr(self, 'integration'):
            await self.integration.close()
    
    def get_status(self, eventtime):
        status = self.integration.get_status(eventtime) if hasattr(self.integration, 'get_status') else {}
        logging.debug(f"[LMNT Marketplace] Status requested at {eventtime}: {status}")
        return status
    
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
            
            # Token refresh endpoint
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/refresh_token", 
                RequestType.POST, 
                self._handle_refresh_token,
                auth_required=False  # Bypass Moonraker's JWT validation
            )
            
            logging.info("[LMNT Marketplace] Registered LMNT Marketplace legacy endpoints")
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error registering legacy endpoints: {str(e)}")
    
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
                    logging.exception("[LMNT Marketplace] Error parsing JSON request")
                    raise self.server.error("Invalid JSON in request body", 400)
            
            username = args.get('username')
            password = args.get('password')
            
            if not username or not password:
                raise self.server.error("Missing username or password", 400)
            
            # Log the request details
            logging.info(f"[LMNT Marketplace] Login request for user: {username}")
            logging.info(f"[LMNT Marketplace] Using CWS URL: {self.integration.cws_url}")
            
            # Delegate to the auth manager
            result = await self.integration.auth_manager.login_user(username, password)
            return result
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error during user login: {str(e)}")
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
                    logging.exception("[LMNT Marketplace] Error parsing JSON request")
                    raise self.server.error("Invalid JSON in request body", 400)
            
            user_token = args.get('user_token')
            printer_name = args.get('printer_name')
            manufacturer = args.get('manufacturer')
            model = args.get('model')
            
            # Only use token from request body
            if not user_token:
                logging.warning("[LMNT Marketplace] No user_token provided in request body")
            else:
                logging.info("[LMNT Marketplace] Using token from request body")
            
            if not user_token or not printer_name:
                raise self.server.error("Missing user token or printer name", 400)
            
            # Log registration request details
            logging.info(f"[LMNT Marketplace] Registering printer: {printer_name}, Manufacturer: {manufacturer}, Model: {model}")
            
            # Delegate to the auth manager
            result = await self.integration.auth_manager.register_printer(
                user_token, printer_name, manufacturer, model)
            return result
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error during printer registration: {str(e)}")
            raise self.server.error(str(e), 500)
    
    async def _handle_manual_check_jobs(self, web_request):
        """Handle manual job check (legacy endpoint)"""
        try:
            # For now, just return job status since check_for_jobs is not implemented
            job_status = await self.integration.job_manager.get_status()
            return {"status": "success", "message": "Job status retrieved", "job_status": job_status}
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error initiating job check: {str(e)}")
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
            logging.error(f"[LMNT Marketplace] Error getting status: {str(e)}")
            raise self.server.error(str(e), 500)
            
    async def _handle_refresh_token(self, web_request):
        """Handle printer token refresh (legacy endpoint)"""
        try:
            # Delegate to the auth manager
            result = await self.integration.auth_manager.refresh_printer_token()
            if result:
                return {
                    "status": "success",
                    "printer_id": self.integration.auth_manager.printer_id,
                    "expiry": self.integration.auth_manager.token_expiry.isoformat() 
                            if self.integration.auth_manager.token_expiry else None
                }
            else:
                raise self.server.error("Failed to refresh printer token", 500)
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error refreshing printer token: {str(e)}")
            raise self.server.error(str(e), 500)


def load_component(config):
    """Load the LMNT Marketplace Plugin component
    
    Args:
        config: component configuration
    
    Returns:
        LmntMarketplacePlugin: initialized plugin instance
    """
    return LmntMarketplacePlugin(config)
