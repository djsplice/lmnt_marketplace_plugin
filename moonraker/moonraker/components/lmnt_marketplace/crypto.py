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
        self.encrypted_psek = None
        self.decryption_key = None
        self.dlt_private_key_ed25519 = None
    
    async def initialize(self, klippy_apis, http_client):
        """Initialize with Klippy APIs and HTTP client"""
        self.klippy_apis = klippy_apis
        self.http_client = http_client
        
        # Load encrypted PSEK if available
        self._load_encrypted_psek()
        self._load_dlt_private_key() # Load DLT private key if available
    
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
    
    def _save_encrypted_psek(self, encrypted_psek):
        """
        Save the encrypted PSEK received from the server
        """
        if not encrypted_psek:
            logging.error("Cannot save empty encrypted PSEK")
            return False
        
        psek_file = os.path.join(self.integration.keys_path, "encrypted_psek.json")
        try:
            with open(psek_file, 'w') as f:
                json.dump({'encrypted_psek': encrypted_psek}, f)
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

        try:
            async with self.http_client.post(decrypt_url, headers=headers, json=payload) as response:
                if response.status == 200:
                    resp_json = await response.json()
                    decrypted_b64 = resp_json.get('decryptedData')
                    if decrypted_b64:
                        return base64.b64decode(decrypted_b64)
                else:
                    error_text = await response.text()
                    logging.error(f"CWS Decryption: Failed. Status: {response.status}, Body: {error_text}")
                    return None
        except Exception as e:
            logging.error(f"CWS Decryption: Request exception: {e}")
            return None

    async def decrypt_dek(self, encrypted_gcode_dek_package, kek_id=None):
        """
        Decrypts the G-code DEK.
        Handles DLT-native and legacy PSEK/CWS encrypted DEK packages.
        """
        # Path 1: DLT-native package (identified by colons)
        if ':' in encrypted_gcode_dek_package:
            logging.info("CryptoManager: Asymmetrically encrypted DEK package detected.")
            if not self.dlt_private_key_ed25519:
                logging.error("CryptoManager: DLT private key not loaded. Cannot decrypt DLT-native package.")
                return None

            try:
                parts = encrypted_gcode_dek_package.split(':')
                if len(parts) != 3:
                    logging.error("CryptoManager: DLT DEK package has incorrect format.")
                    return None
                
                ephemeral_pubkey_b64, nonce_b64, ciphertext_b64 = parts
                ephemeral_pubkey_bytes = base64.b64decode(ephemeral_pubkey_b64)
                nonce_bytes = base64.b64decode(nonce_b64)
                ciphertext_bytes = base64.b64decode(ciphertext_b64)

                # self.dlt_private_key_ed25519 is expected to be an Ed25519SigningKey.
                # The error "'PrivateKey' object has no attribute 'to_curve25519_private_key'"
                # suggests it might actually be a nacl.public.PrivateKey (Curve25519PrivateKey) instance.
                # If it's an Ed25519SigningKey, it will have 'to_curve25519_private_key'.
                # If it's already a Curve25519PrivateKey, it won't, and we can use it directly.
                if hasattr(self.dlt_private_key_ed25519, 'to_curve25519_private_key'):
                    printer_dlt_private_key_curve25519 = self.dlt_private_key_ed25519.to_curve25519_private_key()
                elif isinstance(self.dlt_private_key_ed25519, Curve25519PrivateKey):
                    logging.warning("CryptoManager: dlt_private_key_ed25519 was a Curve25519PrivateKey. Using directly.")
                    printer_dlt_private_key_curve25519 = self.dlt_private_key_ed25519
                else:
                    logging.error(f"CryptoManager: dlt_private_key_ed25519 is of unexpected type {type(self.dlt_private_key_ed25519)}. Cannot proceed with DLT decryption.")
                    return None
                webslicer_ephemeral_public_key_curve25519 = Curve25519PublicKey(ephemeral_pubkey_bytes)
                
                box = nacl.public.Box(printer_dlt_private_key_curve25519, webslicer_ephemeral_public_key_curve25519)
                
                plaintext_dek_bytes = box.decrypt(ciphertext_bytes, nonce_bytes)
                logging.info("CryptoManager: Successfully decrypted G-code DEK via DLT-native path.")
                return plaintext_dek_bytes

            except (binascii.Error, nacl.exceptions.CryptoError) as e:
                logging.error(f"CryptoManager: DLT-native DEK decryption failed: {e}.")
                return None

        # Path 2: Legacy PSEK/CWS package (no colons)
        else:
            logging.info("CryptoManager: Legacy PSEK/CWS DEK package detected.")
            if not kek_id or not encrypted_gcode_dek_package:
                logging.error("CryptoManager: KEK ID or encrypted G-code DEK is missing for legacy path.")
                return None

            try:
                plaintext_psek_bytes = await self._decrypt_data_via_cws(kek_id)
                if not plaintext_psek_bytes:
                    logging.error("CryptoManager: Failed to get plaintext PSEK from CWS.")
                    return None

                iv_from_dek_hex = encrypted_gcode_dek_package[:32]
                encrypted_dek_hex = encrypted_gcode_dek_package[32:]
                iv_from_dek_bytes = bytes.fromhex(iv_from_dek_hex)
                encrypted_dek_bytes = bytes.fromhex(encrypted_dek_hex)

                if len(iv_from_dek_bytes) != 16:
                    logging.error(f"CryptoManager: Invalid IV length: {len(iv_from_dek_bytes)} bytes.")
                    return None

                cipher = Cipher(algorithms.AES(plaintext_psek_bytes), modes.CBC(iv_from_dek_bytes), backend=default_backend())
                decryptor = cipher.decryptor()
                decrypted_dek_padded = decryptor.update(encrypted_dek_bytes) + decryptor.finalize()
                
                unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
                decrypted_dek_unpadded = unpadder.update(decrypted_dek_padded) + unpadder.finalize()
                
                logging.info("CryptoManager: Successfully decrypted G-code DEK via legacy path.")
                return decrypted_dek_unpadded

            except (binascii.Error, ValueError) as e:
                logging.error(f"CryptoManager: G-code DEK decryption failed: {e}.")
                return None
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
        printer_kek_id = job_details_dict.get('printer_kek_id')

        if not gcode_dek_package or not gcode_iv_hex:
            logging.error(f"CryptoManager: Missing crypto materials for job {job_id}")
            return None

        try:
            plaintext_gcode_dek_bytes = await self.decrypt_dek(gcode_dek_package, printer_kek_id)
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
        
    async def get_decryption_key(self):
        """
        Get the decryption key for GCode files by decrypting the PSEK via CWS
        """
        if self.decryption_key:
            return self.decryption_key
        
        if not self.integration.auth_manager.printer_token or not self.encrypted_psek:
            logging.error("Cannot get decryption key: No printer token or encrypted PSEK available")
            return None
        
        decrypted_psek_bytes = await self._decrypt_data_via_cws(self.encrypted_psek)
        if decrypted_psek_bytes:
            self.decryption_key = base64.urlsafe_b64encode(decrypted_psek_bytes)
            logging.info("Successfully obtained decryption key from CWS")
            return self.decryption_key
        
        logging.error("Failed to obtain decryption key from CWS")
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