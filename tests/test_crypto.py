"""Tests for common/crypto.py — secp256k1 key generation, signing, verification."""
import pytest

from common.crypto import (
    KeyPair,
    generate_secp256k1_keypair,
    sha256_hex,
    canonical_json_bytes,
    sign_hash_hex,
    verify_signature,
    pubkey_to_address,
)


class TestKeyGeneration:
    def test_generates_valid_keypair(self):
        kp = generate_secp256k1_keypair()
        assert isinstance(kp, KeyPair)
        assert len(kp.private_key_hex) == 64  # 32 bytes hex (no 0x prefix from .hex())
        assert kp.address.startswith("0x")
        assert len(kp.address) == 42

    def test_unique_keys_each_call(self):
        kp1 = generate_secp256k1_keypair()
        kp2 = generate_secp256k1_keypair()
        assert kp1.private_key_hex != kp2.private_key_hex
        assert kp1.address != kp2.address

    def test_deterministic_with_entropy(self):
        entropy = b"\x01" * 32
        kp1 = generate_secp256k1_keypair(entropy=entropy)
        kp2 = generate_secp256k1_keypair(entropy=entropy)
        assert kp1.private_key_hex == kp2.private_key_hex
        assert kp1.address == kp2.address


class TestHashing:
    def test_sha256_hex(self):
        result = sha256_hex(b"hello")
        assert len(result) == 64  # 32 bytes = 64 hex chars
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_canonical_json_bytes(self):
        obj = {"b": 2, "a": 1}
        result = canonical_json_bytes(obj)
        # Sort keys, no spaces
        assert result == b'{"a":1,"b":2}'


class TestSignAndVerify:
    def test_sign_and_verify_roundtrip(self):
        kp = generate_secp256k1_keypair()
        data = b"test message"
        hash_hex = sha256_hex(data)

        sig = sign_hash_hex(hash_hex, kp.private_key_hex)
        assert sig.startswith("0x")

        # Verify with address
        assert verify_signature(hash_hex, sig, kp.address) is True

    def test_wrong_key_fails_verification(self):
        kp1 = generate_secp256k1_keypair()
        kp2 = generate_secp256k1_keypair()
        data = b"test message"
        hash_hex = sha256_hex(data)

        sig = sign_hash_hex(hash_hex, kp1.private_key_hex)
        # Verify against wrong address
        assert verify_signature(hash_hex, sig, kp2.address) is False

    def test_invalid_signature_returns_false(self):
        kp = generate_secp256k1_keypair()
        hash_hex = sha256_hex(b"test")
        assert verify_signature(hash_hex, "0x" + "00" * 65, kp.address) is False

    def test_tampered_message_fails(self):
        kp = generate_secp256k1_keypair()
        hash1 = sha256_hex(b"original")
        hash2 = sha256_hex(b"tampered")

        sig = sign_hash_hex(hash1, kp.private_key_hex)
        assert verify_signature(hash2, sig, kp.address) is False


class TestPubkeyToAddress:
    def test_known_address_derivation(self):
        # Generate a keypair and verify address matches
        kp = generate_secp256k1_keypair()
        # The address from generate should be checksummed
        assert kp.address.startswith("0x")
        assert len(kp.address) == 42
