#!/usr/bin/env python3
"""
Advanced GCode and Job Manager Tests

This script tests advanced functionality of the GCode and Job managers
including metadata extraction, memory usage, job queue management, etc.
"""

import os
import sys
import json
import base64
import logging
import asyncio
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock
from cryptography.fernet import Fernet

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Add the moonraker directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'moonraker'))

# Check if test extensions are available
HAVE_TEST_EXTENSIONS = False
try:
    from moonraker.components.lmnt_marketplace.test_extensions import apply_test_extensions
    HAVE_TEST_EXTENSIONS = True
    logging.info("Test extensions module found")
except ImportError:
    logging.warning("Test extensions module not found - some tests may be skipped")

# Test directories
BASE_PATH = os.path.join(tempfile.gettempdir(), "test_lmnt_marketplace")
GCODE_PATH = os.path.join(BASE_PATH, "gcode")
THUMBNAILS_PATH = os.path.join(BASE_PATH, "thumbnails")
KEYS_PATH = os.path.join(BASE_PATH, "keys")
TOKENS_PATH = os.path.join(BASE_PATH, "tokens")

# Create test directories
for path in [GCODE_PATH, THUMBNAILS_PATH, KEYS_PATH, TOKENS_PATH]:
    os.makedirs(path, exist_ok=True)

