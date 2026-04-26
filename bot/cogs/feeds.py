"""
feeds.py – InstaShift
======================
All subscription-management slash commands.

Commands
--------
/follow      – Subscribe a channel to an Instagram account
/unfollow    – Remove a subscription
/list        – Show active feeds in this server
/dashboard   – Rich embed overview of all feeds
/checknow    – Force an immediate feed check
/sync        – Re-sync slash commands (admin)
/sync clear  – Remove all slash commands (admin)
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import add_feed, get_feeds, remove_feed

log = logging.getLogger(__name__)

# Instagram brand pink
IG_COLOR = 0xE1306C
SUCCESS_COLOR = 0x00B06B
ERROR_COLOR = 0xFF4444
INFO_COLOR = 0x5865F2


def _feed_to_line(idx: int, feed: dict) -> str:
    """Format a single feed row for list/dashboard embeds."""
    channel = f"<#{feed['channel_id']}>"
    thread = f" → <#{feed['thread_id']}>" if feed.get("thread_id") else ""
    role = f" | <@&{feed['role_id']}>" if feed.get("role_id") else ""
    return f"**{idx}.** [@{feed['instagram_account']}](https://instagram.com/{feed['instagram_account']}) {channel}{thread}{role}"


# ══════════════════════════════════════════════════════════════════════════════
# Cog
# ══════════════════════════════════════════════════════════════════════════════

class FeedsCog(commands.Cog, name="Feeds"):
    """Slash commands for managing Instagram → Discord feed subscriptions."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /follow ───────────────────────────────────────────────────────────────

    @app_commands.command(
        name="follow",
        description="Subscribe a channel to an Instagram account's posts.",
    )
    @app_commands.describe(
        username="Instagram username to follow (without @)",
        channel="Discord channel to post updates in (defaults to current)",
        thread="Optional thread to post in instead of the channel",
        role="Optional role to mention on each new post",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def follow(
        self,
        interaction: discord.Interaction,
        username: str,
        channel: Optional[discord.TextChannel] = None,
        thread: Optional[discord.Thread] = None,
        role: Optional[discord.Role] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        username = username.lstrip("@").strip().lower()
        if not username:
            await interaction.followup.send("❌ Invalid username.", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("❌ Please select a valid text channel.", ephemeral=True)
            return

        feed_id = await add_feed(
            guild_id=interaction.guild_id,
            instagram_account=username,
            channel_id=target_channel.id,
            thread_id=thread.id if thread else None,
            role_id=role.id if role else None,
        )

        if not feed_id:
            await interaction.followup.send(
                f"⚠️ **@{username}** is already being followed in {target_channel.mention}.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="✅ Feed Added",
            color=SUCCESS_COLOR,
            description=(
                f"Now following **[@{username}](https://instagram.com/{username})**\n"
                f"Posts will appear in {target_channel.mention}"
                + (f" → {thread.mention}" if thread else "")
                + (f"\nMention: {role.mention}" if role else "")
            ),
        )
        embed.set_footer(text="Updates check every 10 minutes.")
        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info(
            "[Feeds] Guild %s followed @%s → channel %s",
            interaction.guild_id, username, target_channel.id,
        )

    # ── /unfollow ─────────────────────────────────────────────────────────────

    @app_commands.command(
        name="unfollow",
        description="Stop following an Instagram account in a channel.",
    )
    @app_commands.describe(
        username="Instagram username to unfollow (without @)",
        channel="Channel to remove the subscription from (defaults to current)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def unfollow(
        self,
        interaction: discord.Interaction,
        username: str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        username = username.lstrip("@").strip().lower()
        target_channel = channel or interaction.channel

        deleted = await remove_feed(
            guild_id=interaction.guild_id,
            instagram_account=username,
            channel_id=target_channel.id,
        )

        if deleted:
            embed = discord.Embed(
                title="🗑️ Feed Removed",
                description=f"Stopped following **@{username}** in {target_channel.mention}.",
                color=ERROR_COLOR,
            )
        else:
            embed = discord.Embed(
                title="⚠️ Not Found",
                description=f"No active subscription for **@{username}** in {target_channel.mention}.",
                color=INFO_COLOR,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /list ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="list",
        description="List all active Instagram feed subscriptions in this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_feeds(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        feeds = await get_feeds(interaction.guild_id)
        if not feeds:
            await interaction.followup.send(
                "📭 No active subscriptions. Use **/follow** to add one.", ephemeral=True
            )
            return

        lines = [_feed_to_line(i + 1, f) for i, f in enumerate(feeds)]
        embed = discord.Embed(
            title=f"📋 Active Feeds ({len(feeds)})",
            description="\n".join(lines),
            color=IG_COLOR,
        )
        embed.set_footer(text="Use /unfollow to remove a subscription.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /dashboard ────────────────────────────────────────────────────────────

    @app_commands.command(
        name="dashboard",
        description="Show a rich overview of all Instagram feeds for this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dashboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=False)

        feeds = await get_feeds(interaction.guild_id)
        guild = interaction.guild

        embed = discord.Embed(
            title="📊 InstaShift Dashboard",
            color=IG_COLOR,
        )

        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        if not feeds:
            embed.description = "No active feeds. Use **/follow** to get started!"
        else:
            embed.description = f"**{len(feeds)}** active subscription{'s' if len(feeds) != 1 else ''} in **{guild.name if guild else 'this server'}**."
            for feed in feeds:
                channel = f"<#{feed['channel_id']}>"
                thread = f" → <#{feed['thread_id']}>" if feed.get("thread_id") else ""
                role = f"\nMention: <@&{feed['role_id']}>" if feed.get("role_id") else ""
                last = feed.get("last_media_id") or "—"
                embed.add_field(
                    name=f"📸 @{feed['instagram_account']}",
                    value=f"Channel: {channel}{thread}{role}\nLast seen: `{last[:20]}`",
                    inline=True,
                )

        embed.set_footer(text="Updates check every 10 minutes • /checknow to force a check")
        await interaction.followup.send(embed=embed)

    # ── /checknow ─────────────────────────────────────────────────────────────

    @app_commands.command(
        name="checknow",
        description="Force an immediate check of all Instagram feeds.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def checknow(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        # Trigger the scraper's feed loop manually
        scraper_cog = self.bot.cogs.get("Instagram")
        if scraper_cog is None:
            await interaction.followup.send("❌ Scraper cog not loaded.", ephemeral=True)
            return

        await interaction.followup.send(
            "🔄 Checking feeds now… results will appear in their respective channels.",
            ephemeral=True,
        )
        # Run the feed loop task body directly (non-blocking)
        self.bot.loop.create_task(scraper_cog.feed_loop())  # type: ignore[attr-defined]

    # ── /sync ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="sync",
        description="[Admin] Sync slash commands. Use 'clear' to wipe all commands.",
    )
    @app_commands.describe(mode="Leave empty to sync, type 'clear' to remove all commands.")
    @app_commands.checks.has_permissions(administrator=True)
    async def sync(
        self,
        interaction: discord.Interaction,
        mode: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if mode and mode.strip().lower() == "clear":
            self.bot.tree.clear_commands(guild=interaction.guild)
            await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(
                "🧹 All guild commands cleared. Global commands may still be cached.",
                ephemeral=True,
            )
            return

        # Smart sync: guild first, then global
        synced_guild = await self.bot.tree.sync(guild=interaction.guild)
        synced_global = await self.bot.tree.sync()
        await interaction.followup.send(
            f"✅ Synced **{len(synced_guild)}** guild commands and "
            f"**{len(synced_global)}** global commands.",
            ephemeral=True,
        )
        log.info(
            "[Sync] %d guild + %d global commands synced by %s",
            len(synced_guild), len(synced_global), interaction.user,
        )

    # ── Error handlers ────────────────────────────────────────────────────────

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "❌ You need **Manage Server** permission to use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Cooldown! Retry in {error.retry_after:.1f}s."
        elif isinstance(error, app_commands.BotMissingPermissions):
            msg = "❌ I'm missing required permissions in this channel."
        else:
            log.exception("Unhandled app command error: %s", error)
            msg = f"❌ An unexpected error occurred: {error}"

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FeedsCog(bot))
