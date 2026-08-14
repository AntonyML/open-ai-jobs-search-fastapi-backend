"""Fetch a job posting URL and extract readable text for CV adaptation.

The "adapt by URL" flow lets the user paste a public job link (LinkedIn,
Indeed, a company career page, ...) and lets the LLM tailor the CV from the
page content — no scraping infrastructure involved.  This module:

1. Validates the URL (scheme, host, and — as an SSRF guard — resolves the
   hostname and rejects private / loopback / link-local targets).
2. Fetches the page with a plain browser-like User-Agent.
3. Extracts the page title (og:title > <title>) and the readable text
   (scripts, styles, svg and head content stripped) for the LLM prompts.

Failures raise ``PreconditionError`` with a user-facing message so the
frontend can surface a friendly hint (e.g. LinkedIn blocks bots, suggest
pasting the description instead).

Note: this is best-effort scraping of *public* pages. Job boards that gate
content behind login walls return only their shell — the extracted text will
be too short and the endpoint returns a clear error in that case.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from app.exceptions import PreconditionError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT = 15.0
MAX_RESPONSE_BYTES = 2_000_000  # stop reading huge pages
MAX_TEXT_CHARS = 12_000
MIN_TEXT_CHARS = 120  # below this the page is a login wall / shell


class _TextExtractor(HTMLParser):
    """Pull the page title and visible text out of raw HTML."""

    # head is NOT skipped: <title> lives inside it and its data is captured
    # while it is skipped. meta tags are handled in handle_starttag.
    _SKIPPED_TAGS = {"script", "style", "noscript", "svg", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = 0
        self.title_parts: list[str] = []
        self.og_title: str | None = None
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag in self._SKIPPED_TAGS:
            self.skip_depth += 1
        elif tag == "title":
            self.in_title += 1
        elif tag == "meta" and (
            attrs_map.get("property") == "og:title"
            or attrs_map.get("name") == "twitter:title"
        ):
            content = (attrs_map.get("content") or "").strip()
            if content and self.og_title is None:
                self.og_title = content

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag == "title":
            self.in_title = max(0, self.in_title - 1)

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        stripped = " ".join(data.split())
        if stripped:
            self.text_parts.append(stripped)


def extract_page(html: str) -> dict[str, str]:
    """Return ``{"title": ..., "text": ...}`` from raw HTML."""
    parser = _TextExtractor()
    parser.feed(html)
    text = "\n".join(parser.text_parts)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = (parser.og_title or " ".join(parser.title_parts)).strip()
    return {"title": title, "text": text[:MAX_TEXT_CHARS]}


def _validate_public_url(url: str) -> str:
    """Ensure the URL is http(s) and points at a public (non-private) host."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise PreconditionError(
            "That link doesn't look like a valid job URL. Use a full "
            "http(s) address, e.g. https://www.linkedin.com/jobs/view/123."
        )

    host = (parsed.hostname or "").lower()
    if (
        host == "localhost"
        or host.endswith((".local", ".internal", ".localhost", ".lan"))
        or host in {"127.0.0.1", "::1"}
    ):
        raise PreconditionError("That URL points to a private address and can't be read.")

    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise PreconditionError(
            "We couldn't resolve that link. Double-check the URL and try again."
        ) from None

    for info in addresses:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise PreconditionError("That URL points to a private network and can't be read.")

    return url


async def fetch_job_page(url: str) -> dict[str, str]:
    """Fetch a public job URL and return ``{"title": ..., "text": ...}``.

    Raises ``PreconditionError`` with a user-facing message when the URL is
    invalid, the site blocks automated access, or the page yields no
    readable job content.
    """
    _validate_public_url(url)

    try:
        async with (
            httpx.AsyncClient(
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                max_redirects=5,
            ) as client,
            client.stream("GET", url) as response,
        ):
                if response.status_code >= 400:
                    raise PreconditionError(
                        "The site returned an error for that link "
                        f"(HTTP {response.status_code}). Try another URL "
                        "or paste the job description instead."
                    )
                content_type = response.headers.get("content-type", "")
                if "pdf" in content_type.lower() or (
                    "html" not in content_type.lower() and "text/" not in content_type.lower()
                ):
                    raise PreconditionError(
                        "That link doesn't return a web page. Try the job's "
                        "public URL or paste the description instead."
                    )
                body = await response.aread()
    except PreconditionError:
        raise
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise PreconditionError(
            "We couldn't read that page — the site may block automated "
            "access. Try another link or paste the job description instead."
        ) from exc

    if len(body) > MAX_RESPONSE_BYTES:
        body = body[:MAX_RESPONSE_BYTES]

    page = extract_page(body.decode("utf-8", errors="replace"))

    if len(page["text"]) < MIN_TEXT_CHARS:
        raise PreconditionError(
            "We couldn't extract job content from that page (it may be "
            "behind a login wall). Try another link or paste the "
            "description instead."
        )

    if not page["title"]:
        parsed = urlparse(url)
        page["title"] = f"Job offer ({parsed.netloc})"

    return page
