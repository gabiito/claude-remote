"""RED tests for WU-2 — services/vapid_keygen.py.

Tests run BEFORE the implementation exists; they must fail (ImportError).
Once the green commit lands, all tests here must pass.

Spec: REQ-3.5, REQ-3.6, SC-3.3
"""

from __future__ import annotations

import base64


class TestGenerateKeypair:
    def test_generate_returns_two_nonempty_strings(self) -> None:
        """generate_keypair() returns a tuple of two non-empty strings."""
        from claude_remote.services.vapid_keygen import generate_keypair

        pub, priv = generate_keypair()
        assert isinstance(pub, str)
        assert isinstance(priv, str)
        assert len(pub) > 0
        assert len(priv) > 0

    def test_public_key_is_base64url_no_padding(self) -> None:
        """Public key must be URL-safe base64 with no padding characters."""
        from claude_remote.services.vapid_keygen import generate_keypair

        pub, _ = generate_keypair()
        # No padding, no + or / (those are standard base64, not URL-safe)
        assert "=" not in pub
        assert "+" not in pub
        assert "/" not in pub

    def test_public_key_decodes_to_65_bytes(self) -> None:
        """Public key must decode to exactly 65 bytes (0x04 || X || Y). (SC-3.3)"""
        from claude_remote.services.vapid_keygen import generate_keypair

        pub, _ = generate_keypair()
        padding = "=" * ((4 - len(pub) % 4) % 4)
        decoded = base64.urlsafe_b64decode(pub + padding)
        assert len(decoded) == 65
        assert decoded[0] == 0x04, f"Expected 0x04 prefix, got {hex(decoded[0])}"

    def test_private_key_is_pem(self) -> None:
        """Private key must be PEM-encoded (starts with -----BEGIN)."""
        from claude_remote.services.vapid_keygen import generate_keypair

        _, priv = generate_keypair()
        assert priv.startswith("-----BEGIN")

    def test_two_calls_return_different_keypairs(self) -> None:
        """generate_keypair() must generate a fresh keypair each call."""
        from claude_remote.services.vapid_keygen import generate_keypair

        pub1, priv1 = generate_keypair()
        pub2, priv2 = generate_keypair()
        assert pub1 != pub2
        assert priv1 != priv2
