#!/usr/bin/env python3
"""Extract only the SQL dump and safe image/font files from a user-owned DirectAdmin backup."""
import argparse
import collections
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile

ARCHIVE_FILE = Path("/srv/rishe-sftp/incoming/backup-Oct-20-2026-1.tar.gz")
SQL_FILE = Path("/opt/rishestore-pars/legacy-20261020/rishesto_db.sql")
MEDIA_BASE = Path("/var/lib/docker/volumes/rishestore_pars_brand_assets/_data/wp-content/uploads")
SOURCE_PREFIX = "domains/rishe.store/public_html/wp-content/uploads/"
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".svg", ".ico", ".ttf", ".otf", ".woff", ".woff2"}
MAX_ASSET_SIZE = 64 * 1024 * 1024
MAX_MEDIA_TOTAL = 3 * 1024 * 1024 * 1024
MAX_SQL_SIZE = 700 * 1024 * 1024

def write_member(tar, member, dst):
    stream = tar.extractfile(member)
    if stream is None:
        raise ValueError("Archive member could not be read")
    dst.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temp = dst.with_name(dst.name + ".restore-part")
    with stream, open(temp, "wb") as output:
        shutil.copyfileobj(stream, output, length=1024 * 1024)
    if temp.stat().st_size != member.size:
        temp.unlink(missing_ok=True)
        raise ValueError("Incomplete extracted file")
    os.chmod(temp, 0o644 if dst != SQL_FILE else 0o600)
    os.replace(temp, dst)

def main():
    if not ARCHIVE_FILE.is_file():
        raise FileNotFoundError("Uploaded archive not found")
    SQL_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(SQL_FILE.parent, 0o700)
    if not MEDIA_BASE.parent.is_dir():
        raise ValueError("Expected existing bind-mounted brand asset folder")
    MEDIA_BASE.mkdir(parents=True, exist_ok=True, mode=0o755)
    assets = 0
    total = 0
    skip = collections.Counter()
    sql_found = False
    suffixes = collections.Counter()
    with tarfile.open(ARCHIVE_FILE, mode="r|gz") as archive:
        for member in archive:
            if not member.isfile() or member.issym() or member.islnk():
                continue
            name = member.name.lstrip("./")
            if name == "backup/rishesto_db.sql":
                sql_found = True
                if member.size > MAX_SQL_SIZE:
                    raise ValueError("SQL larger than expected safe limit")
                if not SQL_FILE.is_file() or SQL_FILE.stat().st_size != member.size:
                    write_member(archive, member, SQL_FILE)
                continue
            if not name.startswith(SOURCE_PREFIX):
                continue
            rel = name[len(SOURCE_PREFIX):]
            relative = PurePosixPath(rel)
            if (not rel or rel.startswith("/") or
                any(part in {"", ".", ".."} for part in relative.parts)):
                skip["unsafe_path"] += 1
                continue
            ext = relative.suffix.lower()
            if ext not in ALLOWED:
                skip["unsupported_extension"] += 1
                continue
            if not 0 < member.size <= MAX_ASSET_SIZE:
                skip["invalid_file_size"] += 1
                continue
            if total + member.size > MAX_MEDIA_TOTAL:
                raise ValueError("Safe media extraction budget exceeded")
            target = MEDIA_BASE.joinpath(*relative.parts)
            if not target.is_file() or target.stat().st_size != member.size:
                write_member(archive, member, target)
            assets += 1
            total += member.size
            suffixes[ext] += 1
    if not sql_found or not SQL_FILE.is_file():
        raise ValueError("DirectAdmin SQL dump missing in archive")
    print("ARCHIVE_SQL_PRESENT=YES")
    print("SQL_BYTES=", SQL_FILE.stat().st_size)
    print("MEDIA_FILES=", assets)
    print("MEDIA_BYTES=", total)
    print("MEDIA_TYPES=", suffixes.most_common(12))
    print("SKIPPED_TYPES=", dict(skip))

if __name__ == "__main__":
    main()
