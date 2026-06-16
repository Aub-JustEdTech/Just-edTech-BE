"""
API key utilities: generation.
"""

import secrets


def generate_api_key() -> str:
    """Generate a single API key."""
    return "ak_" + secrets.token_urlsafe(32)
