#!/usr/bin/env python3
"""Generate VAPID keys for Web Push notifications"""
import json
import os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import base64


def generate_vapid_keys():
    """Generate a new EC P-256 key pair for VAPID"""
    # Generate private key
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    
    # Get the raw private key bytes (32 bytes for P-256)
    private_numbers = private_key.private_numbers()
    private_bytes = private_numbers.private_value.to_bytes(32, byteorder='big')
    
    # Get the public key point (uncompressed format, skip the 0x04 prefix)
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    
    # Combine x and y coordinates (each 32 bytes for P-256)
    # Prepend 0x04 byte for uncompressed format (required by Web Push)
    x_bytes = public_numbers.x.to_bytes(32, byteorder='big')
    y_bytes = public_numbers.y.to_bytes(32, byteorder='big')
    public_bytes = b'\x04' + x_bytes + y_bytes
    
    # Base64url encode (no padding)
    private_key_b64 = base64.urlsafe_b64encode(private_bytes).rstrip(b'=').decode('utf-8')
    public_key_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b'=').decode('utf-8')
    
    return private_key_b64, public_key_b64


def main():
    keys_path = os.path.join(os.path.dirname(__file__), "vapid_keys.json")
    
    if os.path.exists(keys_path):
        print("vapid_keys.json already exists!")
        print("Delete it first if you want to regenerate keys.")
        print("\nCurrent public key:")
        with open(keys_path) as f:
            keys = json.load(f)
            print(f"  {keys['public_key']}")
        return
    
    print("Generating VAPID keys...")
    private_key, public_key = generate_vapid_keys()
    
    keys = {
        "private_key": private_key,
        "public_key": public_key,
        "contact": "mailto:your-email@example.com"
    }
    
    with open(keys_path, 'w') as f:
        json.dump(keys, f, indent=2)
    
    print("✓ VAPID keys generated and saved to vapid_keys.json")
    print(f"\nPublic key (for frontend):\n  {public_key}")
    print("\n⚠️  Keep vapid_keys.json secret! Add to .gitignore.")


if __name__ == "__main__":
    main()
