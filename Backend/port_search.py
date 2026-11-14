"""Build reference links from the bundled game catalog."""

from __future__ import annotations

def source_link(repo: str, detail: str) -> dict:
    return {"name": repo, "detail": detail, "url": f"https://github.com/{repo}"}


def port_references(edition: dict) -> list[dict]:
    return [source_link(**reference) for reference in edition.get("references", [])]
