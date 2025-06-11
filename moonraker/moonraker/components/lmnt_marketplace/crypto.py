"""
LMNT Marketplace Crypto Module

Handles encryption and decryption operations for LMNT Marketplace integration:
- PSEK (Printer-Specific Encryption Key) management
- Secure decryption of encrypted GCode files
- Integration with Custodial Wallet Service (CWS) for key management
"""

import os
import json
import logging
import asyncio
import aiohttp
import binascii
import base64
import time
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

class CryptoManager:
    """
    Manages cryptographic operations for LMNT Marketplace
    
    Handles secure key management, decryption of encrypted GCode files,
    and integration with the Custodial Wallet Service (CWS).
    """
    
    def __init__(self, integration):
        """Initialize the Crypto Manager"""
        self.integration = integration
        self.encrypted_psek = None
        self.decryption_key = None
    
    async def initialize(self, klippy_apis, http_client):
        """Initialize with Klippy APIs and HTTP client"""
        self.klippy_apis = klippy_apis
        self.http_client = http_client
        
        # Load encrypted PSEK if available
        self._load_encrypted_psek()
    
    def _load_encrypted_psek(self):
        """Load encrypted PSEK from secure storage"""
        psek_file = os.path.join(self.integration.keys_path, "encrypted_psek.json")
        if os.path.exists(psek_file):
            try:
                with open(psek_file, 'r') as f:
                    data = json.load(f)
                    self.encrypted_psek = data.get('encrypted_psek')
                    if self.encrypted_psek:
                        logging.info("Loaded encrypted PSEK from storage")
                        return True
            except (json.JSONDecodeError, IOError) as e:
                logging.error(f"Error loading encrypted PSEK: {str(e)}")
        
        logging.info("No encrypted PSEK found")
        return False
    
    def _save_encrypted_psek(self, encrypted_psek):
        """
        Save the encrypted PSEK received from the server
        
        According to ADR-003, the kek_id field in the printer registration response
        actually contains the encrypted PSEK (encrypted by the Master Printer KEK).
        """
        if not encrypted_psek:
            logging.error("Cannot save empty encrypted PSEK")
            return False
        
        psek_file = os.path.join(self.integration.keys_path, "encrypted_psek.json")
        try:
            with open(psek_file, 'w') as f:
                json.dump({
                    'encrypted_psek': encrypted_psek
                }, f)
            
            # Update current encrypted PSEK
            self.encrypted_psek = encrypted_psek
            
            logging.info("Saved encrypted PSEK")
            return True
        except IOError as e:
            logging.error(f"Error saving encrypted PSEK: {str(e)}")
            return False
    
    async def _decrypt_data_via_cws(self, data_to_decrypt_b64):
        """Helper to call CWS /ops/decrypt-data endpoint."""
        if not self.integration.auth_manager.printer_token:
            logging.error("CWS Decryption: No printer token available.")
            return None

        decrypt_url = f"{self.integration.cws_url}/ops/decrypt-data"
        headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
        payload = {"dataToDecrypt": data_to_decrypt_b64}

        logging.info(f"CWS Decryption: Sending request to {decrypt_url} with data (first 20): {data_to_decrypt_b64[:20]}...")
        try:
            async with self.http_client.post(decrypt_url, headers=headers, json=payload) as response:
                logging.info(f"CWS Decryption: Response status {response.status}")
                if response.status == 200:
                    resp_json = await response.json()
                    decrypted_b64 = resp_json.get('decryptedData')
                    if decrypted_b64:
                        try:
                            return base64.b64decode(decrypted_b64)
                        except binascii.Error as e:
                            logging.error(f"CWS Decryption: Error decoding base64 response: {e}")
                            return None
                    else:
                        logging.error("CWS Decryption: 'decryptedData' missing in response.")
                        return None
                else:
                    error_text = await response.text()
                    logging.error(f"CWS Decryption: Failed. Status: {response.status}, Body: {error_text}")
                    return None
        except Exception as e:
            logging.error(f"CWS Decryption: Request exception: {e}")
            return None

    async def decrypt_dek(self, encrypted_gcode_dek_hex, kek_id):
        """
        Decrypts the G-code DEK.
        Step 1: Use kek_id (encrypted PSEK) to get plaintext PSEK from CWS.
        Step 2: Use plaintext PSEK to locally decrypt encrypted_gcode_dek_hex.

        Args:
            encrypted_gcode_dek_hex (str): Hex string of IV + Slicer-encrypted G-code DEK.
            kek_id (str): The printer_kek_id (base64 CWS-encrypted PSEK).
            
        Returns:
            bytes: The plaintext G-code DEK as bytes if successful, else None.
        """
        logging.info(f"CryptoManager: decrypt_dek called. Encrypted GDEK (hex, first 64): {encrypted_gcode_dek_hex[:64] if encrypted_gcode_dek_hex else 'None'}, KEK ID (first 20): {kek_id[:20] if kek_id else 'None'}")
        
        if not kek_id:
            logging.error("CryptoManager: KEK ID is missing. Cannot proceed with PSEK decryption.")
            return None
        if not encrypted_gcode_dek_hex:
            logging.error("CryptoManager: Encrypted G-code DEK hex is missing. Cannot proceed with DEK decryption.")
            return None

        plaintext_psek_bytes = None # Initialize for use in exception logging
        try:
            # Step 1: Get PSEK from CWS
            logging.info(f"CryptoManager: Attempting to decrypt KEK ID via CWS (first 20 chars): {kek_id[:20]}...")
            plaintext_psek_bytes = await self._decrypt_data_via_cws(kek_id)
            
            if plaintext_psek_bytes:
                logging.info(f"CryptoManager: Successfully obtained plaintext PSEK from CWS (hex): {plaintext_psek_bytes.hex()}")
                logging.info(f"CryptoManager: Plaintext PSEK length: {len(plaintext_psek_bytes)} bytes.")
            else:
                logging.error("CryptoManager: _decrypt_data_via_cws returned None or empty for PSEK.")
                return None

            if len(plaintext_psek_bytes) != 32: # AES-256 key must be 32 bytes
                logging.error(f"CryptoManager: Invalid PSEK length: {len(plaintext_psek_bytes)} bytes. Expected 32 bytes for AES-256.")
                return None

            logging.info(f"CryptoManager: Encrypted G-code DEK (hex, full): {encrypted_gcode_dek_hex}")
            
            # Step 2: Locally decrypt the G-code DEK using the plaintext PSEK
            if len(encrypted_gcode_dek_hex) < 32:
                logging.error(f"CryptoManager: Encrypted G-code DEK hex is too short to contain an IV: {len(encrypted_gcode_dek_hex)} chars.")
                return None

            iv_from_dek_hex = encrypted_gcode_dek_hex[:32]
            encrypted_dek_actual_hex = encrypted_gcode_dek_hex[32:]

            logging.info(f"CryptoManager: IV for DEK decryption (hex): {iv_from_dek_hex}")
            logging.info(f"CryptoManager: Actual Encrypted DEK for decryption (hex): {encrypted_dek_actual_hex}")

            if not iv_from_dek_hex or not encrypted_dek_actual_hex:
                logging.error("CryptoManager: IV or Encrypted DEK hex is empty after splitting.")
                return None

            iv_from_dek_bytes = bytes.fromhex(iv_from_dek_hex)
            encrypted_dek_actual_bytes = bytes.fromhex(encrypted_dek_actual_hex)
            
            logging.info(f"CryptoManager: IV for DEK decryption (bytes length): {len(iv_from_dek_bytes)}")
            logging.info(f"CryptoManager: Actual Encrypted DEK for decryption (bytes length): {len(encrypted_dek_actual_bytes)}")

            if len(iv_from_dek_bytes) != 16: # AES IV is 16 bytes for AES-128/192/256
                logging.error(f"CryptoManager: Invalid IV length after hex conversion: {len(iv_from_dek_bytes)} bytes. Expected 16 bytes.")
                return None

            cipher = Cipher(
                algorithms.AES(plaintext_psek_bytes),
                modes.CBC(iv_from_dek_bytes),
                backend=default_backend()
            )
            logging.info("CryptoManager: AES cipher initialized for DEK decryption. Attempting decryption...")
            decryptor = cipher.decryptor()
            decrypted_dek_padded = decryptor.update(encrypted_dek_actual_bytes) + decryptor.finalize()
            logging.info(f"CryptoManager: AES DEK decryption complete. Padded DEK (hex, first 64 chars): {decrypted_dek_padded.hex()[:64]}")
            
            unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
            logging.info("CryptoManager: PKCS7 unpadder initialized for DEK. Attempting unpadding...")
            decrypted_dek_unpadded = unpadder.update(decrypted_dek_padded) + unpadder.finalize()
            logging.info(f"CryptoManager: Successfully decrypted and unpadded G-code DEK. Plaintext DEK (hex): {decrypted_dek_unpadded.hex()}")
            logging.info(f"CryptoManager: Plaintext G-code DEK length: {len(decrypted_dek_unpadded)} bytes.")
            
            return decrypted_dek_unpadded

        except binascii.Error as e:
            logging.error(f"CryptoManager: Hex decoding error during G-code DEK decryption: {e}. PSEK used (hex): {plaintext_psek_bytes.hex() if plaintext_psek_bytes else 'Not available'}")
            import traceback
            logging.error(traceback.format_exc())
            return None
        except ValueError as ve: 
            logging.error(f"CryptoManager: ValueError during G-code DEK decryption (often padding or key error): {ve}. PSEK used (hex): {plaintext_psek_bytes.hex() if plaintext_psek_bytes else 'Not available'}")
            import traceback
            logging.error(traceback.format_exc())
            return None
        except Exception as e:
            logging.error(f"CryptoManager: General failure during G-code DEK decryption: {e}. PSEK used (hex): {plaintext_psek_bytes.hex() if plaintext_psek_bytes else 'Not available'}")
            import traceback
            logging.error(traceback.format_exc())
            return None

        
    async def get_decryption_key(self):
        """
        Get the decryption key for GCode files by decrypting the PSEK via CWS
        
        Returns:
            bytes: Decryption key if successful
            None: If decryption key could not be obtained
        """
        # Return cached key if available
        if self.decryption_key:
            return self.decryption_key
        
        # Check if we have a printer token and encrypted PSEK
        if not self.integration.auth_manager.printer_token:
            logging.error("Cannot get decryption key: No printer token available")
            return None
        
        if not self.encrypted_psek:
            logging.error("Cannot get decryption key: No encrypted PSEK available")
            return None
        
        # Use CWS to decrypt the PSEK
        decrypt_url = f"{self.integration.cws_url}/api/{self.integration.api_version}/decrypt-psek"
        
        try:
            headers = {"Authorization": f"Bearer {self.integration.auth_manager.printer_token}"}
            payload = {"encrypted_psek": self.encrypted_psek}
            
            async with self.http_client.post(decrypt_url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    decrypted_psek = data.get('decrypted_psek')
                    
                    if decrypted_psek:
                        try:
                            # Decode base64 PSEK to bytes
                            key_bytes = base64.b64decode(decrypted_psek)
                            
                            # Store in memory only, never on disk
                            self.decryption_key = key_bytes
                            
                            logging.info("Successfully obtained decryption key from CWS")
                            return self.decryption_key
                        except binascii.Error as e:
                            logging.error(f"Error decoding decrypted PSEK: {str(e)}")
                else:
                    error_text = await response.text()
                    logging.error(f"PSEK decryption failed with status {response.status}: {error_text}")
        except Exception as e:
            logging.error(f"Error getting decryption key: {str(e)}")
        
        return None
    
    async def decrypt_gcode(self, encrypted_data, job_id=None, dek=None, iv=None):
        """
        Decrypt GCode data using DEK or PSEK
        
        Args:
            encrypted_data (bytes): Encrypted GCode data
            job_id (str, optional): Job ID for logging purposes
            dek (str, optional): Data Encryption Key in base64 format
            iv (str, optional): Initialization Vector in hex format
            
        Returns:
            str: Decrypted GCode as string if successful
            None: If decryption failed
        """
        job_info = f" for job {job_id}" if job_id else ""
        
        try:
            # Check if both DEK and IV are provided for custom decryption
            if dek and iv:
                logging.info(f"Using provided DEK and IV to decrypt GCode{job_info}")
                logging.info(f"DEK length: {len(dek) if dek else 'None'}, IV length: {len(iv) if iv else 'None'}")
                try:
                    # Convert hex IV to bytes
                    iv_bytes = bytes.fromhex(iv) if isinstance(iv, str) else iv
                    logging.info(f"IV bytes length: {len(iv_bytes)}, IV bytes: {iv_bytes[:8]}...")
                    
                    # Check if DEK is in hex format (not base64)
                    is_hex_dek = all(c in '0123456789abcdefABCDEF' for c in dek) if isinstance(dek, str) else False
                    
                    # Convert DEK to bytes based on format
                    if is_hex_dek:
                        logging.info(f"DEK appears to be in hex format, converting from hex")
                        dek_bytes = bytes.fromhex(dek) if isinstance(dek, str) else dek
                    else:
                        # Try base64 decode as fallback
                        logging.info(f"DEK appears to be in base64 format, converting from base64")
                        dek_bytes = base64.b64decode(dek) if isinstance(dek, str) else dek
                    
                    logging.info(f"DEK bytes length: {len(dek_bytes)}, DEK bytes (first 8): {dek_bytes[:8]}...")
                    
                    # Use AES-CBC for decryption with the provided IV
                    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                    from cryptography.hazmat.backends import default_backend
                    
                    # Create AES cipher with the DEK and IV
                    aes_key = dek_bytes[:32]
                    logging.info(f"AES key length: {len(aes_key)}, using AES-CBC mode")
                    cipher = Cipher(
                        algorithms.AES(aes_key),  # Use first 32 bytes as AES key
                        modes.CBC(iv_bytes),  # Use the provided IV
                        backend=default_backend()
                    )
                    
                    # Create decryptor
                    decryptor = cipher.decryptor()
                    logging.info(f"Decryptor created, encrypted data length: {len(encrypted_data)}")
                    
                    # Decrypt the data
                    decrypted_data = decryptor.update(encrypted_data) + decryptor.finalize()
                    logging.info(f"Decryption completed, decrypted data length: {len(decrypted_data)}")
                    
                    # Remove PKCS7 padding if needed
                    from cryptography.hazmat.primitives.padding import PKCS7
                    unpadder = PKCS7(128).unpadder()
                    try:
                        padded_data = decrypted_data
                        decrypted_data = unpadder.update(padded_data) + unpadder.finalize()
                        logging.info(f"Unpadding successful, final data length: {len(decrypted_data)}")
                    except Exception as e:
                        logging.warning(f"Failed to unpad data, may not be padded: {str(e)}")
                        # Continue with the data as is
                        
                except Exception as e:
                    import traceback
                    logging.error(f"Error using custom decryption with DEK and IV{job_info}: {str(e)}")
                    logging.error(f"Decryption error traceback: {traceback.format_exc()}")
                    logging.info(f"Falling back to Fernet decryption")
                    # Fall back to Fernet decryption
                    try:
                        # Check if DEK is in hex format
                        is_hex_dek = all(c in '0123456789abcdefABCDEF' for c in dek) if isinstance(dek, str) else False
                        
                        # Format DEK for Fernet based on format
                        if is_hex_dek:
                            logging.info(f"Fernet fallback: DEK appears to be in hex format")
                            # Convert hex to bytes then to Fernet key
                            dek_bytes = bytes.fromhex(dek) if isinstance(dek, str) else dek
                            key = base64.urlsafe_b64encode(dek_bytes[:32])
                        else:
                            logging.info(f"Fernet fallback: DEK appears to be in base64 format")
                            # Convert base64 to Fernet key
                            key = base64.urlsafe_b64encode(base64.b64decode(dek)[:32])
                            
                        logging.info(f"Fernet key length: {len(key)}, key: {key[:16]}...")
                        cipher = Fernet(key)
                        decrypted_data = cipher.decrypt(encrypted_data)
                        logging.info(f"Fernet decryption successful, data length: {len(decrypted_data)}")
                    except Exception as inner_e:
                        logging.error(f"Fernet fallback also failed{job_info}: {str(inner_e)}")
                        logging.error(f"Fernet error traceback: {traceback.format_exc()}")
                        # Fall back to PSEK
                        logging.info(f"Falling back to PSEK decryption")
                        key = await self.get_decryption_key()
                        if not key:
                            logging.error(f"Failed to get PSEK{job_info}")
                            return None
                        logging.info(f"PSEK retrieved, length: {len(key)}, attempting Fernet decryption")
                        cipher = Fernet(key)
                        try:
                            decrypted_data = cipher.decrypt(encrypted_data)
                            logging.info(f"PSEK decryption successful, data length: {len(decrypted_data)}")
                        except Exception as psek_e:
                            logging.error(f"PSEK decryption failed{job_info}: {str(psek_e)}")
                            logging.error(f"PSEK error traceback: {traceback.format_exc()}")
                            return None
            # If only DEK is provided (no IV), use Fernet
            elif dek:
                logging.info(f"Using provided DEK with Fernet to decrypt GCode{job_info}")
                logging.info(f"DEK length: {len(dek) if dek else 'None'}")
                try:
                    # Check if DEK is in hex format
                    is_hex_dek = all(c in '0123456789abcdefABCDEF' for c in dek) if isinstance(dek, str) else False
                    
                    if is_hex_dek:
                        logging.info(f"DEK appears to be in hex format, converting from hex")
                        # Convert hex to bytes then to Fernet key
                        dek_bytes = bytes.fromhex(dek) if isinstance(dek, str) else dek
                        key = base64.urlsafe_b64encode(dek_bytes[:32])
                    elif not dek.startswith(b'_') and len(dek) >= 32:
                        logging.info(f"DEK appears to be in base64 format, converting to Fernet key")
                        # Convert base64 DEK to Fernet key format if needed
                        key = base64.urlsafe_b64encode(base64.b64decode(dek)[:32])
                    else:
                        # Assume it's already in correct format
                        logging.info(f"DEK appears to be in Fernet format already")
                        key = dek.encode() if isinstance(dek, str) else dek
                        
                    logging.info(f"Fernet key length: {len(key)}, key: {key[:16]}...")
                    
                    # Create Fernet cipher with the key
                    cipher = Fernet(key)
                    
                    # Decrypt the data
                    decrypted_data = cipher.decrypt(encrypted_data)
                except Exception as e:
                    logging.error(f"Error formatting DEK{job_info}: {str(e)}")
                    # Fall back to PSEK
                    key = await self.get_decryption_key()
                    if not key:
                        logging.error(f"Failed to get PSEK{job_info}")
                        return None
                    cipher = Fernet(key)
                    decrypted_data = cipher.decrypt(encrypted_data)
            else:
                # Get decryption key (PSEK)
                key = await self.get_decryption_key()
                
                if not key:
                    logging.error(f"Failed to get decryption key{job_info}")
                    return None
                
                # Create Fernet cipher with the key
                cipher = Fernet(key)
                
                # Decrypt the data
                decrypted_data = cipher.decrypt(encrypted_data)
            
            # Convert to string
            decrypted_gcode = decrypted_data.decode('utf-8')
            
            logging.info(f"Successfully decrypted GCode{job_info}")
            return decrypted_gcode
        
        except InvalidToken:
            logging.error(f"Invalid token or corrupted data when decrypting GCode{job_info}")
        except Exception as e:
            logging.error(f"Error decrypting GCode{job_info}: {str(e)}")
        
        return None
    
    def clear_decryption_key(self):
        """
        Clear the in-memory decryption key
        
        This should be called after decryption operations are complete
        to minimize the time the key is held in memory.
        """
        if self.decryption_key:
            # Securely clear the key from memory
            self.decryption_key = None
            logging.debug("Cleared decryption key from memory")
    
    def generate_dummy_key(self):
        """
        Generate a dummy key for testing purposes
        
        This should only be used in debug mode and never in production.
        """
        if not self.integration.debug:
            logging.error("Cannot generate dummy key in non-debug mode")
            return None
        
        try:
            # Generate a new Fernet key
            key = Fernet.generate_key()
            logging.warning("Generated dummy key for testing - NOT SECURE FOR PRODUCTION")
            return key
        except Exception as e:
            logging.error(f"Error generating dummy key: {str(e)}")
            return None
