"""Central HBSys connection factory used only by read-only repositories."""

from __future__ import annotations

import os
from typing import Any


def create_hbsys_connection() -> Any:
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("pymysql is not installed") from exc
    return pymysql.connect(
        host=os.getenv("HBSYS_DB_HOST", "192.168.1.2"),
        port=int(os.getenv("HBSYS_DB_PORT", "3306")),
        user=os.getenv("HBSYS_DB_USER", "root"),
        password=os.getenv("HBSYS_DB_PASSWORD", "root"),
        database=os.getenv("HBSYS_DB_NAME", "hbsys_edh"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
        autocommit=True,
        charset="utf8",
    )
