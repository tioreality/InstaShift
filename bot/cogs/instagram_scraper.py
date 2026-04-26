"""
instagram_scraper.py – InstaShift
===================================
Módulo principal de scraping de Instagram.

Responsabilidades
-----------------
* Gestión de sesión persistente (ig_session.json)
* Login autenticado con re-login automático en caso de expiración o challenge
* Obtención de publicaciones y reels recientes de cuentas públicas
* Obtención de Stories (requiere autenticación)
* Comando /preview para previsualizar sin suscripción
* Comando /instagram_status para ver el estado de la sesión
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

# ── Logger del módulo ─────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

# ── Variables de entorno ──────────────────────────────────────────────────────
IG_USERNAME: str = os.getenv("IG_USERNAME", "")
IG_PASSWORD: str = os.getenv("IG_PASSWORD", "")
SESSION_PATH: Path = Path(os.getenv("SESSION_PATH", "ig_session.json"))
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "10"))

# ── Constantes de diseño ──────────────────────────────────────────────────────
# Color rosa característico de Instagram (#E1306C)
IG_COLOR = 0xE1306C

# Máximo de publicaciones a obtener por ciclo (reduce carga en la API)
MAX_POSTS_PER_CYCLE = 5

# Máximo de hashtags a mostrar en el embed
MAX_HASHTAGS = 8


# ══════════════════════════════════════════════════════════════════════════════
# Cliente de Instagram
# ══════════════════════════════════════════════════════════════════════════════

class InstagramClient:
    """
    Wrapper asíncrono y thread-safe alrededor de instagrapi.Client.
    Maneja la sesión persistente y el re-login automático.
    """

    def __init__(self) -> None:
        self._cl: Client = Client()
        self._logged_in: bool = False
        # Lock para evitar múltiples logins simultáneos
        self._lock = asyncio.Lock()

    # ── Helpers de sesión ─────────────────────────────────────────────────────

    def _load_session(self) -> bool:
        """
        Intenta restaurar una sesión previa desde el archivo JSON.
        Retorna True si la sesión fue restaurada exitosamente.
        """
        if not SESSION_PATH.exists():
            return False
        try:
            self._cl.load_settings(SESSION_PATH)
            self._cl.login(IG_USERNAME, IG_PASSWORD)
            self._logged_in = True
            log.info("[IG] Sesión restaurada desde %s", SESSION_PATH)
            return True
        except Exception as exc:
            log.warning("[IG] No se pudo restaurar la sesión (%s). Se hará re-login.", exc)
            return False

    def _save_session(self) -> None:
        """Persiste la sesión actual en el archivo JSON para futuros inicios."""
        SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._cl.dump_settings(SESSION_PATH)
        log.debug("[IG] Sesión guardada en %s", SESSION_PATH)

    # ── Login ─────────────────────────────────────────────────────────────────

    async def ensure_logged_in(self) -> bool:
        """
        Garantiza que el cliente esté autenticado antes de hacer requests.
        Retorna True si el login fue exitoso, False si faltan credenciales.
        """
        async with self._lock:
            # Si ya está autenticado, no hacer nada
            if self._logged_in:
                return True

            # Si no hay credenciales, operar en modo invitado
            if not IG_USERNAME or not IG_PASSWORD:
                log.warning("[IG] Sin credenciales – operando en modo invitado (limitado).")
                return False

            loop = asyncio.get_event_loop()

            # 1. Intentar restaurar sesión previa
            restored = await loop.run_in_executor(None, self._load_session)
            if restored:
                return True

            # 2. Login fresco si no hay sesión válida
            try:
                log.info("[IG] Realizando login fresco como @%s …", IG_USERNAME)
                await loop.run_in_executor(
                    None,
                    lambda: self._cl.login(IG_USERNAME, IG_PASSWORD),
                )
                self._logged_in = True
                await loop.run_in_executor(None, self._save_session)
                log.info("[IG] Login exitoso como @%s.", IG_USERNAME)
                return True
            except BadPassword:
                log.error("[IG] Contraseña incorrecta para @%s.", IG_USERNAME)
            except ChallengeRequired:
                log.error("[IG] Se requiere verificación manual en la app de Instagram.")
            except ReloginAttemptExceeded:
                log.error("[IG] Demasiados intentos de re-login. Espera antes de reintentar.")
            except Exception as exc:
                log.exception("[IG] Error inesperado en el login: %s", exc)

            self._logged_in = False
            return False

    # ── Métodos públicos de obtención de datos ─────────────────────────────────

    async def get_user_info(self, username: str) -> Optional[object]:
        """
        Obtiene información básica del usuario de Instagram.
        Retorna el objeto UserInfo o None si el usuario no existe.
        """
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
            log.warning("[IG] Usuario no encontrado: @%s", username)
        except LoginRequired:
            log.warning("[IG] Se requiere login para obtener info del usuario.")
            self._logged_in = False
        except Exception as exc:
            log.exception("[IG] Error al obtener info de @%s: %s", username, exc)
        return None

    async def get_recent_medias(
        self, username: str, amount: int = MAX_POSTS_PER_CYCLE
    ) -> list[Media]:
        """
        Obtiene las N publicaciones más recientes de un perfil público.
        Retorna lista vacía si hay error o el usuario no existe.
        """
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
            log.warning("[IG] Usuario no encontrado: @%s", username)
        except LoginRequired:
            # Sesión expirada: marcar para re-login en el próximo ciclo
            log.warning("[IG] Sesión expirada – re-autenticando…")
            self._logged_in = False
            await self.ensure_logged_in()
        except Exception as exc:
            log.exception("[IG] Error al obtener publicaciones de @%s: %s", username, exc)
        return []

    async def get_recent_stories(self, username: str) -> list[Media]:
        """
        Obtiene las Stories actuales de un usuario (requiere autenticación).
        Retorna lista vacía si hay error.
        """
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
            log.warning("[IG] Se requiere login para obtener Stories.")
            self._logged_in = False
        except Exception as exc:
            log.exception("[IG] Error al obtener Stories de @%s: %s", username, exc)
        return []

    @property
    def is_authenticated(self) -> bool:
        """Indica si el cliente tiene una sesión activa."""
        return self._logged_in


# ══════════════════════════════════════════════════════════════════════════════
# Constructor de embeds premium
# ══════════════════════════════════════════════════════════════════════════════

def _separar_caption(raw: str) -> tuple[str, list[str]]:
    """
    Separa el caption de los hashtags.

    Retorna una tupla (texto_limpio, lista_de_hashtags).
    El texto limpio no contiene hashtags y está truncado si es muy largo.
    """
    palabras = raw.strip().split()
    texto = []
    tags = []
    for palabra in palabras:
        if palabra.startswith("#"):
            tags.append(palabra)
        else:
            texto.append(palabra)

    caption_limpio = " ".join(texto).strip()
    return caption_limpio, tags


def _construir_linea_stats(media: Media) -> str:
    """
    Genera la línea de estadísticas del embed en formato compacto.
    Ejemplo: ❤️ 1,234  •  💬 56  •  👁️ 12,000
    """
    partes = []

    likes = getattr(media, "like_count", 0) or 0
    if likes:
        partes.append(f"❤️ {likes:,}")

    comentarios = getattr(media, "comment_count", 0) or 0
    if comentarios:
        partes.append(f"💬 {comentarios:,}")

    # Las vistas aplican principalmente a Reels y videos
    vistas = getattr(media, "view_count", 0) or getattr(media, "play_count", 0) or 0
    if vistas:
        partes.append(f"👁️ {vistas:,}")

    return "  •  ".join(partes) if partes else ""


def build_media_embed(media: Media, user_info=None) -> discord.Embed:
    """
    Construye un embed premium y elegante para una publicación de Instagram.

    Diseño del embed
    ----------------
    Autor   : foto de perfil + @usuario (link al perfil)
    Título  : tipo de contenido (📸 Publicación / 🎬 Reel / 📖 Story)
    URL     : enlace directo a la publicación en Instagram
    Imagen  : miniatura o primera imagen en tamaño grande
    Desc.   : caption limpia (sin hashtags) + línea de estadísticas + hashtags clicables
    Footer  : "InstaShift • [fecha]"
    Color   : rosa Instagram #E1306C
    """
    # ── Datos básicos ─────────────────────────────────────────────────────────
    shortcode = getattr(media, "code", None) or str(media.pk)
    ig_url = f"https://www.instagram.com/p/{shortcode}/"

    # ── Tipo de contenido ─────────────────────────────────────────────────────
    media_type = getattr(media, "media_type", 1)
    if media_type == 2:
        tipo = "🎬 Reel"
    elif getattr(media, "is_story", False):
        tipo = "📖 Story"
    else:
        tipo = "📸 Publicación"

    # ── Procesar caption ──────────────────────────────────────────────────────
    raw_caption = getattr(media, "caption_text", "") or ""
    caption_limpio, todos_los_tags = _separar_caption(raw_caption)

    # Truncar caption a 1500 chars para dejar espacio a estadísticas y tags
    if len(caption_limpio) > 1500:
        caption_limpio = caption_limpio[:1497] + "…"

    # ── Línea de estadísticas ─────────────────────────────────────────────────
    linea_stats = _construir_linea_stats(media)

    # ── Hashtags clicables (máximo MAX_HASHTAGS) ──────────────────────────────
    # Los hashtags se convierten en links clicables hacia Instagram
    tags_display = todos_los_tags[:MAX_HASHTAGS]
    hashtags_clicables = "  ".join(
        f"[{t}](https://www.instagram.com/explore/tags/{t.lstrip('#')}/)"
        for t in tags_display
    )
    # Si hay más tags de los mostrados, indicar cuántos se omitieron
    if len(todos_los_tags) > MAX_HASHTAGS:
        hashtags_clicables += f"  *+{len(todos_los_tags) - MAX_HASHTAGS} más*"

    # ── Armar descripción completa ─────────────────────────────────────────────
    partes_desc = []
    if caption_limpio:
        partes_desc.append(caption_limpio)
    if linea_stats:
        partes_desc.append(f"\n{linea_stats}")
    if hashtags_clicables:
        partes_desc.append(f"\n\n{hashtags_clicables}")

    descripcion_final = "".join(partes_desc)[:4096]  # Límite de Discord

    # ── Construir el embed ────────────────────────────────────────────────────
    embed = discord.Embed(
        title=tipo,
        description=descripcion_final or None,
        url=ig_url,
        color=IG_COLOR,
        timestamp=getattr(media, "taken_at", None),
    )

    # ── Autor con foto de perfil ──────────────────────────────────────────────
    if user_info:
        username = getattr(user_info, "username", "instagram")
        nombre_completo = getattr(user_info, "full_name", "") or f"@{username}"
        avatar_url = str(getattr(user_info, "profile_pic_url", "") or "")
        embed.set_author(
            name=f"@{username}  •  {nombre_completo}" if nombre_completo != f"@{username}" else f"@{username}",
            url=f"https://www.instagram.com/{username}/",
            icon_url=avatar_url if avatar_url else discord.Embed.Empty,
        )

    # ── Imagen grande prominente ──────────────────────────────────────────────
    # Buscar la URL de la imagen en orden de prioridad
    thumbnail_url: str = ""

    # 1. thumbnail_url (disponible en reels y algunos posts)
    if hasattr(media, "thumbnail_url") and media.thumbnail_url:
        thumbnail_url = str(media.thumbnail_url)

    # 2. image_versions2 (lista de candidatos, tomar el de mayor resolución)
    if not thumbnail_url and hasattr(media, "image_versions2") and media.image_versions2:
        candidatos = media.image_versions2.get("candidates", [])
        if candidatos:
            # El primer candidato suele ser el de mayor resolución
            thumbnail_url = candidatos[0].get("url", "")

    # 3. resources (carruseles con múltiples imágenes, tomar la primera)
    if not thumbnail_url and hasattr(media, "resources") and media.resources:
        primer_recurso = media.resources[0]
        if hasattr(primer_recurso, "thumbnail_url") and primer_recurso.thumbnail_url:
            thumbnail_url = str(primer_recurso.thumbnail_url)

    if thumbnail_url:
        embed.set_image(url=thumbnail_url)

    # ── Footer limpio ─────────────────────────────────────────────────────────
    embed.set_footer(
        text="InstaShift • Instagram",
        icon_url="https://www.instagram.com/favicon.ico",
    )

    return embed


def build_view(media: Media) -> discord.ui.View:
    """
    Crea una vista con el botón de enlace directo a la publicación de Instagram.
    El botón usa el estilo de enlace (gris por defecto en Discord).
    """
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
# Cog principal
# ══════════════════════════════════════════════════════════════════════════════

class InstagramScraperCog(commands.Cog, name="Instagram"):
    """Tarea en segundo plano + comandos /preview e /instagram_status."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ig = InstagramClient()
        # Bandera para el primer ciclo (anti-spam al arrancar el bot)
        self._primer_ciclo: bool = True
        self.feed_loop.start()

    def cog_unload(self) -> None:
        """Detiene el loop al descargar el cog."""
        self.feed_loop.cancel()

    # ── Tarea periódica de verificación de feeds ───────────────────────────────

    @tasks.loop(minutes=CHECK_INTERVAL)
    async def feed_loop(self) -> None:
        """
        Revisa todos los feeds activos y publica el contenido nuevo en Discord.
        Se ejecuta cada CHECK_INTERVAL minutos (por defecto 10).
        """
        feeds = await get_all_active_feeds()
        if not feeds:
            return  # No hay feeds activos, saltar el ciclo

        # Asegurar sesión antes de empezar a scrapear
        await self.ig.ensure_logged_in()

        for feed in feeds:
            try:
                await self._procesar_feed(feed)
            except Exception as exc:
                log.exception(
                    "[Feed %d] Error inesperado para @%s: %s",
                    feed["id"],
                    feed["instagram_account"],
                    exc,
                )

        # Marcar que el primer ciclo ya terminó
        if self._primer_ciclo:
            self._primer_ciclo = False
            log.info("[FeedLoop] Primer ciclo completado – anti-spam activo a partir de ahora.")

    @feed_loop.before_loop
    async def before_feed_loop(self) -> None:
        """Espera a que el bot esté listo antes de iniciar el loop."""
        await self.bot.wait_until_ready()
        log.info("[FeedLoop] Iniciado (intervalo=%d min).", CHECK_INTERVAL)

    async def _procesar_feed(self, feed: dict) -> None:
        """
        Procesa un feed individual: obtiene publicaciones nuevas y las envía a Discord.

        Lógica anti-duplicados
        ----------------------
        1. En el primer ciclo, se marcan todas las publicaciones como "ya publicadas"
           sin enviarlas, para evitar spam al iniciar el bot.
        2. En ciclos siguientes, se verifica la tabla posted_media antes de publicar.
        """
        account: str = feed["instagram_account"]
        feed_id: int = feed["id"]

        # Obtener publicaciones recientes de Instagram
        medias = await self.ig.get_recent_medias(account, amount=MAX_POSTS_PER_CYCLE)
        if not medias:
            return

        # Obtener info del perfil para el embed (foto de perfil, nombre completo)
        user_info = await self.ig.get_user_info(account)

        # Ordenar de más antiguo a más nuevo para publicar en orden cronológico
        medias.sort(key=lambda m: getattr(m, "taken_at", 0))

        for media in medias:
            media_id = str(media.pk)

            # ── Verificar si ya fue publicada ─────────────────────────────────
            if await is_already_posted(feed_id, media_id):
                continue

            # ── Anti-spam en el primer ciclo ──────────────────────────────────
            if self._primer_ciclo:
                # Marcar como vista sin publicar (evita spam al iniciar)
                await mark_as_posted(feed_id, media_id)
                continue

            # ── Determinar canal de destino (hilo o canal normal) ─────────────
            channel_id = feed.get("thread_id") or feed["channel_id"]
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                log.warning("[Feed %d] Canal %s no encontrado.", feed_id, channel_id)
                continue

            # ── Construir y enviar el embed ───────────────────────────────────
            embed = build_media_embed(media, user_info)
            view = build_view(media)

            # Mención de rol opcional
            contenido: str | None = None
            if feed.get("role_id"):
                contenido = f"<@&{feed['role_id']}>"

            try:
                await channel.send(content=contenido, embed=embed, view=view)
                await mark_as_posted(feed_id, media_id)
                await update_last_media_id(feed_id, media_id)
                log.info(
                    "[Feed %d] Publicado: media %s de @%s",
                    feed_id, media_id, account,
                )
            except discord.Forbidden:
                log.error(
                    "[Feed %d] Sin permisos para enviar en el canal %s.",
                    feed_id, channel_id,
                )
            except discord.HTTPException as exc:
                log.error("[Feed %d] Error HTTP de Discord: %s", feed_id, exc)

    # ── Comando /preview ───────────────────────────────────────────────────────

    @app_commands.command(
        name="preview",
        description="Previsualiza la última publicación de cualquier cuenta pública de Instagram.",
    )
    @app_commands.describe(username="Usuario de Instagram (sin @)")
    @app_commands.checks.cooldown(rate=1, per=15.0)
    async def preview(
        self, interaction: discord.Interaction, username: str
    ) -> None:
        """Muestra la publicación más reciente de una cuenta sin necesidad de suscripción."""
        await interaction.response.defer(thinking=True)

        username = username.lstrip("@").strip()
        if not username:
            await interaction.followup.send(
                "❌ Por favor ingresa un nombre de usuario válido.", ephemeral=True
            )
            return

        # Asegurar sesión activa antes de scrapear
        await self.ig.ensure_logged_in()

        medias = await self.ig.get_recent_medias(username, amount=1)
        if not medias:
            await interaction.followup.send(
                f"❌ No se encontraron publicaciones para **@{username}**. "
                "El usuario no existe o su cuenta es privada.",
                ephemeral=True,
            )
            return

        # Obtener info del perfil para el embed
        user_info = await self.ig.get_user_info(username)
        media = medias[0]

        embed = build_media_embed(media, user_info)
        view = build_view(media)

        await interaction.followup.send(embed=embed, view=view)

    @preview.error
    async def preview_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Manejo de errores para el comando /preview."""
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏳ Comando en cooldown. Intenta de nuevo en {error.retry_after:.1f}s.",
                ephemeral=True,
            )

    # ── Comando /instagram_status ──────────────────────────────────────────────

    @app_commands.command(
        name="instagram_status",
        description="Muestra el estado actual de la sesión de Instagram.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def instagram_status(self, interaction: discord.Interaction) -> None:
        """Informa si el bot está autenticado en Instagram o en modo invitado."""
        await interaction.response.defer(ephemeral=True)

        is_auth = await self.ig.ensure_logged_in()

        if is_auth:
            embed = discord.Embed(
                title="✅ Instagram – Conectado",
                description=(
                    f"Autenticado como **@{IG_USERNAME}**\n"
                    f"Sesión persistida en `{SESSION_PATH.name}`"
                ),
                color=0x00B06B,  # Verde
            )
            embed.set_footer(text="La sesión se renueva automáticamente al expirar.")
        else:
            embed = discord.Embed(
                title="⚠️ Instagram – Modo invitado",
                description=(
                    "No hay credenciales configuradas.\n"
                    "Establece **IG_USERNAME** e **IG_PASSWORD** en tu archivo `.env`."
                ),
                color=0xFFA500,  # Naranja
            )
            embed.set_footer(text="El modo invitado tiene acceso limitado.")

        await interaction.followup.send(embed=embed, ephemeral=True)


# ── Función de configuración del cog ──────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    """Registra el cog en el bot. Llamada automáticamente por load_extension()."""
    await bot.add_cog(InstagramScraperCog(bot))
