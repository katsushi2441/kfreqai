#!/usr/bin/env python3
"""Synchronize the shared Kurage Bludit blog to its four product categories."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CREDS_PATH = PROJECT_ROOT / "user_data" / "blog-bludit-admin.txt"
DEFAULT_BASE = "https://kurage.exbridge.jp/blog"

DESIRED = {
    "kfreqai": "暗号資産AI自動取引の運用・開発記録",
    "kfxai": "FX AI自動取引の運用・開発記録",
    "kcbrain": "暗号資産市場を判断するAI知能API",
    "kfxbrain": "FX市場を判断するAI知能API",
}


def load_creds() -> dict[str, str]:
    creds: dict[str, str] = {}
    for raw in CREDS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            creds[key] = value
    for key in ("BLUDIT_API_TOKEN", "BLUDIT_AUTH_TOKEN"):
        if not creds.get(key):
            raise RuntimeError(f"Missing {key} in {CREDS_PATH}")
    return creds


class Bludit:
    def __init__(self, base: str, creds: dict[str, str], apply: bool) -> None:
        self.base = base.rstrip("/")
        self.token = creds["BLUDIT_API_TOKEN"]
        self.auth = creds["BLUDIT_AUTH_TOKEN"]
        self.apply = apply

    def get(self, path: str, **params: object) -> dict:
        response = requests.get(
            f"{self.base}/api/{path}",
            params={"token": self.token, **params},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def write(self, method: str, path: str, **data: object) -> dict:
        if not self.apply:
            return {"status": "0", "message": "dry-run"}
        payload = {"token": self.token, "authentication": self.auth, **data}
        request_args: dict[str, object] = {"timeout": 30}
        if method == "PUT":
            request_args["json"] = payload
        elif method == "DELETE":
            request_args["params"] = payload
        else:
            request_args["data"] = payload
        response = requests.request(method, f"{self.base}/api/{path}", **request_args)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status")) != "0":
            raise RuntimeError(f"{method} {path}: {payload.get('message', 'unknown error')}")
        return payload


def searchable(page: dict) -> str:
    fields = [page.get("key"), page.get("slug"), page.get("title"), page.get("category")]
    tags = page.get("tags", [])
    fields.append(json.dumps(tags, ensure_ascii=False) if not isinstance(tags, str) else tags)
    return " ".join(str(value or "").lower() for value in fields)


def current_category_key(page: dict, categories: dict[str, dict]) -> str:
    category_name = str(page.get("category") or "")
    if category_name in categories:
        return category_name
    for key, item in categories.items():
        if item.get("name") == category_name:
            return key
    return ""


def category_for(page: dict, current: str) -> str | None:
    if current in DESIRED:
        return current
    text = searchable(page)
    for key in ("kcbrain", "kfxbrain", "kfreqai", "kfxai"):
        if key in text:
            return key
    return None


def synchronize(client: Bludit) -> None:
    categories = {item["key"]: item for item in client.get("categories").get("data", [])}
    for key, description in DESIRED.items():
        if key not in categories:
            print(f"create category: {key}")
            client.write("POST", "categories", name=key, description=description)
        elif categories[key].get("name") != key or categories[key].get("description") != description:
            print(f"update category: {key}")
            client.write(
                "PUT",
                f"categories/{quote(key)}",
                newKey=key,
                name=key,
                description=description,
            )

    pages = client.get("pages", numberOfItems=-1).get("data", [])
    assignments: dict[str, str] = {}
    for page in pages:
        current = current_category_key(page, categories)
        target = category_for(page, current)
        if target is None:
            raise RuntimeError(f"Cannot classify page: {page.get('key')}")
        assignments[str(page["key"])] = target
        if current != target:
            print(f"move page: {page['key']} -> {target}")
            client.write("PUT", f"pages/{quote(str(page['key']))}", category=target)

    if client.apply:
        categories = {item["key"]: item for item in client.get("categories").get("data", [])}
    for key, item in categories.items():
        if key in DESIRED:
            continue
        if item.get("list"):
            raise RuntimeError(f"Refusing to delete non-empty category: {key}")
        print(f"delete category: {key}")
        client.write("DELETE", f"categories/{quote(key)}")

    counts = {key: 0 for key in DESIRED}
    for target in assignments.values():
        counts[target] += 1
    mode = "applied" if client.apply else "dry-run"
    print(f"{mode}: " + ", ".join(f"{key}={counts[key]}" for key in DESIRED))

    if client.apply:
        final = {item["key"]: item for item in client.get("categories").get("data", [])}
        if set(final) != set(DESIRED):
            raise RuntimeError(f"Category verification failed: {sorted(final)}")
        for key, expected in counts.items():
            actual = len(final[key].get("list", []))
            if actual != expected:
                raise RuntimeError(f"Count mismatch for {key}: expected {expected}, got {actual}")
        print("verified: exactly four categories and all page assignments match")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--apply", action="store_true", help="Apply changes; default is dry-run")
    args = parser.parse_args()
    try:
        synchronize(Bludit(args.base, load_creds(), args.apply))
    except (OSError, RuntimeError, requests.RequestException, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
