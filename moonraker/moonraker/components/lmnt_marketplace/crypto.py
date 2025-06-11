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
from cryptography.fernet import Fernet, InvalidToken

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
                try:
                    # Convert hex IV to bytes
                    iv_bytes = bytes.fromhex(iv) if isinstance(iv, str) else iv
                    
                    # Convert DEK to bytes if needed
                    dek_bytes = base64.b64decode(dek) if isinstance(dek, str) else dek
                    
                    # Use AES-CBC for decryption with the provided IV
                    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                    from cryptography.hazmat.backends import default_backend
                    
                    # Create AES cipher with the DEK and IV
                    cipher = Cipher(
                        algorithms.AES(dek_bytes[:32]),  # Use first 32 bytes as AES key
                        modes.CBC(iv_bytes),  # Use the provided IV
                        backend=default_backend()
                    )
                    
                    # Create decryptor
                    decryptor = cipher.decryptor()
                    
                    # Decrypt the data
                    decrypted_data = decryptor.update(encrypted_data) + decryptor.finalize()
                    
                    # Remove PKCS7 padding if needed
                    from cryptography.hazmat.primitives.padding import PKCS7
                    unpadder = PKCS7(128).unpadder()
                    try:
                        decrypted_data = unpadder.update(decrypted_data) + unpadder.finalize()
                    except Exception as e:
                        logging.warning(f"Failed to unpad data, may not be padded: {str(e)}")
                        # Continue with the data as is
                        
                except Exception as e:
                    logging.error(f"Error using custom decryption with DEK and IV{job_info}: {str(e)}")
                    logging.info(f"Falling back to Fernet decryption")
                    # Fall back to Fernet decryption
                    try:
                        # Format DEK for Fernet
                        key = base64.urlsafe_b64encode(base64.b64decode(dek)[:32])
                        cipher = Fernet(key)
                        decrypted_data = cipher.decrypt(encrypted_data)
                    except Exception as inner_e:
                        logging.error(f"Fernet fallback also failed{job_info}: {str(inner_e)}")
                        # Fall back to PSEK
                        key = await self.get_decryption_key()
                        if not key:
                            logging.error(f"Failed to get PSEK{job_info}")
                            return None
                        cipher = Fernet(key)
                        decrypted_data = cipher.decrypt(encrypted_data)
            # If only DEK is provided (no IV), use Fernet
            elif dek:
                logging.info(f"Using provided DEK with Fernet to decrypt GCode{job_info}")
                try:
                    # Ensure DEK is properly formatted for Fernet
                    if not dek.startswith(b'_') and len(dek) >= 32:
                        # Convert base64 DEK to Fernet key format if needed
                        key = base64.urlsafe_b64encode(base64.b64decode(dek)[:32])
                    else:
                        # Assume it's already in correct format
                        key = dek.encode() if isinstance(dek, str) else dek
                    
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
