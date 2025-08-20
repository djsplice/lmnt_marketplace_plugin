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

            # Lightweight local UI for pairing/registration and status
            self.server.register_endpoint(
                "/machine/lmnt_marketplace/ui",
                RequestType.GET,
                self._handle_ui,
                wrap_result=False,
                content_type='text/html; charset=UTF-8',
                auth_required=False  # Local-only convenience page
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

    async def _handle_ui(self, web_request):
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
            parts.append("    :root { --bg:#0D0D0D; --panel:#404040; --muted:#98A6A4; --text:#edf6e9; --accent:#7ee4a4; --accentHover:#a9ecca; --primary:#bfbfbf; --primaryHover:#adadad; --border:#525252; }\n")
            parts.append("    body { background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, sans-serif; margin:0; padding:24px; }\n")
            parts.append("    .container { max-width: 900px; margin: 0 auto; }\n")
            parts.append("    .card { background: var(--panel); border:1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 16px; }\n")
            parts.append("    h1 { font-size: 22px; margin: 0 0 12px; }\n    h2 { font-size: 18px; margin: 0 0 10px; color: var(--muted); }\n")
            parts.append("    label { display:block; font-size: 13px; color: var(--muted); margin: 10px 0 4px; }\n")
            parts.append("    input { width: 100%; padding: 10px; border-radius: 8px; border:1px solid var(--border); background:#1a1a1a; color:var(--text); }\n")
            parts.append("    button { background: var(--accent); border:none; color:#0D0D0D; padding:10px 14px; border-radius: 8px; cursor:pointer; font-weight:700; }\n")
            parts.append("    button:hover { background: var(--accentHover); }\n    button:disabled { opacity: .6; cursor:not-allowed; }\n")
            parts.append("    button.loading { position: relative; padding-left: 36px; }\n")
            parts.append("    .spinner { position:absolute; left:12px; top:50%; width:16px; height:16px; margin-top:-8px; border:2px solid rgba(13,13,13,0.25); border-top-color:#0D0D0D; border-radius:50%; animation: spin 1s linear infinite; }\n")
            parts.append("    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }\n")
            parts.append("    .row { display:flex; gap:12px; flex-wrap: wrap; }\n    .row > div { flex:1 1 220px; }\n")
            parts.append("    .badge { display:inline-block; padding:4px 8px; border-radius:9999px; font-size:12px; background:#2a2a2a; color:var(--muted); }\n")
            parts.append("    .pairCode { display:inline-block; padding:6px 10px; border-radius:8px; background: var(--accent); color:#0D0D0D; font-weight:800; letter-spacing:0.5px; }\n")
            parts.append("    pre { background: #1a1a1a; padding:12px; border-radius: 8px; overflow:auto; border:1px solid var(--border); }\n")
            parts.append("    .copyBtn { margin-left:8px; padding:4px 8px; font-size:12px; border-radius:6px; }\n")
            parts.append("    .muted { color: var(--muted); font-size:12px; margin-left:8px; }\n")
            parts.append("  </style>\n</head>\n<body>\n  <div class=\"container\">\n")
            parts.append("    <div class=\"card\">\n      <h1>LMNT Marketplace — Printer Setup</h1>\n")
            parts.append("      <div style=\"font-size:13px;color:var(--muted)\">Local UI for pairing this printer to your LMNT Marketplace account.</div>\n    </div>\n\n")
            parts.append("    <div class=\"card\" id=\"statusCard\">\n      <h2>Status</h2>\n      <div id=\"status\"><span class=\"badge\">Loading…</span></div>\n    </div>\n\n")
            parts.append("    <div class=\"card\">\n      <h2>Pairing</h2>\n      <div class=\"row\">\n        <div>\n          <label>Marketplace URL</label>\n          <input id=\"marketUrl\" placeholder=\"https://marketplace.local\" value=\"")
            parts.append(esc(market_url).replace('"', '\\"'))
            parts.append("\" />\n        </div>\n        <div>\n          <label>Printer Name</label>\n          <input id=\"printerName\" placeholder=\"My Printer\" value=\"")
            parts.append(esc(printer_name).replace('"', '\\"'))
            parts.append("\" />\n        </div>\n        <div>\n          <label>Manufacturer</label>\n          <input id=\"manufacturer\" value=\"LMNT\" />\n        </div>\n        <div>\n          <label>Model</label>\n          <input id=\"model\" placeholder=\"Optional\" />\n        </div>\n      </div>\n      <div style=\"margin-top:12px; display:flex; gap:10px;\">\n        <button id=\"startBtn\">Start Pairing</button>\n      </div>\n      <div style=\"margin-top:12px\">\n        <div id=\"pairInfo\"></div>\n        <pre id=\"pairJson\" style=\"display:none\"></pre>\n      </div>\n    </div>\n  </div>\n\n")
            # JS (single consolidated script)
            parts.append("  <script>\n    (function(){\n      const $ = (id) => document.getElementById(id);\n      const startBtn = $('startBtn');\n      let sessionId = null;\n      let pollTimer = null;\n      let statusTimer = null;\n\n      async function fetchJSON(path, opts={}){\n        const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));\n        if (!res.ok) throw new Error('HTTP ' + res.status);\n        return await res.json();\n      }\n\n      async function postJSON(path, body){\n        const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body||{}) });\n        if (!res.ok) throw new Error('HTTP ' + res.status);\n        return await res.json();\n      }\n\n      function renderStatus(s){\n        try {\n          const auth = s && s.auth ? s.auth : {};\n          const registered = !!auth.authenticated;\n          const printerId = auth.printer_id || '—';\n          const printerName = auth.printer_name || null;\n\n          const expiry = auth.token_expiry || null;\n          let humanExpiry = null;\n          let timeRemaining = null;\n          if (expiry) {\n            try {\n              const expMs = Date.parse(expiry);\n              if (!isNaN(expMs)) {\n                const diffMs = expMs - Date.now();\n                if (diffMs > 0) {\n                  const mins = Math.floor(diffMs / 60000);\n                  const hrs = Math.floor(mins / 60);\n                  const remMins = mins % 60;\n                  timeRemaining = (hrs > 0 ? (hrs + 'h ') : '') + remMins + 'm';\n                } else {\n                  timeRemaining = 'Expired';\n                }\n                const d = new Date(expiry);\n                if (!isNaN(d)) {\n                  humanExpiry = d.toLocaleString('en-US', { timeZone: 'UTC', month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) + ' UTC';\n                }\n              }\n            } catch (_) {}\n          }\n          let html = '';\n          html += '<div>Status: <b>' + (registered ? 'Registered' : 'Not Registered') + '</b></div>';\n          if (printerName) html += '<div>Printer Name: <b>' + printerName + '</b></div>';\n          html += '<div>Printer ID: <b>' + printerId + '</b>' + (printerId && printerId !== '—' ? ' <button class=\"copyBtn\" data-copy=\"' + printerId + '\">Copy</button>' : '') + '</div>';\n\n          if (expiry) html += '<div>Token expiry: <b>' + (humanExpiry || expiry) + '</b>' + (humanExpiry ? ' <span class=\"muted\">(' + expiry + ')</span>' : '') + '</div>';\n          if (timeRemaining) html += '<div>Time remaining: <b>' + timeRemaining + '</b></div>';\n          if (s && s.version) html += '<div>Plugin version: <b>' + s.version + '</b></div>';\n          $('status').innerHTML = html;\n          // Wire up copy buttons\n          try {\n            const btns = document.querySelectorAll('.copyBtn');\n            btns.forEach(btn => {\n              btn.onclick = async () => {\n                try { await navigator.clipboard.writeText(btn.dataset.copy || '');\n                  const prev = btn.textContent; btn.textContent = 'Copied!';\n                  setTimeout(() => { btn.textContent = prev; }, 1200);\n                } catch(_) {}\n              };\n            });\n          } catch(_) {}\n        } catch (e) {\n          $('status').innerHTML = '<span class=\"badge\">Unable to parse status</span>';\n        }\n      }\n\n      async function loadStatus(){\n        try {\n          const s = await fetchJSON('/machine/lmnt_marketplace/status');\n          const payload = (s && s.result) ? s.result : s;\n          renderStatus(payload);\n        } catch (e) {\n          $('status').innerHTML = '<span class=\"badge\">Status error: ' + e.message + '</span>';\n        }\n      }\n\n      function showWaitingWithCode(code){\n        if (startBtn) startBtn.disabled = true;\n        const pi = $('pairInfo');\n        if (pi) {\n          var msg;\n          if (code) {\n            msg = 'Please go to your LMNT Profile page and use this code: ' +\n                  '<span class=\"pairCode\">' + code + '</span>' +\n                  ' to complete your printer registration!';\n          } else {\n            msg = 'Waiting for approval…';\n          }\n          pi.innerHTML = msg;\n        }\n      }\n\n      function setLoading(loading){\n        if (!startBtn) return;\n        if (loading) {\n          startBtn.disabled = true;\n          startBtn.classList.add('loading');\n          startBtn.dataset.label = startBtn.textContent;\n          startBtn.innerHTML = '<span class=\"spinner\"></span> Processing…';\n        } else {\n          startBtn.classList.remove('loading');\n          startBtn.innerHTML = startBtn.dataset.label || 'Start Pairing';\n          startBtn.disabled = false;\n        }\n      }\n\n      async function checkStatusAndMaybeComplete(){\n        try {\n          const st = await postJSON('/machine/lmnt_marketplace/pair/status', { session_id: sessionId });\n          const status = (st && (st.status || (st.result && st.result.status))) || 'unknown';\n          if (status === 'approved' || status === 'ready' || status === 'authorized') {\n            clearInterval(pollTimer);\n            await complete();\n          }\n        } catch(e){ /* ignore transient errors */ }\n      }\n\n      async function complete(){\n        try {\n          const done = await postJSON('/machine/lmnt_marketplace/pair/complete', { session_id: sessionId });\n          const pi = $('pairInfo');\n          if (pi) pi.innerHTML = 'Paired!';\n          const pj = $('pairJson');\n          if (pj) { pj.textContent = JSON.stringify(done, null, 2); pj.style.display = 'block'; }\n          // Optimistically update the Status card immediately using response\n          try {\n            const optimistic = { auth: {\n              authenticated: true,\n              printer_id: done && (done.printer_id || (done.result && done.result.printer_id)) || null,\n              token_expiry: done && (done.expiry || (done.result && done.result.expiry)) || null,\n            }};\n            renderStatus(optimistic);\n          } catch(_) {}\n          // Also pull fresh status from backend\n          try { loadStatus(); } catch(_) {}\n          setTimeout(() => { try { location.reload(); } catch(_) {} }, 1500);\n        } catch(e){\n          const pi = $('pairInfo');\n          if (pi) pi.innerHTML = 'Complete failed: ' + e.message;\n          setLoading(false);\n        }\n      }\n\n      async function startFlow(){\n        try {\n          const body = {\n            printer_name: $('printerName')?.value || 'Printer',\n            manufacturer: $('manufacturer')?.value || 'LMNT',\n            model: $('model')?.value || null\n          };\n          const res = await postJSON('/machine/lmnt_marketplace/pair/start', body);\n          sessionId = (res && (res.session_id || (res.result && res.result.session_id))) || null;\n          const code = (res && (res.pairing_code || (res.result && res.result.pairing_code))) || null;\n          const pj = $('pairJson');\n          if (pj) { pj.textContent = JSON.stringify(res, null, 2); pj.style.display = 'block'; }\n          if (sessionId){\n            showWaitingWithCode(code);\n            setLoading(true);\n            pollTimer = setInterval(checkStatusAndMaybeComplete, 2000);\n            if (typeof loadStatus === 'function'){\n              statusTimer = setInterval(() => { try { loadStatus(); } catch(_) {} }, 10000);\n            }\n          }\n        } catch(e){\n          const pi = $('pairInfo');\n          if (pi) pi.innerHTML = 'Error: ' + e.message;\n          setLoading(false);\n        }\n      }\n\n      if (startBtn) { startBtn.onclick = (ev) => { ev.preventDefault(); startFlow(); }; }\n      try { loadStatus(); } catch(_) {}\n      setInterval(() => { try { loadStatus(); } catch(_) {} }, 10000);\n    })();\n  </script>\n")
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
