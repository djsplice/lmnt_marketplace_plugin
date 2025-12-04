# LMNT Marketplace Plugin for Moonraker
# Integrates 3D printers with the LMNT Marketplace for secure model printing
# This is a thin wrapper that loads the modular LMNT Marketplace integration

import logging
import os
import sys
import traceback
import json as jsonw
import time

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
        # Simple in-memory rate limiting state
        self._rate_limit_state = {}
        
        # Register our custom klippy_connection component - commented out as klippy.py and klippy_connection.py mods are reverted
        
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

    # --- Helpers ---
    def _rate_limit(self, name: str, min_interval_sec: float):
        """Tiny in-memory rate limiter by operation name.
        Raises a 429 if called more frequently than min_interval_sec.
        """
        try:
            now = time.monotonic()
            last = self._rate_limit_state.get(name)
            if last is not None and (now - last) < min_interval_sec:
                raise self.server.error("Too many requests", 429)
            self._rate_limit_state[name] = now
        except AttributeError:
            # Fallback if server.error is not available for some reason
            raise Exception("Too many requests")
    
    def get_status(self, eventtime):
        status = self.integration.get_status(eventtime) if hasattr(self.integration, 'get_status') else {}
        logging.debug(f"[LMNT Marketplace] Status requested at {eventtime}: {status}")
        return status
    
    def _register_legacy_endpoints(self):
        """Register legacy endpoints for backward compatibility"""
        try:
            # User authentication and printer registration endpoints
            
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

            # Lightweight local UI for pairing/registration and status
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/ui",
                RequestType.GET,
                self._handle_ui_new,
                wrap_result=False,
                content_type='text/html; charset=UTF-8',
                auth_required=False  # Local-only convenience page
            )
            
            # Static file endpoints for new UI
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/ui/styles.css",
                RequestType.GET,
                self._handle_ui_css,
                wrap_result=False,
                content_type='text/css; charset=UTF-8',
                auth_required=False
            )
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/ui/script.js",
                RequestType.GET,
                self._handle_ui_js,
                wrap_result=False,
                content_type='application/javascript; charset=UTF-8',
                auth_required=False
            )
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/ui/lmnt-logo-v2.svg",
                RequestType.GET,
                self._handle_ui_logo,
                wrap_result=False,
                content_type='image/svg+xml; charset=UTF-8',
                auth_required=False
            )

            # Start pairing endpoint (device-initiated registration)
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/start_pairing",
                RequestType.POST,
                self._handle_start_pairing,
                auth_required=False  # Public within LAN; no Moonraker JWT required
            )

            # Marketplace pairing flow endpoints
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/pair/start",
                RequestType.POST,
                self._handle_pair_start,
                wrap_result=False,
                auth_required=False
            )
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/pair/status",
                RequestType.POST,
                self._handle_pair_status,
                wrap_result=False,
                auth_required=False
            )
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/pair/complete",
                RequestType.POST,
                self._handle_pair_complete,
                wrap_result=False,
                auth_required=False
            )
            
            logging.info("[LMNT Marketplace] Registered LMNT Marketplace legacy endpoints")
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error registering legacy endpoints: {str(e)}")

    # Legacy endpoint handlers that delegate to the modular integration
    

    async def _handle_pair_start(self, web_request):
        """Start pairing with marketplace by forwarding key + metadata."""
        try:
            # Rate limit to avoid rapid repeats
            self._rate_limit('pair_start', 0.75)
            args = {}
            for key in web_request.get_args():
                args[key] = web_request.get_str(key)
            if not args:
                body = web_request.get_body()
                if body:
                    try:
                        args = jsonw.loads(body)
                    except Exception:
                        logging.exception("[LMNT Marketplace] pair/start: invalid JSON body")
                        raise self.server.error("Invalid JSON in request body", 400)
            printer_name = args.get('printer_name') or self.integration.auth_manager.printer_name or 'Printer'
            manufacturer = args.get('manufacturer') or 'LMNT'
            model = args.get('model') or None
            result = await self.integration.auth_manager.start_pairing(printer_name, manufacturer, model)
            return result
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error during pair/start: {str(e)}")
            raise self.server.error(str(e), 500)

    async def _handle_pair_status(self, web_request):
        """Check pairing status with marketplace using session_id."""
        try:
            # Slightly permissive: allow one every 0.5s (UI polls every 2s)
            self._rate_limit('pair_status', 0.5)
            args = {}
            for key in web_request.get_args():
                args[key] = web_request.get_str(key)
            if not args:
                body = web_request.get_body()
                if body:
                    try:
                        args = jsonw.loads(body)
                    except Exception:
                        logging.exception("[LMNT Marketplace] pair/status: invalid JSON body")
                        raise self.server.error("Invalid JSON in request body", 400)
            session_id = args.get('session_id')
            if not session_id:
                raise self.server.error("Missing session_id", 400)
            result = await self.integration.auth_manager.pairing_status(session_id)
            return result
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error during pair/status: {str(e)}")
            raise self.server.error(str(e), 500)

    async def _handle_pair_complete(self, web_request):
        """Complete pairing with marketplace and save token."""
        try:
            # Prevent accidental double-submits
            self._rate_limit('pair_complete', 0.75)
            args = {}
            for key in web_request.get_args():
                args[key] = web_request.get_str(key)
            if not args:
                body = web_request.get_body()
                if body:
                    try:
                        args = jsonw.loads(body)
                    except Exception:
                        logging.exception("[LMNT Marketplace] pair/complete: invalid JSON body")
                        raise self.server.error("Invalid JSON in request body", 400)
            session_id = args.get('session_id')
            if not session_id:
                raise self.server.error("Missing session_id", 400)
            result = await self.integration.auth_manager.complete_pairing(session_id)
            return result
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error during pair/complete: {str(e)}")
            raise self.server.error(str(e), 500)

    async def _handle_start_pairing(self, web_request):
        """Initiate printer pairing and return key material + metadata.

        This does not contact the marketplace yet; it returns the printer's
        X25519 public key (Base64), key fingerprint, and echoes provided
        metadata so the caller can proceed to the marketplace pairing step.
        """
        try:
            # Parse args/body
            args = {}
            for key in web_request.get_args():
                args[key] = web_request.get_str(key)

            if not args:
                body = web_request.get_body()
                if body:
                    try:
                        args = jsonw.loads(body)
                    except Exception:
                        logging.exception("[LMNT Marketplace] start_pairing: invalid JSON body")
                        raise self.server.error("Invalid JSON in request body", 400)

            printer_name = args.get('printer_name') or self.integration.auth_manager.printer_name
            manufacturer = args.get('manufacturer') or 'LMNT'
            model = args.get('model') or None

            # Ensure keypair exists and fetch public key + fingerprint
            if not self.integration.auth_manager.dlt_private_key:
                # Attempt to load/generate via initialize path already executed
                logging.info("[LMNT Marketplace] start_pairing: ensuring keypair")
                # Best-effort ensure: call internal method if present
                if hasattr(self.integration.auth_manager, '_ensure_dlt_keypair'):
                    self.integration.auth_manager._ensure_dlt_keypair()

            pub_b64 = None
            key_id = None
            if hasattr(self.integration.auth_manager, 'get_public_key_b64'):
                pub_b64 = self.integration.auth_manager.get_public_key_b64()
            if hasattr(self.integration.auth_manager, 'get_key_fingerprint'):
                key_id = self.integration.auth_manager.get_key_fingerprint()

            if not pub_b64 or not key_id:
                logging.error("[LMNT Marketplace] start_pairing: missing key material")
                raise self.server.error("Key material not available", 500)

            response = {
                "status": "ok",
                "key_type": "x25519",
                "public_key": pub_b64,
                "key_id": key_id,
                "printer": {
                    "name": printer_name,
                    "manufacturer": manufacturer,
                    "model": model
                }
            }
            return response
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error during start_pairing: {str(e)}")
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

    async def _handle_ui_new(self, web_request):
        """Serve the new file-based HTML UI for pairing and status."""
        try:
            import os
            market_url = getattr(self.integration, 'marketplace_url', None) or ""
            printer_name = getattr(self.integration.auth_manager, 'printer_name', None) or ""
            
            # Get the path to the ui directory
            ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
            html_path = os.path.join(ui_dir, 'index.html')
            
            # Read the HTML template
            with open(html_path, 'r', encoding='utf-8') as f:
                html = f.read()
            
            # Replace template variables
            html = html.replace('{{ market_url }}', market_url)
            html = html.replace('{{ printer_name }}', printer_name)
            
            return html
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error serving new UI: {e}")
            raise self.server.error(str(e), 500)
    
    async def _handle_ui_css(self, web_request):
        """Serve the CSS file for the UI."""
        try:
            import os
            ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
            css_path = os.path.join(ui_dir, 'styles.css')
            with open(css_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error serving CSS: {e}")
            raise self.server.error(str(e), 500)
    
    async def _handle_ui_js(self, web_request):
        """Serve the JavaScript file for the UI."""
        try:
            import os
            ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
            # Try both script.js and scripts.js for compatibility
            js_path = os.path.join(ui_dir, 'script.js')
            if not os.path.exists(js_path):
                js_path = os.path.join(ui_dir, 'scripts.js')
            with open(js_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error serving JS: {e}")
            raise self.server.error(str(e), 500)
    
    async def _handle_ui_logo(self, web_request):
        """Serve the SVG logo file for the UI."""
        try:
            import os
            ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
            logo_path = os.path.join(ui_dir, 'lmnt-logo-v2.svg')
            if not os.path.exists(logo_path):
                # Fallback to a simple SVG if the logo file is not found
                return '<svg viewBox="0 0 100 30" xmlns="http://www.w3.org/2000/svg"><text x="10" y="20" fill="#7ee4a4" font-size="18" font-weight="bold">LMNT</text></svg>'
            with open(logo_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error serving logo: {e}")
            # Return a simple fallback SVG
            return '<svg viewBox="0 0 100 30" xmlns="http://www.w3.org/2000/svg"><text x="10" y="20" fill="#7ee4a4" font-size="18" font-weight="bold">LMNT</text></svg>'
    
    async def _handle_ui_old(self, web_request):
        """Serve a minimal HTML UI for pairing and status."""
        try:
            # Defaults
            market_url = getattr(self.integration, 'marketplace_url', None) or ""
            printer_name = getattr(self.integration.auth_manager, 'printer_name', None) or ""

            def esc(s):
                try:
                    s = str(s)
                except Exception:
                    return ""
                return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

            parts = []
            parts.append("<!DOCTYPE html>\n")
            parts.append("<html lang=\"en\">\n<head>\n")
            parts.append("  <meta charset=\"utf-8\" />\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n")
            parts.append("  <title>LMNT Marketplace - Printer Setup</title>\n")
            parts.append("  <style>\n")
            # OKLCH color system from shared-theme.css
            parts.append("    :root {\n")
            parts.append("      --bg-deepest: oklch(0.05 0 0); --bg-deep: oklch(0.15 0 0); --bg: oklch(0.05 0 0);\n")
            parts.append("      --surface: oklch(0.30 0 0); --surface-mid: oklch(0.35 0 0); --surface-elevated: oklch(0.37 0 0);\n")
            parts.append("      --primary: oklch(0.78 0 0); --primary-hover: oklch(0.71 0 0);\n")
            parts.append("      --secondary: oklch(0.51 0 0); --secondary-hover: oklch(0.58 0 0);\n")
            parts.append("      --accent: oklch(0.93 0.12 162); --accent-hover: oklch(0.90 0.11 164); --accent-dark: oklch(0.87 0.14 158);\n")
            parts.append("      --text-primary: oklch(0.96 0.02 135); --text-secondary: oklch(0.51 0 0); --text-muted: oklch(0.67 0.02 192); --text-accent: oklch(0.87 0.14 158);\n")
            parts.append("      --border-subtle: oklch(0.25 0 0); --border: oklch(0.37 0 0); --border-accent: oklch(0.67 0.02 192);\n")
            parts.append("      --success: oklch(0.82 0.17 145); --warning: oklch(0.85 0.16 85); --error: oklch(0.73 0.18 25);\n")
            parts.append("    }\n")
            parts.append("    * { box-sizing: border-box; }\n")
            parts.append("    body { background: linear-gradient(135deg, #0a0a0a 0%, #1a1a1a 50%, #0d0d0d 100%); background-attachment: fixed; color: var(--text-primary); font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin:0; padding:0; line-height:1.6; min-height: 100vh; }\n")
            parts.append("    .header { position: sticky; top: 0; z-index: 1000; background: linear-gradient(180deg, rgba(18, 18, 18, 0.96) 0%, rgba(12, 12, 12, 0.94) 100%); backdrop-filter: blur(10px); border-bottom: 1px solid rgba(126, 228, 164, 0.16); box-shadow: 0 18px 36px rgba(0, 0, 0, 0.65), 0 8px 18px rgba(0, 0, 0, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.12), inset 0 -2px 4px rgba(0, 0, 0, 0.35); }\n")
            parts.append("    .header-inner { max-width: 1200px; margin: 0 auto; padding: 1rem 1.5rem; display: flex; justify-content: space-between; align-items: center; }\n")
            parts.append("    .logo-group { display: flex; align-items: center; gap: 0.75rem; }\n")
            parts.append("    .logo-link { text-decoration: none; display: flex; align-items: center; }\n")
            parts.append("    .logo-svg { height: 40px; width: auto; }\n")
            parts.append("    .nav { display: flex; align-items: center; gap: 1.5rem; }\n")
            parts.append("    .nav-item { color: var(--text-primary); text-decoration: none; font-size: 0.9375rem; font-weight: 500; transition: color 0.2s; }\n")
            parts.append("    .nav-item:hover { color: var(--accent-dark); }\n")
            parts.append("    .btn-accent { background: var(--accent); color: var(--bg); padding: 0.5rem 1rem; border-radius: 0.5rem; font-weight: 600; text-decoration: none; transition: background 0.2s; border: none; cursor: pointer; }\n")
            parts.append("    .btn-accent:hover { background: var(--accent-hover); }\n")
            parts.append("    .container { max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }\n")
            parts.append("    .page-title { font-size: 2rem; font-weight: 700; margin: 0 0 0.5rem; color: var(--text-primary); }\n")
            parts.append("    .page-subtitle { font-size: 1rem; color: var(--text-muted); margin: 0 0 2rem; }\n")
            parts.append("    .card { background: rgba(64, 64, 64, 0.38); backdrop-filter: blur(26px) saturate(200%); -webkit-backdrop-filter: blur(26px) saturate(200%); border:1px solid rgba(126, 228, 164, 0.28); border-radius: 18px; padding: 1.75rem; margin-bottom: 1.75rem; box-shadow: 0 16px 45px rgba(0, 0, 0, 0.65), 0 8px 25px rgba(0, 0, 0, 0.42), 0 4px 16px rgba(126, 228, 164, 0.14), inset 0 2px 0 rgba(255, 255, 255, 0.14), inset 0 -2px 0 rgba(0, 0, 0, 0.35); }\n")
            parts.append("    .card-title { font-size: 1.25rem; font-weight: 600; margin: 0 0 1rem; color: var(--text-accent); }\n")
            parts.append("    .card-section { margin-bottom: 1.5rem; }\n    .card-section:last-child { margin-bottom: 0; }\n")
            parts.append("    label { display:block; font-size: 0.875rem; color: var(--text-muted); margin: 0 0 0.375rem; font-weight: 500; }\n")
            parts.append("    input { width: 100%; padding: 0.75rem; border-radius: 0.5rem; border:1px solid var(--border); background: var(--bg-deep); color: var(--text-primary); font-size: 0.9375rem; transition: border-color 0.2s; }\n")
            parts.append("    input:focus { outline: none; border-color: var(--accent-dark); }\n")
            parts.append("    button { background: var(--accent); border:none; color: var(--bg); padding: 0.75rem 1.25rem; border-radius: 0.5rem; cursor:pointer; font-weight:600; font-size: 0.9375rem; transition: background 0.2s; }\n")
            parts.append("    button:hover { background: var(--accent-hover); }\n    button:disabled { opacity: .5; cursor:not-allowed; }\n")
            parts.append("    button.loading { position: relative; padding-left: 2.5rem; }\n")
            parts.append("    .spinner { position:absolute; left:0.875rem; top:50%; width:1rem; height:1rem; margin-top:-0.5rem; border:2px solid rgba(13,13,13,0.25); border-top-color: var(--bg); border-radius:50%; animation: spin 1s linear infinite; }\n")
            parts.append("    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }\n")
            parts.append("    .row { display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:1rem; }\n")
            parts.append("    .badge { display:inline-flex; align-items:center; padding:0.375rem 0.75rem; border-radius:9999px; font-size:0.8125rem; background: var(--surface-elevated); color: var(--text-muted); font-weight: 500; }\n")
            parts.append("    .badge-success { background: rgba(74, 222, 128, 0.15); color: var(--success); }\n")
            parts.append("    .pairCode { display:inline-block; padding:0.5rem 0.875rem; border-radius:0.5rem; background: var(--accent); color: var(--bg); font-weight:700; letter-spacing:0.05em; font-size: 1.125rem; }\n")
            parts.append("    pre { background: var(--bg-deep); padding:1rem; border-radius: 0.5rem; overflow:auto; border:1px solid var(--border); font-size: 0.8125rem; }\n")
            parts.append("    .muted { color: var(--text-muted); font-size:0.8125rem; }\n")
            parts.append("    .status-card-body { display: flex; flex-direction: column; gap: 1.5rem; }\n")
            parts.append("    .status-summary { display: flex; align-items: center; gap: 1rem; padding: 1rem 1.25rem; border-radius: 1rem; background: linear-gradient(120deg, rgba(126, 228, 164, 0.12), rgba(126, 228, 164, 0.04)); border: 1px solid rgba(126, 228, 164, 0.18); box-shadow: inset 0 1px 0 rgba(255,255,255,0.12); }\n")
            parts.append("    .status-summary.status-warning { background: linear-gradient(120deg, rgba(250, 173, 20, 0.12), rgba(250, 173, 20, 0.04)); border-color: rgba(250, 173, 20, 0.28); }\n")
            parts.append("    .status-summary-label { font-size: 0.825rem; letter-spacing: 0.08em; text-transform: uppercase; color: rgba(255,255,255,0.64); font-weight: 600; }\n")
            parts.append("    .status-summary-value { font-size: 1.4rem; font-weight: 700; color: var(--text-primary); letter-spacing: 0.04em; }\n")
            parts.append("    .status-summary-subtle { font-size: 0.9rem; color: rgba(255,255,255,0.55); margin-top: 0.25rem; }\n")
            parts.append("    .status-icon { width: 3.25rem; height: 3.25rem; flex-shrink: 0; display: grid; place-items: center; border-radius: 50%; background: rgba(126, 228, 164, 0.18); border: 1px solid rgba(126, 228, 164, 0.3); box-shadow: 0 8px 18px rgba(126, 228, 164, 0.25); }\n")
            parts.append("    .status-summary.status-warning .status-icon { background: rgba(250, 173, 20, 0.16); border-color: rgba(250, 173, 20, 0.28); box-shadow: 0 8px 18px rgba(250, 173, 20, 0.22); }\n")
            parts.append("    .status-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }\n")
            parts.append("    .status-tile { position: relative; padding: 1rem 1.1rem 1rem 1rem; border-radius: 0.875rem; background: rgba(19, 19, 19, 0.8); border: 1px solid rgba(126, 228, 164, 0.12); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); box-shadow: inset 0 1px 0 rgba(255,255,255,0.06); display: flex; gap: 0.75rem; align-items: flex-start; }\n")
            parts.append("    .status-tile-icon { width: 2.25rem; height: 2.25rem; border-radius: 0.75rem; display: grid; place-items: center; background: rgba(126, 228, 164, 0.14); border: 1px solid rgba(126, 228, 164, 0.22); box-shadow: inset 0 1px 0 rgba(255,255,255,0.08); color: var(--accent-dark); }\n")
            parts.append("    .status-icon svg, .status-tile-icon svg { width: 22px; height: 22px; stroke-linecap: round; stroke-linejoin: round; }\n")
            parts.append("    .status-tile-label { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.07em; color: rgba(255,255,255,0.5); font-weight: 600; margin-bottom: 0.35rem; }")
            parts.append("\n    .status-tile-value { font-size: 1rem; color: var(--text-primary); font-weight: 600; word-break: break-word; }")
            parts.append("\n    .status-tile-subtle { font-size: 0.75rem; color: rgba(255,255,255,0.45); margin-top: 0.35rem; }")
            # Pairing code display styles
            parts.append("    .pairing-display { background: rgba(89, 89, 89, 0.3); backdrop-filter: blur(20px) saturate(180%); -webkit-backdrop-filter: blur(20px) saturate(180%); border: 2px solid var(--accent-dark); border-radius: 1rem; padding: 2rem; text-align: center; margin: 1.5rem 0; box-shadow: 0 16px 48px rgba(126, 228, 164, 0.2), 0 8px 24px rgba(0, 0, 0, 0.5), inset 0 2px 0 rgba(255, 255, 255, 0.2), inset 0 -2px 0 rgba(0, 0, 0, 0.4); }\n")
            parts.append("    .pairing-title { font-size: 1.125rem; font-weight: 600; color: var(--text-accent); margin: 0 0 1rem; }\n")
            parts.append("    .pairing-code-display { background: rgba(13, 13, 13, 0.7); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border: 2px dashed var(--accent-dark); border-radius: 0.75rem; padding: 1.5rem; margin: 1rem 0; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6), inset 0 0 30px rgba(126, 228, 164, 0.15), inset 0 2px 0 rgba(126, 228, 164, 0.1); position: relative; }\n")
            parts.append("    .pairing-code-label { font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.05em; }\n")
            parts.append("    .pairing-code-value { font-size: 2.5rem; font-weight: 700; color: var(--accent); font-family: 'Courier New', monospace; letter-spacing: 0.15em; margin: 0.5rem 0; user-select: all; text-shadow: 0 0 20px rgba(126, 228, 164, 0.5), 0 2px 4px rgba(0, 0, 0, 0.8); }\n")
            parts.append("    .copy-code-btn { position: absolute; top: 1rem; right: 1rem; background: var(--accent-dark); color: var(--bg); border: none; padding: 0.5rem 1rem; border-radius: 0.5rem; font-size: 0.875rem; font-weight: 600; cursor: pointer; transition: all 0.2s; box-shadow: 0 4px 12px rgba(126, 228, 164, 0.3); }\n")
            parts.append("    .copy-code-btn:hover { background: var(--accent); transform: translateY(-2px); box-shadow: 0 6px 16px rgba(126, 228, 164, 0.4); }\n")
            parts.append("    .copy-code-btn:active { transform: translateY(0); }\n")
            parts.append("    .pairing-instructions { font-size: 0.9375rem; color: var(--text-primary); line-height: 1.6; margin: 1rem 0; }\n")
            parts.append("    .pairing-instructions strong { color: var(--accent-dark); }\n")
            parts.append("    .pairing-url { display: inline-block; background: var(--bg-deep); padding: 0.375rem 0.75rem; border-radius: 0.375rem; color: var(--accent-dark); font-family: monospace; font-size: 0.875rem; margin: 0.5rem 0; }\n")
            parts.append("    .pairing-steps { text-align: left; max-width: 500px; margin: 1.5rem auto 0; }\n")
            parts.append("    .pairing-step { display: flex; gap: 0.75rem; margin-bottom: 1rem; align-items: flex-start; }\n")
            parts.append("    .step-number { flex-shrink: 0; width: 2rem; height: 2rem; background: var(--accent-dark); color: var(--bg); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 0.875rem; }\n")
            parts.append("    .step-content { flex: 1; padding-top: 0.25rem; }\n")
            parts.append("    .waiting-indicator { display: inline-flex; align-items: center; gap: 0.5rem; color: var(--text-muted); font-size: 0.875rem; margin-top: 1rem; }\n")
            parts.append("    .waiting-spinner { width: 1rem; height: 1rem; border: 2px solid var(--surface-elevated); border-top-color: var(--accent-dark); border-radius: 50%; animation: spin 1s linear infinite; }\n")
            # Fireworks animation
            parts.append("    @keyframes firework { 0% { transform: translate(0, 0) scale(1); opacity: 1; } 50% { opacity: 1; } 100% { transform: translate(var(--x), var(--y)) scale(0); opacity: 0; } }\n")
            parts.append("    .fireworks-container { position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 9999; }\n")
            parts.append("    .firework { position: absolute; width: 6px; height: 6px; border-radius: 50%; animation: firework 3s ease-out forwards; box-shadow: 0 0 8px currentColor; }\n")
            parts.append("    .success-celebration { animation: celebrate 0.6s ease-out; }\n")
            parts.append("    @keyframes celebrate { 0% { transform: scale(0.8); opacity: 0; } 50% { transform: scale(1.05); } 100% { transform: scale(1); opacity: 1; } }\n")
            parts.append("    .page-footer { margin-top: 3rem; text-align: center; font-size: 0.75rem; color: rgba(255,255,255,0.25); letter-spacing: 0.12em; text-transform: uppercase; }\n")
            parts.append("  </style>\n</head>\n<body>\n")
            # Fireworks container
            parts.append("  <div class=\"fireworks-container\" id=\"fireworks\"></div>\n\n")
            # Header with LMNT logo and navigation
            parts.append("  <header class=\"header\">\n    <div class=\"header-inner\">\n")
            parts.append("      <div class=\"logo-group\">\n")
            parts.append("        <a href=\"")
            parts.append(esc(market_url) if market_url else "#")
            parts.append("\" class=\"logo-link\">\n")
            # Inline SVG logo (simplified version)
            parts.append("          <svg class=\"logo-svg\" viewBox=\"0 0 220 60\" xmlns=\"http://www.w3.org/2000/svg\" aria-hidden=\"true\">\n")
            parts.append("            <g fill=\"#7ee4a4\" fill-rule=\"evenodd\">\n")
            parts.append("              <path d=\"M16 8L4 18.5v5.6L16 13.6l12 10.5v-5.6z\" opacity=\"0.8\"/>\n")
            parts.append("              <path d=\"M16 19.2L4 29.7v5.6L16 24.8l12 10.5v-5.6z\" opacity=\"0.65\"/>\n")
            parts.append("              <path d=\"M16 30.4L4 40.9v5.6L16 36l12 10.5v-5.6z\" opacity=\"0.5\"/>\n")
            parts.append("              <path d=\"M60 40V18h-9.7l-9.1 12.3V18H32v22h9.7V30.6L50.8 40H60Zm19.5.6c8.9 0 14.5-5.5 14.5-13.3 0-7.7-5.6-13.3-14.5-13.3-8.9 0-14.4 5.6-14.4 13.3 0 7.8 5.5 13.3 14.4 13.3Zm19.3-.6V18h-9.7v22h9.7Zm24.7 0V33h8.5c6.7 0 11.2-4.2 11.2-10.5 0-6.2-4.5-10.5-11.2-10.5H113V40h10.5Z\" transform=\"translate(22 4)\"/>\n")
            parts.append("            </g>\n")
            parts.append("          </svg>\n")
            parts.append("        </a>\n      </div>\n")
            parts.append("      <nav class=\"nav\">\n")
            parts.append("        <a href=\"")
            parts.append(esc(market_url) if market_url else "#")
            parts.append("\" class=\"nav-item\">Marketplace</a>\n")
            parts.append("        <a href=\"")
            parts.append(esc(market_url) if market_url else "#")
            parts.append("/about\" class=\"nav-item\">About</a>\n")
            parts.append("      </nav>\n    </div>\n  </header>\n\n")
            # Main content
            parts.append("  <div class=\"container\">\n")
            parts.append("    <h1 class=\"page-title\">Printer Setup</h1>\n")
            parts.append("    <p class=\"page-subtitle\">Connect your 3D printer to the LMNT Marketplace for secure, encrypted model printing.</p>\n\n")
            parts.append("    <div class=\"card\" id=\"statusCard\">\n      <h2 class=\"card-title\">Status</h2>\n      <div id=\"status\" class=\"status-card-body\">\n        <div class=\"status-summary\">\n          <div class=\"status-icon\"><div class=\"waiting-spinner\"></div></div>\n          <div>\n            <div class=\"status-summary-label\">Connection</div>\n            <div class=\"status-summary-value\">Loading…</div>\n            <div class=\"status-summary-subtle\">Retrieving printer details</div>\n          </div>\n        </div>\n      </div>\n    </div>\n\n")
            parts.append("    <div class=\"card\">\n      <h2 class=\"card-title\">Pairing</h2>\n      <div class=\"row\">\n        <div>\n          <label>Printer Name</label>\n          <input id=\"printerName\" placeholder=\"My Printer\" value=\"")
            parts.append(esc(printer_name).replace('"', '\\"'))
            parts.append("\" />\n        </div>\n        <div>\n          <label>Manufacturer</label>\n          <input id=\"manufacturer\" value=\"LMNT\" />\n        </div>\n        <div>\n          <label>Model</label>\n          <input id=\"model\" placeholder=\"Optional\" />\n        </div>\n      </div>\n      <div class=\"card-section\" style=\"display:flex; gap:0.75rem; margin-top:1.5rem;\">\n        <button id=\"startBtn\">Start Pairing</button>\n      </div>\n      <div class=\"card-section\" id=\"pairInfoSection\" style=\"display:none;\">\n        <div id=\"pairInfo\"></div>\n        <div class=\"pairing-display\" id=\"pairingDisplay\" style=\"display:none;\">\n          <div class=\"pairing-title\">Pairing Code Generated</div>\n          <div class=\"pairing-code-display\">\n            <button class=\"copy-code-btn\" id=\"copyCodeBtn\">Copy Code</button>\n            <div class=\"pairing-code-label\">Your Pairing Code</div>\n            <div class=\"pairing-code-value\" id=\"pairCodeValue\"></div>\n          </div>\n          <div class=\"pairing-steps\">\n            <div class=\"pairing-step\">\n              <div class=\"step-number\">1</div>\n              <div class=\"step-content\">\n                Go to your LMNT Marketplace profile page:\n                <div class=\"pairing-url\" id=\"pairingUrl\">https://marketplace.local/profile</div>\n              </div>\n            </div>\n            <div class=\"pairing-step\">\n              <div class=\"step-number\">2</div>\n              <div class=\"step-content\">Click <strong>\"Add Printer\"</strong> or <strong>\"Pair Printer\"</strong></div>\n            </div>\n            <div class=\"pairing-step\">\n              <div class=\"step-number\">3</div>\n              <div class=\"step-content\">Enter the pairing code shown above and click <strong>\"Authorize\"</strong></div>\n            </div>\n          </div>\n          <div class=\"waiting-indicator\">\n            <div class=\"waiting-spinner\"></div>\n            Waiting for authorization...\n          </div>\n        </div>\n        <pre id=\"pairJson\" style=\"display:none;\"></pre>\n      </div>\n    </div>\n\n")
            parts.append("    <footer class=\"page-footer\" id=\"pluginVersion\"></footer>\n\n")
            # JS (single consolidated script)
            parts.append("  <script>\n    (function(){\n      const $ = (id) => document.getElementById(id);\n      const startBtn = $('startBtn');\n      let sessionId = null;\n      let pollTimer = null;\n      let statusTimer = null;\n\n      async function fetchJSON(path, opts={}){\n        const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));\n        if (!res.ok) throw new Error('HTTP ' + res.status);\n        return await res.json();\n      }\n\n      async function postJSON(path, body){\n        const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body||{}) });\n        if (!res.ok) throw new Error('HTTP ' + res.status);\n        return await res.json();\n      }\n\n      function renderStatus(s){\n        try {\n          const auth = s && s.auth ? s.auth : {};\n          const registered = !!auth.authenticated;\n          const printerId = auth.printer_id || '—';\n          const printerName = auth.printer_name || '';\n          const expiry = auth.token_expiry || null;\n          let humanExpiry = null;\n          let timeRemaining = null;\n          if (expiry) {\n            try {\n              const expMs = Date.parse(expiry);\n              if (!isNaN(expMs)) {\n                const diffMs = expMs - Date.now();\n                if (diffMs > 0) {\n                  const mins = Math.floor(diffMs / 60000);\n                  const hrs = Math.floor(mins / 60);\n                  const remMins = mins % 60;\n                  timeRemaining = (hrs > 0 ? (hrs + 'h ') : '') + remMins + 'm';\n                } else {\n                  timeRemaining = 'Expired';\n                }\n                const d = new Date(expiry);\n                if (!isNaN(d)) {\n                  humanExpiry = d.toLocaleString('en-US', { timeZone: 'UTC', month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) + ' UTC';\n                }\n              }\n            } catch (_) {}\n          }\n\n          const summaryClass = registered ? 'status-summary' : 'status-summary status-warning';\n          const statusLabel = registered ? 'Connection' : 'Connection';\n          const summaryValue = registered ? 'Registered' : 'Awaiting Pairing';\n          const summarySubtle = registered\n            ? (printerName ? 'Authorized as ' + printerName : 'Secure pairing active.')\n            : 'Press “Start Pairing” to connect this printer to LMNT.';\n          const iconSuccess = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M9 12.5l2.2 2.2L19 7\"></path><circle cx=\"12\" cy=\"12\" r=\"9\"></circle></svg>';\n          const iconWarning = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M12 8v5\"></path><path d=\"M12 17h.01\"></path><path d=\"M10.3 3.86 2.38 18a2 2 0 0 0 1.74 3h15.76a2 2 0 0 0 1.74-3l-7.92-14.14a2 2 0 0 0-3.4 0z\"></path></svg>';\n          const iconPrinter = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M6 9V4h12v5\"></path><path d=\"M6 18h12v2H6z\"></path><rect x=\"4\" y=\"9\" width=\"16\" height=\"8\" rx=\"2\"></rect><path d=\"M8 13h8\"></path></svg>';\n          const iconClock = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><circle cx=\"12\" cy=\"12\" r=\"9\"></circle><path d=\"M12 7v5l3 3\"></path></svg>';\n          const iconShield = '<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M12 3 5 5v6c0 5.55 3.84 10.74 7 11 3.16-.26 7-5.45 7-11V5l-7-2z\"></path><path d=\"m9 12 2 2 4-4\"></path></svg>';\n          const statusIcon = registered ? iconSuccess : iconWarning;\n          const configuredMarketUrl = '" + (esc(market_url).replace("'", "\'") if market_url else "") + "';\n\n          const tiles = [];\n          const printerSubtitle = printerName\n            ? 'Named ' + printerName\n            : (registered ? 'Pairing complete.' : 'Pairing assigns a permanent printer ID.');\n          tiles.push(`\n            <div class=\"status-tile\">\n              <div class=\"status-tile-icon\">${iconPrinter}</div>\n              <div>\n                <div class=\"status-tile-label\">Printer</div>\n                <div class=\"status-tile-value\">${printerId && printerId !== '—' ? printerId : 'Not yet assigned'}</div>\n                ${printerSubtitle ? `<div class=\"status-tile-subtle\">${printerSubtitle}</div>` : ''}\n              </div>\n            </div>\n          `);\n\n          tiles.push(`\n            <div class=\"status-tile\">\n              <div class=\"status-tile-icon\">${iconClock}</div>\n              <div>\n                <div class=\"status-tile-label\">Access Token</div>\n                <div class=\"status-tile-value\">${humanExpiry || (registered ? 'Active' : 'Not issued')}</div>\n                ${timeRemaining ? `<div class=\"status-tile-subtle\">Renews in ${timeRemaining}</div>` : (expiry ? `<div class=\"status-tile-subtle\">Expires at ${expiry}</div>` : `<div class=\"status-tile-subtle\">${registered ? 'Automatically refreshed' : 'Issued after approval'}</div>`)}\n              </div>\n            </div>\n          `);\n\n          if (configuredMarketUrl) {\n            tiles.push(`\n              <div class=\"status-tile\">\n                <div class=\"status-tile-icon\">${iconShield}</div>\n                <div>\n                  <div class=\"status-tile-label\">Marketplace Host</div>\n                  <div class=\"status-tile-value\">${configuredMarketUrl}</div>\n                  <div class=\"status-tile-subtle\">All pairing requests use encrypted TLS.</div>\n                </div>\n              </div>\n            `);\n          }\n\n          const statusMarkup = `\n            <div class=\"status-card-body\">\n              <div class=\"${summaryClass}\">\n                <div class=\"status-icon\">${statusIcon}</div>\n                <div>\n                  <div class=\"status-summary-label\">${statusLabel}</div>\n                  <div class=\"status-summary-value\">${summaryValue}</div>\n                  <div class=\"status-summary-subtle\">${summarySubtle}</div>\n                </div>\n              </div>\n              ${tiles.length ? `<div class=\"status-grid\">${tiles.join('')}</div>` : ''}\n            </div>\n          `;\n\n          const statusEl = $('status');\n          if (statusEl) statusEl.innerHTML = statusMarkup;\n\n          const footer = $('pluginVersion');\n          if (footer) {\n            footer.textContent = s && s.version ? `LMNT Marketplace Plugin • v${s.version}` : '';\n          }\n        } catch (e) {\n          const statusEl = $('status');\n          if (statusEl) {\n            statusEl.innerHTML = `\n              <div class=\"status-card-body\">\n                <div class=\"status-summary status-warning\">\n                  <div class=\"status-icon\">${'<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M12 8v5\"></path><path d=\"M12 17h.01\"></path><path d=\"M10.3 3.86 2.38 18a2 2 0 0 0 1.74 3h15.76a2 2 0 0 0 1.74-3l-7.92-14.14a2 2 0 0 0-3.4 0z\"></path></svg>'}</div>\n                  <div>\n                    <div class=\"status-summary-label\">Status</div>\n                    <div class=\"status-summary-value\">Unavailable</div>\n                    <div class=\"status-summary-subtle\">${e && e.message ? e.message : 'Unable to parse status response.'}</div>\n                  </div>\n                </div>\n              </div>\n            `;\n          }\n          const footer = $('pluginVersion');\n          if (footer) footer.textContent = '';\n        }\n      }\n\n      async function loadStatus(){\n        try {\n          const s = await fetchJSON('/machine/lmnt_marketplace/status');\n          const payload = (s && s.result) ? s.result : s;\n          renderStatus(payload);\n        } catch (e) {\n          const statusEl = $('status');\n          if (statusEl) {\n            statusEl.innerHTML = `\n              <div class=\"status-card-body\">\n                <div class=\"status-summary status-warning\">\n                  <div class=\"status-icon\">${'<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M12 8v5\"></path><path d=\"M12 17h.01\"></path><path d=\"M10.3 3.86 2.38 18a2 2 0 0 0 1.74 3h15.76a2 2 0 0 0 1.74-3l-7.92-14.14a2 2 0 0 0-3.4 0z\"></path></svg>'}</div>\n                  <div>\n                    <div class=\"status-summary-label\">Status</div>\n                    <div class=\"status-summary-value\">Unavailable</div>\n                    <div class=\"status-summary-subtle\">${e && e.message ? e.message : 'Unable to reach LMNT Marketplace.'}</div>\n                  </div>\n                </div>\n              </div>\n            `;\n          }\n          const footer = $('pluginVersion');\n          if (footer) footer.textContent = '';\n        }\n      }\n\n      function showWaitingWithCode(code){\n        if (startBtn) startBtn.disabled = true;\n        const section = $('pairInfoSection');\n        const display = $('pairingDisplay');\n        const codeValue = $('pairCodeValue');\n        if (section && code) {\n          section.style.display = 'block';\n          if (display) display.style.display = 'block';\n          if (codeValue) {\n            codeValue.textContent = code;\n          }\n          // Wire up copy button\n          const copyBtn = $('copyCodeBtn');\n          if (copyBtn) {\n            copyBtn.onclick = async () => {\n              try {\n                await navigator.clipboard.writeText(code);\n                const prev = copyBtn.textContent;\n                copyBtn.textContent = 'Copied!';\n                setTimeout(() => { copyBtn.textContent = prev; }, 2000);\n              } catch(e) {\n                copyBtn.textContent = 'Failed';\n                setTimeout(() => { copyBtn.textContent = 'Copy Code'; }, 2000);\n              }\n            };\n          }\n          // Update marketplace URL in instructions from config\n          const marketUrl = ")
            parts.append("'")
            parts.append(esc(market_url) if market_url else "https://marketplace.local")
            parts.append("';\n          const pairingUrl = $('pairingUrl');\n          if (pairingUrl) {\n            pairingUrl.textContent = marketUrl + '/profile';\n          }\n        } else if (section) {\n          section.style.display = 'block';\n          const pi = $('pairInfo');\n          if (pi) pi.innerHTML = '<div class=\"waiting-indicator\"><div class=\"waiting-spinner\"></div> Waiting for approval…</div>';\n        }\n      }\n\n      function setLoading(loading){\n        if (!startBtn) return;\n        if (loading) {\n          startBtn.disabled = true;\n          startBtn.classList.add('loading');\n          startBtn.dataset.label = startBtn.textContent;\n          startBtn.innerHTML = '<span class=\"spinner\"></span> Processing…';\n        } else {\n          startBtn.classList.remove('loading');\n          startBtn.innerHTML = startBtn.dataset.label || 'Start Pairing';\n          startBtn.disabled = false;\n        }\n      }\n\n      async function checkStatusAndMaybeComplete(){\n        try {\n          const st = await postJSON('/machine/lmnt_marketplace/pair/status', { session_id: sessionId });\n          const status = (st && (st.status || (st.result && st.result.status))) || 'unknown';\n          if (status === 'approved' || status === 'ready' || status === 'authorized') {\n            clearInterval(pollTimer);\n            await complete();\n          }\n        } catch(e){ /* ignore transient errors */ }\n      }\n\n      function launchFireworks() {\n        const container = $('fireworks');\n        if (!container) return;\n        const colors = ['#7ee4a4', '#baf2d3', '#4ADE80', '#a9ecca', '#DFF2EF'];\n        const bursts = 8;\n        for (let b = 0; b < bursts; b++) {\n          setTimeout(() => {\n            const centerX = Math.random() * window.innerWidth;\n            const centerY = Math.random() * (window.innerHeight * 0.6);\n            const particles = 30;\n            for (let i = 0; i < particles; i++) {\n              const particle = document.createElement('div');\n              particle.className = 'firework';\n              const angle = (Math.PI * 2 * i) / particles;\n              const velocity = 50 + Math.random() * 100;\n              const x = Math.cos(angle) * velocity;\n              const y = Math.sin(angle) * velocity;\n              particle.style.left = centerX + 'px';\n              particle.style.top = centerY + 'px';\n              particle.style.background = colors[Math.floor(Math.random() * colors.length)];\n              particle.style.setProperty('--x', x + 'px');\n              particle.style.setProperty('--y', y + 'px');\n              container.appendChild(particle);\n              setTimeout(() => particle.remove(), 3000);\n            }\n          }, b * 200);\n        }\n      }\n\n      async function complete(){\n        try {\n          const done = await postJSON('/machine/lmnt_marketplace/pair/complete', { session_id: sessionId });\n          const display = $('pairingDisplay');\n          if (display) display.style.display = 'none';\n          const pi = $('pairInfo');\n          if (pi) {\n            pi.innerHTML = '<div class=\"pairing-display success-celebration\"><div class=\"pairing-title\" style=\"color: var(--success);\">✓ Pairing Successful!</div><div class=\"pairing-instructions\">Your printer has been successfully registered with the LMNT Marketplace.</div></div>';\n          }\n          setLoading(false);\n          launchFireworks();\n          // Optimistically update the Status card immediately using response\n          try {\n            const optimistic = { auth: {\n              authenticated: true,\n              printer_id: done && (done.printer_id || (done.result && done.result.printer_id)) || null,\n              token_expiry: done && (done.expiry || (done.result && done.result.expiry)) || null,\n            }};\n            renderStatus(optimistic);\n          } catch(_) {}\n          // Also pull fresh status from backend\n          try { loadStatus(); } catch(_) {}\n          setTimeout(() => { try { location.reload(); } catch(_) {} }, 1500);\n        } catch(e){\n          const pi = $('pairInfo');\n          if (pi) pi.innerHTML = 'Complete failed: ' + e.message;\n          setLoading(false);\n        }\n      }\n\n      async function startFlow(){\n        try {\n          const body = {\n            printer_name: $('printerName')?.value || 'Printer',\n            manufacturer: $('manufacturer')?.value || 'LMNT',\n            model: $('model')?.value || null\n          };\n          const res = await postJSON('/machine/lmnt_marketplace/pair/start', body);\n          sessionId = (res && (res.session_id || (res.result && res.result.session_id))) || null;\n          const code = (res && (res.pairing_code || (res.result && res.result.pairing_code))) || null;\n          const pj = $('pairJson');\n          if (pj) { pj.textContent = JSON.stringify(res, null, 2); pj.style.display = 'block'; }\n          if (sessionId){\n            showWaitingWithCode(code);\n            setLoading(true);\n            pollTimer = setInterval(checkStatusAndMaybeComplete, 2000);\n            if (typeof loadStatus === 'function'){\n              statusTimer = setInterval(() => { try { loadStatus(); } catch(_) {} }, 10000);\n            }\n          }\n        } catch(e){\n          const pi = $('pairInfo');\n          if (pi) pi.innerHTML = 'Error: ' + e.message;\n          setLoading(false);\n        }\n      }\n\n      if (startBtn) { startBtn.onclick = (ev) => { ev.preventDefault(); startFlow(); }; }\n      try { loadStatus(); } catch(_) {}\n      setInterval(() => { try { loadStatus(); } catch(_) {} }, 10000);\n    })();\n  </script>\n")
            html = "".join(parts)
            return html
        except Exception as e:
            logging.error(f"[LMNT Marketplace] Error serving UI: {e}")
            raise self.server.error(str(e), 500)

def load_component(config):
    """Load the LMNT Marketplace Plugin component
    
    Args:
        config: component configuration
    
    Returns:
        LmntMarketplacePlugin: initialized plugin instance
    """
    return LmntMarketplacePlugin(config)
