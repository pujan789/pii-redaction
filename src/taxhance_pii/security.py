from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import PurePath

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


class InvalidUpload(ValueError):
    pass


def new_access_token() -> str:
    return secrets.token_urlsafe(32)


def keyed_hash(value: str, pepper: bytes) -> str:
    return hmac.new(pepper, value.encode("utf-8"), hashlib.sha256).hexdigest()


def token_matches(token: str, expected_hash: str, pepper: bytes) -> bool:
    if not token or not expected_hash:
        return False
    return hmac.compare_digest(keyed_hash(token, pepper), expected_hash)


def client_fingerprint(ip_address: str, pepper: bytes) -> str:
    return keyed_hash(f"client:{ip_address}", pepper)[:32]


def content_fingerprint(text: str, pepper: bytes) -> str:
    """Create a correlation-safe fingerprint without retaining detected text."""

    normalized = " ".join(text.casefold().split())
    return keyed_hash(f"content:{normalized}", pepper)[:16]


def validate_upload_metadata(filename: str, content_type: str) -> tuple[str, str]:
    leaf_name = PurePath(filename.replace("\\", "/")).name
    suffix = PurePath(leaf_name).suffix.casefold()
    expected_type = ALLOWED_EXTENSIONS.get(suffix)
    if expected_type is None:
        raise InvalidUpload("unsupported_file_type")
    normalized_type = content_type.split(";", maxsplit=1)[0].strip().casefold()
    compatible = normalized_type in {expected_type, "application/octet-stream"}
    if suffix in {".jpg", ".jpeg"} and normalized_type == "image/jpg":
        compatible = True
    if not compatible:
        raise InvalidUpload("content_type_mismatch")
    return suffix, expected_type


def validate_magic(prefix: bytes, extension: str) -> None:
    signatures: dict[str, tuple[bytes, ...]] = {
        ".pdf": (b"%PDF-",),
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".jpg": (b"\xff\xd8\xff",),
        ".jpeg": (b"\xff\xd8\xff",),
        ".tif": (b"II*\x00", b"MM\x00*"),
        ".tiff": (b"II*\x00", b"MM\x00*"),
    }
    if not any(prefix.startswith(signature) for signature in signatures[extension]):
        raise InvalidUpload("file_signature_mismatch")
