"""Tests for the job URL reader (adapt-by-URL flow)."""

import socket
from unittest.mock import patch

import pytest

from app.exceptions import PreconditionError
from app.services.job_url_reader import extract_page, fetch_job_page

PUBLIC_ADDR = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


class FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aread(self):
        return self._body


class FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url):
        return self._response


# ── extract_page ───────────────────────────────────────────────────


def test_extract_page_uses_og_title_and_strips_scripts():
    html = (
        "<html><head><title>Ignored</title>"
        "<meta property='og:title' content='Senior Engineer - Acme'/></head>"
        "<body><script>var secret = 1;</script>"
        "<p>We are hiring a <b>Senior Engineer</b> for our team.</p>"
        "<style>.hidden{display:none}</style></body></html>"
    )
    page = extract_page(html)
    assert page["title"] == "Senior Engineer - Acme"
    assert "Senior Engineer" in page["text"]
    assert "var secret" not in page["text"]
    assert ".hidden" not in page["text"]


def test_extract_page_falls_back_to_title_tag():
    html = "<html><head><title>  Job at  Acme  </title></head><body><p>Hello</p></body></html>"
    page = extract_page(html)
    assert page["title"] == "Job at  Acme"


# ── URL validation ─────────────────────────────────────────────────


async def test_fetch_job_page_rejects_bad_scheme():
    with pytest.raises(PreconditionError):
        await fetch_job_page("ftp://example.com/jobs/1")


async def test_fetch_job_page_rejects_private_host():
    with (
        patch(
            "app.services.job_url_reader.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))],
        ),
        pytest.raises(PreconditionError),
    ):
        await fetch_job_page("https://internal.corp.example/jobs/1")


async def test_fetch_job_page_rejects_unresolvable_host():
    with (
        patch(
            "app.services.job_url_reader.socket.getaddrinfo",
            side_effect=socket.gaierror,
        ),
        pytest.raises(PreconditionError),
    ):
        await fetch_job_page("https://nonexistent-host.invalid/jobs/1")


# ── Fetching / content ─────────────────────────────────────────────


@patch("app.services.job_url_reader.socket.getaddrinfo", return_value=PUBLIC_ADDR)
@patch(
    "app.services.job_url_reader.httpx.AsyncClient",
    return_value=FakeClient(
        FakeResponse(
            body=(
                b"<html><head><meta property='og:title' content='Senior Python Engineer'/></head>"
                b"<body><p>We are hiring a Senior Python Engineer with FastAPI and "
                b"Kubernetes. You will design, build and operate distributed systems "
                b"in a remote-first team.</p>"
                b"<ul><li>5+ years of Python</li><li>Kubernetes and Docker</li>"
                b"<li>PostgreSQL and Redis</li></ul></body></html>"
            )
        )
    ),
)
async def test_fetch_job_page_extracts_text_and_title(_client, _dns):
    page = await fetch_job_page("https://www.example.com/jobs/123")
    assert page["title"] == "Senior Python Engineer"
    assert "Senior Python Engineer" in page["text"]
    assert "PostgreSQL and Redis" in page["text"]


@patch("app.services.job_url_reader.socket.getaddrinfo", return_value=PUBLIC_ADDR)
@patch(
    "app.services.job_url_reader.httpx.AsyncClient",
    return_value=FakeClient(FakeResponse(status_code=403)),
)
async def test_fetch_job_page_http_error_is_friendly(_client, _dns):
    with pytest.raises(PreconditionError, match="HTTP 403"):
        await fetch_job_page("https://www.example.com/jobs/123")


@patch("app.services.job_url_reader.socket.getaddrinfo", return_value=PUBLIC_ADDR)
@patch(
    "app.services.job_url_reader.httpx.AsyncClient",
    return_value=FakeClient(
        FakeResponse(body=b"<html><body><p>Please log in to continue</p></body></html>")
    ),
)
async def test_fetch_job_page_rejects_login_wall(_client, _dns):
    with pytest.raises(PreconditionError, match="login wall|extract"):
        await fetch_job_page("https://www.example.com/jobs/123")
