"""
instagram_scraper.py – InstaShift
===================================
Módulo principal de scraping de Instagram para el bot de Discord.

Responsabilidades
-----------------
* Gestión de sesión persistente (ig_session.json)
* Login autenticado con re-login automático en caso de expiración o challenge
* Obtención de publicaciones y reels recientes de cuentas públicas
* Obtención de Stories (requiere autenticación)
* Comando /preview para previsualizar sin suscripción
* Comando /instagram_status para ver el estado de la sesión de Instagram

Notas de mantenimiento
-----------------------
* El cog se registra mediante setup() al final del archivo (requerido por load_extension)
* FeedLoop se inicia en __init__ y se cancela en cog_unload para evitar memory leaks
* El primer ciclo siempre marca sin publicar (anti-spam al arrancar)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
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

# ── Logger del módulo ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

# ── Variables de entorno ───────────────────────────────────────────────────────────────────────
IG_USERNAME: str = os.getenv("IG_USERNAME", "")
IG_PASSWORD: str = os.getenv("IG_PASSWORD", "")
SESSION_PATH: Path = Path(os.getenv("SESSION_PATH", "ig_session.json"))

# Intervalo de polling en minutos (por defecto cada 10 minutos)
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "10"))

# ── Constantes de diseño ──────────────────────────────────────────────────────────────────────────
# Color rosa Instagram (#E1306C) — borde izquierdo del embed
IG_COLOR = 0xE1306C

# Máximo de publicaciones a obtener por ciclo (para no saturar la API)
MAX_POSTS_PER_CYCLE = 5

# Máximo de hashtags clicables a mostrar en el embed
MAX_HASHTAGS = 8


# ══════════════════════════════════════════════════════════════════════════════
# Cliente de Instagram — wrapper sobre instagrapi.Client
# ══════════════════════════════════════════════════════════════════════════════

class InstagramClient:
    """
    Wrapper thread-safe sobre instagrapi.Client con persistencia de sesión.

    Toda operación bloqueante de instagrapi se ejecuta en un executor
    para no bloquear el event loop de asyncio.
    """

    def __init__(self) -> None:
        # Cliente de instagrapi (operaciones sincónicas)
        self._cl: Client = Client()
        # Flag de estado de autenticación
        self._logged_in: bool = False
        # Lock para evitar logins concurrentes simultáneos
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Sesión persistente ───────────────────────────────────────────────────────────────────────────

    def _load_session(self) -> bool:
        """
        Intenta cargar la sesión guardada desde disco.
        Retorna True si la sesión se cargó y verificó correctamente.
        """
        if not SESSION_PATH.exists():
            return False
        try:
            self._cl.load_settings(SESSION_PATH)
            # Verificar que la sesión cargada sigue siendo válida
            self._cl.get_timeline_feed()
            self._logged_in = True
            log.info("[IG] Sesión restaurada desde %s", SESSION_PATH)
            return True
        except LoginRequired:
            log.warning("[IG] Sesión expirada, se requiere re-login.")
            return False
        except Exception as exc:
            log.warning("[IG] No se pudo restaurar la sesión: %s", exc)
            return False

    def _save_session(self) -> None:
        """Persiste la sesión actual en disco para futuros reinicios."""
        SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._cl.dump_settings(SESSION_PATH)
        log.debug("[IG] Sesión guardada en %s", SESSION_PATH)

    # ── Login ─────────────────────────────────────────────────────────────────────────────────────

    async def ensure_logged_in(self) -> bool:
        """
        Garantiza que el cliente esté autenticado antes de hacer requests.

        Flujo:
        1. Si ya está autenticado → retorna True inmediatamente.
        2. Si hay sesión guardada válida → la restaura.
        3. Si no → hace login fresco con usuario/contraseña.

        Retorna True si el login fue exitoso, False si faltan credenciales.
        """
        async with self._lock:
            # Ya autenticado — nada que hacer
            if self._logged_in:
                return True

            # Sin credenciales → modo invitado (acceso limitado)
            if not IG_USERNAME or not IG_PASSWORD:
                log.warning("[IG] Sin credenciales – operando en modo invitado (limitado).")
                return False

            loop = asyncio.get_event_loop()

            # 1. Intentar restaurar sesión guardada
            restored = await loop.run_in_executor(None, self._load_session)
            if restored:
                return True

            # 2. Login fresco con credenciales
            try:
                log.info("[IG] Iniciando sesión como @%s …", IG_USERNAME)
                await loop.run_in_executor(
                    None,
                    lambda: self._cl.login(IG_USERNAME, IG_PASSWORD),
                )
                self._logged_in = True
                await loop.run_in_executor(None, self._save_session)
                log.info("[IG] Login exitoso.")
                return True
            except BadPassword:
                log.error("[IG] Contraseña incorrecta para @%s.", IG_USERNAME)
            except ChallengeRequired:
                log.error("[IG] Se requiere verificación de challenge (2FA u otro).")
            except ReloginAttemptExceeded:
                log.error("[IG] Demasiados intentos de re-login. Espera antes de reintentar.")
            except Exception as exc:
                log.exception("[IG] Error inesperado durante el login: %s", exc)

            return False

    # ── Obtención de datos ────────────────────────────────────────────────────────────────────────

    async def get_user_info(self, username: str) -> Optional[UserShort]:
        """
        Obtiene la información básica de un perfil público.
        Retorna None si hay error o el usuario no existe.
        """
        await self.ensure_logged_in()
        loop = asyncio.get_event_loop()
        try:
            user = await loop.run_in_executor(
                None, lambda: self._cl.user_info_by_username(username)
            )
            return user
        except UserNotFound:
            log.warning("[IG] Usuario @%s no encontrado.", username)
        except Exception as exc:
            log.exception("[IG] Error al obtener info de @%s: %s", username, exc)
        return None

    async def get_recent_medias(self, username: str, amount: int = 5) -> list[Media]:
        """
        Obtiene las publicaciones más recientes de una cuenta pública.
        Retorna lista vacía si hay error.
        """
        await self.ensure_logged_in()
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
            log.warning("[IG] Usuario @%s no encontrado.", username)
        except MediaNotFound:
            log.warning("[IG] No se encontraron publicaciones para @%s.", username)
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
# Constructor de embeds premium — estilo TweetShift / REALITY
# ══════════════════════════════════════════════════════════════════════════════

def _separar_caption(raw: str) -> tuple[str, list[str]]:
    """
    Separa el caption de Instagram en texto limpio y lista de hashtags.

    Retorna:
        (texto_sin_hashtags, [lista_de_hashtags])

    El texto limpio se usa en el cuerpo del embed; los hashtags se muestran
    como enlaces clicables al final de la descripción.
    """
    palabras = raw.strip().split()
    texto: list[str] = []
    tags: list[str] = []

    for palabra in palabras:
        if palabra.startswith("#"):
            # Separar hashtags del texto principal
            tags.append(palabra)
        else:
            texto.append(palabra)

    caption_limpio = " ".join(texto).strip()
    return caption_limpio, tags


def _formatear_numero(n: int) -> str:
    """
    Formatea un número grande en formato compacto K/M.

    Ejemplos:
        1234    → "1.2K"
        1500000 → "1.5M"
        999     → "999"
    """
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _construir_linea_stats(media: Media) -> str:
    """
    Genera la línea de estadísticas del embed en una sola línea compacta.

    Formato: ❤️ 1.2K  •  💬 56  •  👁️ 12K

    Las estadísticas se formatean en K/M para mayor legibilidad.
    Solo se muestran las métricas disponibles (no todas las publicaciones tienen vistas).
    """
    partes: list[str] = []

    # Likes (corazones)
    likes = getattr(media, "like_count", 0) or 0
    if likes:
        partes.append(f"❤️ {_formatear_numero(likes)}")

    # Comentarios
    comentarios = getattr(media, "comment_count", 0) or 0
    if comentarios:
        partes.append(f"💬 {_formatear_numero(comentarios)}")

    # Vistas — disponibles principalmente en Reels y videos
    vistas = getattr(media, "view_count", 0) or 0
    if vistas:
        partes.append(f"👁️ {_formatear_numero(vistas)}")

    # Separar con bullet point para una sola línea limpia
    return "  •  ".join(partes)


def build_media_embed(media: Media, user_info: Optional[UserShort] = None) -> discord.Embed:
    """
    Construye un embed premium de Discord para una publicación de Instagram.

    Diseño del embed (estilo TweetShift/REALITY):
    ───────────────────────────────────────────────
    Author  : 📸 foto de perfil + "@username"
    Title   : "📸 Nueva publicación de @username" (o 🎥 Reel, 📖 Story)
    URL     : enlace directo a la publicación
    Descripción:
        · Caption limpio (sin hashtags)
        · Línea de stats: ❤️ Likes  •  💬 Comentarios  •  👁️ Vistas
        · Hashtags clicables al final (máximo 8)
    Image   : imagen/thumbnail prominente
    Color   : rosa Instagram #E1306C (borde izquierdo)
    Footer  : "InstaShift • Instagram • hora actual"
    """
    # ── URL de la publicación ─────────────────────────────────────────────────────────────────────────────
    shortcode = getattr(media, "code", None) or str(media.pk)
    ig_url = f"https://www.instagram.com/p/{shortcode}/"

    # ── Tipo de contenido (Post / Reel / Story) ───────────────────────────────────────────
    media_type = getattr(media, "media_type", 1)
    if media_type == 2:
        # Tipo 2 = video/reel en la API de Instagram
        tipo = "🎥 Reel"
    elif getattr(media, "is_story", False):
        tipo = "📖 Story"
    else:
        # Tipo 1 = imagen estándar (post normal o carrusel)
        tipo = "📸 Publicación"

    # ── Username para el título ──────────────────────────────────────────────────────────────────────
    username_display = ""
    if user_info:
        username_display = getattr(user_info, "username", "") or ""

    # Título claro con tipo y cuenta — estilo "Nueva publicación de @usuario"
    if username_display:
        titulo = f"{tipo.split()[0]} Nueva publicación de @{username_display}"
    else:
        titulo = f"{tipo} — Nueva publicación"

    # ── Procesar caption: separar texto de hashtags ───────────────────────────────────────────
    raw_caption = getattr(media, "caption_text", "") or ""
    caption_limpio, todos_los_tags = _separar_caption(raw_caption)

    # Truncar caption para dejar espacio a estadísticas y hashtags
    if len(caption_limpio) > 1500:
        caption_limpio = caption_limpio[:1497] + "…"

    # ── Línea de estadísticas (❤️ • 💬 • 👁️) ──────────────────────────────────────────────
    linea_stats = _construir_linea_stats(media)

    # ── Hashtags clicables (máximo MAX_HASHTAGS) ────────────────────────────────────────────
    # Limitamos a 8 para no saturar el embed
    tags_display = todos_los_tags[:MAX_HASHTAGS]
    hashtags_clicables = ""

    if tags_display:
        # Cada hashtag enlaza al explorador de hashtags de Instagram
        hashtags_clicables = "  ".join(
            f"[{t}](https://www.instagram.com/explore/tags/{t.lstrip('#')}/)"
            for t in tags_display
        )
        # Indicar cuántos hashtags adicionales fueron omitidos
        if len(todos_los_tags) > MAX_HASHTAGS:
            hashtags_clicables += f"  *+{len(todos_los_tags) - MAX_HASHTAGS} más*"

    # ── Armar descripción completa del embed ────────────────────────────────────────────────
    # Orden: caption → estadísticas → hashtags clicables
    partes_desc: list[str] = []
    if caption_limpio:
        partes_desc.append(caption_limpio)
    if linea_stats:
        # Línea de stats separada con salto de línea
        partes_desc.append(f"\n{linea_stats}")
    if hashtags_clicables:
        # Hashtags separados del resto para mayor claridad visual
        partes_desc.append(f"\n\n{hashtags_clicables}")

    # Discord limita la descripción a 4096 caracteres
    descripcion_final = "".join(partes_desc)[:4096]

    # ── Construir el embed con color rosa Instagram ───────────────────────────────────────────
    embed = discord.Embed(
        title=titulo,
        description=descripcion_final or None,
        url=ig_url,
        color=IG_COLOR,  # Rosa Instagram #E1306C — borde izquierdo del embed
        timestamp=getattr(media, "taken_at", None),
    )

    # ── Author icon: foto de perfil como icono pequeño ────────────────────────────────────────────
    if user_info:
        username = getattr(user_info, "username", "instagram")
        nombre_completo = getattr(user_info, "full_name", "") or f"@{username}"
        avatar_url = str(getattr(user_info, "profile_pic_url", "") or "")

        # Mostrar nombre completo si es diferente al username
        nombre_author = (
            f"@{username}  •  {nombre_completo}"
            if nombre_completo != f"@{username}"
            else f"@{username}"
        )

        embed.set_author(
            name=nombre_author,
            url=f"https://www.instagram.com/{username}/",
            # La foto de perfil aparece como icono circular junto al nombre
            icon_url=avatar_url if avatar_url else discord.Embed.Empty,
        )

    # ── Imagen grande prominente (la foto de la publicación) ────────────────────────────────
    thumbnail_url: str = ""

    # Prioridad 1: thumbnail_url — disponible en reels y algunos posts
    if hasattr(media, "thumbnail_url") and media.thumbnail_url:
        thumbnail_url = str(media.thumbnail_url)

    # Prioridad 2: image_versions2 — lista de candidatos, tomar mayor resolución
    if not thumbnail_url and hasattr(media, "image_versions2") and media.image_versions2:
        candidates = media.image_versions2.get("candidates", [])
        if candidates:
            # El primer candidato suele ser el de mayor resolución
            thumbnail_url = str(candidates[0].get("url", ""))

    # Prioridad 3: resources del carrusel — primera imagen del álbum
    if not thumbnail_url and hasattr(media, "resources") and media.resources:
        first_res = media.resources[0]
        if hasattr(first_res, "thumbnail_url") and first_res.thumbnail_url:
            thumbnail_url = str(first_res.thumbnail_url)

    # Establecer la imagen grande del embed
    if thumbnail_url:
        embed.set_image(url=thumbnail_url)

    # ── Footer limpio: marca + hora actual ────────────────────────────────────────────────────────
    # Formato: "InstaShift • Instagram • HH:MM UTC"
    hora_actual = datetime.now(timezone.utc).strftime("%H:%M UTC")
    embed.set_footer(text=f"InstaShift  •  Instagram  •  {hora_actual}")

    return embed


def build_view(media: Media) -> discord.ui.View:
    """
    Crea una vista con el botón "Ver en Instagram".

    El botón usa estilo link (URL) — es el único estilo permitido por Discord
    para botones con URL externas. Se muestra visible y clicable en el mensaje.

    IMPORTANTE: Los botones con url= DEBEN usar ButtonStyle.link.
    No se puede combinar url= con ButtonStyle.primary u otros estilos de color.
    """
    shortcode = getattr(media, "code", None) or str(media.pk)
    ig_url = f"https://www.instagram.com/p/{shortcode}/"

    # Crear vista persistente (timeout=None = no expira nunca)
    view = discord.ui.View(timeout=None)

    # Botón "Ver en Instagram" — visible, real y clicable
    # Enlaza directamente a la publicación en Instagram
    view.add_item(
        discord.ui.Button(
            label="Ver en Instagram",
            emoji="📸",
            url=ig_url,
            # NOTA: ButtonStyle.link es OBLIGATORIO cuando se especifica url=
            # Discord no permite combinar url= con estilos de color (primary, etc.)
            style=discord.ButtonStyle.link,
        )
    )
    return view


# ══════════════════════════════════════════════════════════════════════════════
# Cog principal del scraper
# ══════════════════════════════════════════════════════════════════════════════

class InstagramScraperCog(commands.Cog, name="Instagram"):
    """
    Cog que gestiona el scraping de Instagram y los comandos relacionados.

    Funciones principales:
    - FeedLoop: tarea periódica que comprueba feeds y publica en Discord
    - /preview: previsualiza la última publicación de cualquier cuenta
    - /instagram_status: muestra el estado de la sesión de Instagram
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Instancia del cliente de Instagram (gestiona sesión y scraping)
        self.ig = InstagramClient()
        # Flag para el primer ciclo — evita publicar todo el historial al arrancar
        self._primer_ciclo: bool = True
        # Iniciar el loop de feeds automáticamente al cargar el cog
        self.feed_loop.start()

    def cog_unload(self) -> None:
        """
        Se llama cuando el cog se descarga (bot cerrando o reload).
        Cancela el task loop para evitar memory leaks y tareas huérfanas.
        """
        self.feed_loop.cancel()

    # ── Task loop principal ─────────────────────────────────────────────────────────────────────────────

    @tasks.loop(minutes=CHECK_INTERVAL)
    async def feed_loop(self) -> None:
        """
        Tarea periódica que se ejecuta cada CHECK_INTERVAL minutos.

        Para cada feed activo:
        1. Obtiene las últimas publicaciones de Instagram
        2. Filtra las ya publicadas (anti-duplicados)
        3. En el primer ciclo solo marca sin publicar (anti-spam)
        4. Publica las nuevas en el canal de Discord configurado
        """
        # Obtener todos los feeds activos de la base de datos
        feeds = await get_all_active_feeds()
        if not feeds:
            # Sin feeds configurados — nada que hacer
            return

        # Asegurar sesión de Instagram activa antes de scrapear
        await self.ig.ensure_logged_in()

        # Procesar cada feed por separado
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

        # Marcar que el primer ciclo ya terminó (anti-spam activo a partir de ahora)
        if self._primer_ciclo:
            self._primer_ciclo = False
            log.info("[FeedLoop] Primer ciclo completado – anti-spam activo.")

    @feed_loop.before_loop
    async def before_feed_loop(self) -> None:
        """
        Hook que se ejecuta antes de que inicie el loop.
        Espera a que el bot esté completamente conectado y listo.
        """
        await self.bot.wait_until_ready()
        log.info("[FeedLoop] Iniciado (intervalo=%d min).", CHECK_INTERVAL)

    async def _procesar_feed(self, feed: dict) -> None:
        """
        Procesa un feed individual: obtiene publicaciones nuevas y las envía a Discord.

        Lógica anti-duplicados
        ----------------------
        1. En el primer ciclo: marcar TODAS como vistas sin publicar (evita spam al arrancar).
        2. En ciclos siguientes: publicar solo las no marcadas en la BD.

        Args:
            feed: dict con campos 'id', 'instagram_account', 'channel_id', etc.
        """
        account: str = feed["instagram_account"]
        feed_id: int = feed["id"]

        # Obtener publicaciones recientes de Instagram
        medias = await self.ig.get_recent_medias(account, amount=MAX_POSTS_PER_CYCLE)
        if not medias:
            return

        # Obtener info del perfil para el embed (foto de perfil, nombre completo)
        user_info = await self.ig.get_user_info(account)

        # Ordenar de más antiguo a más nuevo → publicar en orden cronológico
        medias.sort(key=lambda m: getattr(m, "taken_at", 0))

        for media in medias:
            media_id = str(media.pk)

            # ── Verificar si ya fue publicada (anti-duplicados) ────────────────────────────
            if await is_already_posted(feed_id, media_id):
                continue

            # ── Anti-spam en el primer ciclo ───────────────────────────────────────────────
            if self._primer_ciclo:
                # Marcar como vista SIN publicar — evita inundar el canal al arrancar
                await mark_as_posted(feed_id, media_id)
                continue

            # ── Determinar canal de destino ──────────────────────────────────────────────────────────────
            channel_id: int = feed["channel_id"]
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                log.warning("[Feed %d] Canal %s no encontrado.", feed_id, channel_id)
                continue

            # ── Mención de rol opcional ─────────────────────────────────────────────────────────────────────────
            # Si el feed tiene configurado un rol para mencionar, se incluye en el contenido
            role_id = feed.get("role_id")
            contenido: str | None = None
            if role_id:
                contenido = f"<@&{role_id}>"

            # ── Construir embed y botón de enlace ─────────────────────────────────────────────────────────────
            embed = build_media_embed(media, user_info)
            view = build_view(media)

            # ── Publicar en Discord ─────────────────────────────────────────────────────────────────────────────────
            try:
                await channel.send(content=contenido, embed=embed, view=view)
                # Registrar en BD para anti-duplicados
                await mark_as_posted(feed_id, media_id)
                await update_last_media_id(feed_id, media_id)
                log.info(
                    "[Feed %d] Publicado: media %s de @%s",
                    feed_id, media_id, account,
                )
            except discord.Forbidden:
                log.error(
                    "[Feed %d] Sin permisos para enviar al canal %s.",
                    feed_id, channel_id,
                )
            except discord.HTTPException as exc:
                log.error("[Feed %d] Error HTTP de Discord: %s", feed_id, exc)

    # ── Comando /preview ───────────────────────────────────────────────────────────────────────────────

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
        # Diferir para evitar timeout mientras se hace el scraping
        await interaction.response.defer(thinking=True)

        # Limpiar el username (quitar @ si viene con él)
        username = username.lstrip("@").strip()
        if not username:
            await interaction.followup.send(
                "❌ Por favor ingresa un nombre de usuario válido.", ephemeral=True
            )
            return

        # Asegurar sesión activa antes de scrapear
        await self.ig.ensure_logged_in()

        # Obtener la publicación más reciente
        medias = await self.ig.get_recent_medias(username, amount=1)
        if not medias:
            await interaction.followup.send(
                f"❌ No se encontraron publicaciones para **@{username}**.\n"
                "Verifica que la cuenta sea pública y exista.",
                ephemeral=True,
            )
            return

        # Obtener info del perfil para el embed
        user_info = await self.ig.get_user_info(username)
        media = medias[0]

        # Construir embed premium y botón de enlace
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

    # ── Comando /instagram_status ──────────────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="instagram_status",
        description="Muestra el estado actual de la sesión de Instagram.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def instagram_status(self, interaction: discord.Interaction) -> None:
        """
        Informa si el bot está autenticado en Instagram o en modo invitado.
        Requiere permiso de 'Gestionar servidor'.
        """
        await interaction.response.defer(ephemeral=True)

        # Verificar estado de autenticación
        is_auth = await self.ig.ensure_logged_in()

        if is_auth:
            # ── Sesión activa ──────────────────────────────────────────────────────────────────────────────
            embed = discord.Embed(
                title="✅ Instagram — Sesión activa",
                description=f"Autenticado como **@{IG_USERNAME}**",
                color=0x00B347,  # Verde de éxito
            )
            embed.add_field(
                name="Archivo de sesión",
                value=f"`{SESSION_PATH}`",
                inline=False,
            )
        else:
            # ── Sin sesión / modo invitado ───────────────────────────────────────────────────────────────────
            embed = discord.Embed(
                title="⚠️ Instagram — Modo invitado",
                description=(
                    "No hay credenciales configuradas.\n"
                    "Establece **IG_USERNAME** e **IG_PASSWORD** en tu archivo `.env`."
                ),
                color=0xFFA500,  # Naranja de advertencia
            )
            embed.set_footer(text="El modo invitado tiene acceso muy limitado.")

        await interaction.followup.send(embed=embed, ephemeral=True)


# ── Función requerida por discord.py para cargar el cog ───────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    """
    Registra el cog en el bot.

    Esta función ES OBLIGATORIA y debe llamarse 'setup'.
    discord.py la llama automáticamente cuando se ejecuta load_extension().
    Sin esta función, el cog NO se carga y los comandos NO se sincronizan.
    """
    await bot.add_cog(InstagramScraperCog(bot))
