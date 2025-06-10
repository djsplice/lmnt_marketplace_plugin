"""
LMNT Marketplace Authentication Module

Handles authentication and token management for LMNT Marketplace integration:
- User authentication
- Printer registration and token management
- JWT token validation and refresh
"""

import os
import json
import logging
import asyncio
import aiohttp
import time
import base64
import re
from datetime import datetime, timedelta
import jwt

class AuthManager:
    """
    Manages authentication and token operations for LMNT Marketplace
    
    Handles user login, printer registration, token validation,
    token refresh, and JWT management.
    """
    
    def __init__(self, integration):
        """Initialize the Authentication Manager"""
        self.integration = integration
        self.printer_token = None
        self.token_expiry = None
        self.user_token = None  # Temporary storage for user JWT during registration
        self.printer_id = None
        self.klippy_apis = None
        
        # Create HTTP client for API calls
        self.http_client = aiohttp.ClientSession()
        logging.info("Created HTTP client for AuthManager")
        
        # Load existing printer token if available
        self.load_printer_token()
        
    def _redact_sensitive_data(self, data, is_json=False):
        """Redact sensitive information from logs when debug mode is disabled"""
        if self.integration.debug_mode:
            return data
            
        # If it's JSON data, convert to string for processing
        if is_json and isinstance(data, dict):
            data_str = json.dumps(data)
        else:
            data_str = str(data)
            
        # Redact JWT tokens
        jwt_pattern = r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'  
        redacted_str = re.sub(jwt_pattern, '[REDACTED_TOKEN]', data_str)
        
        # Redact passwords
        if '"password"' in redacted_str:
            redacted_str = re.sub(r'"password"\s*:\s*"[^"]*"', '"password":"[REDACTED]"', redacted_str)
            
        # Convert back to dict if it was JSON
        if is_json and isinstance(data, dict):
            try:
                return json.loads(redacted_str)
            except json.JSONDecodeError:
                return {"redacted": "[JSON with redacted values]"}
        
        return redacted_str
    
    async def initialize(self, klippy_apis, http_client):
        """Initialize with Klippy APIs and HTTP client"""
        self.klippy_apis = klippy_apis
        
        # Use the provided HTTP client if not already created
        if http_client is not None and not hasattr(self, 'http_client'):
            self.http_client = http_client
            logging.info("Using provided HTTP client for AuthManager")
    
    def register_endpoints(self, register_endpoint):
        """Register HTTP endpoints for authentication"""
        # User login endpoint
        register_endpoint(
            "/lmnt/login", 
            ["POST"], 
            self._handle_user_login,
            transports=["http"]
        )
        
        # Printer registration endpoint
        register_endpoint(
            "/lmnt/register", 
            ["POST"], 
            self._handle_register_printer,
            transports=["http"]
        )
        
        # Manual registration endpoint
        register_endpoint(
            "/lmnt/manual_register", 
            ["POST"], 
            self._handle_manual_register,
            transports=["http"]
        )
        
        # Token refresh endpoint
        register_endpoint(
            "/lmnt/refresh_token", 
            ["POST"], 
            self._handle_refresh_token,
            transports=["http"]
        )
    
    def load_printer_token(self):
        """Load saved printer token from secure storage"""
        logging.info("LMNT AUTH: Loading printer token from storage")
        token_file = os.path.join(self.integration.tokens_path, "printer_token.json")
        logging.info(f"LMNT AUTH: Looking for token file at {token_file}")
        
        if os.path.exists(token_file):
            try:
                with open(token_file, 'r') as f:
                    data = json.load(f)
                    self.printer_token = data.get('token')
                    expiry_str = data.get('expiry')
                    if expiry_str:
                        self.token_expiry = datetime.fromisoformat(expiry_str)
                    
                    # Extract printer_id from token if available
                    if self.printer_token:
                        self.printer_id = self._get_printer_id_from_token()
                        if self.printer_id:
                            logging.info(f"LMNT AUTH: Loaded printer token for printer ID: {self.printer_id}")
                            return True
                        else:
                            logging.error("LMNT AUTH: Token loaded but no printer ID found in token")
                    else:
                        logging.error("LMNT AUTH: Token file exists but contains no token")
            except (json.JSONDecodeError, IOError) as e:
                logging.error(f"LMNT AUTH: Error loading printer token: {str(e)}")
        else:
            logging.info(f"LMNT AUTH: No token file found at {token_file}")
        
        logging.info("LMNT AUTH: No valid printer token found. Printer needs to be registered.")
        logging.info("LMNT AUTH: Use the /machine/lmnt_marketplace/register_printer endpoint to register this printer")
        return False
    
    def save_printer_token(self, token, expiry):
        """Save printer token to secure storage"""
        if not token:
            logging.error("Cannot save empty printer token")
            return False
        
        token_file = os.path.join(self.integration.tokens_path, "printer_token.json")
        try:
            with open(token_file, 'w') as f:
                json.dump({
                    'token': token,
                    'expiry': expiry.isoformat() if expiry else None
                }, f)
            
            # Update in-memory token
            self.printer_token = token
            self.token_expiry = expiry
            
            # Extract printer_id from token
            self.printer_id = self._get_printer_id_from_token()
            
            # Log with redacted token if not in debug mode
            if self.integration.debug_mode:
                logging.info(f"Saved printer token: {token} for printer ID: {self.printer_id}")
            else:
                logging.info(f"Saved printer token for printer ID: {self.printer_id}")
            return True
        except IOError as e:
            logging.error(f"Error saving printer token: {str(e)}")
            return False
    
    def _get_printer_id_from_token(self):
        """Extract printer ID from the JWT token"""
        if not self.printer_token:
            logging.error("LMNT AUTH: Cannot extract printer ID - no token available")
            return None
        
        try:
            # Decode JWT without verification to extract printer_id
            # This is safe because we're not using the token for authentication here
            payload = jwt.decode(self.printer_token, options={"verify_signature": False})
            
            # Check for both camelCase and snake_case variations of printer ID
            printer_id = payload.get('printer_id') or payload.get('printerId')
            
            if printer_id:
                logging.info(f"LMNT AUTH: Successfully extracted printer ID from token: {printer_id}")
            else:
                logging.error("LMNT AUTH: Token does not contain a printer_id or printerId claim")
                logging.error(f"LMNT AUTH: Available claims in token: {list(payload.keys())}")
                if self.integration.debug_mode:
                    logging.debug(f"LMNT AUTH: Token payload: {payload}")
            
            return printer_id
        except Exception as e:
            logging.error(f"LMNT AUTH: Error extracting printer ID from token: {str(e)}")
            import traceback
            logging.error(f"LMNT AUTH: {traceback.format_exc()}")
            return None
    
    def check_token_refresh(self):
        """Check if token needs to be refreshed and schedule refresh if needed"""
        if not self.printer_token or not self.token_expiry:
            logging.info("No printer token available for refresh check")
            return
        
        # Calculate time until expiry
        now = datetime.now()
        time_until_expiry = self.token_expiry - now
        
        # If token expires in less than 7 days, refresh it
        if time_until_expiry < timedelta(days=7):
            logging.info(f"Printer token expires in {time_until_expiry}, scheduling refresh")
            asyncio.create_task(self.refresh_printer_token())
        else:
            # Schedule next check in 24 hours
            logging.debug(f"Token valid for {time_until_expiry}, next check in 24 hours")
            self.integration.eventloop.delay_callback(
                24 * 60 * 60, self.check_token_refresh)
    
    async def refresh_printer_token(self):
        """
        Refresh the printer token with the marketplace
        
        Uses the /api/refresh-printer-token endpoint which is specifically
        designed for printer token refresh. This endpoint validates the current printer token
        and issues a new one with extended expiration.
        """
        if not self.printer_token:
            logging.error("Cannot refresh printer token: No token available")
            return False
        
        refresh_url = f"{self.integration.marketplace_url}/api/refresh-printer-token"
        
        try:
            headers = {"Authorization": f"Bearer {self.printer_token}"}
            
            # Redact sensitive information in headers
            redacted_headers = self._redact_sensitive_data(headers)
            logging.info(f"Sending token refresh request with headers: {redacted_headers}")
            
            async with self.http_client.post(refresh_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    # Redact sensitive information in response
                    redacted_data = self._redact_sensitive_data(data, is_json=True)
                    logging.info(f"Token refresh response: {redacted_data}")
                    # Try both field names - some APIs use 'token', others use 'printer_token'
                    new_token = data.get('printer_token') or data.get('token')
                    
                    if new_token:
                        # Get expiry from response or calculate it
                        # Try both field names - some APIs use 'token_expires', others use 'expiry'
                        token_expires = data.get('token_expires') or data.get('expiry')
                        expiry = None
                        if token_expires:
                            try:
                                expiry = datetime.fromisoformat(token_expires.replace('Z', '+00:00'))
                                logging.info(f"Using expiry date from response: {expiry}")
                            except ValueError:
                                # Calculate expiry (30 days from now) if parsing fails
                                expiry = datetime.now() + timedelta(days=30)
                                logging.warning(f"Could not parse token expiry: {token_expires}, using default")
                        else:
                            # Calculate expiry (30 days from now) if not provided
                            expiry = datetime.now() + timedelta(days=30)
                            logging.info("No expiry in response, using default 30 days")
                        
                        # Save the new token
                        self.save_printer_token(new_token, expiry)
                        logging.info("Printer token refreshed successfully")
                        return True
                    else:
                        logging.error("Token refresh response missing token or printer_token field")
                else:
                    error_text = await response.text()
                    logging.error(f"Token refresh failed with status {response.status}: {error_text}")
        except Exception as e:
            logging.error(f"Error refreshing printer token: {str(e)}")
        
        # Schedule another attempt in 1 hour if refresh failed
        self.integration.eventloop.delay_callback(
            60 * 60, self.check_token_refresh)
        return False
    
    async def login_user(self, username, password):
        """Login user to the LMNT Marketplace
        
        Args:
            username: User's email or username
            password: User's password
            
        Returns:
            dict: Login response with token
        """
        try:
            if not username or not password:
                raise self.integration.server.error("Missing username or password", 400)
            
            # Authenticate with CWS using the correct endpoint
            login_url = f"{self.integration.cws_url}/auth/login"
            logging.info(f"Attempting login with URL: {login_url}")
            
            # Create payload but redact for logging
            payload = {"email": username, "password": password}
            redacted_payload = self._redact_sensitive_data(payload, is_json=True)
            logging.info(f"Login request payload: {redacted_payload}")
            
            async with self.http_client.post(
                login_url, 
                json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"CWS login failed: {error_text}")
                    raise self.integration.server.error(f"Login failed: {error_text}", response.status)
                
                data = await response.json()
                # Redact sensitive information in response for logging
                redacted_data = self._redact_sensitive_data(data, is_json=True)
                logging.info(f"Login response: {redacted_data}")
                
                token = data.get('token')
                
                if not token:
                    raise self.integration.server.error("Login response missing token", 500)
                
                # Store user token temporarily for printer registration
                self.user_token = token
                
                # Return success but redact token in response to client
                return {"status": "success", "token": "[TOKEN_RECEIVED]"} if not self.integration.debug_mode else {"status": "success", "token": token}
        except aiohttp.ClientError as e:
            logging.error(f"HTTP error during user login: {str(e)}")
            raise self.integration.server.error(f"Connection error: {str(e)}", 500)
        except Exception as e:
            logging.error(f"Error during user login: {str(e)}")
            raise self.integration.server.error(f"Login error: {str(e)}", 500)
            
    async def _handle_user_login(self, web_request):
        """Handle user login to the CWS and obtain user JWT"""
        try:
            # Extract login credentials from request
            login_data = await web_request.get_json_data()
            email = login_data.get('email')
            password = login_data.get('password')
            
            if not email or not password:
                raise web_request.error(
                    "Missing email or password", 400)
            
            result = await self.login_user(email, password)
            return result
        except Exception as e:
            logging.error(f"Error during user login handler: {str(e)}")
            raise web_request.error(f"Login error: {str(e)}", 500)
    
    async def register_printer(self, user_token, printer_name, manufacturer=None, model=None):
        """
        Register printer with the LMNT Marketplace
        
        Args:
            user_token: User's JWT token
            printer_name: Name for the printer
            manufacturer: Printer manufacturer (optional)
            model: Printer model (optional)
            
        Returns:
            dict: Registration response
        """
        try:
            if not printer_name:
                raise self.integration.server.error("Missing printer name", 400)
            
            if not user_token:
                raise self.integration.server.error("Missing user token", 401)
            
            # Store user token temporarily for registration
            self.user_token = user_token
            
            # Register printer with marketplace using the correct endpoint
            register_url = f"{self.integration.marketplace_url}/api/register-printer"
            logging.info(f"Registering printer with URL: {register_url}")
            
            # Use standard Authorization header for the marketplace API
            headers = {"Authorization": f"Bearer {self.user_token}"}
            
            # Build payload with all required fields
            payload = {
                "printer_name": printer_name,
                "manufacturer": manufacturer or "LMNT Printer",
                "model": model or "Klipper"
            }
            logging.info(f"Registering printer with payload: {payload}")
            
            # Redact sensitive information in headers
            redacted_headers = self._redact_sensitive_data(headers)            
            logging.info(f"Sending registration request with headers: {redacted_headers}")
            try:
                async with self.http_client.post(
                    register_url,
                    headers=headers,
                    json=payload
                ) as response:
                    response_text = await response.text()
                    logging.info(f"Registration response status: {response.status}")
                    
                    # Redact sensitive information in response
                    redacted_response = self._redact_sensitive_data(response_text)
                    logging.info(f"Registration response body: {redacted_response}")
                    
                    if response.status != 200 and response.status != 201:
                        logging.error(f"Printer registration failed: {response_text}")
                        raise self.integration.server.error(f"Registration failed: {response_text}", response.status)
                    
                    # Try to parse as JSON if possible
                    try:
                        data = json.loads(response_text)
                        printer_token = data.get('printer_token')  # Changed from 'token' to 'printer_token'
                        if printer_token:
                            # Save token and expiry
                            token_expires = data.get('token_expires')
                            expiry = None
                            if token_expires:
                                try:
                                    expiry = datetime.fromisoformat(token_expires.replace('Z', '+00:00'))
                                except ValueError:
                                    logging.warning(f"Could not parse token expiry: {token_expires}")
                            
                            self.save_printer_token(printer_token, expiry)
                            logging.info("Printer token saved successfully")
                            self.printer_id = data.get('id')
                        return data
                    except json.JSONDecodeError:
                        logging.warning("Response was not valid JSON, returning as text")
                        return {"success": True, "message": response_text}
            except Exception as e:
                logging.error(f"Exception during registration request: {str(e)}")
                raise self.integration.server.error(f"Registration request error: {str(e)}", 500)
        except aiohttp.ClientError as e:
            logging.error(f"HTTP error during printer registration: {str(e)}")
            raise self.integration.server.error(f"Connection error: {str(e)}", 500)
        except Exception as e:
            logging.error(f"Error during printer registration: {str(e)}")
            raise self.integration.server.error(f"Registration error: {str(e)}", 500)
            
    async def _handle_register_printer(self, web_request):
        """Handle printer registration with marketplace"""
        try:
            # Extract registration data from request
            reg_data = await web_request.get_json_data()
            printer_name = reg_data.get('printer_name')
            
            if not printer_name:
                raise web_request.error(
                    "Missing printer name", 400)
            
            if not self.user_token:
                raise web_request.error(
                    "User must login first", 401)
            
            result = await self.register_printer(self.user_token, printer_name)
            return result
        except Exception as e:
            logging.error(f"Error during printer registration handler: {str(e)}")
            raise web_request.error(f"Registration error: {str(e)}", 500)
    
    async def _handle_manual_register(self, web_request):
        """Handle manual printer registration with the LMNT Marketplace"""
        try:
            # Extract registration data from request
            reg_data = await web_request.get_json_data()
            printer_token = reg_data.get('printer_token')
            
            if not printer_token:
                raise web_request.error(
                    "Missing printer token", 400)
            
            try:
                # Decode JWT without verification to extract expiry
                payload = jwt.decode(printer_token, options={"verify_signature": False})
                exp_timestamp = payload.get('exp')
                
                if not exp_timestamp:
                    raise web_request.error(
                        "Invalid token: missing expiration", 400)
                
                expiry = datetime.fromtimestamp(exp_timestamp)
                
                # Save the printer token
                if self.save_printer_token(printer_token, expiry):
                    return {
                        "status": "success", 
                        "printer_id": self.printer_id,
                        "expiry": expiry.isoformat()
                    }
                else:
                    raise web_request.error(
                        "Failed to save printer token", 500)
            except jwt.PyJWTError as e:
                logging.error(f"Invalid JWT token: {str(e)}")
                raise web_request.error(
                    f"Invalid printer token: {str(e)}", 400)
        except Exception as e:
            logging.error(f"Error during manual registration: {str(e)}")
            raise web_request.error(
                f"Registration error: {str(e)}", 500)
    
    async def _handle_refresh_token(self, web_request):
        """Handle manual token refresh request"""
        success = await self.refresh_printer_token()
        
        if success:
            return {
                "status": "success",
                "printer_id": self.printer_id,
                "expiry": self.token_expiry.isoformat() if self.token_expiry else None
            }
        else:
            raise web_request.error(
                "Failed to refresh printer token", 500)
    
    def get_status(self):
        """
        Get the current authentication status
        
        Returns:
            dict: Authentication status information
        """
        return {
            "authenticated": self.printer_token is not None,
            "printer_id": self.printer_id,
            "token_expiry": self.token_expiry.isoformat() if self.token_expiry else None
        }
        
    async def handle_klippy_shutdown(self):
        """Handle Klippy shutdown"""
        self.klippy_apis = None
        
    async def close(self):
        """Close the manager and release resources"""
        if hasattr(self, 'http_client') and self.http_client is not None:
            await self.http_client.close()
            logging.info("Closed HTTP client for AuthManager")
            self.http_client = None
    
    def validate_printer_token(self, token):
        """
        Validate a printer token
        
        Returns:
            dict: Token payload if valid
            None: If token is invalid
        """
        try:
            # Decode the token without verification first to get the algorithm
            unverified_payload = jwt.decode(token, options={"verify_signature": False})
            
            # Now verify with the appropriate algorithm
            algorithm = unverified_payload.get('alg', 'HS256')
            payload = jwt.decode(token, "secret", algorithms=[algorithm])
            
            # Check if token is expired
            exp = payload.get('exp')
            if exp and datetime.fromtimestamp(exp) < datetime.now():
                logging.warning("Printer token has expired")
                return None
            
            return payload
        except Exception as e:
            logging.error(f"Error validating printer token: {str(e)}")
            return None
