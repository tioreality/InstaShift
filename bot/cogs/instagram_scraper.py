"""
instagram_scraper.py – InstaShift
===================================
Handles all Instagram interaction via instagrapi.

Key responsibilities
--------------------
* Persistent session management (ig_session.json)
* Authenticated login with automatic re-login on challenge / expired session
* Fetching recent media from a public profile (posts + reels)
* Fetching Stories (requires auth)
* /preview command for quick testing without a subscription
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    LoginRequired,
    MediaNotFound,
    ReloginAttemptExceeded,
    UserNotFound,
)
from instagrapi.types import Media, UserShort

from bot.database import (
    get_all_active_feeds,
    is_already_posted,
    mark_as_posted,
    update_last_media_id,
)

log = logging.getLogger(__name__)

# ── Environment ───────────────────────────────────────────────────────────────
IG_USERNAME: str = os.getenv("IG_USERNAME", "")
IG_PASSWORD: str = os.getenv("IG_PASSWORD", "")
SESSION_PATH: Path = Path(os.getenv("SESSION_PATH", "ig_session.json"))
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "10"))

# Instagram brand colour (pink gradient midpoint)
IG_COLOR = 0xE1306C

# Max posts to fetch per cycle (keeps API calls light)
MAX_POSTS_PER_CYCLE = 5


# ══════════════════════════════════════════════════════════════════════════════
# Instagram client wrapper
# ══════════════════════════════════════════════════════════════════════════════

class InstagramClient:
    """Thread-safe wrapper around instagrapi.Client with session persistence."""

    def __init__(self) -> None:
        self._cl: Client = Client()
        self._logged_in: bool = False
        self._lock = asyncio.Lock()

    # ── Session helpers ───────────────────────────────────────────────────────

    def _load_session(self) -> bool:
        """Try to restore a previous session. Returns True on success."""
        if not SESSION_PATH.exists():
            return False
        try:
            self._cl.load_settings(SESSION_PATH)
            self._cl.login(IG_USERNAME, IG_PASSWORD)
            self._logged_in = True
            log.info("[IG] Session restored from %s", SESSION_PATH)
            return True
        except Exception as exc:
            log.warning("[IG] Session restore failed (%s). Will re-login.", exc)
            return False

    def _save_session(self) -> None:
        SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._cl.dump_settings(SESSION_PATH)
        log.debug("[IG] Session saved to %s", SESSION_PATH)

    # ── Login ─────────────────────────────────────────────────────────────────

    async def ensure_logged_in(self) -> bool:
        """
        Ensure the client is authenticated.
        Returns True if login succeeded, False if credentials are missing.
        """
        async with self._lock:
            if self._logged_in:
                return True

            if not IG_USERNAME or not IG_PASSWORD:
                log.warning("[IG] No credentials set – running in guest mode.")
                return False

            # 1. Try to restore session
            loop = asyncio.get_event_loop()
            restored = await loop.run_in_executor(None, self._load_session)
            if restored:
                return True

            # 2. Fresh login
            try:
                log.info("[IG] Performing fresh login as %s …", IG_USERNAME)
                await loop.run_in_executor(
                    None,
                    lambda: self._cl.login(IG_USERNAME, IG_PASSWORD),
                )
                self._logged_in = True
                await loop.run_in_executor(None, self._save_session)
                log.info("[IG] Login successful.")
                return True
            except BadPassword:
                log.error("[IG] Bad password for %s.", IG_USERNAME)
            except ChallengeRequired:
                log.error("[IG] Challenge required – solve it manually in the app.")
            except ReloginAttemptExceeded:
                log.error("[IG] Too many re-login attempts. Wait before retrying.")
            except Exception as exc:
                log.exception("[IG] Unexpected login error: %s", exc)

            self._logged_in = False
            return False

    # ── Public fetch methods ──────────────────────────────────────────────────

    async def get_user_info(self, username: str) -> Optional[UserShort]:
        """Return basic user info or None if user not found."""
        loop = asyncio.get_event_loop()
        try:
            user_id = await loop.run_in_executor(
                None, lambda: self._cl.user_id_from_username(username)
            )
            info = await loop.run_in_executor(
                None, lambda: self._cl.user_info(user_id)
            )
            return info
        except UserNotFound:
            log.warning("[IG] User not found: %s", username)
        except LoginRequired:
            log.warning("[IG] Login required to fetch user info.")
            self._logged_in = False
        except Exception as exc:
            log.exception("[IG] Error fetching user info for %s: %s", username, exc)
        return None

    async def get_recent_medias(
        self, username: str, amount: int = MAX_POSTS_PER_CYCLE
    ) -> list[Media]:
        """Fetch the N most recent posts/reels from a public profile."""
        loop = asyncio.get_event_loop()
        try:
            user_id = await loop.run_in_executor(
                None, lambda: self._cl.user_id_from_username(username)
            )
            medias = await loop.run_in_executor(
                None, lambda: self._cl.user_medias(user_id, amount=amount)
            )
            return medias
        except UserNotFound:
            log.warning("[IG] User not found: %s", username)
        except LoginRequired:
            log.warning("[IG] Login required – re-authenticating…")
            self._logged_in = False
            await self.ensure_logged_in()
        except Exception as exc:
            log.exception("[IG] Error fetching medias for %s: %s", username, exc)
        return []

    async def get_recent_stories(self, username: str) -> list[Media]:
        """Fetch current stories (requires auth)."""
        await self.ensure_logged_in()
        loop = asyncio.get_event_loop()
        try:
            user_id = await loop.run_in_executor(
                None, lambda: self._cl.user_id_from_username(username)
            )
            stories = await loop.run_in_executor(
                None, lambda: self._cl.user_stories(user_id)
            )
            return stories
        except LoginRequired:
            log.warning("[IG] Login required to fetch stories.")
            self._logged_in = False
        except Exception as exc:
            log.exception("[IG] Error fetching stories for %s: %s", username, exc)
        return []

    @property
    def is_authenticated(self) -> bool:
        return self._logged_in


# ══════════════════════════════════════════════════════════════════════════════
# Discord embed builder
# ══════════════════════════════════════════════════════════════════════════════

def build_media_embed(media: Media, user_info=None) -> discord.Embed:
    """
    Construct a polished Discord embed for a single Instagram media item.

    Layout
    ------
    Author  : profile picture + @username + "Instagram" sub-text
    Title   : media type label (📸 Post / 🎬 Reel / 📖 Story)
    Image   : thumbnail or first image
    Fields  : ❤️ likes · 💬 comments · 📅 date
    Footer  : hashtags (trimmed)
    URL     : direct Instagram link
    Colour  : IG pink
    """
    shortcode = getattr(media, "code", None) or str(media.pk)
    ig_url = f"https://www.instagram.com/p/{shortcode}/"

    # Media type label
    media_type = getattr(media, "media_type", 1)
    if media_type == 2:
        type_label = "🎬 Reel"
    elif getattr(media, "is_story", False):
        type_label = "📖 Story"
    else:
        type_label = "📸 Post"

    # Caption: separate text from hashtags
    raw_caption = getattr(media, "caption_text", "") or ""
    lines = raw_caption.strip().split()
    tags = [w for w in lines if w.startswith("#")]
    words = [w for w in lines if not w.startswith("#")]
    clean_caption = " ".join(words).strip()
    hashtag_line = "  ".join(tags[:15])  # cap at 15 tags

    embed = discord.Embed(
        title=type_label,
        description=clean_caption[:2000] or None,
        url=ig_url,
        color=IG_COLOR,
        timestamp=getattr(media, "taken_at", None),
    )

    # Author row (profile pic + username)
    if user_info:
        username = getattr(user_info, "username", "instagram")
        avatar_url = str(getattr(user_info, "profile_pic_url", "") or "")
        embed.set_author(
            name=f"@{username}",
            url=f"https://www.instagram.com/{username}/",
            icon_url=avatar_url or discord.Embed.Empty,
        )

    # Media image
    thumbnail_url: str = ""
    if hasattr(media, "thumbnail_url") and media.thumbnail_url:
        thumbnail_url = str(media.thumbnail_url)
    elif hasattr(media, "image_versions2") and media.image_versions2:
        candidates = media.image_versions2.get("candidates", [])
        if candidates:
            thumbnail_url = candidates[0].get("url", "")

    if thumbnail_url:
        embed.set_image(url=thumbnail_url)

    # Inline stats
    likes = getattr(media, "like_count", 0) or 0
    comments = getattr(media, "comment_count", 0) or 0

    if likes or comments:
        embed.add_field(name="❤️ Likes", value=f"{likes:,}", inline=True)
        embed.add_field(name="💬 Comments", value=f"{comments:,}", inline=True)

    # Hashtags in footer
    if hashtag_line:
        embed.set_footer(text=hashtag_line[:200])

    return embed


def build_view(media: Media) -> discord.ui.View:
    """Button linking directly to the Instagram post."""
    shortcode = getattr(media, "code", None) or str(media.pk)
    ig_url = f"https://www.instagram.com/p/{shortcode}/"
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Ver en Instagram",
            emoji="📸",
            url=ig_url,
            style=discord.ButtonStyle.link,
        )
    )
    return view


# ══════════════════════════════════════════════════════════════════════════════
# Cog
# ══════════════════════════════════════════════════════════════════════════════

class InstagramScraperCog(commands.Cog, name="Instagram"):
    """Background task + /preview command for Instagram scraping."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ig = InstagramClient()
        self._first_cycle: bool = True
        self.feed_loop.start()

    def cog_unload(self) -> None:
        self.feed_loop.cancel()

    # ── Background task ───────────────────────────────────────────────────────

    @tasks.loop(minutes=CHECK_INTERVAL)
    async def feed_loop(self) -> None:
        """Poll every feed and post new media to Discord."""
        feeds = await get_all_active_feeds()
        if not feeds:
            return

        await self.ig.ensure_logged_in()

        for feed in feeds:
            try:
                await self._process_feed(feed)
            except Exception as exc:
                log.exception(
                    "[Feed %d] Unhandled error for @%s: %s",
                    feed["id"],
                    feed["instagram_account"],
                    exc,
                )

        if self._first_cycle:
            self._first_cycle = False
            log.info("[FeedLoop] First cycle complete – anti-spam active.")

    @feed_loop.before_loop
    async def before_feed_loop(self) -> None:
        await self.bot.wait_until_ready()
        log.info("[FeedLoop] Starting (interval=%d min).", CHECK_INTERVAL)

    async def _process_feed(self, feed: dict) -> None:
        """Check a single feed and publish any unseen media."""
        account: str = feed["instagram_account"]
        feed_id: int = feed["id"]

        medias = await self.ig.get_recent_medias(account, amount=MAX_POSTS_PER_CYCLE)
        if not medias:
            return

        user_info = await self.ig.get_user_info(account)

        # Sort oldest → newest so we post in chronological order
        medias.sort(key=lambda m: getattr(m, "taken_at", 0))

        for media in medias:
            media_id = str(media.pk)

            # ── Anti-duplicate ──────────────────────────────────────────────
            if await is_already_posted(feed_id, media_id):
                continue

            # ── Skip on first cycle to avoid spam ──────────────────────────
            if self._first_cycle:
                await mark_as_posted(feed_id, media_id)
                continue

            # ── Determine target channel / thread ──────────────────────────
            channel_id = feed.get("thread_id") or feed["channel_id"]
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                log.warning("[Feed %d] Channel %s not found.", feed_id, channel_id)
                continue

            # ── Build and send embed ────────────────────────────────────────
            embed = build_media_embed(media, user_info)
            view = build_view(media)

            # Optional role mention
            content: str | None = None
            if feed.get("role_id"):
                content = f"<@&{feed['role_id']}>"

            try:
                await channel.send(content=content, embed=embed, view=view)
                await mark_as_posted(feed_id, media_id)
                await update_last_media_id(feed_id, media_id)
                log.info("[Feed %d] Posted media %s from @%s", feed_id, media_id, account)
            except discord.Forbidden:
                log.error("[Feed %d] No permission to send in channel %s.", feed_id, channel_id)
            except discord.HTTPException as exc:
                log.error("[Feed %d] Discord HTTP error: %s", feed_id, exc)

    # ── /preview command ──────────────────────────────────────────────────────

    @app_commands.command(
        name="preview",
        description="Preview the latest Instagram post from any public account.",
    )
    @app_commands.describe(username="Instagram username (without @)")
    @app_commands.checks.cooldown(rate=1, per=15.0)
    async def preview(
        self, interaction: discord.Interaction, username: str
    ) -> None:
        await interaction.response.defer(thinking=True)

        username = username.lstrip("@").strip()
        if not username:
            await interaction.followup.send("❌ Please provide a valid username.", ephemeral=True)
            return

        await self.ig.ensure_logged_in()

        medias = await self.ig.get_recent_medias(username, amount=1)
        if not medias:
            await interaction.followup.send(
                f"❌ No posts found for **@{username}** or the account is private.",
                ephemeral=True,
            )
            return

        user_info = await self.ig.get_user_info(username)
        media = medias[0]
        embed = build_media_embed(media, user_info)
        view = build_view(media)

        await interaction.followup.send(embed=embed, view=view)

    @preview.error
    async def preview_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏳ Command on cooldown. Try again in {error.retry_after:.1f}s.",
                ephemeral=True,
            )

    # ── /instagram_status command ─────────────────────────────────────────────

    @app_commands.command(
        name="instagram_status",
        description="Check the current Instagram session status.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def instagram_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        is_auth = await self.ig.ensure_logged_in()
        if is_auth:
            embed = discord.Embed(
                title="✅ Instagram Status",
                description=f"Authenticated as **@{IG_USERNAME}**",
                color=0x00B06B,
            )
        else:
            embed = discord.Embed(
                title="⚠️ Instagram Status",
                description="Not authenticated (guest mode). Set **IG_USERNAME** and **IG_PASSWORD**.",
                color=0xFFA500,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InstagramScraperCog(bot))
