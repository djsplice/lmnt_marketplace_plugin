#!/usr/bin/env python3
"""
LMNT Marketplace Modular Integration Test Script

This script tests the modular components of the LMNT Marketplace integration
without requiring a full Moonraker instance.

Usage:
    python3 test_modular_integration.py [--auth] [--crypto] [--gcode] [--jobs] [--all]
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
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from cryptography.fernet import Fernet

# Add the moonraker directory to the path so we can import our modules
sys.path.append(os.path.join(os.path.dirname(__file__), 'moonraker'))

# Import the modules we want to test
from moonraker.components.lmnt_marketplace.auth import AuthManager
from moonraker.components.lmnt_marketplace.crypto import CryptoManager
from moonraker.components.lmnt_marketplace.gcode import GCodeManager
from moonraker.components.lmnt_marketplace.jobs import JobManager

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


class MockIntegration:
    """Mock integration class for testing individual modules"""
    
    def __init__(self):
        self.marketplace_url = MARKETPLACE_URL
        self.api_version = API_VERSION
        self.debug = DEBUG
        self.secure_storage_path = BASE_PATH
        self.tokens_path = TOKENS_PATH
        self.keys_path = KEYS_PATH
        self.gcode_path = GCODE_PATH
        self.thumbnails_path = THUMBNAILS_PATH
        
        # Mock managers
        self.auth_manager = None
        self.crypto_manager = None
        self.gcode_manager = None
        self.job_manager = None


class AuthManagerTest(unittest.TestCase):
    """Test cases for the AuthManager module"""
    
    def setUp(self):
        """Set up test environment"""
        self.integration = MockIntegration()
        self.http_client = MagicMock()
        self.auth_manager = AuthManager(self.integration, self.http_client)
    
    async def test_login_user(self):
        """Test user login functionality"""
        # Mock HTTP client response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = MagicMock(return_value={
            "token": "test_user_token",
            "expiry": (datetime.now() + timedelta(days=7)).isoformat()
        })
        
        # Set up mock for ClientSession.post
        self.http_client.post = MagicMock(return_value=mock_response)
        
        # Call the login method
        result = await self.auth_manager.login_user(TEST_EMAIL, TEST_PASSWORD)
        
        # Verify results
        self.assertEqual(result.get('status'), 'success')
        self.assertEqual(self.auth_manager.user_token, "test_user_token")
        self.assertIsNotNone(self.auth_manager.user_token_expiry)
    
    async def test_register_printer(self):
        """Test printer registration functionality"""
        # Set up user token
        self.auth_manager.user_token = "test_user_token"
        
        # Mock HTTP client response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = MagicMock(return_value={
            "token": "test_printer_token",
            "expiry": (datetime.now() + timedelta(days=30)).isoformat(),
            "printer_id": "test_printer_id",
            "kek_id": "encrypted_psek_data"
        })
        
        # Set up mock for ClientSession.post
        self.http_client.post = MagicMock(return_value=mock_response)
        
        # Call the register method
        result = await self.auth_manager.register_printer("test_user_token", TEST_PRINTER_NAME)
        
        # Verify results
        self.assertEqual(result.get('status'), 'success')
        self.assertEqual(self.auth_manager.printer_token, "test_printer_token")
        self.assertIsNotNone(self.auth_manager.token_expiry)
        self.assertTrue(self.auth_manager.printer_registered)


class CryptoManagerTest(unittest.TestCase):
    """Test cases for the CryptoManager module"""
    
    def setUp(self):
        """Set up test environment"""
        self.integration = MockIntegration()
        self.http_client = MagicMock()
        
        # Create a mock auth manager
        self.auth_manager = MagicMock()
        self.auth_manager.printer_token = "test_printer_token"
        self.integration.auth_manager = self.auth_manager
        
        self.crypto_manager = CryptoManager(self.integration, self.http_client)
    
    async def test_save_load_encrypted_psek(self):
        """Test saving and loading encrypted PSEK"""
        # Generate test data
        test_psek = b"test_encrypted_psek_data"
        
        # Save the PSEK
        await self.crypto_manager.save_encrypted_psek(test_psek)
        
        # Load the PSEK
        loaded_psek = await self.crypto_manager.load_encrypted_psek()
        
        # Verify results
        self.assertEqual(loaded_psek, test_psek)
    
    async def test_get_decryption_key(self):
        """Test getting decryption key from CWS"""
        # Set up encrypted PSEK
        test_psek = b"test_encrypted_psek_data"
        await self.crypto_manager.save_encrypted_psek(test_psek)
        
        # Mock HTTP client response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = MagicMock(return_value={
            "decrypted_data": base64.b64encode(Fernet.generate_key()).decode()
        })
        
        # Set up mock for ClientSession.post
        self.http_client.post = MagicMock(return_value=mock_response)
        
        # Call the get_decryption_key method
        key = await self.crypto_manager.get_decryption_key()
        
        # Verify results
        self.assertIsNotNone(key)
        self.assertTrue(len(key) > 0)


class GCodeManagerTest(unittest.TestCase):
    """Test cases for the GCodeManager module"""
    
    def setUp(self):
        """Set up test environment"""
        self.integration = MockIntegration()
        self.http_client = MagicMock()
        
        # Create a mock crypto manager
        self.crypto_manager = MagicMock()
        test_key = Fernet.generate_key()
        self.crypto_manager.get_decryption_key = MagicMock(return_value=test_key)
        self.integration.crypto_manager = self.crypto_manager
        
        # Create the GCode manager
        self.gcode_manager = GCodeManager(self.integration, self.http_client)
        
        # Create a test encrypted file
        self.test_gcode = ";FLAVOR:Marlin\n;LAYER_COUNT:10\nG28 ; Home all axes\nG1 Z5 F5000 ; lift nozzle\n"
        self.cipher = Fernet(test_key)
        self.encrypted_data = self.cipher.encrypt(self.test_gcode.encode())
        
        self.test_file_path = os.path.join(GCODE_PATH, "test_encrypted.gcode")
        with open(self.test_file_path, "wb") as f:
            f.write(self.encrypted_data)
    
    async def test_extract_metadata(self):
        """Test extracting metadata from GCode"""
        # Create mock klippy_apis
        klippy_apis = MagicMock()
        
        # Call the decrypt_and_stream method
        result = await self.gcode_manager.decrypt_and_stream(
            klippy_apis, self.test_file_path, "test_job_id")
        
        # Verify results
        self.assertTrue(result)
        self.assertEqual(self.gcode_manager.current_metadata.get("layer_count"), 10)


class JobManagerTest(unittest.TestCase):
    """Test cases for the JobManager module"""
    
    def setUp(self):
        """Set up test environment"""
        self.integration = MockIntegration()
        self.http_client = MagicMock()
        self.klippy_apis = MagicMock()
        
        # Create mock managers
        self.auth_manager = MagicMock()
        self.auth_manager.printer_token = "test_printer_token"
        self.integration.auth_manager = self.auth_manager
        
        self.crypto_manager = MagicMock()
        self.integration.crypto_manager = self.crypto_manager
        
        self.gcode_manager = MagicMock()
        self.integration.gcode_manager = self.gcode_manager
        
        # Create the job manager
        self.job_manager = JobManager(self.integration, self.http_client, self.klippy_apis)
    
    async def test_check_printer_ready(self):
        """Test checking if printer is ready"""
        # Mock klippy_apis response
        self.klippy_apis.query_objects = MagicMock(return_value={
            "print_stats": {"state": "standby"},
            "webhooks": {"state": "ready"}
        })
        
        # Call the check_printer_ready method
        result = await self.job_manager._check_printer_ready()
        
        # Verify results
        self.assertTrue(result)
    
    async def test_update_job_status(self):
        """Test updating job status"""
        # Mock HTTP client response
        mock_response = MagicMock()
        mock_response.status = 200
        
        # Set up mock for ClientSession.post
        self.http_client.post = MagicMock(return_value=mock_response)
        
        # Call the update_job_status method
        result = await self.job_manager._update_job_status("test_job_id", "printing", "Test message")
        
        # Verify results
        self.assertTrue(result)


async def run_tests(args):
    """Run the specified tests"""
    # Create test suite
    suite = unittest.TestSuite()
    
    # Add tests based on arguments
    if args.auth or args.all:
        suite.addTest(AuthManagerTest("test_login_user"))
        suite.addTest(AuthManagerTest("test_register_printer"))
    
    if args.crypto or args.all:
        suite.addTest(CryptoManagerTest("test_save_load_encrypted_psek"))
        suite.addTest(CryptoManagerTest("test_get_decryption_key"))
    
    if args.gcode or args.all:
        suite.addTest(GCodeManagerTest("test_extract_metadata"))
    
    if args.jobs or args.all:
        suite.addTest(JobManagerTest("test_check_printer_ready"))
        suite.addTest(JobManagerTest("test_update_job_status"))
    
    # Run the tests
    runner = unittest.TextTestRunner()
    result = runner.run(suite)
    
    return result.wasSuccessful()


async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Test LMNT Marketplace modular integration")
    parser.add_argument("--auth", action="store_true", help="Test auth module")
    parser.add_argument("--crypto", action="store_true", help="Test crypto module")
    parser.add_argument("--gcode", action="store_true", help="Test gcode module")
    parser.add_argument("--jobs", action="store_true", help="Test jobs module")
    parser.add_argument("--all", action="store_true", help="Test all modules")
    
    args = parser.parse_args()
    
    # If no arguments provided, test all modules
    if not (args.auth or args.crypto or args.gcode or args.jobs or args.all):
        args.all = True
    
    # Run the tests
    success = await run_tests(args)
    
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
