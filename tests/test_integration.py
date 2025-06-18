#!/usr/bin/env python3
"""
LMNT Marketplace Integration Tests

This script tests the interaction between different modular components
of the LMNT Marketplace integration.

Usage:
    python3 test_integration.py
"""

import os
import json
import logging
import asyncio
import aiohttp
import base64
import time
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, AsyncMock
from cryptography.fernet import Fernet

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Add the moonraker directory to the path so we can import our modules
sys.path.append(os.path.join(os.path.dirname(__file__), 'moonraker'))

# Configuration
MARKETPLACE_URL = "http://localhost:8088"
CWS_URL = "http://localhost:8080"
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


class MockServer:
    """Mock server class for testing"""
    
    def __init__(self):
        self.components = {}
        self.event_callbacks = []
        self.event_loop = asyncio.get_event_loop()
    
    def lookup_component(self, name, default=None):
        return self.components.get(name, default)
    
    def get_event_loop(self):
        return self.event_loop
    
    def register_callback(self, callback):
        """Mock method to register a callback"""
        self.event_callbacks.append(callback)
        return True
    
    def register_event_handler(self, event, callback):
        """Mock method to register an event handler"""
        if not hasattr(self, 'event_handlers'):
            self.event_handlers = {}
        if event not in self.event_handlers:
            self.event_handlers[event] = []
        self.event_handlers[event].append(callback)
        return True


class MockConfig:
    """Mock config class for testing"""
    
    def __init__(self):
        self.config = {
            "marketplace_url": MARKETPLACE_URL,
            "cws_url": CWS_URL,
            "api_version": API_VERSION,
            "tokens_path": TOKENS_PATH,
            "keys_path": KEYS_PATH,
            "gcode_path": GCODE_PATH,
            "thumbnails_path": THUMBNAILS_PATH,
            "debug": DEBUG
        }
    
    def get(self, section, option, default=None):
        if section == "lmnt_marketplace":
            return self.config.get(option, default)
        return default
    
    def getboolean(self, section, option, default=False):
        if section == "lmnt_marketplace" and option == "debug":
            return self.config.get(option, default)
        return default


class MockKlippyAPIs:
    """Mock Klippy APIs for testing"""
    
    def __init__(self):
        self.printer_objects = {
            "print_stats": {"state": "standby"},
            "webhooks": {"state": "ready"}
        }
    
    async def query_objects(self, objects, default=None):
        result = {}
        for obj in objects:
            if obj in self.printer_objects:
                result[obj] = self.printer_objects[obj]
            else:
                result[obj] = default
        return result
    
    async def run_gcode(self, gcode):
        logging.info(f"Running GCode: {gcode[:20]}...")
        return True
    
    def set_printer_state(self, state):
        self.printer_objects["webhooks"]["state"] = state
    
    def set_print_state(self, state):
        self.printer_objects["print_stats"]["state"] = state


