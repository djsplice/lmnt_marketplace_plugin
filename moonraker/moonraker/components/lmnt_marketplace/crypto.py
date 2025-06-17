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
import nacl.secret
import nacl.utils
import nacl.public # For nacl.public.Box
from nacl.public import PrivateKey as Curve25519PrivateKey, PublicKey as Curve25519PublicKey
from nacl.signing import SigningKey as Ed25519SigningKey # To load the stored Ed25519 private key

class CryptoManager:
    """
    Manages cryptographic operations for LMNT Marketplace
    
    Handles secure key management, decryption of encrypted GCode files,
    and integration with the Custodial Wallet Service (CWS).
    """
    
    def __init__(self, integration):
        """Initialize the Crypto Manager"""
        self.integration = integration
        self.dlt_private_key_ed25519 = None
    
    async def initialize(self, klippy_apis, http_client):
        """Initialize with Klippy APIs and HTTP client"""
        self.klippy_apis = klippy_apis
        self.http_client = http_client
        
        # Load DLT private key if available
        self._load_dlt_private_key() # Load DLT private key if available
    
    def _load_dlt_private_key(self):
        """Load DLT private key (Ed25519 signing key) from secure storage."""
        key_file = os.path.join(self.integration.keys_path, "dlt_keypair.json")
        if os.path.exists(key_file):
            try:
                with open(key_file, 'r') as f:
                    data = json.load(f)
                    private_key_b64 = data.get('private_key_b64')
                    if private_key_b64:
                        private_key_bytes = base64.b64decode(private_key_b64)
                        # This key is passed in from integration.py now, so we just load it here as a fallback
                        if not self.dlt_private_key_ed25519:
                            self.dlt_private_key_ed25519 = Ed25519SigningKey(private_key_bytes)
                            logging.info("Loaded DLT private key (Ed25519) from storage.")
                        return True
            except (json.JSONDecodeError, IOError, binascii.Error) as e:
                logging.error(f"Error loading DLT private key: {str(e)}")
        
        logging.info("No DLT private key found or error loading it.")
        return False
    
    async def decrypt_dek(self, encrypted_gcode_dek_package):
        """
        Decrypts the G-code DEK.
        Handles asymmetrically encrypted DEK packages (distinguished by colon separators)
        which are encrypted using the printer's public key.
        """
        if ':' not in encrypted_gcode_dek_package:
            # This check distinguishes the new asymmetric format from the old (now removed) symmetric one.
            logging.error(f"CryptoManager: DEK package format not recognized as asymmetric: {encrypted_gcode_dek_package[:30]}...")
            return None

        logging.info("CryptoManager: Asymmetrically encrypted DEK package detected, using printer-generated key path.")
        if not self.dlt_private_key_ed25519:
            logging.error("CryptoManager: Printer's private key not loaded. Cannot decrypt asymmetric package.")
            return None

        try:
            parts = encrypted_gcode_dek_package.split(':')
            if len(parts) != 3:
                logging.error("CryptoManager: Asymmetric DEK package has incorrect format.")
                return None
            
            ephemeral_pubkey_b64, nonce_b64, ciphertext_b64 = parts
            ephemeral_pubkey_bytes = base64.b64decode(ephemeral_pubkey_b64)
            nonce_bytes = base64.b64decode(nonce_b64)
            ciphertext_bytes = base64.b64decode(ciphertext_b64)

            if hasattr(self.dlt_private_key_ed25519, 'to_curve25519_private_key'):
                printer_dlt_private_key_curve25519 = self.dlt_private_key_ed25519.to_curve25519_private_key()
            elif isinstance(self.dlt_private_key_ed25519, Curve25519PrivateKey):
                logging.warning("CryptoManager: dlt_private_key_ed25519 was a Curve25519PrivateKey. Using directly.")
                printer_dlt_private_key_curve25519 = self.dlt_private_key_ed25519
            else:
                logging.error(f"CryptoManager: Printer's private key is of unexpected type {type(self.dlt_private_key_ed25519)}. Cannot proceed with asymmetric decryption.")
                return None
            webslicer_ephemeral_public_key_curve25519 = Curve25519PublicKey(ephemeral_pubkey_bytes)
            
            box = nacl.public.Box(printer_dlt_private_key_curve25519, webslicer_ephemeral_public_key_curve25519)
            
            plaintext_dek_bytes = box.decrypt(ciphertext_bytes, nonce_bytes)
            logging.info("CryptoManager: Successfully decrypted G-code DEK using printer's private key.")
            return plaintext_dek_bytes

        except (binascii.Error, nacl.exceptions.CryptoError) as e:
            logging.error(f"CryptoManager: Asymmetric DEK decryption failed: {e}.")
            return None

    async def decrypt_gcode(self, encrypted_data, job_id=None, dek=None, iv=None):
        """
        Decrypt GCode data using a provided DEK and IV.
        """
        job_info = f" for job {job_id}" if job_id else ""
        if not dek or not iv:
            logging.error(f"DEK or IV not provided for G-code decryption{job_info}.")
            return None

        try:
            iv_bytes = bytes.fromhex(iv)
            # DEK is already passed as bytes from decrypt_gcode_file_from_job_details
            dek_bytes = dek

            cipher = Cipher(algorithms.AES(dek_bytes), modes.CBC(iv_bytes), backend=default_backend())
            decryptor = cipher.decryptor()
            decrypted_padded = decryptor.update(encrypted_data) + decryptor.finalize()

            unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
            decrypted_data = unpadder.update(decrypted_padded) + unpadder.finalize()
            
            logging.info(f"Successfully decrypted G-code content{job_info}.")
            return decrypted_data

        except (binascii.Error, ValueError) as e:
            logging.error(f"Failed to decrypt G-code content{job_info}: {e}")
            return None

    async def decrypt_gcode_file_from_job_details(self, encrypted_filepath, job_details_dict, job_id):
        """
        Decrypts an encrypted G-code file using details from the job dictionary.
        """
        gcode_dek_package = job_details_dict.get('gcode_dek_package')
        gcode_iv_hex = job_details_dict.get('gcode_iv_hex')

        if not gcode_dek_package or not gcode_iv_hex:
            logging.error(f"CryptoManager: Missing crypto materials for job {job_id}")
            return None

        try:
            # The printer_kek_id is no longer used as we only support the asymmetric printer-generated key path here.
            plaintext_gcode_dek_bytes = await self.decrypt_dek(gcode_dek_package)
            if not plaintext_gcode_dek_bytes:
                logging.error(f"CryptoManager: Failed to obtain plaintext G-code DEK for job {job_id}")
                return None

            with open(encrypted_filepath, 'rb') as f_enc:
                encrypted_gcode_content = f_enc.read()

            decrypted_gcode_bytes = await self.decrypt_gcode(
                encrypted_gcode_content,
                job_id=job_id,
                dek=plaintext_gcode_dek_bytes,
                iv=gcode_iv_hex
            )

            if not decrypted_gcode_bytes:
                logging.error(f"CryptoManager: Failed to decrypt G-code content for job {job_id}")
                return None

            base, ext = os.path.splitext(os.path.basename(encrypted_filepath))
            decrypted_filename = f"{base}.decrypted{ext or '.gcode'}"
            decrypted_filepath = os.path.join(self.integration.encrypted_path, decrypted_filename)
            
            with open(decrypted_filepath, 'wb') as f_dec:
                f_dec.write(decrypted_gcode_bytes)
            
            logging.info(f"CryptoManager: Successfully saved decrypted G-code for job {job_id} to {decrypted_filepath}")
            return decrypted_filepath

        except Exception as e:
            logging.error(f"CryptoManager: Error in decrypt_gcode_file_from_job_details for job {job_id}: {e}")
            return None
        
    async def decrypt_with_key(self, encrypted_data, key):
        """Decrypt data using a provided Fernet key"""
        if not key:
            logging.error("Decryption failed: No key provided")
            return None
        
        try:
            cipher = Fernet(key)
            decrypted_data = cipher.decrypt(encrypted_data)
            return decrypted_data
        except InvalidToken:
            logging.error("Decryption failed: Invalid token")
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during decryption: {str(e)}")
            return None

    def generate_dummy_key(self):
        """Generates a dummy Fernet key for testing purposes"""
        try:
            key = Fernet.generate_key()
            logging.info(f"Generated dummy Fernet key: {key.decode()}")
            return key
        except Exception as e:
            logging.error(f"Error generating dummy key: {str(e)}")
            return None