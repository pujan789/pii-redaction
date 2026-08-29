from __future__ import annotations

import pytest

from taxhance_pii.security import (
    InvalidUpload,
    client_fingerprint,
    keyed_hash,
    token_matches,
    validate_magic,
    validate_upload_metadata,
)


def test_tokens_use_constant_hash_contract() -> None:
    pepper = b"x" * 32
    digest = keyed_hash("secret", pepper)
    assert token_matches("secret", digest, pepper)
    assert not token_matches("different", digest, pepper)
    assert client_fingerprint("127.0.0.1", pepper) != client_fingerprint("127.0.0.2", pepper)


@pytest.mark.parametrize(
    ("filename", "content_type", "extension"),
    [
        ("return.pdf", "application/pdf", ".pdf"),
        ("SCAN.JPEG", "image/jpeg", ".jpeg"),
        ("nested/scan.png", "image/png", ".png"),
    ],
)
def test_upload_metadata(filename: str, content_type: str, extension: str) -> None:
    assert validate_upload_metadata(filename, content_type)[0] == extension


def test_upload_metadata_rejects_extension_content_mismatch() -> None:
    with pytest.raises(InvalidUpload, match="content_type_mismatch"):
        validate_upload_metadata("return.pdf", "image/png")


def test_magic_validation_rejects_renamed_executable() -> None:
    with pytest.raises(InvalidUpload, match="file_signature_mismatch"):
        validate_magic(b"MZ\x90\x00", ".pdf")
