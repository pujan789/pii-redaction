"""Hand-built minimal PDFs for tests that need annotations, forms, or encryption.

No PDF-writing dependency exists in the dev environment, so these build the
byte stream directly. Kept deliberately tiny: one page, Helvetica, no xref
streams.
"""

from __future__ import annotations

import hashlib
import io
import struct

_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")

STATIC_TEXT = (
    b"Employee social security number Wages tips other compensation "
    b"Federal income tax withheld Box 1 Box 2"
)

# The annotation rectangle in PDF points (x1, y1, x2, y2), y measured from the bottom.
ANNOTATION_RECT = (100, 500, 460, 540)


def build_pdf(objects: list[tuple[int, bytes]], trailer_extra: bytes = b"") -> bytes:
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number, body in objects:
        offsets[number] = out.tell()
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    count = max(offsets) + 1
    out.write(f"xref\n0 {count}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for index in range(1, count):
        out.write(f"{offsets[index]:010d} 00000 n \n".encode())
    out.write(
        b"trailer\n<< /Size "
        + str(count).encode()
        + b" /Root 1 0 R "
        + trailer_extra
        + b" >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return out.getvalue()


def pdf_stream(extra: str, data: bytes) -> bytes:
    return f"<< {extra} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def _page_objects(annots: bytes = b"", catalog_extra: bytes = b"") -> list[tuple[int, bytes]]:
    content = b"BT /F1 12 Tf 72 720 Td (" + STATIC_TEXT + b") Tj ET"
    return [
        (1, b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b" >>"),
        (2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        (
            3,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R " + annots + b" >>",
        ),
        (4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
        (5, pdf_stream("", content)),
    ]


def plain_text_pdf() -> bytes:
    return build_pdf(_page_objects())


def link_annotation_pdf() -> bytes:
    objects = _page_objects(annots=b"/Annots [6 0 R]")
    objects.append(
        (
            6,
            b"<< /Type /Annot /Subtype /Link /Rect [72 700 300 730] /Border [0 0 0] "
            b"/A << /S /URI /URI (https://example.test) >> >>",
        )
    )
    return build_pdf(objects)


def _appearance_stream(content: bytes) -> tuple[int, bytes]:
    return (
        7,
        pdf_stream(
            "/Type /XObject /Subtype /Form /BBox [0 0 360 40] "
            "/Resources << /Font << /Helv 4 0 R >> >>",
            content,
        ),
    )


def freetext_annotation_pdf() -> bytes:
    """A page whose typed-over text lives only in a FreeText annotation."""
    objects = _page_objects(annots=b"/Annots [6 0 R]")
    objects.append(
        (
            6,
            b"<< /Type /Annot /Subtype /FreeText /Rect [100 500 460 540] /F 4 /P 3 0 R "
            b"/DA (/Helv 22 Tf 0 g) /Contents (JOHN Q SAMPLE 123-45-6789) "
            b"/AP << /N 7 0 R >> >>",
        )
    )
    objects.append(
        _appearance_stream(b"BT /Helv 22 Tf 0 g 2 8 Td (JOHN Q SAMPLE 123-45-6789) Tj ET")
    )
    return build_pdf(objects)


def acroform_pdf() -> bytes:
    """A page with one filled AcroForm text field (value only in the widget)."""
    objects = _page_objects(
        annots=b"/Annots [6 0 R]",
        catalog_extra=(
            b"/AcroForm << /Fields [6 0 R] /DA (/Helv 0 Tf 0 g) "
            b"/DR << /Font << /Helv 4 0 R >> >> >>"
        ),
    )
    objects.append(
        (
            6,
            b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (taxpayer) "
            b"/V (JOHN Q SAMPLE 123-45-6789) /Rect [100 500 460 540] /F 4 /P 3 0 R "
            b"/DA (/Helv 22 Tf 0 g) /AP << /N 7 0 R >> >>",
        )
    )
    objects.append(
        _appearance_stream(
            b"/Tx BMC BT /Helv 22 Tf 0 g 2 8 Td (JOHN Q SAMPLE 123-45-6789) Tj ET EMC"
        )
    )
    return build_pdf(objects)


def _rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 255
        state[i], state[j] = state[j], state[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) & 255
        j = (j + state[i]) & 255
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) & 255])
    return bytes(out)


def _md5(data: bytes) -> bytes:
    # MD5 is what the PDF 1.x standard security handler specifies; this is a
    # test fixture, not a security boundary.
    return hashlib.md5(data).digest()  # noqa: S324


def password_protected_pdf(user_password: bytes = b"secret") -> bytes:
    """RC4 revision-2 encryption; opens only with the user password."""
    user = (user_password + _PAD)[:32]
    owner = (b"owner" + _PAD)[:32]
    owner_entry = _rc4(_md5(owner)[:5], user)
    permissions = -1
    document_id = _md5(b"fixture")
    key = _md5(user + owner_entry + struct.pack("<i", permissions) + document_id)[:5]
    user_entry = _rc4(key, _PAD)

    def object_key(number: int) -> bytes:
        return _md5(key + struct.pack("<I", number)[:3] + b"\x00\x00")[:10]

    def hexstring(value: bytes) -> bytes:
        return b"<" + value.hex().encode() + b">"

    content = b"BT /F1 12 Tf 72 720 Td (Secret 123-45-6789) Tj ET"
    objects = _page_objects()
    objects[4] = (5, pdf_stream("", _rc4(object_key(5), content)))
    objects.append(
        (
            6,
            b"<< /Filter /Standard /V 1 /R 2 /Length 40 /P -1 /O "
            + hexstring(owner_entry)
            + b" /U "
            + hexstring(user_entry)
            + b" >>",
        )
    )
    return build_pdf(
        objects,
        trailer_extra=b"/Encrypt 6 0 R /ID ["
        + hexstring(document_id)
        + b" "
        + hexstring(document_id)
        + b"]",
    )
