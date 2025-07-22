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
import hashlib
import secrets
import base64
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import re
from datetime import datetime, timedelta
import jwt

# PyNaCl for ED25519 key operations
import nacl.utils
from nacl.public import PrivateKey
from nacl.encoding import HexEncoder, Base64Encoder

class AuthManager:
    """
    Manages authentication and token operations for LMNT Marketplace

    Handles DLT key pair generation and management for printer identity.
    
    Handles user login, printer registration, token validation,
    token refresh, and JWT management.
    """
    
    def __init__(self, integration):
        """Initialize the Authentication Manager"""
        self.integration = integration
        self.printer_token = None
        self.token_expiry = None
        self.token_created_at = None
        self.user_token = None  # Temporary storage for user JWT during registration
        self.printer_id = None
        self.printer_name = None  # Store printer name for re-registration
        self.printer_kek_id = None # Added to store printer_kek_id
        self.dlt_private_key = None # For DLT-native printer key pair
        self.klippy_apis = None
        self.http_client = None  # Will be set during initialize()
        self._owns_http_client = False  # Track if we own the HTTP client
        
        # Load existing printer token if available
        self.load_printer_token()
        # Load existing DLT private key if available
        self._load_dlt_private_key()
        
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
        
        # Use the provided HTTP client or create one if not provided
        if http_client is not None:
            self.http_client = http_client
            self._owns_http_client = False  # Using shared client
            logging.info("Using provided HTTP client for AuthManager")
        elif not self.http_client:
            # Create HTTP client if not provided
            self.http_client = aiohttp.ClientSession()
            self._owns_http_client = True  # We own this client
            logging.info("Created HTTP client for AuthManager")
    
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
        """Load saved printer token and kek_id from secure storage"""
        logging.info("LMNT AUTH: Starting token load process...")
        token_file = os.path.join(self.integration.tokens_path, "printer_token.json")
        logging.info(f"LMNT AUTH: Looking for token file at {token_file}")
        logging.info(f"LMNT AUTH: Token file exists: {os.path.exists(token_file)}")
        
        if not os.path.exists(token_file):
            logging.info(f"LMNT AUTH: No token file found at {token_file}")
            return False
        
        if os.path.exists(token_file):
            try:
                with open(token_file, 'r') as f:
                    data = json.load(f)
                    self.printer_token = data.get('token')
                    expiry_str = data.get('expiry')
                    created_at_str = data.get('created_at')
                    self.printer_name = data.get('printer_name')
                    
                    if expiry_str:
                        self.token_expiry = datetime.fromisoformat(expiry_str)
                    
                    if created_at_str:
                        self.token_created_at = datetime.fromisoformat(created_at_str)
                    else:
                        # If no creation time, assume it was created 1 day before now
                        self.token_created_at = datetime.now() - timedelta(days=1)
                        
                    if self.printer_name:
                        logging.info(f"LMNT AUTH: Loaded printer name: {self.printer_name}")
                    else:
                        logging.info("LMNT AUTH: No printer name found in token file")
                    
                    if self.printer_token:
                        logging.info(f"LMNT AUTH: Token loaded, attempting to extract printer ID...")
                        
                        # Always extract printer ID first, even if token is expired
                        # The JobManager needs the printer ID to function
                        self.printer_id = self._get_printer_id_from_token()
                        
                        if self.printer_id:
                            logging.info(f"LMNT AUTH: Successfully extracted printer ID: {self.printer_id}")
                        else:
                            logging.error("LMNT AUTH: Token loaded but no printer ID found in token")
                            # Let's try to decode the token and see what's in it
                            payload = self._decode_token(self.printer_token)
                            if payload:
                                logging.error(f"LMNT AUTH: Token payload keys: {list(payload.keys())}")
                                if self.integration.debug_mode:
                                    logging.debug(f"LMNT AUTH: Full token payload: {payload}")
                            else:
                                logging.error("LMNT AUTH: Failed to decode token payload")
                        
                        # Check if token is already expired
                        now = self._get_timezone_aware_now()
                        if self.token_expiry:
                            comparison = self._safe_datetime_comparison(self.token_expiry, now)
                            if comparison is not None and comparison <= 0:  # expired or equal
                                logging.warning(f"LMNT AUTH: Loaded token is expired (expired: {self.token_expiry}, now: {now})")
                                # Schedule token refresh/re-registration but still return True since we have a printer ID
                                self.integration.eventloop.delay_callback(
                                    5, self.check_token_refresh)
                            elif comparison is not None:
                                logging.info(f"LMNT AUTH: Token is valid until {self.token_expiry}")
                            else:
                                logging.error("LMNT AUTH: Could not compare token expiry times, scheduling refresh")
                                self.integration.eventloop.delay_callback(
                                    5, self.check_token_refresh)
                        
                        # Return True if we have a printer ID, regardless of token expiry
                        # The JobManager needs the printer ID to function properly
                        return bool(self.printer_id)
                    else:
                        logging.error("LMNT AUTH: Token file exists but contains no token")
            except (json.JSONDecodeError, IOError) as e:
                logging.error(f"LMNT AUTH: Error loading printer token: {str(e)}")
        else:
            logging.info(f"LMNT AUTH: No token file found at {token_file}")
        
        logging.info("LMNT AUTH: No valid printer token found. Printer needs to be registered.")
        logging.info("LMNT AUTH: Use the /machine/lmnt_marketplace/register_printer endpoint to register this printer")
        return False

    def _load_dlt_private_key(self):
        """
        Load DLT private key from disk, supporting both legacy and encrypted formats
        """
        if not hasattr(self.integration, 'tokens_path') or not self.integration.tokens_path:
            logging.error("LMNT AUTH DLT: tokens_path not configured in integration object.")
            return False
        
        # Try new encrypted format first
        enc_key_filename = "printer_dlt_private_key.enc"
        enc_key_file_path = os.path.join(self.integration.tokens_path, enc_key_filename)
        
        # Also check for legacy format
        hex_key_filename = "printer_dlt_private_key.hex"
        hex_key_file_path = os.path.join(self.integration.tokens_path, hex_key_filename)
        
        logging.info(f"LMNT AUTH DLT: Looking for DLT private key file at {enc_key_file_path} or {hex_key_file_path}")

        # Try encrypted format first
        if os.path.exists(enc_key_file_path):
            try:
                with open(enc_key_file_path, 'r') as f:
                    key_data = f.read().strip()
                
                if key_data:
                    # New encrypted format
                    logging.info("LMNT AUTH DLT: Loading encrypted private key")
                    decrypted_private_key = self._decrypt_private_key(key_data)
                    if decrypted_private_key:
                        self.dlt_private_key = PrivateKey(decrypted_private_key, encoder=HexEncoder)
                        logging.info("LMNT AUTH DLT: Successfully loaded encrypted DLT private key.")
                        # Optionally log public key for verification if in debug mode
                        if self.integration.debug_mode:
                            public_key_b64 = self.dlt_private_key.public_key.encode(encoder=Base64Encoder).decode('utf-8')
                            logging.debug(f"LMNT AUTH DLT: Loaded public key (b64): {public_key_b64[:10]}...")
                        return True
                    else:
                        logging.error("LMNT AUTH DLT: Failed to decrypt DLT private key")
                        # Could be hardware change - fall through to check legacy format
                else:
                    logging.warning(f"LMNT AUTH DLT: DLT private key file {enc_key_file_path} is empty.")
            except Exception as e:
                logging.error(f"LMNT AUTH DLT: Error loading encrypted DLT private key from {enc_key_file_path}: {str(e)}")
        
        # Try legacy plaintext format
        if os.path.exists(hex_key_file_path):
            try:
                with open(hex_key_file_path, 'r') as f:
                    key_data = f.read().strip()
                
                if key_data:
                    # Legacy plaintext format - migrate to encrypted
                    logging.info("LMNT AUTH DLT: Migrating plaintext private key to encrypted format")
                    
                    # Load the key first to verify it's valid
                    try:
                        self.dlt_private_key = PrivateKey(key_data, encoder=HexEncoder)
                    except Exception as e:
                        logging.error(f"LMNT AUTH DLT: Invalid plaintext private key: {e}")
                        return False
                    
                    # Encrypt and save the key
                    if self._save_dlt_private_key_to_disk(key_data):
                        logging.info("LMNT AUTH DLT: Successfully migrated to encrypted private key")
                        
                        # Remove old plaintext file
                        try:
                            os.remove(hex_key_file_path)
                            logging.info("LMNT AUTH DLT: Removed old plaintext key file")
                        except Exception as e:
                            logging.warning(f"LMNT AUTH DLT: Could not remove old plaintext key file: {e}")
                        
                        # Optionally log public key for verification if in debug mode
                        if self.integration.debug_mode:
                            public_key_b64 = self.dlt_private_key.public_key.encode(encoder=Base64Encoder).decode('utf-8')
                            logging.debug(f"LMNT AUTH DLT: Loaded public key (b64): {public_key_b64[:10]}...")
                        return True
                    else:
                        logging.error("LMNT AUTH DLT: Failed to migrate private key to encrypted format")
                        return False
                else:
                    logging.warning(f"LMNT AUTH DLT: Legacy DLT private key file {hex_key_file_path} is empty.")
            except Exception as e:
                logging.error(f"LMNT AUTH DLT: Error loading legacy DLT private key from {hex_key_file_path}: {str(e)}")
                
        logging.warning(f"LMNT AUTH DLT: DLT private key not found. Will need to generate a new one.")
        return False

    def _save_dlt_private_key_to_disk(self, private_key_hex_str):
        """
        Save DLT private key to disk in encrypted format
        """
        if not hasattr(self.integration, 'tokens_path') or not self.integration.tokens_path:
            logging.error("LMNT AUTH DLT: tokens_path not configured in integration object for saving.")
            return False

        dlt_key_filename = "printer_dlt_private_key.enc"  # Changed extension to .enc
        dlt_key_file_path = os.path.join(self.integration.tokens_path, dlt_key_filename)
        
        try:
            # Encrypt the private key
            encrypted_private_key = self._encrypt_private_key(private_key_hex_str)
            if not encrypted_private_key:
                logging.error("LMNT AUTH DLT: Failed to encrypt DLT private key")
                return False
            
            # Write encrypted key to file
            with open(dlt_key_file_path, 'w') as f:
                f.write(encrypted_private_key)
            
            # Set secure file permissions
            try:
                os.chmod(dlt_key_file_path, 0o600)  # Owner read/write only
            except Exception as e:
                logging.warning(f"LMNT AUTH DLT: Could not set secure permissions on key file: {e}")
            
            logging.info(f"LMNT AUTH DLT: Successfully saved encrypted DLT private key to {dlt_key_file_path}")
            return True
        except Exception as e:
            logging.error(f"LMNT AUTH DLT: Error saving encrypted DLT private key to {dlt_key_file_path}: {str(e)}")
            return False

    def save_printer_token(self, token, expiry):
        """Save printer token to secure storage"""
        if not token:
            logging.error("Cannot save empty printer token")
            return False
        
        token_file = os.path.join(self.integration.tokens_path, "printer_token.json")
        try:
            # Store token creation time for proactive refresh
            now = datetime.now()
            with open(token_file, 'w') as f:
                json.dump({
                    'token': token,
                    'expiry': expiry.isoformat() if expiry else None,
                    'created_at': now.isoformat(),
                    'printer_name': self.printer_name if hasattr(self, 'printer_name') else None
                }, f)
            
            # Update in-memory token
            self.printer_token = token
            self.token_expiry = expiry
            self.token_created_at = now
            
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
    
    def _get_timezone_aware_now(self):
        """
        Get current time in the appropriate timezone for comparison
        
        Returns:
            datetime: Current time, timezone-aware if needed
        """
        from datetime import timezone
        return datetime.now(timezone.utc)
    
    def _safe_datetime_comparison(self, dt1, dt2):
        """
        Safely compare two datetime objects, handling timezone differences
        
        Args:
            dt1: First datetime
            dt2: Second datetime
            
        Returns:
            int: -1 if dt1 < dt2, 0 if equal, 1 if dt1 > dt2, None if comparison fails
        """
        try:
            # If both have timezone info or both don't, direct comparison
            if (dt1.tzinfo is None) == (dt2.tzinfo is None):
                if dt1 < dt2:
                    return -1
                elif dt1 > dt2:
                    return 1
                else:
                    return 0
            
            # Mixed timezone awareness - convert both to UTC
            from datetime import timezone
            
            if dt1.tzinfo is None:
                dt1_utc = dt1.replace(tzinfo=timezone.utc)
            else:
                dt1_utc = dt1.astimezone(timezone.utc)
                
            if dt2.tzinfo is None:
                dt2_utc = dt2.replace(tzinfo=timezone.utc)
            else:
                dt2_utc = dt2.astimezone(timezone.utc)
                
            if dt1_utc < dt2_utc:
                return -1
            elif dt1_utc > dt2_utc:
                return 1
            else:
                return 0
                
        except Exception as e:
            logging.error(f"LMNT AUTH: Error comparing datetimes: {e}")
            return None
    
    def _decode_token(self, token, verify=False):
        """
        Decode a JWT token and return the payload
        
        Args:
            token: JWT token to decode
            verify: Whether to verify the token signature
            
        Returns:
            dict: Token payload or None if decoding fails
        """
        if not token:
            return None
            
        try:
            # Decode the JWT token with or without verification
            if verify:
                # When verifying, we need to specify algorithms and provide a key
                # This would require the secret key, which we don't have
                # For now, we'll skip verification since we don't have the key
                logging.warning("LMNT AUTH: Token verification requested but no key available")
                return None
            else:
                # When not verifying signature, provide a dummy key and disable verification
                payload = jwt.decode(
                    token,
                    key="dummy",  # Dummy key since we're not verifying
                    options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
                    algorithms=["HS256", "HS512", "RS256", "RS512", "ES256", "ES512"]
                )
            return payload
        except jwt.ExpiredSignatureError:
            logging.warning("LMNT AUTH: Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logging.error(f"LMNT AUTH: Invalid token: {str(e)}")
            return None
        except Exception as e:
            logging.error(f"LMNT AUTH: Error decoding token: {str(e)}")
            import traceback
            logging.error(f"LMNT AUTH: {traceback.format_exc()}")
            return None
    
    def _get_token_expiry_from_jwt(self, token):
        """
        Extract expiry time directly from JWT token
        
        Args:
            token: JWT token to extract expiry from
            
        Returns:
            datetime: Token expiry time or None if extraction fails
        """
        if not token:
            return None
            
        payload = self._decode_token(token)
        if not payload:
            return None
            
        # Try standard 'exp' claim first
        exp = payload.get('exp')
        if exp:
            try:
                # JWT exp is in seconds since epoch - convert to UTC timezone-aware datetime
                from datetime import timezone
                return datetime.fromtimestamp(exp, tz=timezone.utc)
            except Exception as e:
                logging.error(f"LMNT AUTH: Error converting exp to datetime: {str(e)}")
        
        # Try custom expiry fields if exp not found
        expiry_str = payload.get('expiry') or payload.get('token_expires')
        if expiry_str:
            try:
                return datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
            except Exception as e:
                logging.error(f"LMNT AUTH: Error parsing expiry string: {str(e)}")
                
        return None
    
    def _get_printer_id_from_token(self):
        """Extract printer ID from the JWT token"""
        if not self.printer_token:
            logging.error("LMNT AUTH: Cannot extract printer ID - no token available")
            return None
        
        logging.info("LMNT AUTH: Attempting to decode token to extract printer ID...")
        payload = self._decode_token(self.printer_token)
        if not payload:
            logging.error("LMNT AUTH: Failed to decode token payload")
            return None
            
        logging.info(f"LMNT AUTH: Token decoded successfully, available claims: {list(payload.keys())}")
        
        # Check for both camelCase and snake_case variations of printer ID
        printer_id = payload.get('printer_id') or payload.get('printerId')
        
        if printer_id:
            logging.info(f"LMNT AUTH: Successfully extracted printer ID from token: {printer_id}")
        else:
            logging.error("LMNT AUTH: Token does not contain a printer_id or printerId claim")
            logging.error(f"LMNT AUTH: Available claims in token: {list(payload.keys())}")
            # Log the actual values to help debug
            logging.error(f"LMNT AUTH: printer_id value: {payload.get('printer_id')}")
            logging.error(f"LMNT AUTH: printerId value: {payload.get('printerId')}")
            if self.integration.debug_mode:
                logging.debug(f"LMNT AUTH: Full token payload: {payload}")
        
        return printer_id
    
    def is_token_valid(self, token=None):
        """
        Check if a token is valid by decoding it and checking expiry
        
        Args:
            token: JWT token to check, or use self.printer_token if None
            
        Returns:
            bool: True if token is valid, False otherwise
        """
        token_to_check = token or self.printer_token
        if not token_to_check:
            return False
            
        # Try to decode the token - this will return None if expired or invalid
        payload = self._decode_token(token_to_check)
        if not payload:
            return False
            
        # Check if token has an expiry and if it's in the future
        jwt_expiry = self._get_token_expiry_from_jwt(token_to_check)
        if jwt_expiry:
            now = datetime.now()
            if jwt_expiry <= now:
                logging.warning(f"LMNT AUTH: Token has expired at {jwt_expiry}")
                return False
                
        return True
        
    def get_token_status(self, token=None):
        """
        Get detailed status information about a token
        
        Args:
            token: JWT token to check, or use self.printer_token if None
            
        Returns:
            dict: Token status information including validity, expiry, time remaining, etc.
        """
        token_to_check = token or self.printer_token
        status = {
            "valid": False,
            "exists": bool(token_to_check),
            "expired": False,
            "expiry": None,
            "time_remaining": None,
            "printer_id": None,
            "created_at": None,
            "lifetime_percent": None
        }
        
        if not token_to_check:
            return status
            
        # Try to decode the token
        payload = self._decode_token(token_to_check)
        if not payload:
            status["valid"] = False
            return status
            
        # Token is at least decodable
        status["valid"] = True
        
        # Extract expiry
        jwt_expiry = self._get_token_expiry_from_jwt(token_to_check)
        if jwt_expiry:
            status["expiry"] = jwt_expiry
            now = datetime.now()
            time_remaining = jwt_expiry - now
            status["time_remaining"] = time_remaining
            status["expired"] = time_remaining <= timedelta(0)
            
            # Extract creation time if available
            if hasattr(self, 'token_created_at') and self.token_created_at:
                status["created_at"] = self.token_created_at
                token_lifetime = jwt_expiry - self.token_created_at
                if token_lifetime.total_seconds() > 0:
                    elapsed = now - self.token_created_at
                    status["lifetime_percent"] = (elapsed.total_seconds() / token_lifetime.total_seconds()) * 100
        
        # Extract printer ID
        if payload.get('printer_id') or payload.get('printerId'):
            status["printer_id"] = payload.get('printer_id') or payload.get('printerId')
            
        return status
    
    def check_token_refresh(self):
        """Check if token needs to be refreshed and schedule refresh if needed"""
        if not self.printer_token:
            logging.info("LMNT AUTH: No printer token available for refresh check")
            return
        
        # First, verify the token is valid by decoding it
        # This catches malformed tokens that might have been corrupted
        if not self._decode_token(self.printer_token):
            logging.warning("LMNT AUTH: Token is invalid or malformed, attempting re-registration")
            asyncio.create_task(self._handle_expired_token())
            return
        
        # Get expiry directly from JWT if possible
        jwt_expiry = self._get_token_expiry_from_jwt(self.printer_token)
        if jwt_expiry:
            # Use the expiry from JWT if available
            self.token_expiry = jwt_expiry
            logging.info(f"LMNT AUTH: Using expiry from JWT: {jwt_expiry}")
        
        # Calculate time until expiry using timezone-safe comparison
        now = self._get_timezone_aware_now()
        
        # If we don't have a valid expiry time, try to re-register
        if not self.token_expiry:
            logging.warning("LMNT AUTH: No token expiry information available, attempting re-registration")
            asyncio.create_task(self._handle_expired_token())
            return
        
        # Use safe datetime comparison to check if token is expired
        comparison = self._safe_datetime_comparison(self.token_expiry, now)
        if comparison is None:
            logging.error("LMNT AUTH: Could not compare token expiry, attempting re-registration")
            asyncio.create_task(self._handle_expired_token())
            return
        elif comparison <= 0:  # expired or equal
            logging.warning(f"LMNT AUTH: Printer token has expired, attempting re-registration")
            asyncio.create_task(self._handle_expired_token())
            return
        
        # Calculate time until expiry for logging and scheduling
        try:
            # Convert both to same timezone for arithmetic
            from datetime import timezone
            if self.token_expiry.tzinfo is not None:
                expiry_utc = self.token_expiry.astimezone(timezone.utc)
                now_utc = now.astimezone(timezone.utc)
            else:
                expiry_utc = self.token_expiry
                now_utc = now.replace(tzinfo=None)
            time_until_expiry = expiry_utc - now_utc
        except Exception as e:
            logging.error(f"LMNT AUTH: Error calculating time until expiry: {e}")
            # Default to a safe assumption
            time_until_expiry = timedelta(days=1)
        
        # Calculate token lifetime and time since creation
        token_lifetime = None
        time_since_creation = None
        
        if hasattr(self, 'token_created_at') and self.token_created_at:
            token_lifetime = self.token_expiry - self.token_created_at
            time_since_creation = now - self.token_created_at
            
            # Log token age information
            days_since_creation = time_since_creation.total_seconds() / (24 * 60 * 60)
            total_lifetime_days = token_lifetime.total_seconds() / (24 * 60 * 60)
            percent_used = (time_since_creation.total_seconds() / token_lifetime.total_seconds()) * 100 if token_lifetime.total_seconds() > 0 else 0
            
            logging.info(f"LMNT AUTH: Token age: {days_since_creation:.1f} days ({percent_used:.1f}% of {total_lifetime_days:.1f} day lifetime)")
        else:
            # Default values if we don't have creation time
            token_lifetime = timedelta(days=30)
            time_since_creation = timedelta(days=0)
            logging.info(f"LMNT AUTH: Token creation time unknown, assuming new token")
        
        # Determine refresh threshold - 80% of lifetime or 7 days before expiry, whichever comes first
        refresh_threshold = token_lifetime * 0.8 if token_lifetime else timedelta(days=23)  # 80% of 30 days
        days_until_expiry = time_until_expiry.total_seconds() / (24 * 60 * 60)
        
        # Refresh conditions:
        # 1. Less than 7 days remain before expiry
        # 2. Token has been used for more than 80% of its lifetime
        # 3. Token is older than 23 days (for 30-day tokens)
        if time_until_expiry < timedelta(days=7):
            logging.info(f"LMNT AUTH: Printer token expires in {days_until_expiry:.1f} days, scheduling refresh")
            asyncio.create_task(self.refresh_printer_token())
        elif time_since_creation and time_since_creation > refresh_threshold:
            logging.info(f"LMNT AUTH: Token has reached {percent_used:.1f}% of its lifetime, scheduling refresh")
            asyncio.create_task(self.refresh_printer_token())
        else:
            # Calculate next check time - check daily if expiring soon, otherwise every 3 days
            next_check_hours = 24 if time_until_expiry < timedelta(days=10) else 24 * 3
            
            logging.info(f"LMNT AUTH: Token valid for {days_until_expiry:.1f} more days, next check in {next_check_hours/24:.1f} days")
            self.integration.eventloop.delay_callback(
                next_check_hours * 60 * 60, self.check_token_refresh)
    
    async def _handle_expired_token(self):
        """
        Handle the case when a token is completely expired
        
        This method attempts to re-register the printer if we have the necessary information,
        or logs a clear message about how to re-register manually.
        
        Returns:
            bool: True if re-registration was successful, False otherwise
        """
        logging.warning("LMNT AUTH: Token has expired and needs re-registration")
        
        # Backup the expired token for debugging purposes
        token_file = os.path.join(self.integration.tokens_path, "printer_token.json")
        expired_token_file = os.path.join(self.integration.tokens_path, "printer_token.json.expired")
        
        # Store the current token data before clearing it
        current_token = self.printer_token
        current_printer_name = self.printer_name
        current_printer_id = self.printer_id  # Store printer ID before clearing
        
        # Clear token state first to ensure we don't use expired tokens
        self.printer_token = None
        self.token_expiry = None
        self.token_created_at = None
        self.printer_id = None
        
        # Backup the expired token file
        if os.path.exists(token_file):
            try:
                # Create a timestamp for the expired token backup
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = f"{expired_token_file}.{timestamp}"
                
                # Copy instead of rename to preserve the original for debugging
                import shutil
                shutil.copy2(token_file, backup_file)
                logging.info(f"LMNT AUTH: Backed up expired token to {backup_file}")
                
                # Now rename the original to .expired
                os.rename(token_file, expired_token_file)
                logging.info("LMNT AUTH: Renamed expired token file to printer_token.json.expired")
            except Exception as e:
                logging.error(f"LMNT AUTH: Failed to backup expired token file: {str(e)}")
        
        # Check if we have the necessary information for automatic re-registration
        # Use printer ID from JWT token instead of printer name for more reliable identification
        if self.user_token and current_printer_id:
            logging.info(f"LMNT AUTH: Attempting automatic re-registration for printer ID: {current_printer_id}")
            try:
                # For automatic re-registration, we need the printer name from the stored value
                # or we could look it up by printer ID if the API supports it
                if current_printer_name:
                    result = await self.register_printer(self.user_token, current_printer_name)
                    if result and self.printer_token:
                        logging.info("LMNT AUTH: Automatic re-registration successful")
                        return True
                    else:
                        logging.error("LMNT AUTH: Automatic re-registration failed - no token returned")
                else:
                    logging.warning(f"LMNT AUTH: Have printer ID {current_printer_id} but no printer name for re-registration")
            except Exception as e:
                logging.error(f"LMNT AUTH: Automatic re-registration failed: {str(e)}")
                import traceback
                logging.error(f"LMNT AUTH: {traceback.format_exc()}")
        else:
            missing = []
            if not self.user_token:
                missing.append("user_token")
            if not current_printer_id:
                missing.append("printer_id")
            if not current_printer_name:
                missing.append("printer_name")
            logging.warning(f"LMNT AUTH: Cannot automatically re-register - missing {', '.join(missing)}")
            if current_printer_id:
                logging.info(f"LMNT AUTH: Printer ID available: {current_printer_id}")
            else:
                logging.warning("LMNT AUTH: No printer ID available from expired token")
        
        # If automatic re-registration failed or we don't have the necessary information,
        # log a clear message about how to re-register manually
        logging.warning("LMNT AUTH: Manual re-registration required. Please use the Moonraker API endpoint:")
        logging.warning("LMNT AUTH: POST /printer/lmnt_marketplace/register_printer with user_token and printer_name")
        logging.warning("LMNT AUTH: Or use the LMNT Marketplace UI to re-register this printer")
        
        return False
        
    async def refresh_printer_token(self):
        """
        Refresh the printer token with the marketplace
        
        Uses the /api/refresh-printer-token endpoint which is specifically
        designed for printer token refresh. This endpoint validates the current printer token
        and issues a new one with extended expiration.
        
        Returns:
            bool: True if refresh was successful, False otherwise
        """
        if not self.printer_token:
            logging.error("LMNT AUTH: Cannot refresh printer token: No token available")
            return False
        
        # First verify that the token is valid (not malformed)
        if not self._decode_token(self.printer_token):
            logging.error("LMNT AUTH: Cannot refresh token: Token is invalid or malformed")
            return await self._handle_expired_token()
        
        refresh_url = f"{self.integration.marketplace_url}/api/refresh-printer-token"
        
        try:
            headers = {"Authorization": f"Bearer {self.printer_token}"}
            
            # Redact sensitive information in headers
            redacted_headers = self._redact_sensitive_data(headers)
            logging.info(f"LMNT AUTH: Sending token refresh request with headers: {redacted_headers}")
            
            # Track start time for performance monitoring
            start_time = datetime.now()
            
            async with self.http_client.post(refresh_url, headers=headers) as response:
                # Calculate response time for monitoring
                response_time = (datetime.now() - start_time).total_seconds()
                logging.info(f"LMNT AUTH: Token refresh API response time: {response_time:.2f} seconds")
                
                if response.status == 200:
                    data = await response.json()
                    # Redact sensitive information in response
                    redacted_data = self._redact_sensitive_data(data, is_json=True)
                    logging.info(f"LMNT AUTH: Token refresh response: {redacted_data}")
                    
                    # Try both field names - some APIs use 'token', others use 'printer_token'
                    new_token = data.get('printer_token') or data.get('token')
                    
                    if new_token:
                        # First validate the new token
                        if not self._decode_token(new_token):
                            logging.error("LMNT AUTH: Received invalid token from refresh endpoint")
                            return False
                        
                        # Try to get expiry directly from JWT token
                        jwt_expiry = self._get_token_expiry_from_jwt(new_token)
                        if jwt_expiry:
                            expiry = jwt_expiry
                            logging.info(f"LMNT AUTH: Using expiry from JWT token: {expiry}")
                        else:
                            # Fall back to response fields if JWT doesn't have expiry
                            # Try both field names - some APIs use 'token_expires', others use 'expiry'
                            token_expires = data.get('token_expires') or data.get('expiry')
                            expiry = None
                            if token_expires:
                                try:
                                    expiry = datetime.fromisoformat(token_expires.replace('Z', '+00:00'))
                                    logging.info(f"LMNT AUTH: Using expiry date from response: {expiry}")
                                except ValueError:
                                    # Calculate expiry (30 days from now) if parsing fails
                                    expiry = datetime.now() + timedelta(days=30)
                                    logging.warning(f"LMNT AUTH: Could not parse token expiry: {token_expires}, using default")
                            else:
                                # Calculate expiry (30 days from now) if not provided
                                expiry = datetime.now() + timedelta(days=30)
                                logging.info("LMNT AUTH: No expiry in response, using default 30 days")
                        
                        # Save the new token
                        self.save_printer_token(new_token, expiry)
                        logging.info("LMNT AUTH: Printer token refreshed successfully")
                        return True
                    else:
                        logging.error("LMNT AUTH: Token refresh response missing token or printer_token field")
                elif response.status == 401:
                    # Token is expired or invalid, try re-registration
                    error_text = await response.text()
                    logging.warning(f"LMNT AUTH: Token refresh failed with 401 status: {error_text}")
                    return await self._handle_expired_token()
                else:
                    error_text = await response.text()
                    logging.error(f"LMNT AUTH: Token refresh failed with status {response.status}: {error_text}")
                    
                    # For 5xx errors, retry sooner
                    if 500 <= response.status < 600:
                        logging.info("LMNT AUTH: Server error detected, scheduling retry in 15 minutes")
                        self.integration.eventloop.delay_callback(
                            15 * 60, self.refresh_printer_token)
                        return False
        except aiohttp.ClientError as e:
            logging.error(f"LMNT AUTH: Network error refreshing printer token: {str(e)}")
            # Network errors should retry sooner
            self.integration.eventloop.delay_callback(
                5 * 60, self.refresh_printer_token)
            return False
        except Exception as e:
            logging.error(f"LMNT AUTH: Error refreshing printer token: {str(e)}")
            import traceback
            logging.error(f"LMNT AUTH: {traceback.format_exc()}")
        
        # Schedule another attempt in 1 hour if refresh failed
        logging.info("LMNT AUTH: Scheduling next token refresh attempt in 1 hour")
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
                # Redact sensitive information in response
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

            # DLT Key Pair Management
            printer_public_key_b64_str = None
            if self.dlt_private_key is None:
                logging.info("LMNT AUTH DLT: No existing DLT private key found, generating a new one.")
                try:
                    self.dlt_private_key = PrivateKey.generate()
                    private_key_hex = self.dlt_private_key.encode(encoder=HexEncoder).decode('utf-8')
                    if not self._save_dlt_private_key(private_key_hex):
                        # Handle failure to save key, maybe raise an error or log prominently
                        logging.error("LMNT AUTH DLT: CRITICAL - Failed to save newly generated DLT private key.")
                        # Depending on policy, might prevent registration or proceed without DLT key
                        pass # Or raise an exception
                    else:
                        logging.info("LMNT AUTH DLT: Successfully generated and saved new DLT private key.")
                except Exception as e:
                    logging.error(f"LMNT AUTH DLT: Error generating/saving DLT key pair: {str(e)}")
                    # Ensure dlt_private_key is None if generation/saving fails
                    self.dlt_private_key = None
            
            if self.dlt_private_key:
                try:
                    printer_public_key_b64_str = self.dlt_private_key.public_key.encode(encoder=Base64Encoder).decode('utf-8')
                    logging.info(f"LMNT AUTH DLT: Using DLT public key (b64): {printer_public_key_b64_str[:10]}...")
                except Exception as e:
                    logging.error(f"LMNT AUTH DLT: Error encoding public key: {str(e)}")
                    printer_public_key_b64_str = None # Ensure it's None if encoding fails
            else:
                logging.warning("LMNT AUTH DLT: DLT private key not available. Proceeding with registration without DLT public key.")
            
            # Use standard Authorization header for the marketplace API
            headers = {"Authorization": f"Bearer {self.user_token}"}
            
            # Store printer name for potential re-registration later
            self.printer_name = printer_name
            
            # Build payload with all required fields
            payload = {
                "printer_name": printer_name,
                "manufacturer": manufacturer or "LMNT Printer",  # Default if None or empty
                "model": model or "Klipper"  # Default if None or empty
            }
            if printer_public_key_b64_str:
                payload["printer_public_key"] = printer_public_key_b64_str
            
            # Redact sensitive information in payload for logging
            redacted_payload = self._redact_sensitive_data(payload, is_json=True)
            logging.info(f"Registering printer with payload: {redacted_payload}")
            
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
                        retrieved_printer_id = data.get('id') # Get the printer_id (changed from 'printer_id')

                        if printer_token and retrieved_printer_id:
                            self.printer_id = retrieved_printer_id # Set instance printer_id

                            # Save token and expiry
                            token_expires = data.get('token_expires')
                            expiry = None
                            if token_expires:
                                try:
                                    expiry = datetime.fromisoformat(token_expires.replace('Z', '+00:00'))
                                except ValueError:
                                    logging.warning(f"Could not parse token expiry: {token_expires}")
                            
                            self.save_printer_token(printer_token, expiry)
                            logging.info(f"Printer registered successfully with ID: {self.printer_id}.")
                        else:
                            missing = [item for item, val in [('printer_token', printer_token), ('printer_id', retrieved_printer_id)] if not val]
                            logging.error(f"Printer registration response missing critical fields: {', '.join(missing)}. Full response: {data}")
                            # self.printer_id is not set here if registration is incomplete
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
        # Only close HTTP client if we created our own (not shared)
        # The shared client will be closed by the Integration
        if hasattr(self, '_owns_http_client') and self._owns_http_client and self.http_client is not None:
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

    def _get_hardware_fingerprint(self):
        """
        Generate hardware-specific fingerprint for key derivation
        Uses multiple hardware identifiers to create unique machine fingerprint
        """
        try:
            fingerprint_data = []
            
            # CPU info (Linux/Raspberry Pi)
            try:
                with open('/proc/cpuinfo', 'r') as f:
                    for line in f:
                        if 'Serial' in line or 'Hardware' in line:
                            fingerprint_data.append(line.strip())
            except:
                pass
            
            # Machine ID (systemd)
            try:
                with open('/etc/machine-id', 'r') as f:
                    fingerprint_data.append(f.read().strip())
            except:
                pass
            
            # Network MAC addresses
            try:
                import uuid
                mac = uuid.getnode()
                fingerprint_data.append(str(mac))
            except:
                pass
            
            # Boot ID (changes on reboot, but provides additional entropy)
            try:
                with open('/proc/sys/kernel/random/boot_id', 'r') as f:
                    boot_id = f.read().strip()
                    # Use only part of boot_id to avoid issues with reboots
                    fingerprint_data.append(boot_id[:8])
            except:
                pass
            
            # Fallback to hostname if nothing else available
            if not fingerprint_data:
                import socket
                fingerprint_data.append(socket.gethostname())
            
            # Create stable hash
            combined = '|'.join(sorted(fingerprint_data))
            fingerprint = hashlib.sha256(combined.encode()).digest()
            
            logging.debug(f"LMNT AUTH: Generated hardware fingerprint from {len(fingerprint_data)} sources")
            return fingerprint
            
        except Exception as e:
            logging.warning(f"LMNT AUTH: Could not generate hardware fingerprint: {e}")
            # Fallback to a default (less secure but functional)
            return hashlib.sha256(b"fallback_fingerprint").digest()

    def _derive_encryption_key(self, hardware_fingerprint, salt):
        """
        Derive encryption key from hardware fingerprint using PBKDF2
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,  # 256-bit key
            salt=salt,
            iterations=100000,  # Strong iteration count
            backend=default_backend()
        )
        return kdf.derive(hardware_fingerprint)

    def _encrypt_private_key(self, private_key_hex):
        """
        Encrypt private key using hardware-bound encryption
        
        Returns:
            str: Base64-encoded encrypted data (salt + iv + encrypted_key)
        """
        try:
            # Generate random salt and IV
            salt = secrets.token_bytes(32)
            iv = secrets.token_bytes(16)
            
            # Derive encryption key from hardware
            hardware_fp = self._get_hardware_fingerprint()
            encryption_key = self._derive_encryption_key(hardware_fp, salt)
            
            # Encrypt the private key
            cipher = Cipher(
                algorithms.AES(encryption_key),
                modes.CBC(iv),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()
            
            # Pad private key to block size
            private_key_bytes = private_key_hex.encode('utf-8')
            padding_length = 16 - (len(private_key_bytes) % 16)
            padded_key = private_key_bytes + bytes([padding_length] * padding_length)
            
            encrypted_key = encryptor.update(padded_key) + encryptor.finalize()
            
            # Return base64-encoded salt + iv + encrypted_key
            encrypted_data = salt + iv + encrypted_key
            return base64.b64encode(encrypted_data).decode('utf-8')
            
        except Exception as e:
            logging.error(f"LMNT AUTH: Failed to encrypt private key: {e}")
            return None

    def _decrypt_private_key(self, encrypted_data_b64):
        """
        Decrypt private key using hardware-bound decryption
        
        Args:
            encrypted_data_b64 (str): Base64-encoded salt + iv + encrypted_key
            
        Returns:
            str: Decrypted private key hex string
        """
        try:
            # Decode from base64
            encrypted_data = base64.b64decode(encrypted_data_b64)
            
            if len(encrypted_data) < 48:  # 32 (salt) + 16 (iv) minimum
                logging.error("LMNT AUTH: Encrypted data too short")
                return None
                
            # Extract components
            salt = encrypted_data[:32]
            iv = encrypted_data[32:48]
            encrypted_key = encrypted_data[48:]
            
            # Derive decryption key from hardware
            hardware_fp = self._get_hardware_fingerprint()
            decryption_key = self._derive_encryption_key(hardware_fp, salt)
            
            # Decrypt the private key
            cipher = Cipher(
                algorithms.AES(decryption_key),
                modes.CBC(iv),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            
            decrypted_padded = decryptor.update(encrypted_key) + decryptor.finalize()
            
            # Remove padding
            padding_length = decrypted_padded[-1]
            private_key_bytes = decrypted_padded[:-padding_length]
            
            return private_key_bytes.decode('utf-8')
            
        except Exception as e:
            logging.error(f"LMNT AUTH: Failed to decrypt private key: {e}")
            return None

    def _is_key_encrypted(self, key_data):
        """
        Check if the key data is encrypted (base64) or plaintext hex
        
        Returns:
            bool: True if encrypted, False if plaintext
        """
        try:
            # Encrypted keys are base64 encoded and longer
            if len(key_data) > 64 and '=' in key_data:
                # Try to decode as base64
                base64.b64decode(key_data)
                return True
            return False
        except:
            return False
