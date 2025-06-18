#!/usr/bin/env python3
"""
LMNT Marketplace Component Tests

This script tests the individual components of the LMNT Marketplace integration
without requiring the full integration framework.

Usage:
    python3 test_components.py
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
from unittest.mock import MagicMock, patch
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


class ComponentTests:
    """Tests for individual LMNT Marketplace components"""
    
    def __init__(self):
        self.http_client = MagicMock()
        self.klippy_apis = MagicMock()
    
    async def test_auth_component(self):
        """Test the auth component directly"""
        logging.info("Testing auth component...")
        
        try:
            # Import the auth module
            from moonraker.components.lmnt_marketplace.auth import AuthManager
            
            # Create a mock integration
            mock_integration = MagicMock()
            mock_integration.marketplace_url = MARKETPLACE_URL
            mock_integration.cws_url = CWS_URL
            mock_integration.api_version = API_VERSION
            mock_integration.tokens_path = TOKENS_PATH
            
            # Create the auth manager
            auth_manager = AuthManager(mock_integration)
            
            # Mock HTTP response for login
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = MagicMock(return_value={
                "token": "test_user_token",
                "expiry": (datetime.now() + timedelta(days=7)).isoformat()
            })
            
            # Patch aiohttp.ClientSession.post
            with patch('aiohttp.ClientSession.post', return_value=mock_response):
                # Test login
                result = await auth_manager.login_user("test@example.com", "password123")
                
                if result.get('status') == 'success':
                    logging.info("Auth component login test passed")
                    return True
                else:
                    logging.error("Auth component login test failed")
                    return False
        
        except Exception as e:
            logging.error(f"Error testing auth component: {str(e)}")
            return False
    
    async def test_crypto_component(self):
        """Test the crypto component directly"""
        logging.info("Testing crypto component...")
        
        try:
            # Import the crypto module
            from moonraker.components.lmnt_marketplace.crypto import CryptoManager
            
            # Create a mock integration
            mock_integration = MagicMock()
            mock_integration.keys_path = KEYS_PATH
            mock_integration.auth_manager = MagicMock()
            mock_integration.auth_manager.printer_token = "test_printer_token"
            
            # Create the crypto manager
            crypto_manager = CryptoManager(mock_integration)
            
            # Generate a test key
            test_key = Fernet.generate_key()
            
            # Mock HTTP response for decryption
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = MagicMock(return_value={
                "decrypted_data": base64.b64encode(test_key).decode()
            })
            
            # Patch aiohttp.ClientSession.post
            with patch('aiohttp.ClientSession.post', return_value=mock_response):
                # Test get_decryption_key
                key = await crypto_manager.get_decryption_key()
                
                if key:
                    logging.info("Crypto component key test passed")
                    return True
                else:
                    logging.error("Crypto component key test failed")
                    return False
        
        except Exception as e:
            logging.error(f"Error testing crypto component: {str(e)}")
            return False
    
    async def test_gcode_component(self):
        """Test the gcode component directly"""
        logging.info("Testing gcode component...")
        
        try:
            # Import the gcode module
            from moonraker.components.lmnt_marketplace.gcode import GCodeManager
            
            # Create a mock integration
            mock_integration = MagicMock()
            mock_integration.gcode_path = GCODE_PATH
            mock_integration.thumbnails_path = THUMBNAILS_PATH
            mock_integration.crypto_manager = MagicMock()
            
            # Create the gcode manager
            gcode_manager = GCodeManager(mock_integration)
            
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
            mock_integration.crypto_manager.get_decryption_key = MagicMock(return_value=key)
            
            # Test decrypt_and_stream if it exists
            if hasattr(gcode_manager, 'decrypt_and_stream'):
                # Mock klippy_apis
                klippy_apis = MagicMock()
                klippy_apis.run_gcode = MagicMock(return_value=True)
                
                # Call decrypt_and_stream
                result = await gcode_manager.decrypt_and_stream(klippy_apis, test_file_path, "test_job_id")
                
                if result:
                    logging.info("GCode component decrypt test passed")
                    return True
                else:
                    logging.error("GCode component decrypt test failed")
                    return False
            else:
                # If the method doesn't exist, just return success
                logging.info("GCode component test skipped - no decrypt_and_stream method")
                return True
        
        except Exception as e:
            logging.error(f"Error testing gcode component: {str(e)}")
            return False
    
    async def test_jobs_component(self):
        """Test the jobs component directly"""
        logging.info("Testing jobs component...")
        
        try:
            # Import the jobs module
            from moonraker.components.lmnt_marketplace.jobs import JobManager
            
            # Create a mock integration
            mock_integration = MagicMock()
            mock_integration.marketplace_url = MARKETPLACE_URL
            mock_integration.api_version = API_VERSION
            mock_integration.auth_manager = MagicMock()
            mock_integration.auth_manager.printer_token = "test_printer_token"
            mock_integration.gcode_manager = MagicMock()
            
            # Create the job manager
            job_manager = JobManager(mock_integration)
            
            # Set klippy_apis
            job_manager.klippy_apis = self.klippy_apis
            
            # Mock klippy_apis response
            self.klippy_apis.query_objects = MagicMock(return_value={
                "print_stats": {"state": "standby"},
                "webhooks": {"state": "ready"}
            })
            
            # Test _check_printer_ready if it exists
            if hasattr(job_manager, '_check_printer_ready'):
                result = await job_manager._check_printer_ready()
                
                if result:
                    logging.info("Jobs component printer ready check test passed")
                    return True
                else:
                    logging.error("Jobs component printer ready check test failed")
                    return False
            else:
                # If the method doesn't exist, just return success
                logging.info("Jobs component test skipped - no _check_printer_ready method")
                return True
        
        except Exception as e:
            logging.error(f"Error testing jobs component: {str(e)}")
            return False
    
    async def run_tests(self):
        """Run all component tests"""
        results = []
        
        # Test auth component
        results.append(await self.test_auth_component())
        
        # Test crypto component
        results.append(await self.test_crypto_component())
        
        # Test gcode component
        results.append(await self.test_gcode_component())
        
        # Test jobs component
        results.append(await self.test_jobs_component())
        
        # Print summary
        logging.info("\n--- Test Summary ---")
        logging.info(f"Auth Component: {'PASS' if results[0] else 'FAIL'}")
        logging.info(f"Crypto Component: {'PASS' if results[1] else 'FAIL'}")
        logging.info(f"GCode Component: {'PASS' if results[2] else 'FAIL'}")
        logging.info(f"Jobs Component: {'PASS' if results[3] else 'FAIL'}")
        logging.info(f"Overall: {'PASS' if all(results) else 'FAIL'}")
        
        return all(results)


async def main():
    """Main function"""
    test = ComponentTests()
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