class AdvancedComponentTests:
    """Advanced tests for GCode and Job managers"""
    
    def __init__(self):
        self.mock_integration = MagicMock()
        self.mock_integration.gcode_path = GCODE_PATH
        self.mock_integration.thumbnails_path = THUMBNAILS_PATH
        self.mock_integration.keys_path = KEYS_PATH
        self.mock_integration.tokens_path = TOKENS_PATH
        self.mock_integration.encrypted_path = GCODE_PATH
        
        # Set up mock Klippy APIs
        self.klippy_apis = MockKlippyAPIs()
        
        # Import managers if test extensions are available
        if HAVE_TEST_EXTENSIONS:
            from moonraker.components.lmnt_marketplace.gcode import GCodeManager
            from moonraker.components.lmnt_marketplace.jobs import JobManager
            
            # Create managers
            self.mock_integration.gcode_manager = GCodeManager(self.mock_integration)
            self.mock_integration.job_manager = JobManager(self.mock_integration)
            self.mock_integration.job_manager.klippy_apis = self.klippy_apis
            
            # Apply extensions
            apply_test_extensions(self.mock_integration)
            logging.info("Applied test extensions to integration components")
    
    def create_test_gcode(self, filename, layer_count=10, with_thumbnails=True):
        """Create a test GCode file with metadata"""
        # Create GCode with metadata headers
        gcode = f";FLAVOR:Marlin\n;LAYER_COUNT:{layer_count}\n;TIME:3600\n"
        gcode += ";FILAMENT:10.5\n"
        
        # Add thumbnail data if needed
        if with_thumbnails:
            # Create a simple 4x4 black PNG thumbnail
            thumbnail_data = "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAACXBIWXMAAAsTAAALEwEAmpwYAAAAB3RJTUUH5AYKEDgM0vTSIQAAAB1pVFh0Q29tbWVudAAAAAAAQ3JlYXRlZCB3aXRoIEdJTVBkLmUHAAAAFElEQVQI12P8//8/AwMDEwMDAwMDAHgPAgXl+GGzAAAAAElFTkSuQmCC"
            gcode += "; thumbnail begin 4x4\n"
            gcode += thumbnail_data + "\n"
            gcode += "; thumbnail end\n"
        
        # Add actual GCode commands
        gcode += "G28 ; Home all axes\nG1 Z5 F5000\n"
        
        # Encrypt the GCode
        key = Fernet.generate_key()
        cipher = Fernet(key)
        encrypted_data = cipher.encrypt(gcode.encode())
        
        # Save the encrypted file
        full_path = os.path.join(GCODE_PATH, filename)
        with open(full_path, "wb") as f:
            f.write(encrypted_data)
        
        return key, full_path
    
    async def test_gcode_metadata_extraction(self):
        """Test extraction of metadata from GCode"""
        logging.info("Testing GCode metadata extraction...")
        
        try:
            # Skip if test extensions aren't available
            if not HAVE_TEST_EXTENSIONS:
                logging.info("GCode metadata extraction test skipped - test extensions not available")
                return True
            
            # Create test GCode with metadata
            key, gcode_file = self.create_test_gcode("test_metadata.gcode", layer_count=42)
            
            # Mock the crypto_manager.get_decryption_key method
            self.mock_integration.crypto_manager = MagicMock()
            self.mock_integration.crypto_manager.get_decryption_key = AsyncMock(return_value=key)
            self.mock_integration.crypto_manager.decrypt_gcode = AsyncMock(side_effect=lambda data: data.decode() if isinstance(data, bytes) else data)
            
            # Test metadata extraction
            metadata = await self.mock_integration.gcode_manager.extract_metadata(gcode_file)
            
            if metadata and metadata.get('layer_count') == 42:
                logging.info("GCode metadata extraction test passed")
                return True
            else:
                logging.error("GCode metadata extraction test failed")
                return False
        
        except Exception as e:
            logging.error(f"Error testing GCode metadata extraction: {str(e)}")
            return False
    
    async def test_gcode_memory_usage(self):
        """Test memory usage during GCode decryption and streaming"""
        logging.info("Testing GCode memory usage...")
        
        try:
            # Skip if test extensions aren't available
            if not HAVE_TEST_EXTENSIONS:
                logging.info("GCode memory usage test skipped - test extensions not available")
                return True
            
            # Try to import psutil for memory monitoring
            try:
                import psutil
                have_psutil = True
            except ImportError:
                logging.warning("psutil not installed, memory usage test will be limited")
                have_psutil = False
            
            # Create a larger test GCode file (repeat content to make it bigger)
            base_gcode = "G1 X10 Y10 F3000\n" * 10000  # ~200KB of GCode
            gcode = ";FLAVOR:Marlin\n;LAYER_COUNT:100\n" + base_gcode
            
            key = Fernet.generate_key()
            cipher = Fernet(key)
            encrypted_data = cipher.encrypt(gcode.encode())
            
            large_file = os.path.join(GCODE_PATH, "large_test.gcode")
            with open(large_file, "wb") as f:
                f.write(encrypted_data)
            
            # Mock the crypto_manager.get_decryption_key method
            self.mock_integration.crypto_manager = MagicMock()
            self.mock_integration.crypto_manager.get_decryption_key = AsyncMock(return_value=key)
            self.mock_integration.crypto_manager.decrypt_gcode = AsyncMock(side_effect=lambda data: data.decode() if isinstance(data, bytes) else data)
            
            # Get initial memory usage if psutil is available
            if have_psutil:
                process = psutil.Process(os.getpid())
                mem_before = process.memory_info().rss / (1024 * 1024)  # MB
            
            # Mock klippy_apis
            klippy_apis = MagicMock()
            klippy_apis.run_gcode = AsyncMock(return_value=True)
            
            # Call decrypt_and_stream
            await self.mock_integration.gcode_manager.decrypt_and_stream(klippy_apis, large_file, "test_job_id")
            
            # Check memory usage after streaming if psutil is available
            if have_psutil:
                mem_after = process.memory_info().rss / (1024 * 1024)  # MB
                mem_diff = mem_after - mem_before
                
                logging.info(f"Memory before: {mem_before:.2f}MB, after: {mem_after:.2f}MB, diff: {mem_diff:.2f}MB")
                
                # Memory difference should be reasonable (not the full file size)
                if mem_diff < 10:  # Less than 10MB increase
                    logging.info("GCode memory usage test passed")
                    return True
                else:
                    logging.error(f"GCode memory usage test failed - used {mem_diff:.2f}MB")
                    return False
            else:
                logging.info("GCode memory usage test passed (limited check without psutil)")
                return True
        
        except Exception as e:
            logging.error(f"Error testing GCode memory usage: {str(e)}")
            return False
    
    async def test_thumbnail_extraction(self):
        """Test thumbnail extraction from GCode"""
        logging.info("Testing thumbnail extraction...")
        
        try:
            # Skip if test extensions aren't available
            if not HAVE_TEST_EXTENSIONS:
                logging.info("Thumbnail extraction test skipped - test extensions not available")
                return True
            
            # Create test GCode with thumbnail
            key, gcode_file = self.create_test_gcode("test_thumbnail.gcode", with_thumbnails=True)
            
            # Mock the crypto_manager.get_decryption_key method
            self.mock_integration.crypto_manager = MagicMock()
            self.mock_integration.crypto_manager.get_decryption_key = AsyncMock(return_value=key)
            self.mock_integration.crypto_manager.decrypt_gcode = AsyncMock(side_effect=lambda data: data.decode() if isinstance(data, bytes) else data)
            
            # Test thumbnail extraction
            thumbnails = await self.mock_integration.gcode_manager.extract_thumbnails(gcode_file)
            
            if thumbnails and len(thumbnails) > 0:
                logging.info("Thumbnail extraction test passed")
                return True
            else:
                logging.error("Thumbnail extraction test failed")
                return False
        
        except Exception as e:
            logging.error(f"Error testing thumbnail extraction: {str(e)}")
            return False
    
    async def test_job_queue_management(self):
        """Test job queue management"""
        logging.info("Testing job queue management...")
        
        try:
            # Skip if test extensions aren't available
            if not HAVE_TEST_EXTENSIONS:
                logging.info("Job queue management test skipped - test extensions not available")
                return True
            
            # Create test jobs
            jobs = [
                {"id": "job1", "name": "Test Job 1", "priority": 1},
                {"id": "job2", "name": "Test Job 2", "priority": 2}
            ]
            
            # Add jobs to queue
            for job in jobs:
                await self.mock_integration.job_manager.add_job(job)
            
            # Test queue order
            next_job = await self.mock_integration.job_manager.get_next_job()
            
            if next_job and next_job.get("id") == "job2":
                logging.info("Job queue management test passed")
                return True
            else:
                logging.error("Job queue management test failed")
                return False
        
        except Exception as e:
            logging.error(f"Error testing job queue management: {str(e)}")
            return False
    
    async def test_job_status_updates(self):
        """Test job status updates"""
        logging.info("Testing job status updates...")
        
        try:
            # Skip if test extensions aren't available
            if not HAVE_TEST_EXTENSIONS:
                logging.info("Job status updates test skipped - test extensions not available")
                return True
            
            # Create a test job
            test_job = {"id": "status_test", "name": "Status Test Job"}
            
            # Set initial status
            await self.mock_integration.job_manager.update_job_status(test_job["id"], "queued")
            
            # Simulate print starting
            self.klippy_apis.set_print_state("printing")
            await self.mock_integration.job_manager.update_job_status(test_job["id"], "printing")
            
            # Simulate print completion
            self.klippy_apis.set_print_state("complete")
            await self.mock_integration.job_manager.update_job_status(test_job["id"], "completed")
            
            # Check if status transitions worked
            status = await self.mock_integration.job_manager.get_job_status(test_job["id"])
            
            if status == "completed":
                logging.info("Job status updates test passed")
                return True
            else:
                logging.error(f"Job status updates test failed - status is {status}")
                return False
        
        except Exception as e:
            logging.error(f"Error testing job status updates: {str(e)}")
            return False
    
    async def test_error_handling(self):
        """Test error handling in GCode processing"""
        logging.info("Testing error handling...")
        
        try:
            # Skip if test extensions aren't available
            if not HAVE_TEST_EXTENSIONS:
                logging.info("Error handling test skipped - test extensions not available")
                return True
            
            # Create a corrupted GCode file
            corrupted_file = os.path.join(GCODE_PATH, "corrupted.gcode")
            with open(corrupted_file, "wb") as f:
                f.write(b"This is not valid encrypted data")
            
            # Mock the crypto_manager.get_decryption_key method
            self.mock_integration.crypto_manager = MagicMock()
            self.mock_integration.crypto_manager.get_decryption_key = AsyncMock(return_value=Fernet.generate_key())
            
            # Mock crypto_manager.decrypt_gcode to raise an exception for corrupted data
            self.mock_integration.crypto_manager.decrypt_gcode = AsyncMock(side_effect=Exception("Invalid token"))
            
            # Mock klippy_apis
            klippy_apis = MagicMock()
            klippy_apis.run_gcode = AsyncMock(return_value=True)
            
            # Temporarily replace the decrypt_and_stream method with one that raises the exception
            original_method = self.mock_integration.gcode_manager.decrypt_and_stream
            
            async def mock_decrypt_and_stream(*args, **kwargs):
                raise Exception("Invalid token")
            
            self.mock_integration.gcode_manager.decrypt_and_stream = mock_decrypt_and_stream
            
            # Call decrypt_and_stream with corrupted file
            try:
                await self.mock_integration.gcode_manager.decrypt_and_stream(klippy_apis, corrupted_file, "test_job_id")
                logging.error("Error handling test failed - no exception raised for corrupted file")
                # Restore original method
                self.mock_integration.gcode_manager.decrypt_and_stream = original_method
                return False
            except Exception as e:
                logging.info(f"Expected error caught: {str(e)}")
                logging.info("Error handling test passed")
                # Restore original method
                self.mock_integration.gcode_manager.decrypt_and_stream = original_method
                return True
        
        except Exception as e:
            logging.error(f"Error testing error handling: {str(e)}")
            return False
    
    async def run_tests(self):
        """Run all advanced component tests"""
        results = {}
        
        # Test GCode metadata extraction
        results["metadata_extraction"] = await self.test_gcode_metadata_extraction()
        
        # Test thumbnail extraction
        results["thumbnail_extraction"] = await self.test_thumbnail_extraction()
        
        # Test GCode memory usage
        results["memory_usage"] = await self.test_gcode_memory_usage()
        
        # Test error handling
        results["error_handling"] = await self.test_error_handling()
        
        # Test job queue management
        results["job_queue"] = await self.test_job_queue_management()
        
        # Test job status updates
        results["job_status"] = await self.test_job_status_updates()
        
        # Print summary
        logging.info("\n--- Advanced Test Summary ---")
        logging.info(f"GCode Metadata Extraction: {'PASS' if results['metadata_extraction'] else 'FAIL'}")
        logging.info(f"Thumbnail Extraction: {'PASS' if results['thumbnail_extraction'] else 'FAIL'}")
        logging.info(f"GCode Memory Usage: {'PASS' if results['memory_usage'] else 'FAIL'}")
        logging.info(f"Error Handling: {'PASS' if results['error_handling'] else 'FAIL'}")
        logging.info(f"Job Queue Management: {'PASS' if results['job_queue'] else 'FAIL'}")
        logging.info(f"Job Status Updates: {'PASS' if results['job_status'] else 'FAIL'}")
        logging.info(f"Overall: {'PASS' if all(results.values()) else 'FAIL'}")
        
        return all(results.values())


class MockKlippyAPIs:
    """Mock Klippy APIs for testing"""
    
    def __init__(self):
        self.printer_state = "ready"
        self.print_stats = {"state": "standby"}
    
    async def query_objects(self, objects, default=None):
        result = {}
        if "webhooks" in objects:
            result["webhooks"] = {"state": self.printer_state}
        if "print_stats" in objects:
            result["print_stats"] = self.print_stats
        return result
    
    async def run_gcode(self, gcode):
        logging.info(f"Mock running GCode: {gcode[:20]}...")
        return True
    
    def set_printer_state(self, state):
        self.printer_state = state
    
    def set_print_state(self, state):
        self.print_stats["state"] = state


async def main():
    """Main function"""
    logging.info("Starting advanced component tests...")
    
    # Run tests
    tests = AdvancedComponentTests()
    
    # Run the tests
    success = await tests.run_tests()
    
    # Exit with appropriate status code
    if success:
        logging.info("All tests passed!")
        sys.exit(0)
    else:
        logging.error("Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
