#!/usr/bin/env python3
"""One-shot FTP deploy of the local Bludit tree to heteml's /blog/ dir.

Usage: FTP_HOST=... FTP_USER=... FTP_PASS=... python3 deploy_bludit.py
(reads from environment; source aixec/.env first)
"""
import ftplib
import os
import sys

LOCAL_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blog-bludit")
REMOTE_ROOT = "/web/kurage_exbridge_jp/blog"


def ensure_dir(ftp, remote_dir):
    parts = remote_dir.strip("/").split("/")
    path = ""
    for part in parts:
        path += "/" + part
        try:
            ftp.mkd(path)
        except ftplib.error_perm:
            pass  # already exists


def main():
    host = os.environ["FTP_HOST"]
    user = os.environ["FTP_USER"]
    password = os.environ["FTP_PASS"]

    ftp = ftplib.FTP(host, timeout=30)
    ftp.login(user, password)

    uploaded = 0
    dirs_seen = set()
    for dirpath, dirnames, filenames in os.walk(LOCAL_ROOT):
        rel_dir = os.path.relpath(dirpath, LOCAL_ROOT)
        remote_dir = REMOTE_ROOT if rel_dir == "." else f"{REMOTE_ROOT}/{rel_dir}"
        if remote_dir not in dirs_seen:
            ensure_dir(ftp, remote_dir)
            dirs_seen.add(remote_dir)
        for fname in filenames:
            local_path = os.path.join(dirpath, fname)
            remote_path = f"{remote_dir}/{fname}"
            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {remote_path}", f)
            uploaded += 1
            if uploaded % 50 == 0:
                print(f"  {uploaded} files uploaded...", flush=True)

    ftp.quit()
    print(f"Done. {uploaded} files uploaded to {REMOTE_ROOT}")


if __name__ == "__main__":
    main()
