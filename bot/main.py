"""
main.py – InstaShift
====================
Punto de entrada del bot. Carga los cogs, inicializa la base de datos
y conecta el bot a Discord.

Uso:
    python -m bot.main
    bash run.sh
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.database import init_db

# ── Cargar variables de entorno desde .env ────────────────────────────────────────────────────
load_dotenv()

# ── Configuración de logging ───────────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("instashift")

# ── Constantes de configuración ────────────────────────────────────────────────────────────────────────────
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")

# Si GUILD_ID está definido, los comandos se sincronizan solo en ese servidor
# (ideal para desarrollo: instantáneo). En producción déjalo vacío o úsalo
# para que todos los comandos aparezcan de inmediato en tu servidor.
GUILD_ID_RAW: str = os.getenv("GUILD_ID", "")
TEST_GUILD: discord.Object | None = (
    discord.Object(id=int(GUILD_ID_RAW)) if GUILD_ID_RAW.strip() else None
)

# Lista de extensiones (cogs) a cargar al iniciar el bot.
# IMPORTANTE: Cada archivo DEBE tener una función "async def setup(bot)" al final.
EXTENSIONS: list[str] = [
    "bot.cogs.instagram_scraper",  # Scraper + /preview + /instagram_status
    "bot.cogs.feeds",              # /follow /unfollow /list /dashboard /checknow /sync
]


# ══════════════════════════════════════════════════════════════════════════════
# Clase principal del bot
# ══════════════════════════════════════════════════════════════════════════════

class InstaShift(commands.Bot):
    """Bot personalizado con setup asíncrono y manejo de ciclo de vida."""

    def __init__(self) -> None:
        # Intents mínimos necesarios para el bot
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True

        super().__init__(
            command_prefix=commands.when_mentioned,  # Solo slash commands
            intents=intents,
            help_command=None,
            description="📸 Espeja feeds de Instagram en canales de Discord.",
        )

    # ── Ciclo de vida ───────────────────────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """
        Se ejecuta UNA VEZ antes de conectar a Discord.

        Orden crítico:
        1. Inicializar BD (los cogs la necesitan)
        2. Cargar cada cog (registran comandos en self.tree)
        3. Sincronizar comandos (después de cargar, para incluirlos todos)
        """
        # 1. Inicializar la base de datos SQLite
        log.info("⏳ Inicializando base de datos...")
        await init_db()
        log.info("✅ Base de datos lista.")

        # 2. Cargar cada cog con logging detallado de errores
        cogs_cargados = 0
        cogs_fallados = 0
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info("✅ Extensión cargada: %s", ext)
                cogs_cargados += 1
            except Exception as exc:
                cogs_fallados += 1
                # Log detallado para diagnóstico: muestra el traceback completo
                log.error(
                    "❌ ERROR al cargar la extensión '%s':\n"
                    "  Tipo  : %s\n"
                    "  Error : %s\n"
                    "  Traza : %s",
                    ext,
                    type(exc).__name__,
                    exc,
                    "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                )

        log.info(
            "📦 Cogs: %d cargados, %d fallados (de %d total).",
            cogs_cargados, cogs_fallados, len(EXTENSIONS),
        )

        # 3. Sincronizar comandos slash
        if TEST_GUILD:
            # Modo desarrollo/servidor único: sync instantáneo
            self.tree.copy_global_to(guild=TEST_GUILD)
            synced = await self.tree.sync(guild=TEST_GUILD)
            log.info(
                "✅ Comandos sincronizados en servidor %s (%d comandos). "
                "Visibles inmediatamente.",
                TEST_GUILD.id, len(synced),
            )
        else:
            # Modo global: puede tardar hasta 1 hora en propagarse a Discord
            synced = await self.tree.sync()
            log.info(
                "✅ Comandos sincronizados globalmente (%d comandos). "
                "Pueden tardar hasta 1 hora. Configura GUILD_ID para ser instantáneo.",
                len(synced),
            )

    async def on_ready(self) -> None:
        """Se ejecuta cuando el bot está conectado y listo para recibir eventos."""
        log.info("=" * 60)
        log.info("  📸 InstaShift está en línea!")
        log.info("  Usuario    : %s (ID: %s)", self.user, self.user.id)
        log.info("  Servidores : %d", len(self.guilds))
        log.info("  Cogs       : %s", ", ".join(self.cogs.keys()) or "ninguno")
        log.info("  discord.py : %s", discord.__version__)
        log.info("=" * 60)

        # Establecer presencia del bot en Discord
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="feeds de Instagram 📸",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Log cuando el bot ingresa a un nuevo servidor."""
        log.info("Ingresé al servidor: %s (ID: %s)", guild.name, guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Log cuando el bot es removido de un servidor."""
        log.info("Fui removido del servidor: %s (ID: %s)", guild.name, guild.id)


# ══════════════════════════════════════════════════════════════════════════════
# Punto de entrada principal
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    """Función principal que inicia el bot."""
    if not DISCORD_TOKEN:
        log.critical(
            "❌ DISCORD_TOKEN no está configurado. "
            "Añádelo en las variables de entorno o en el archivo .env"
        )
        sys.exit(1)

    async with InstaShift() as bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot detenido por el usuario (KeyboardInterrupt).")
