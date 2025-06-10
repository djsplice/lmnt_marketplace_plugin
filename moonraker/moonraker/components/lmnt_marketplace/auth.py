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
        
        # Load existing printer token if available
        self.load_printer_token()
    
    async def initialize(self, klippy_apis, http_client):
        """Initialize with Klippy APIs and HTTP client"""
        self.klippy_apis = klippy_apis
        self.http_client = http_client
    
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
        token_file = os.path.join(self.integration.tokens_path, "printer_token.json")
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
                        logging.info(f"Loaded printer token for printer ID: {self.printer_id}")
                        return True
            except (json.JSONDecodeError, IOError) as e:
                logging.error(f"Error loading printer token: {str(e)}")
        
        logging.info("No valid printer token found")
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
            
            # Update current token and expiry
            self.printer_token = token
            self.token_expiry = expiry
            
            # Extract printer_id from token
            self.printer_id = self._get_printer_id_from_token()
            
            logging.info(f"Saved printer token for printer ID: {self.printer_id}")
            return True
        except IOError as e:
            logging.error(f"Error saving printer token: {str(e)}")
            return False
    
    def _get_printer_id_from_token(self):
        """Extract printer ID from the JWT token"""
        if not self.printer_token:
            return None
        
        try:
            # Decode JWT without verification to extract printer_id
            # This is safe because we're not using the token for authentication here
            payload = jwt.decode(self.printer_token, options={"verify_signature": False})
            return payload.get('printer_id')
        except Exception as e:
            logging.error(f"Error extracting printer ID from token: {str(e)}")
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
        
        refresh_url = f"{self.integration.marketplace_url}/api/{self.integration.api_version}/refresh-printer-token"
        
        try:
            headers = {"Authorization": f"Bearer {self.printer_token}"}
            
            async with self.http_client.post(refresh_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    new_token = data.get('token')
                    
                    if new_token:
                        # Calculate expiry (30 days from now)
                        expiry = datetime.now() + timedelta(days=30)
                        
                        # Save the new token
                        self.save_printer_token(new_token, expiry)
                        logging.info("Printer token refreshed successfully")
                        return True
                    else:
                        logging.error("Token refresh response missing token field")
                else:
                    error_text = await response.text()
                    logging.error(f"Token refresh failed with status {response.status}: {error_text}")
        except Exception as e:
            logging.error(f"Error refreshing printer token: {str(e)}")
        
        # Schedule another attempt in 1 hour if refresh failed
        self.integration.eventloop.delay_callback(
            60 * 60, self.check_token_refresh)
        return False
    
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
            
            # Authenticate with CWS
            login_url = f"{self.integration.cws_url}/api/{self.integration.api_version}/login"
            
            async with self.http_client.post(
                login_url, 
                json={"email": email, "password": password}
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"CWS login failed: {error_text}")
                    raise web_request.error(
                        f"Login failed: {error_text}", response.status)
                
                data = await response.json()
                token = data.get('token')
                
                if not token:
                    raise web_request.error(
                        "Login response missing token", 500)
                
                # Store user token temporarily for printer registration
                self.user_token = token
                
                return {"status": "success", "token": token}
        except aiohttp.ClientError as e:
            logging.error(f"HTTP error during user login: {str(e)}")
            raise web_request.error(
                f"Connection error: {str(e)}", 500)
        except Exception as e:
            logging.error(f"Error during user login: {str(e)}")
            raise web_request.error(
                f"Login error: {str(e)}", 500)
    
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
            
            # Register printer with marketplace
            register_url = f"{self.integration.marketplace_url}/api/{self.integration.api_version}/register-printer"
            
            headers = {"Authorization": f"Bearer {self.user_token}"}
            
            async with self.http_client.post(
                register_url,
                headers=headers,
                json={"name": printer_name}
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"Printer registration failed: {error_text}")
                    raise web_request.error(
                        f"Registration failed: {error_text}", response.status)
                
                data = await response.json()
                printer_token = data.get('token')
                encrypted_psek = data.get('kek_id')  # Actually contains encrypted PSEK
                
                if not printer_token:
                    raise web_request.error(
                        "Registration response missing token", 500)
                
                # Calculate expiry (30 days from now)
                expiry = datetime.now() + timedelta(days=30)
                
                # Save the printer token
                self.save_printer_token(printer_token, expiry)
                
                # Save the encrypted PSEK if provided
                if encrypted_psek:
                    self.integration.crypto_manager._save_encrypted_psek(encrypted_psek)
                
                # Clear user token after registration
                self.user_token = None
                
                return {"status": "success", "printer_id": self.printer_id}
        except aiohttp.ClientError as e:
            logging.error(f"HTTP error during printer registration: {str(e)}")
            raise web_request.error(
                f"Connection error: {str(e)}", 500)
        except Exception as e:
            logging.error(f"Error during printer registration: {str(e)}")
            raise web_request.error(
                f"Registration error: {str(e)}", 500)
    
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
    
    def validate_printer_token(self, token):
        """
        Validate a printer token
        
        Returns:
            dict: Token payload if valid
            None: If token is invalid
        """
        if not token:
            return None
        
        try:
            # In a production environment, this should verify the signature
            # For now, we just decode and check expiration
            payload = jwt.decode(token, options={"verify_signature": False})
            
            # Check if token is expired
            exp_timestamp = payload.get('exp')
            if not exp_timestamp:
                logging.error("Token missing expiration claim")
                return None
            
            expiry = datetime.fromtimestamp(exp_timestamp)
            if datetime.now() > expiry:
                logging.error("Token is expired")
                return None
            
            return payload
        except jwt.PyJWTError as e:
            logging.error(f"Token validation error: {str(e)}")
            return None
