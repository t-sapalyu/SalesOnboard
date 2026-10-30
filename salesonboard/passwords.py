"""Password hashing with scrypt, from Python's standard library.

We store only a random salt and the hash, never the password itself.
scrypt is slow on purpose, so guessing passwords from a stolen hash is costly.
The salt makes two equal passwords give different hashes.

Stored format: "scrypt$<salt as hex>$<hash as hex>"
"""

import hashlib
import hmac
import secrets

# scrypt cost settings: about 16 MB of memory and a few milliseconds per hash.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    parts = stored_hash.split("$")
    if len(parts) != 3 or parts[0] != "scrypt":
        return False

    try:
        salt = bytes.fromhex(parts[1])
        expected = bytes.fromhex(parts[2])
    except ValueError:  # not valid hex: a broken hash never matches
        return False

    actual = _scrypt(password, salt)
    # compare_digest takes the same time whether the first or last byte
    # differs, so an attacker cannot learn the hash by timing the check.
    return hmac.compare_digest(actual, expected)


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
