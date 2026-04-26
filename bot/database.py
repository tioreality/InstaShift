"""
database.py – InstaShift
========================
Capa de acceso a datos usando SQLite asíncrono (aiosqlite).

Tablas
------
feeds        : suscripciones por servidor (guild)
posted_media : registro anti-duplicados de publicaciones ya enviadas a Discord
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import aiosqlite

# ── Logger del módulo ─────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

# ── Ruta de la base de datos (configurable por variable de entorno) ────────────
DB_PATH: Path = Path(os.getenv("DB_PATH", "instashift.db"))


# ══════════════════════════════════════════════════════════════════════════════
# Inicialización
# ══════════════════════════════════════════════════════════════════════════════

async def init_db() -> None:
    """
    Crea las tablas si no existen.
    Debe llamarse una vez durante el inicio del bot (en setup_hook).
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            -- Habilitar WAL para mejor rendimiento en escrituras concurrentes
            PRAGMA journal_mode=WAL;
            -- Activar claves foráneas para integridad referencial
            PRAGMA foreign_keys=ON;

            -- Tabla de suscripciones (feeds)
            CREATE TABLE IF NOT EXISTS feeds (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id          INTEGER NOT NULL,                  -- ID del servidor de Discord
                instagram_account TEXT    NOT NULL COLLATE NOCASE,  -- @usuario de Instagram
                channel_id        INTEGER NOT NULL,                  -- canal de destino
                thread_id         INTEGER,                           -- hilo opcional de destino
                role_id           INTEGER,                           -- rol a mencionar (opcional)
                last_media_id     TEXT,                              -- ID del último contenido visto
                active            INTEGER NOT NULL DEFAULT 1,        -- 1=activo, 0=pausado
                created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
                -- No permitir suscripciones duplicadas para la misma cuenta+canal en el mismo servidor
                UNIQUE (guild_id, instagram_account, channel_id)
            );

            -- Tabla anti-duplicados: registra qué contenido ya fue publicado por feed
            CREATE TABLE IF NOT EXISTS posted_media (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id       INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
                media_id      TEXT    NOT NULL,
                posted_at     TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE (feed_id, media_id)  -- Garantiza que no se duplique por feed
            );
        """)
        await db.commit()

    log.info("Base de datos lista: %s", DB_PATH)


# ══════════════════════════════════════════════════════════════════════════════
# CRUD de feeds
# ══════════════════════════════════════════════════════════════════════════════

async def add_feed(
    guild_id: int,
    instagram_account: str,
    channel_id: int,
    thread_id: Optional[int] = None,
    role_id: Optional[int] = None,
) -> int:
    """
    Inserta una nueva suscripción de feed.
    Retorna el ID de la fila insertada (0 si ya existía).
    """
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
    """
    Elimina una suscripción de feed.
    Retorna True si se eliminó alguna fila.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """DELETE FROM feeds
               WHERE guild_id=? AND instagram_account=? AND channel_id=?""",
            (guild_id, instagram_account.lstrip("@"), channel_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_feeds(guild_id: int) -> list[dict]:
    """Retorna todos los feeds activos de un servidor específico."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM feeds WHERE guild_id=? AND active=1 ORDER BY id",
            (guild_id,),
        ) as cur:
            return [dict(row) async for row in cur]


async def get_all_active_feeds() -> list[dict]:
    """
    Retorna todos los feeds activos de todos los servidores.
    Usado por la tarea periódica del scraper.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM feeds WHERE active=1 ORDER BY guild_id, id"
        ) as cur:
            return [dict(row) async for row in cur]


async def update_last_media_id(feed_id: int, media_id: str) -> None:
    """Actualiza el cursor del feed al ID del último contenido publicado."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE feeds SET last_media_id=? WHERE id=?",
            (media_id, feed_id),
        )
        await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Anti-duplicados
# ══════════════════════════════════════════════════════════════════════════════

async def is_already_posted(feed_id: int, media_id: str) -> bool:
    """
    Verifica si un contenido ya fue publicado en Discord para este feed.
    Retorna True si ya existe el registro.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM posted_media WHERE feed_id=? AND media_id=?",
            (feed_id, media_id),
        ) as cur:
            return await cur.fetchone() is not None


async def mark_as_posted(feed_id: int, media_id: str) -> None:
    """
    Registra que un contenido fue publicado, ignorando duplicados.
    Esto evita publicar el mismo contenido más de una vez por feed.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO posted_media (feed_id, media_id) VALUES (?, ?)",
            (feed_id, media_id),
        )
        await db.commit()
