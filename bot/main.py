"""
main.py – InstaShift
====================
Bot entry-point.  Loads cogs, initialises the database and starts the bot.

Run via:
    python -m bot.main
or:
    bash run.sh
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.database import init_db

# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv()

# ── Logging configuration ─────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("instashift")

# ── Constants ─────────────────────────────────────────────────────────────────
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
GUILD_ID_RAW: str = os.getenv("GUILD_ID", "")
TEST_GUILD: discord.Object | None = (
    discord.Object(id=int(GUILD_ID_RAW)) if GUILD_ID_RAW.strip() else None
)

# Cogs to load on startup
EXTENSIONS: list[str] = [
    "bot.cogs.instagram_scraper",
    "bot.cogs.feeds",
]


# ══════════════════════════════════════════════════════════════════════════════
# Bot class
# ══════════════════════════════════════════════════════════════════════════════

class InstaShift(commands.Bot):
    """Custom Bot subclass with async setup."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True

        super().__init__(
            command_prefix=commands.when_mentioned,  # prefix unused (slash-only)
            intents=intents,
            help_command=None,
            description="📸 Mirror Instagram feeds to Discord channels.",
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called once before the bot connects to Discord."""
        # Initialise database
        await init_db()
        log.info("Database initialised.")

        # Load cogs
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info("Loaded extension: %s", ext)
            except Exception as exc:
                log.exception("Failed to load extension %s: %s", ext, exc)

        # Sync slash commands
        if TEST_GUILD:
            # Instant sync to a single guild (dev mode)
            self.tree.copy_global_to(guild=TEST_GUILD)
            synced = await self.tree.sync(guild=TEST_GUILD)
            log.info("Slash commands synced to guild %s (%d commands)", TEST_GUILD.id, len(synced))
        else:
            # Global sync (may take up to 1 hour to propagate)
            synced = await self.tree.sync()
            log.info("Slash commands synced globally (%d commands)", len(synced))

    async def on_ready(self) -> None:
        log.info("=" * 55)
        log.info("  InstaShift is online!")
        log.info("  Logged in as : %s (ID: %s)", self.user, self.user.id)
        log.info("  Guild count  : %d", len(self.guilds))
        log.info("  discord.py   : %s", discord.__version__)
        log.info("=" * 55)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Instagram feeds 📸",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild: %s (ID: %s)", guild.name, guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Left guild: %s (ID: %s)", guild.name, guild.id)


# ══════════════════════════════════════════════════════════════════════════════
# Entry-point
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    if not DISCORD_TOKEN:
        log.critical("DISCORD_TOKEN is not set. Please configure your .env file.")
        sys.exit(1)

    async with InstaShift() as bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user (KeyboardInterrupt).")
