"""Referral URL helpers."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse


def renaiss_base_url() -> str:
    return os.getenv("RENAISS_BASE_URL", "https://www.renaiss.xyz").rstrip("/")


def renaiss_referral_url() -> str:
    return os.getenv("RENAISS_REFERRAL_URL", "").strip()


def add_referral(url: str | None) -> str | None:
    direct_referral = renaiss_referral_url()
    if direct_referral:
        return direct_referral

    if not url:
        return None
    code = os.getenv("RENAISS_REFERRAL_CODE", "").strip()
    if not code:
        return url

    param = os.getenv("RENAISS_REFERRAL_PARAM", "ref").strip() or "ref"
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[param] = code
    return urlunparse(parsed._replace(query=urlencode(query)))


def build_search_url(card_name: str, category: str | None = None) -> str:
    query = card_name.strip()
    if category:
        query = f"{query} {category}"
    return add_referral(f"{renaiss_base_url()}/?search={quote_plus(query)}") or renaiss_base_url()
