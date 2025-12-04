#!/usr/bin/env python3
"""
LMNT Marketplace Integration Test Script

This script tests the core functionality of the LMNT Marketplace Plugin
without requiring a full Moonraker instance.

Usage:
    python3 test_marketplace_integration.py [--email EMAIL] [--password PASSWORD] [--skip-login] [--skip-register] [--refresh-only] [--decrypt-only]
"""

import os
import json
import logging
import asyncio
import aiohttp
import base64
import time
import argparse
import sys
from datetime import datetime, timedelta
from cryptography.fernet import Fernet

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Configuration
MARKETPLACE_URL = "http://localhost:8088"
API_VERSION = "v1"
DEBUG = True

# Storage paths
BASE_PATH = os.path.expanduser("~/test_lmnt_marketplace")
TOKENS_PATH = os.path.join(BASE_PATH, "tokens")
KEYS_PATH = os.path.join(BASE_PATH, "keys")
GCODE_PATH = os.path.join(BASE_PATH, "gcode")

# Ensure directories exist
os.makedirs(TOKENS_PATH, exist_ok=True)
os.makedirs(KEYS_PATH, exist_ok=True)
os.makedirs(GCODE_PATH, exist_ok=True)

# Default test credentials (can be overridden via command line args)
TEST_EMAIL = "test@example.com"
TEST_PASSWORD = "password123"
TEST_PRINTER_NAME = "Test Printer"
TEST_MANUFACTURER = "Test Manufacturer"
TEST_MODEL = "Test Model"

