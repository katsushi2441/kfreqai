#!/usr/bin/env python3
"""Deploy and activate the self-hosted Kurage Blog comments plugin only."""

import ftplib
import io
import json
import os
import secrets
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PLUGIN = PROJECT_ROOT / "blog-bludit" / "bl-plugins" / "kurage-comments"
REMOTE_BLOG = "/web/kurage_exbridge_jp/blog"
REMOTE_PLUGIN = f"{REMOTE_BLOG}/bl-plugins/kurage-comments"
REMOTE_DB_DIR = f"{REMOTE_BLOG}/bl-content/databases/plugins/kurage-comments"
REMOTE_DB = f"{REMOTE_DB_DIR}/db.php"
REMOTE_WORKSPACE = f"{REMOTE_BLOG}/bl-content/workspaces/kurage-comments"


def ensure_dir(ftp: ftplib.FTP, remote_dir: str) -> None:
    current = ""
    for part in remote_dir.strip("/").split("/"):
        current += f"/{part}"
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            pass


def retrieve_text(ftp: ftplib.FTP, remote_path: str) -> str | None:
    output = io.BytesIO()
    try:
        ftp.retrbinary(f"RETR {remote_path}", output.write)
    except ftplib.error_perm:
        return None
    return output.getvalue().decode("utf-8")


def parse_plugin_db(raw: str | None) -> dict:
    if not raw:
        return {}
    lines = raw.splitlines()
    if lines and lines[0].startswith("<?php"):
        lines = lines[1:]
    try:
        data = json.loads("\n".join(lines))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    ftp = ftplib.FTP(os.environ["FTP_HOST"], timeout=30)
    ftp.login(os.environ["FTP_USER"], os.environ["FTP_PASS"])

    ensure_dir(ftp, REMOTE_PLUGIN)
    uploaded = 0
    for local_path in sorted(LOCAL_PLUGIN.rglob("*")):
        if not local_path.is_file():
            continue
        relative = local_path.relative_to(LOCAL_PLUGIN).as_posix()
        remote_path = f"{REMOTE_PLUGIN}/{relative}"
        ensure_dir(ftp, str(Path(remote_path).parent).replace("\\", "/"))
        with local_path.open("rb") as handle:
            ftp.storbinary(f"STOR {remote_path}", handle)
        uploaded += 1

    ensure_dir(ftp, REMOTE_DB_DIR)
    ensure_dir(ftp, f"{REMOTE_WORKSPACE}/comments")
    ensure_dir(ftp, f"{REMOTE_WORKSPACE}/rate")

    db = parse_plugin_db(retrieve_text(ftp, REMOTE_DB))
    db.update(
        {
            "enablePages": True,
            "secret": db.get("secret") or secrets.token_hex(32),
            "position": int(db.get("position") or 1),
        }
    )
    payload = (
        "<?php defined('BLUDIT') or die('Bludit CMS.'); ?>\n"
        + json.dumps(db, ensure_ascii=False, separators=(",", ":"))
    ).encode("utf-8")
    ftp.storbinary(f"STOR {REMOTE_DB}", io.BytesIO(payload))
    ftp.quit()

    print(f"Uploaded {uploaded} plugin files and activated Kurage Comments.")


if __name__ == "__main__":
    main()
