"""Global test fixtures for the test suite."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the get_settings() LRU cache before each test.

    This ensures tests see fresh Settings instances, especially after
    adding new fields like R2 configuration.
    """
    from app.core.settings import get_settings

    get_settings.cache_clear()
    yield


@pytest.fixture(autouse=True)
def mock_r2_s3():
    """Mock actual S3 operations from r2_storage for ALL tests.

    This fixture patches the boto3 client creation and individual S3
    operations so no real S3 calls are made. Unlike patching
    _r2_configured(), this allows tests to verify both configured and
    not-configured code paths.
    """
    with (
        patch("app.services.r2_storage._get_client") as mock_get_client,
    ):
        mock_client = mock_get_client.return_value
        # Default stubs for S3 operations
        mock_client.put_object.return_value = {}
        mock_client.delete_object.return_value = {}
        mock_client.generate_presigned_url.return_value = "https://mock-r2.example.com/test.pdf?signed=abc"
        mock_client.head_object.return_value = {}
        mock_client.delete_objects.return_value = {}
        mock_paginator = mock_client.get_paginator.return_value
        mock_paginator.paginate.return_value = iter([{"Contents": []}])
        yield
