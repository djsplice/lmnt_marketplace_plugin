#!/usr/bin/env python3
"""
LMNT Marketplace Full Integration Test Script

This script tests the complete workflow of the modular LMNT Marketplace integration
by simulating a real-world scenario from authentication to job completion.

Usage:
    python3 test_full_integration.py [--email EMAIL] [--password PASSWORD] [--skip-login] [--skip-register]
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
from unittest.mock import MagicMock

# Add the moonraker directory to the path so we can import our modules
sys.path.append(os.path.join(os.path.dirname(__file__), 'moonraker'))

# Import the modular integration
from moonraker.components.lmnt_marketplace import LmntMarketplaceIntegration

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
THUMBNAILS_PATH = os.path.join(BASE_PATH, "thumbnails")

# Ensure directories exist
os.makedirs(TOKENS_PATH, exist_ok=True)
os.makedirs(KEYS_PATH, exist_ok=True)
os.makedirs(GCODE_PATH, exist_ok=True)
os.makedirs(THUMBNAILS_PATH, exist_ok=True)

# Test data
TEST_EMAIL = "test@example.com"
TEST_PASSWORD = "password123"
TEST_PRINTER_NAME = "Test Printer"
TEST_MANUFACTURER = "Test Manufacturer"
TEST_MODEL = "Test Model"


class MockServer:
    """Mock server class for testing the integration"""
    
    def __init__(self):
        self.components = {}
        self.event_handlers = {}
        self.endpoints = {}
    
    def register_event_handler(self, event, callback):
        """Register an event handler"""
        if event not in self.event_handlers:
            self.event_handlers[event] = []
        self.event_handlers[event].append(callback)
        logging.info(f"Registered event handler for {event}")
    
    def register_endpoint(self, endpoint, method, callback):
        """Register an HTTP endpoint"""
        self.endpoints[endpoint] = {
            "method": method,
            "callback": callback
        }
        logging.info(f"Registered endpoint {endpoint} with method {method}")
    
    def lookup_component(self, name, default=None):
        """Look up a component by name"""
        return self.components.get(name, default)
    
    def add_component(self, name, component):
        """Add a component"""
        self.components[name] = component
        logging.info(f"Added component {name}")
    
    def get_event_loop(self):
        """Get the event loop"""
        return asyncio.get_event_loop()


class MockConfig:
    """Mock configuration class for testing the integration"""
    
    def __init__(self):
        self.config = {
            "marketplace_url": MARKETPLACE_URL,
            "api_version": API_VERSION,
            "debug": DEBUG,
            "secure_storage_path": BASE_PATH,
            "tokens_path": TOKENS_PATH,
            "keys_path": KEYS_PATH,
            "gcode_path": GCODE_PATH,
            "thumbnails_path": THUMBNAILS_PATH
        }
    
    def get(self, key, default=None):
        """Get a configuration value"""
        return self.config.get(key, default)
    
    def getboolean(self, key, default=False):
        """Get a boolean configuration value"""
        return bool(self.config.get(key, default))
    
    def get_server(self):
        """Get the server instance"""
        return server


class MockKlippyAPIs:
    """Mock Klippy APIs for testing"""
    
    def __init__(self):
        self.printer_state = "ready"
        self.print_stats = {
            "state": "standby",
            "filename": "",
            "progress": 0.0,
            "total_duration": 0.0
        }
    
    async def query_objects(self, params):
        """Query printer objects"""
        objects = params.get('objects', {})
        result = {}
        
        if 'webhooks' in objects:
            result['webhooks'] = {"state": self.printer_state}
        
        if 'print_stats' in objects:
            result['print_stats'] = self.print_stats
        
        return result
    
    async def run_gcode(self, gcode):
        """Run GCode command"""
        logging.info(f"Running GCode: {gcode}")
        return True
    
    def set_print_progress(self, progress):
        """Set print progress for testing"""
        self.print_stats["progress"] = progress
        
        # Update state based on progress
        if progress == 0:
            self.print_stats["state"] = "standby"
        elif progress < 1.0:
            self.print_stats["state"] = "printing"
        else:
            self.print_stats["state"] = "complete"


class IntegrationTest:
    """Test class for full LMNT Marketplace integration"""
    
    def __init__(self):
        """Initialize the test class"""
        # Create mock server and config
        self.config = MockConfig()
        
        # Create HTTP client
        self.http_client = aiohttp.ClientSession()
        
        # Create the integration
        self.integration = LmntMarketplaceIntegration(self.config, server)
        
        # Create mock Klippy APIs
        self.klippy_apis = MockKlippyAPIs()
        server.add_component('klippy_apis', self.klippy_apis)
    
    async def initialize(self):
        """Initialize the integration"""
        await self.integration.initialize(self.klippy_apis)
        logging.info("Integration initialized")
    
    async def cleanup(self):
        """Clean up resources"""
        await self.http_client.close()
        logging.info("Resources cleaned up")
    
    async def test_user_login(self, email, password):
        """Test user login"""
        logging.info(f"Testing user login with {email}...")
        result = await self.integration.auth_manager.login_user(email, password)
        
        if result.get('status') == 'success':
            logging.info("User login successful")
            return True
        else:
            logging.error(f"User login failed: {result.get('message')}")
            return False
    
    async def test_register_printer(self, printer_name):
        """Test printer registration"""
        logging.info(f"Testing printer registration for {printer_name}...")
        
        if not self.integration.auth_manager.user_token:
            logging.error("No user token available. Please login first.")
            return False
        
        result = await self.integration.auth_manager.register_printer(
            self.integration.auth_manager.user_token, printer_name)
        
        if result.get('status') == 'success':
            logging.info("Printer registration successful")
            return True
        else:
            logging.error(f"Printer registration failed: {result.get('message')}")
            return False
    
    async def test_token_refresh(self):
        """Test printer token refresh"""
        logging.info("Testing printer token refresh...")
        
        if not self.integration.auth_manager.printer_token:
            logging.error("No printer token available. Please register printer first.")
            return False
        
        # Store the original token for comparison
        original_token = self.integration.auth_manager.printer_token
        
        # Refresh the token
        result = await self.integration.auth_manager.refresh_printer_token()
        
        if result:
            new_token = self.integration.auth_manager.printer_token
            if new_token != original_token:
                logging.info("Token refresh successful - received new token")
                return True
            else:
                logging.warning("Token refresh returned same token")
                return True
        else:
            logging.error("Token refresh failed")
            return False
    
    async def test_psek_handling(self):
        """Test PSEK handling"""
        logging.info("Testing PSEK handling...")
        
        # Generate a test PSEK
        test_psek = b"test_encrypted_psek_data"
        
        # Save the PSEK
        await self.integration.crypto_manager.save_encrypted_psek(test_psek)
        logging.info("Saved encrypted PSEK")
        
        # Load the PSEK
        loaded_psek = await self.integration.crypto_manager.load_encrypted_psek()
        
        if loaded_psek == test_psek:
            logging.info("PSEK load/save test successful")
            return True
        else:
            logging.error("PSEK load/save test failed")
            return False
    
    async def test_gcode_decryption(self):
        """Test GCode decryption and streaming"""
        logging.info("Testing GCode decryption and streaming...")
        
        # Create a test GCode file
        test_gcode = ";FLAVOR:Marlin\n;LAYER_COUNT:42\nG28 ; Home all axes\nG1 Z5 F5000 ; lift nozzle\n"
        
        # Generate a key and encrypt the GCode
        key = Fernet.generate_key()
        cipher = Fernet(key)
        encrypted_data = cipher.encrypt(test_gcode.encode())
        
        # Save the encrypted GCode
        test_file_path = os.path.join(GCODE_PATH, "test_encrypted.gcode")
        with open(test_file_path, "wb") as f:
            f.write(encrypted_data)
        
        # Mock the get_decryption_key method to return our test key
        self.integration.crypto_manager.get_decryption_key = MagicMock(return_value=key)
        
        # Decrypt and stream the GCode
        result = await self.integration.gcode_manager.decrypt_and_stream(
            self.klippy_apis, test_file_path, "test_job_id")
        
        if result:
            metadata = self.integration.gcode_manager.current_metadata
            if metadata.get("layer_count") == 42:
                logging.info("GCode decryption and metadata extraction successful")
                return True
            else:
                logging.error("GCode metadata extraction failed")
                return False
        else:
            logging.error("GCode decryption failed")
            return False
    
    async def test_job_management(self):
        """Test job management"""
        logging.info("Testing job management...")
        
        # Create a test job
        test_job = {
            "id": "test_job_id",
            "gcode_url": "http://example.com/test.gcode"
        }
        
        # Mock the download_gcode method
        self.integration.job_manager._download_gcode = MagicMock(
            return_value=os.path.join(GCODE_PATH, "test_encrypted.gcode"))
        
        # Mock the decrypt_and_stream method
        self.integration.gcode_manager.decrypt_and_stream = MagicMock(return_value=True)
        
        # Start the job
        self.integration.job_manager.current_print_job = test_job
        result = await self.integration.job_manager._start_print(
            test_job, os.path.join(GCODE_PATH, "test_encrypted.gcode"))
        
        if result:
            logging.info("Job start successful")
            
            # Test job status update
            update_result = await self.integration.job_manager._update_job_status(
                "test_job_id", "printing", "Test job is printing")
            
            if update_result:
                logging.info("Job status update successful")
                return True
            else:
                logging.error("Job status update failed")
                return False
        else:
            logging.error("Job start failed")
            return False
    
    async def run_full_workflow(self, args):
        """Run the full integration workflow"""
        success = True
        
        # Initialize the integration
        await self.initialize()
        
        # User login
        if not args.skip_login:
            if not await self.test_user_login(args.email, args.password):
                success = False
        
        # Printer registration
        if success and not args.skip_register:
            if not await self.test_register_printer(TEST_PRINTER_NAME):
                success = False
        
        # Token refresh
        if success:
            if not await self.test_token_refresh():
                success = False
        
        # PSEK handling
        if success:
            if not await self.test_psek_handling():
                success = False
        
        # GCode decryption
        if success:
            if not await self.test_gcode_decryption():
                success = False
        
        # Job management
        if success:
            if not await self.test_job_management():
                success = False
        
        # Clean up
        await self.cleanup()
        
        return success


async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Test LMNT Marketplace full integration")
    parser.add_argument("--email", default=TEST_EMAIL, help="Email for login")
    parser.add_argument("--password", default=TEST_PASSWORD, help="Password for login")
    parser.add_argument("--skip-login", action="store_true", help="Skip login test")
    parser.add_argument("--skip-register", action="store_true", help="Skip printer registration test")
    
    args = parser.parse_args()
    
    # Create and run the integration test
    test = IntegrationTest()
    success = await test.run_full_workflow(args)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


# Create global server instance
server = MockServer()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error running tests: {str(e)}")
        sys.exit(1)
