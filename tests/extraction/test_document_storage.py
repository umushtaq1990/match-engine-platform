"""Pure-logic tests for app/extraction/document_storage.py's validation —
no object storage involved. See tests/extraction/test_cv_routes_db.py for
the real-storage, real-DB round trip.
"""
import pytest

from app.extraction import document_storage


def test_valid_pdf_passes():
    document_storage.validate_cv_upload("application/pdf", 1024, b"%PDF-1.4")


def test_rejects_non_pdf_content_type():
    with pytest.raises(ValueError, match="Only application/pdf"):
        document_storage.validate_cv_upload("image/png", 1024, b"%PDF-1.4")


def test_rejects_oversized_file():
    with pytest.raises(ValueError, match="too large"):
        document_storage.validate_cv_upload(
            "application/pdf", document_storage.MAX_CV_SIZE_BYTES + 1, b"%PDF-1.4"
        )


def test_accepts_file_exactly_at_the_size_cap():
    document_storage.validate_cv_upload(
        "application/pdf", document_storage.MAX_CV_SIZE_BYTES, b"%PDF-1.4"
    )


def test_rejects_content_claiming_pdf_but_wrong_magic_bytes():
    with pytest.raises(ValueError, match="not a valid PDF"):
        document_storage.validate_cv_upload("application/pdf", 1024, b"PK\x03\x04")  # a zip/docx header


def test_object_key_is_scoped_per_candidate():
    assert document_storage.object_key(42) == "candidates/42/cv.pdf"
    assert document_storage.object_key(1) != document_storage.object_key(2)