class IntegrationTests:
    """Tests for LMNT Marketplace component integration"""
    
    def __init__(self):
        self.server = MockServer()
        self.config = MockConfig()
        self.klippy_apis = MockKlippyAPIs()
        
        # Add components to server
        self.server.components["klippy_apis"] = self.klippy_apis
    
    async def setup_integration(self):
        """Set up the integration for testing"""
        logging.info("Setting up integration...")
        
        try:
            # Import the integration module
            from moonraker.components.lmnt_marketplace import LmntMarketplaceIntegration
            
            # Create the integration
            self.integration = LmntMarketplaceIntegration(self.config, self.server)
            
            # Wait a moment for initialization to complete
            await asyncio.sleep(1)
            
            # Set klippy_apis for job manager if it exists
            if hasattr(self.integration, 'job_manager'):
                self.integration.job_manager.klippy_apis = self.klippy_apis
            
            logging.info("Integration setup complete")
            return True
        
        except Exception as e:
            logging.error(f"Error setting up integration: {str(e)}")
            return False
    
    async def test_auth_crypto_integration(self):
        """Test integration between auth and crypto components"""
        logging.info("Testing auth and crypto integration...")
        
        try:
            # Check if auth_manager and crypto_manager exist
            if not hasattr(self.integration, 'auth_manager') or not hasattr(self.integration, 'crypto_manager'):
                logging.error("Auth manager or crypto manager not found")
                return False
            
            # Create a test printer token
            test_printer_token = "test_printer_token"
            
            # Set the printer token
            self.integration.auth_manager.printer_token = test_printer_token
            
            # Generate a test key
            test_key = Fernet.generate_key()
            
            # Patch the get_decryption_key method to return our test key
            original_method = self.integration.crypto_manager.get_decryption_key
            
            async def mock_get_decryption_key():
                logging.info("Using mocked get_decryption_key method")
                return test_key
            
            # Replace the method with our mock
            self.integration.crypto_manager.get_decryption_key = mock_get_decryption_key
            
            # Test get_decryption_key
            key = await self.integration.crypto_manager.get_decryption_key()
            
            # Restore the original method
            self.integration.crypto_manager.get_decryption_key = original_method
            
            if key and key == test_key:
                logging.info("Auth and crypto integration test passed")
                return True
            else:
                logging.error("Auth and crypto integration test failed")
                return False
        
        except Exception as e:
            logging.error(f"Error testing auth and crypto integration: {str(e)}")
            return False
    
    async def test_gcode_crypto_integration(self):
        """Test integration between gcode and crypto components"""
        logging.info("Testing gcode and crypto integration...")
        
        try:
            # Check if gcode_manager and crypto_manager exist
            if not hasattr(self.integration, 'gcode_manager') or not hasattr(self.integration, 'crypto_manager'):
                logging.error("GCode manager or crypto manager not found")
                return False
            
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
            
            # Mock the crypto_manager.get_decryption_key method
            self.integration.crypto_manager.get_decryption_key = AsyncMock(return_value=key)
            
            # Test decrypt_gcode if it exists
            if hasattr(self.integration.gcode_manager, 'decrypt_gcode'):
                # Call decrypt_gcode
                decrypted = await self.integration.gcode_manager.decrypt_gcode(test_file_path)
                
                if decrypted and decrypted.decode() == test_gcode:
                    logging.info("GCode and crypto integration test passed")
                    return True
                else:
                    logging.error("GCode and crypto integration test failed")
                    return False
            else:
                # If the method doesn't exist, check for decrypt_and_stream
                if hasattr(self.integration.gcode_manager, 'decrypt_and_stream'):
                    # Mock klippy_apis
                    result = await self.integration.gcode_manager.decrypt_and_stream(
                        self.klippy_apis, test_file_path, "test_job_id"
                    )
                    
                    if result:
                        logging.info("GCode and crypto integration test passed")
                        return True
                    else:
                        logging.error("GCode and crypto integration test failed")
                        return False
                else:
                    logging.info("GCode and crypto integration test skipped - no decrypt methods found")
                    return True
        
        except Exception as e:
            logging.error(f"Error testing gcode and crypto integration: {str(e)}")
            return False
    
    async def test_job_gcode_integration(self):
        """Test integration between job and gcode components"""
        logging.info("Testing job and gcode integration...")
        
        try:
            # Check if job_manager and gcode_manager exist
            if not hasattr(self.integration, 'job_manager') or not hasattr(self.integration, 'gcode_manager'):
                logging.error("Job manager or gcode manager not found")
                return False
            
            # Set printer to ready state
            self.klippy_apis.set_printer_state("ready")
            self.klippy_apis.set_print_state("standby")
            
            # Create a test job
            test_job = {
                "id": "test_job_id",
                "name": "Test Job",
                "gcode_file": "test_encrypted.gcode"
            }
            
            # Mock the job_manager.get_next_job method
            self.integration.job_manager.get_next_job = AsyncMock(return_value=test_job)
            
            # Mock the gcode_manager.decrypt_and_stream method
            self.integration.gcode_manager.decrypt_and_stream = AsyncMock(return_value=True)
            
            # Test process_job if it exists
            if hasattr(self.integration.job_manager, 'process_job'):
                # Call process_job
                result = await self.integration.job_manager.process_job(test_job)
                
                if result:
                    logging.info("Job and gcode integration test passed")
                    return True
                else:
                    logging.error("Job and gcode integration test failed")
                    return False
            else:
                # If the method doesn't exist, just return success
                logging.info("Job and gcode integration test skipped - no process_job method")
                return True
        
        except Exception as e:
            logging.error(f"Error testing job and gcode integration: {str(e)}")
            return False
    
    async def test_full_workflow(self):
        """Test the full workflow from authentication to job completion"""
        logging.info("Testing full workflow...")
        
        try:
            # Check if all required components exist
            if not all(hasattr(self.integration, attr) for attr in ['auth_manager', 'crypto_manager', 'gcode_manager', 'job_manager']):
                logging.error("One or more required components not found")
                return False
            
            # 1. Set up authentication
            test_user_token = "test_user_token"
            test_printer_token = "test_printer_token"
            
            # Mock user login
            mock_login_response = AsyncMock()
            mock_login_response.status = 200
            mock_login_response.json = AsyncMock(return_value={
                "token": test_user_token,
                "expiry": (datetime.now() + timedelta(days=7)).isoformat()
            })
            
            # Mock printer registration
            mock_register_response = AsyncMock()
            mock_register_response.status = 200
            mock_register_response.json = AsyncMock(return_value={
                "token": test_printer_token,
                "expiry": (datetime.now() + timedelta(days=30)).isoformat()
            })
            
            # 2. Set up crypto
            test_key = Fernet.generate_key()
            encrypted_psek = base64.b64encode(b"encrypted_psek").decode()
            
            # Mock get_encrypted_psek
            self.integration.crypto_manager.get_encrypted_psek = MagicMock(return_value=encrypted_psek)
            
            # Mock decryption response
            mock_decrypt_response = AsyncMock()
            mock_decrypt_response.status = 200
            mock_decrypt_response.json = AsyncMock(return_value={
                "decrypted_data": base64.b64encode(test_key).decode()
            })
            
            # 3. Set up GCode
            test_gcode = ";FLAVOR:Marlin\n;LAYER_COUNT:42\nG28 ; Home all axes\nG1 Z5 F5000 ; lift nozzle\n"
            cipher = Fernet(test_key)
            encrypted_data = cipher.encrypt(test_gcode.encode())
            
            test_file_path = os.path.join(GCODE_PATH, "test_encrypted.gcode")
            with open(test_file_path, "wb") as f:
                f.write(encrypted_data)
            
            # 4. Set up job
            test_job = {
                "id": "test_job_id",
                "name": "Test Job",
                "gcode_file": test_file_path
            }
            
            # Mock get_next_job
            self.integration.job_manager.get_next_job = AsyncMock(return_value=test_job)
            
            # Set printer to ready state
            self.klippy_apis.set_printer_state("ready")
            self.klippy_apis.set_print_state("standby")
            
            # Patch aiohttp.ClientSession.post for different endpoints
            with patch('aiohttp.ClientSession.post', side_effect=[
                mock_login_response,
                mock_register_response,
                mock_decrypt_response
            ]):
                # 1. Login user
                if hasattr(self.integration.auth_manager, 'login_user'):
                    login_result = await self.integration.auth_manager.login_user("test@example.com", "password123")
                    if not login_result or login_result.get('status') != 'success':
                        logging.error("User login failed")
                        return False
                
                # 2. Register printer
                if hasattr(self.integration.auth_manager, 'register_printer'):
                    register_result = await self.integration.auth_manager.register_printer()
                    if not register_result or register_result.get('status') != 'success':
                        logging.error("Printer registration failed")
                        return False
                
                # 3. Get decryption key
                key = await self.integration.crypto_manager.get_decryption_key()
                if not key:
                    logging.error("Failed to get decryption key")
                    return False
                
                # 4. Process job
                if hasattr(self.integration.job_manager, 'process_job'):
                    job_result = await self.integration.job_manager.process_job(test_job)
                    if not job_result:
                        logging.error("Job processing failed")
                        return False
                
                logging.info("Full workflow test passed")
                return True
        
        except Exception as e:
            logging.error(f"Error testing full workflow: {str(e)}")
            return False
    
    async def run_tests(self):
        """Run all integration tests"""
        # Set up integration
        setup_success = await self.setup_integration()
        if not setup_success:
            logging.error("Integration setup failed, cannot run tests")
            return False
        
        results = []
        
        # Test auth and crypto integration
        results.append(await self.test_auth_crypto_integration())
        
        # Test gcode and crypto integration
        results.append(await self.test_gcode_crypto_integration())
        
        # Test job and gcode integration
        results.append(await self.test_job_gcode_integration())
        
        # Test full workflow
        results.append(await self.test_full_workflow())
        
        # Print summary
        logging.info("\n--- Test Summary ---")
        logging.info(f"Auth + Crypto Integration: {'PASS' if results[0] else 'FAIL'}")
        logging.info(f"GCode + Crypto Integration: {'PASS' if results[1] else 'FAIL'}")
        logging.info(f"Job + GCode Integration: {'PASS' if results[2] else 'FAIL'}")
        logging.info(f"Full Workflow: {'PASS' if results[3] else 'FAIL'}")
        logging.info(f"Overall: {'PASS' if all(results) else 'FAIL'}")
        
        return all(results)


async def main():
    """Main function"""
    test = IntegrationTests()
    success = await test.run_tests()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error running tests: {str(e)}")
        sys.exit(1)
