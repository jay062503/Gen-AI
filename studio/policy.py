"""Baseline request policy.

This is a *minimum* guard, not a moderation system. FLUX.2 klein's licence asks
deployers to apply filters; before exposing the studio to staff or customers,
add your own review step (e.g. in the Enfactum gateway that calls this service).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Policy:
    max_prompt_chars: int = 5000


_MINOR = r"(?:child|children|kid|minor|underage|under-age|teen(?:ager)?|schoolgirl|schoolboy|toddler|infant|loli\w*|shota)"
_SEXUAL = r"(?:nude|naked|sex\w*|explicit|porn\w*|erotic|undress\w*|topless|genital\w*)"

_BLOCKED = [
    re.compile(r"\b(?:csam|child sexual abuse|child porn\w*)\b", re.I),
    re.compile(r"\bnon-?\s?consensual\s+(?:intimate|sexual)\b", re.I),
    re.compile(rf"\b{_MINOR}\b.{{0,60}}\b{_SEXUAL}\b|\b{_SEXUAL}\b.{{0,60}}\b{_MINOR}\b", re.I | re.S),
]
# Edits of a real person's photo into nudity ("nudify") are refused outright.
_BLOCKED_EDIT = re.compile(
    r"\b(?:undress|nudify|remove\s+(?:her|his|their|the)?\s*(?:cloth\w*|shirt|top|bra|pants|underwear)|make\s+(?:her|him|them)\s+(?:nude|naked))\b",
    re.I,
)


def validate_prompt(prompt: str, policy: Policy | None = None, *, is_edit: bool = False) -> None:
    policy = policy or Policy()
    p = (prompt or "").strip()
    if not p:
        raise ValueError("prompt is required")
    if len(p) > policy.max_prompt_chars:
        raise ValueError("prompt exceeds the configured limit")
    if any(rx.search(p) for rx in _BLOCKED) or (is_edit and _BLOCKED_EDIT.search(p)):
        raise ValueError("prompt rejected by media safety policy")


def validate_callback(url: str | None, allowed_hosts: tuple) -> None:
    """Webhooks are opt-in: only hosts listed in MEDIA_CALLBACK_HOSTS are allowed (SSRF guard)."""
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("callback_url must be an http(s) URL")
    if parsed.hostname.lower() not in allowed_hosts:
        raise ValueError("callback host is not allowed (set MEDIA_CALLBACK_HOSTS)")


def request_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()
