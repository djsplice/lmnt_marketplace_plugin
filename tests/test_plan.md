# LMNT Marketplace Integration Test Plan

## Overview

This document outlines the testing strategy for the modular LMNT Marketplace integration with Klipper/Moonraker. The integration consists of several modular components that handle different aspects of the marketplace functionality:

1. **Auth Manager** - Handles authentication, token management, and printer registration
2. **Crypto Manager** - Manages encryption keys and secure decryption of GCode files
3. **GCode Manager** - Handles GCode file processing, metadata extraction, and streaming
4. **Job Manager** - Manages print jobs, status updates, and job control

## Test Levels

### 1. Unit Tests

Unit tests focus on testing individual functions and methods within each module to ensure they work correctly in isolation.

#### Auth Manager Tests
- Test user login functionality
- Test printer registration
- Test token refresh
- Test token validation
- Test token storage and retrieval

#### Crypto Manager Tests
- Test key generation
- Test key storage and retrieval
- Test decryption of encrypted data
- Test secure handling of keys

#### GCode Manager Tests
- Test GCode metadata extraction
- Test thumbnail extraction
- Test GCode streaming
- Test memory-only decryption

#### Job Manager Tests
- Test job status updates
- Test job progress tracking
- Test job control (start, pause, resume, cancel)
- Test printer readiness checks

### 2. Integration Tests

Integration tests focus on testing the interaction between different modules to ensure they work together correctly.

#### Auth + Crypto Integration
- Test retrieving decryption keys using authentication tokens
- Test secure key storage with authenticated access

#### GCode + Crypto Integration
- Test decrypting GCode files using keys from the Crypto Manager
- Test secure streaming of decrypted GCode

#### Job + GCode Integration
- Test starting a print job with encrypted GCode
- Test job progress updates during GCode streaming

#### Full Integration Test
- Test complete workflow from authentication to job completion
- Test error handling and recovery

### 3. System Tests

System tests focus on testing the entire system as a whole, including integration with Klipper/Moonraker.

#### Marketplace API Integration
- Test communication with Marketplace API
- Test handling of API responses and errors

#### Custodial Wallet Service Integration
- Test communication with CWS API
- Test secure key retrieval

#### Klipper/Moonraker Integration
- Test event handling
- Test GCode streaming to Klipper
- Test job status updates from Klipper

### 4. Security Tests

Security tests focus on ensuring that the integration handles sensitive data securely.

#### Key Management
- Test that keys are never written to disk in plaintext
- Test secure storage of encrypted keys

#### GCode Security
- Test that decrypted GCode is never written to disk
- Test memory-only processing of decrypted GCode

#### Token Security
- Test secure storage of authentication tokens
- Test token refresh and expiration handling

## Test Implementation

### Test Scripts

1. **test_components.py** - Tests individual components in isolation
2. **test_integration.py** - Tests interaction between components
3. **test_full_workflow.py** - Tests complete workflow from authentication to job completion
4. **test_security.py** - Tests security aspects of the integration
5. **advanced_component_tests.py** - Comprehensive tests for GCode and Job managers using dynamic extensions

### Extension System

To facilitate testing without modifying core components, we've implemented a dynamic extension system:

#### Extension Modules

1. **test_extensions.py** - Core module for applying extensions to manager instances
2. **gcode_extensions.py** - Extensions for the GCodeManager
3. **jobs_extensions.py** - Extensions for the JobManager

#### GCode Manager Extensions

- **extract_metadata** - Extracts metadata from encrypted GCode files
- **extract_thumbnails** - Extracts and saves thumbnails from encrypted GCode files
- **decrypt_and_stream** - Decrypts GCode in memory and streams it line-by-line to Klipper

#### Job Manager Extensions

- **add_job** - Adds a job to the queue
- **get_next_job** - Gets the next job from the queue
- **remove_job** - Removes a job from the queue
- **update_job_status** - Updates the status of a job
- **get_job_status** - Gets the current status of a job
- **process_job** - Processes a job through its lifecycle

### Mock Objects

To facilitate testing without requiring a full Klipper/Moonraker instance, we'll use mock objects for:

- Klipper APIs
- HTTP client
- Marketplace API
- Custodial Wallet Service API

### Test Data

We'll use the following test data:

- Sample encrypted GCode files
- Test authentication tokens
- Test encryption keys

## Test Execution

### Prerequisites

- Python 3.7+
- Required Python packages: aiohttp, cryptography, pyjwt
- Virtual environment for testing

### Running Tests

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install aiohttp cryptography pyjwt

# Run component tests
python test_components.py

# Run integration tests
python test_integration.py

# Run full workflow test
python test_full_workflow.py

# Run security tests
python test_security.py
```

## Test Reporting

Test results will be logged to the console and can be captured for further analysis. Each test will report:

- Test name
- Test status (PASS/FAIL)
- Error messages (if any)
- Summary of test results

## Continuous Integration

For future development, we recommend setting up a CI pipeline that:

1. Runs all tests on each commit
2. Reports test results
3. Blocks merging if tests fail

## Next Steps

1. Implement the test scripts outlined in this plan
2. Run tests and fix any issues found
3. Set up continuous integration for ongoing testing
4. Document test results and any issues found