class MarketplaceIntegrationTest:
    """Test class for LMNT Marketplace integration"""
    
    def __init__(self):
        self.user_token = None
        self.user_token_expiry = None
        self.printer_token = None
        self.token_expiry = None
        self.printer_registered = False
    
    async def save_user_token(self, token, expiry):
        """Save user token to file"""
        token_data = {
            "token": token,
            "expiry": expiry.isoformat() if isinstance(expiry, datetime) else expiry
        }
        
        token_path = os.path.join(TOKENS_PATH, "user_token.json")
        with open(token_path, 'w') as f:
            json.dump(token_data, f)
        
        logging.info(f"User token saved to {token_path}")
        return True
    
    async def save_printer_token(self):
        """Save printer token to file"""
        if not self.printer_token:
            logging.error("No printer token to save")
            return False
            
        token_data = {
            "token": self.printer_token,
            "expiry": self.token_expiry.isoformat() if isinstance(self.token_expiry, datetime) else self.token_expiry
        }
        
        token_path = os.path.join(TOKENS_PATH, "printer_token.json")
        with open(token_path, 'w') as f:
            json.dump(token_data, f)
        
        logging.info(f"Printer token saved to {token_path}")
        return True
    
    async def load_tokens(self):
        """Load tokens from files if they exist"""
        # Try to load user token
        user_token_path = os.path.join(TOKENS_PATH, "user_token.json")
        if os.path.exists(user_token_path):
            try:
                with open(user_token_path, 'r') as f:
                    data = json.load(f)
                    self.user_token = data.get("token")
                    expiry_str = data.get("expiry")
                    if expiry_str:
                        self.user_token_expiry = datetime.fromisoformat(expiry_str)
                logging.info("Loaded user token from file")
            except Exception as e:
                logging.error(f"Error loading user token: {str(e)}")
        
        # Try to load printer token
        printer_token_path = os.path.join(TOKENS_PATH, "printer_token.json")
        if os.path.exists(printer_token_path):
            try:
                with open(printer_token_path, 'r') as f:
                    data = json.load(f)
                    self.printer_token = data.get("token")
                    expiry_str = data.get("expiry")
                    if expiry_str:
                        self.token_expiry = datetime.fromisoformat(expiry_str)
                        if self.token_expiry > datetime.now():
                            self.printer_registered = True
                logging.info("Loaded printer token from file")
            except Exception as e:
                logging.error(f"Error loading printer token: {str(e)}")
    
    
    async def register_printer(self):
        """Test printer registration with marketplace"""
        logging.info("Testing printer registration...")
        
        if not self.user_token:
            logging.error("No user token available. Please login first.")
            return False
        
        try:
            url = f"{MARKETPLACE_URL}/api/register-printer"
            headers = {
                'Authorization': f'Bearer {self.user_token}',
                'Content-Type': 'application/json'
            }
            
            data = {
                "printer_name": TEST_PRINTER_NAME,
                "manufacturer": TEST_MANUFACTURER,
                "model": TEST_MODEL
            }
            
            timeout = aiohttp.ClientTimeout(total=10)  # 10 second timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                try:
                    async with session.post(url, json=data, headers=headers) as response:
                        if response.status == 201:
                            # Registration successful
                            response_data = await response.json()
                            
                            # Extract and store printer token
                            self.printer_token = response_data.get('token') or response_data.get('printer_token')
                            
                            # Parse expiry if provided, otherwise default to 30 days
                            expiry = response_data.get('expiry') or response_data.get('token_expires')
                            if expiry:
                                try:
                                    self.token_expiry = datetime.fromisoformat(expiry)
                                except (ValueError, TypeError):
                                    self.token_expiry = datetime.now() + timedelta(days=30)
                            else:
                                self.token_expiry = datetime.now() + timedelta(days=30)
                        
                            # Save token to secure storage
                            await self.save_printer_token()
                            
                            # Extract and save encrypted PSEK
                            kek_id = response_data.get('kek_id')
                            if kek_id:
                                psek_path = os.path.join(KEYS_PATH, "kek_id")
                                with open(psek_path, 'w') as f:
                                    f.write(kek_id)
                                logging.info(f"Saved encrypted PSEK to {psek_path}")
                            
                            self.printer_registered = True
                            logging.info("Printer registration successful")
                            return True
                        else:
                            error_text = await response.text()
                            logging.error(f"Registration failed: {response.status} - {error_text}")
                            return False
                except asyncio.TimeoutError:
                    logging.error(f"Registration request timed out after 10 seconds")
                    return False
        except Exception as e:
            logging.error(f"Error during printer registration: {str(e)}")
            return False
    
    async def refresh_printer_token(self):
        """Test refreshing the printer token"""
        logging.info("Testing printer token refresh...")
        
        if not self.printer_token:
            logging.error("No printer token to refresh")
            return False
            
        try:
            # Call marketplace token refresh endpoint
            url = f"{MARKETPLACE_URL}/api/refresh-printer-token"
            headers = {
                'Authorization': f'Bearer {self.printer_token}',
                'Content-Type': 'application/json'
            }
            
            logging.info(f"Refreshing printer token via {url}")
            timeout = aiohttp.ClientTimeout(total=10)  # 10 second timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                try:
                    async with session.post(url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            new_token = data.get('token')
                            
                            # Handle different possible response formats
                            expiry = data.get('expiry') or data.get('expires_at')
                            
                            if new_token:
                                # If expiry is provided, use it; otherwise default to 30 days
                                if expiry:
                                    try:
                                        # Try to parse as ISO format
                                        expiry_dt = datetime.fromisoformat(expiry)
                                    except (ValueError, TypeError):
                                        # If it's a timestamp or other format, default to 30 days
                                        expiry_dt = datetime.now() + timedelta(days=30)
                                else:
                                    expiry_dt = datetime.now() + timedelta(days=30)
                                
                                # Update token and expiry
                                self.printer_token = new_token
                                self.token_expiry = expiry_dt
                                
                                # Save to secure storage
                                await self.save_printer_token()
                                
                                logging.info(f"Printer token refreshed successfully, new expiry: {expiry_dt}")
                                return True
                            else:
                                logging.error("Token refresh response missing token")
                                return False
                        else:
                            error_text = await response.text()
                            logging.error(f"Token refresh failed: {response.status} - {error_text}")
                            return False
                except asyncio.TimeoutError:
                    logging.error(f"Token refresh request timed out after 10 seconds")
                    return False
        except Exception as e:
            logging.error(f"Error refreshing printer token: {str(e)}")
            return False
    
    async def get_decryption_key(self):
        """Test getting the decryption key via CWS"""
        logging.info("Testing PSEK decryption via CWS...")
        
        try:
            # 1. Read the encrypted PSEK (kek_id) received during registration
            kek_id_path = os.path.join(KEYS_PATH, "kek_id")
            if not os.path.exists(kek_id_path):
                logging.error("No encrypted PSEK found")
                return None
                
            with open(kek_id_path, 'r') as f:
                encrypted_psek = f.read()
            
            
            # For testing/debug environment, use a simulated key
            logging.warning("Using dummy PSEK for testing - in production would use CWS decryption")
            return Fernet.generate_key()
        except Exception as e:
            logging.error(f"Error getting decryption key: {str(e)}")
            return None
    
    async def simulate_decrypt_gcode(self):
        """Simulate decrypting a GCode file"""
        logging.info("Simulating GCode decryption...")
        
        # Get the decryption key (PSEK)
        psek = await self.get_decryption_key()
        if not psek:
            logging.error("Failed to get decryption key")
            return False
        
        try:
            # Create a sample encrypted file for testing
            test_gcode = "G28 ; Home all axes\nG1 X100 Y100 Z10 F3000 ; Move to position\nM104 S200 ; Set extruder temperature"
            
            # Encrypt the test GCode using the PSEK
            fernet = Fernet(psek)
            encrypted_data = fernet.encrypt(test_gcode.encode())
            
            # Save the encrypted file
            encrypted_path = os.path.join(GCODE_PATH, "encrypted_test.gcode")
            with open(encrypted_path, 'wb') as f:
                f.write(encrypted_data)
            
            logging.info(f"Created test encrypted GCode file: {encrypted_path}")
            
            # Now decrypt it
            with open(encrypted_path, 'rb') as f:
                encrypted_content = f.read()
            
            decrypted_data = fernet.decrypt(encrypted_content).decode()
            
            # Save the decrypted file
            decrypted_path = os.path.join(GCODE_PATH, "decrypted_test.gcode")
            with open(decrypted_path, 'w') as f:
                f.write(decrypted_data)
            
            logging.info(f"Successfully decrypted GCode to: {decrypted_path}")
            logging.info(f"Decrypted content: {decrypted_data}")
            
            return True
        except Exception as e:
            logging.error(f"Error simulating GCode decryption: {str(e)}")
            return False

async def main():
    # Set a global timeout for the entire script
    try:
        # Run with a timeout of 60 seconds for the entire script
        return await asyncio.wait_for(_main(), timeout=60)
    except asyncio.TimeoutError:
        logging.error("Script execution timed out after 60 seconds")
        return

async def _main():
    """Main test function"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Test LMNT Marketplace API integration')
    parser.add_argument('--skip-login', action='store_true', help='Deprecated: Login is no longer supported in this script')
    parser.add_argument('--skip-register', action='store_true', help='Skip printer registration step')
    parser.add_argument('--refresh-only', action='store_true', help='Only test token refresh')
    parser.add_argument('--decrypt-only', action='store_true', help='Only test GCode decryption')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    # Set log level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    
    test = MarketplaceIntegrationTest()
    
    # Load any existing tokens
    await test.load_tokens()
    
    # Handle specific test modes
    if args.refresh_only:
        if not test.printer_token:
            logging.error("No printer token found for refresh test")
            return
        await test.refresh_printer_token()
        return
    
    if args.decrypt_only:
        await test.simulate_decrypt_gcode()
        return
    
    
    # Test printer registration if needed and not skipped
    if not args.skip_register and not test.printer_registered:
        if not await test.register_printer():
            logging.error("Printer registration failed, cannot continue")
            return
    
    # Test token refresh
    if test.printer_token:
        if not await test.refresh_printer_token():
            logging.warning("Token refresh failed, but continuing with tests")
    
    # Test GCode decryption
    if not await test.simulate_decrypt_gcode():
        logging.error("GCode decryption simulation failed")
    
    logging.info("All tests completed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nScript terminated by user")
    except Exception as e:
        logging.error(f"Unhandled exception: {str(e)}")
    finally:
        print("Script execution completed")
