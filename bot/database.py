"""
database.py – InstaShift
========================
Async SQLite wrapper using aiosqlite.

Tables
------
feeds        : per-server subscription records
posted_media : anti-duplicate log of already-published media IDs
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import aiosqlite

log = logging.getLogger(__name__)

# ── Path resolution ───────────────────────────────────────────────────────────
DB_PATH: Path = Path(os.getenv("DB_PATH", "instashift.db"))


# ══════════════════════════════════════════════════════════════════════════════
# Public helpers
# ══════════════════════════════════════════════════════════════════════════════

async def init_db() -> None:
    """Create tables if they don't exist. Call once on bot startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS feeds (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id          INTEGER NOT NULL,
                instagram_account TEXT    NOT NULL COLLATE NOCASE,
                channel_id        INTEGER NOT NULL,
                thread_id         INTEGER,          -- optional thread override
                role_id           INTEGER,          -- mention on new post
                last_media_id     TEXT,             -- newest seen media shortcode
                active            INTEGER NOT NULL DEFAULT 1,
                created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE (guild_id, instagram_account, channel_id)
            );

            CREATE TABLE IF NOT EXISTS posted_media (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id       INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
                media_id      TEXT    NOT NULL,
                posted_at     TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE (feed_id, media_id)
            );
        """)
        await db.commit()
    log.info("Database ready: %s", DB_PATH)


# ── Feed CRUD ─────────────────────────────────────────────────────────────────

async def add_feed(
    guild_id: int,
    instagram_account: str,
    channel_id: int,
    thread_id: Optional[int] = None,
    role_id: Optional[int] = None,
) -> int:
    """Insert a new feed subscription. Returns the row id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT OR IGNORE INTO feeds
               (guild_id, instagram_account, channel_id, thread_id, role_id)
               VALUES (?, ?, ?, ?, ?)""",
            (guild_id, instagram_account.lstrip("@"), channel_id, thread_id, role_id),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def remove_feed(guild_id: int, instagram_account: str, channel_id: int) -> bool:
    """Delete a feed subscription. Returns True if a row was deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """DELETE FROM feeds
               WHERE guild_id=? AND instagram_account=? AND channel_id=?""",
            (guild_id, instagram_account.lstrip("@"), channel_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_feeds(guild_id: int) -> list[dict]:
    """Return all active feeds for a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM feeds WHERE guild_id=? AND active=1 ORDER BY id",
            (guild_id,),
        ) as cur:
            return [dict(row) async for row in cur]


async def get_all_active_feeds() -> list[dict]:
    """Return all active feeds across all guilds (for the poller task)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM feeds WHERE active=1 ORDER BY guild_id, id"
        ) as cur:
            return [dict(row) async for row in cur]


async def update_last_media_id(feed_id: int, media_id: str) -> None:
    """Advance the cursor for a feed after posting."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE feeds SET last_media_id=? WHERE id=?",
            (media_id, feed_id),
        )
        await db.commit()


# ── Anti-duplicate helpers ────────────────────────────────────────────────────

async def is_already_posted(feed_id: int, media_id: str) -> bool:
    """True if this media was already published to Discord for this feed."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM posted_media WHERE feed_id=? AND media_id=?",
            (feed_id, media_id),
        ) as cur:
            return await cur.fetchone() is not None


async def mark_as_posted(feed_id: int, media_id: str) -> None:
    """Record that a media item was published, ignoring duplicates."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO posted_media (feed_id, media_id) VALUES (?, ?)",
            (feed_id, media_id),
        )
        await db.commit()
