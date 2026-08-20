"""Unit tests for app.services.r2_storage.

All tests mock boto3 via conftest's mock_r2_s3 fixture — no real S3 calls.
Tests that verify the "not configured" path patch _r2_configured directly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services import r2_storage


class TestR2Configured:
    """Test _r2_configured() detection."""

    def test_configured_when_all_set(self):
        with patch("app.services.r2_storage.get_settings") as mock:
            mock.return_value = MagicMock(
                r2_account_id="acc123",
                r2_access_key_id="key123",
                r2_secret_access_key="secret123",
            )
            assert r2_storage._r2_configured() is True

    def test_not_configured_when_account_empty(self):
        with patch("app.services.r2_storage.get_settings") as mock:
            mock.return_value = MagicMock(
                r2_account_id="",
                r2_access_key_id="key123",
                r2_secret_access_key="secret123",
            )
            assert r2_storage._r2_configured() is False

    def test_not_configured_when_all_empty(self):
        with patch("app.services.r2_storage.get_settings") as mock:
            mock.return_value = MagicMock(
                r2_account_id="",
                r2_access_key_id="",
                r2_secret_access_key="",
            )
            assert r2_storage._r2_configured() is False


class TestUploadPdf:
    """Test upload_pdf()."""

    def test_upload_success(self):
        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(r2_bucket_name="test-bucket")
            mock_client.return_value = MagicMock()

            r2_storage.upload_pdf("test/file.pdf", b"%PDF-1.4 content")

            mock_client.return_value.put_object.assert_called_once()
            call_kwargs = mock_client.return_value.put_object.call_args[1]
            assert call_kwargs["Bucket"] == "test-bucket"
            assert call_kwargs["Key"] == "test/file.pdf"
            assert call_kwargs["Body"] == b"%PDF-1.4 content"
            assert call_kwargs["ContentType"] == "application/pdf"

    def test_upload_raises_when_not_configured(self):
        with patch("app.services.r2_storage._r2_configured", return_value=False):
            with pytest.raises(RuntimeError, match="R2 is not configured"):
                r2_storage.upload_pdf("test/file.pdf", b"data")

    def test_upload_raises_on_client_error(self):
        from botocore.exceptions import ClientError

        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(r2_bucket_name="test-bucket")
            mock_client.return_value.put_object.side_effect = ClientError(
                {"Error": {"Code": "500", "Message": "Internal"}},
                "PutObject",
            )

            with pytest.raises(ClientError):
                r2_storage.upload_pdf("test/file.pdf", b"data")


class TestGenerateSignedUrl:
    """Test generate_signed_url()."""

    def test_generates_url(self):
        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(
                r2_bucket_name="test-bucket", r2_signed_url_ttl=3600
            )
            mock_client.return_value.generate_presigned_url.return_value = (
                "https://r2.example.com/signed?token=abc"
            )

            url = r2_storage.generate_signed_url("test/file.pdf")

            assert url == "https://r2.example.com/signed?token=abc"
            mock_client.return_value.generate_presigned_url.assert_called_once_with(
                "get_object",
                Params={"Bucket": "test-bucket", "Key": "test/file.pdf"},
                ExpiresIn=3600,
            )

    def test_custom_expires_in(self):
        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(
                r2_bucket_name="test-bucket", r2_signed_url_ttl=3600
            )
            mock_client.return_value.generate_presigned_url.return_value = "url"

            r2_storage.generate_signed_url("key", expires_in=7200)

            call_kwargs = mock_client.return_value.generate_presigned_url.call_args[1]
            assert call_kwargs["ExpiresIn"] == 7200

    def test_raises_when_not_configured(self):
        with patch("app.services.r2_storage._r2_configured", return_value=False):
            with pytest.raises(RuntimeError, match="R2 is not configured"):
                r2_storage.generate_signed_url("key")


class TestDeletePdf:
    """Test delete_pdf()."""

    def test_delete_success(self):
        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(r2_bucket_name="test-bucket")
            mock_client.return_value = MagicMock()

            result = r2_storage.delete_pdf("test/file.pdf")

            assert result is True
            mock_client.return_value.delete_object.assert_called_once_with(
                Bucket="test-bucket", Key="test/file.pdf"
            )

    def test_delete_returns_false_when_not_configured(self):
        with patch("app.services.r2_storage._r2_configured", return_value=False):
            assert r2_storage.delete_pdf("key") is False

    def test_delete_returns_false_on_error(self):
        from botocore.exceptions import ClientError

        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(r2_bucket_name="test-bucket")
            mock_client.return_value.delete_object.side_effect = ClientError(
                {"Error": {"Code": "500", "Message": "err"}}, "DeleteObject"
            )

            result = r2_storage.delete_pdf("key")
            assert result is False


class TestDeleteUserPrefix:
    """Test delete_user_prefix()."""

    def test_returns_zero_when_not_configured(self):
        with patch("app.services.r2_storage._r2_configured", return_value=False):
            assert r2_storage.delete_user_prefix("user-1") == 0

    def test_deletes_objects_in_cv_scope(self):
        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(r2_bucket_name="test-bucket")
            mock_client_obj = MagicMock()
            mock_client.return_value = mock_client_obj

            page_cv = {"Contents": [{"Key": "generated_cvs/user-1/a.pdf"}, {"Key": "generated_cvs/user-1/b.pdf"}]}
            empty_page = {"Contents": []}

            mock_paginator = MagicMock()
            mock_paginator.paginate.side_effect = [
                iter([page_cv]),
                iter([empty_page]),
            ]
            mock_client_obj.get_paginator.return_value = mock_paginator

            result = r2_storage.delete_user_prefix("user-1")

            assert result == 2
            mock_client_obj.delete_objects.assert_called_once()
            call_kwargs = mock_client_obj.delete_objects.call_args[1]
            assert len(call_kwargs["Delete"]["Objects"]) == 2


class TestObjectExists:
    """Test object_exists()."""

    def test_returns_true_when_exists(self):
        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(r2_bucket_name="test-bucket")
            mock_client.return_value.head_object.return_value = {}

            assert r2_storage.object_exists("test/file.pdf") is True

    def test_returns_false_when_not_exists(self):
        from botocore.exceptions import ClientError

        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(r2_bucket_name="test-bucket")
            mock_client.return_value.head_object.side_effect = ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
            )

            assert r2_storage.object_exists("test/missing.pdf") is False

    def test_returns_false_when_not_configured(self):
        with patch("app.services.r2_storage._r2_configured", return_value=False):
            assert r2_storage.object_exists("key") is False


class TestDownloadToTemp:
    """Test download_to_temp()."""

    def test_returns_none_when_not_configured(self):
        with patch("app.services.r2_storage._r2_configured", return_value=False):
            assert r2_storage.download_to_temp("key") is None

    def test_returns_none_on_error(self):
        from botocore.exceptions import ClientError

        with patch("app.services.r2_storage.get_settings") as mock_settings, \
             patch("app.services.r2_storage._get_client") as mock_client:
            mock_settings.return_value = MagicMock(r2_bucket_name="test-bucket")
            mock_client.return_value.download_file.side_effect = ClientError(
                {"Error": {"Code": "404"}}, "GetObject"
            )

            result = r2_storage.download_to_temp("missing.pdf")
            assert result is None
