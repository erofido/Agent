"""Web search and page fetching for models without built-in web tools (DeepSeek)."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_PAGE_CHARS = 12000


async def brave_search(http: httpx.AsyncClient, api_key: str, query: str, count: int = 5) -> str:
    resp = await http.get(
        BRAVE_URL,
        params={"q": query, "count": count},
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
    )
    resp.raise_for_status()
    results = (resp.json().get("web") or {}).get("results") or []
    if not results:
        return "No results."
    return "\n\n".join(
        f"{r.get('title', '')}\n{r.get('url', '')}\n{_strip_tags(r.get('description', ''))}" for r in results[:count]
    )


async def fetch_url(http: httpx.AsyncClient, url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only http(s) URLs can be fetched")
    await _reject_private_host(parsed.hostname)
    resp = await http.get(url, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (personal-agent)"})
    if resp.is_redirect:
        return f"Redirects to {resp.headers.get('location')}. Fetch that URL if needed."
    resp.raise_for_status()
    text = resp.text
    if "html" in resp.headers.get("content-type", ""):
        text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", text)
        text = _strip_tags(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_PAGE_CHARS] + (" …(truncated)" if len(text) > MAX_PAGE_CHARS else "")


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


async def _reject_private_host(host: str) -> None:
    """Don't let the model reach this server's own network (localhost, cloud metadata, LAN)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("That address is not on the public internet")
