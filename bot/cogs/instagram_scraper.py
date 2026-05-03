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
* discord.Embed.Empty fue eliminado en discord.py 2.0 — NO usar
* asyncio.get_event_loop() deprecado en Python 3.10+ — usar get_running_loop()
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

# ── Importar instagrapi con manejo de error claro ───────────────────────────────────────────────
# Si instagrapi no está instalado, el cog puede no cargar.
# El error quedará visible en los logs con el mensaje de abajo.
try:
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
    INSTAGRAPI_OK = True
except ImportError as _instagrapi_err:
    # Si falla la importación, el cog se registra igual pero avisa en los logs
    # y todas las funciones de scraping devuelven resultados vacíos/None.
    logging.getLogger(__name__).critical(
        "[InstaShift] FALLO CRITICO: instagrapi no está instalado o tiene error. "
        "Ejecuta: pip install instagrapi>=2.0.0,<3.0.0  |  Error: %s",
        _instagrapi_err,
    )
    INSTAGRAPI_OK = False
    # Tipos de fallback para que el módulo no falle al importar
    Client = None  # type: ignore[misc,assignment]
    BadPassword = ChallengeRequired = LoginRequired = Exception
    MediaNotFound = ReloginAttemptExceeded = UserNotFound = Exception
    Media = UserShort = object  # type: ignore[misc,assignment]

from bot.database import (
    get_all_active_feeds,
    is_already_posted,
    mark_as_posted,
    update_last_media_id,
)

# ── Logger del módulo ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

# ── Variables de entorno ──────────────────────────────────────────────────────────────────
# NOTA IMPORTANTE: estas variables se leen aqui (nivel modulo) cuando Python
# importa el modulo. En Railway las variables del servicio ya estan inyectadas
# en el entorno del SO antes de que el proceso arranque, por lo que os.getenv()
# las recoge correctamente sin necesidad de un archivo .env.
IG_USERNAME: str = os.getenv("IG_USERNAME", "").strip()
IG_PASSWORD: str = os.getenv("IG_PASSWORD", "").strip()
SESSION_PATH: Path = Path(os.getenv("SESSION_PATH", "ig_session.json").strip())

# Intervalo de polling en minutos (por defecto cada 10 minutos)
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "10").strip() or "10")

# ── Log de credenciales al importar el modulo ─────────────────────────────────────────────
# Estos logs aparecen cuando el cog se carga (en setup_hook, antes del login).
# Son la segunda linea de verificacion despues de los logs en main.py.
if IG_USERNAME:
    log.info("[ENV] IG_USERNAME cargado correctamente: %s", IG_USERNAME)
else:
    log.warning("[ENV] IG_USERNAME NO encontrado o vacio en este modulo.")

if IG_PASSWORD:
    log.info("[ENV] IG_PASSWORD cargada (%d caracteres)", len(IG_PASSWORD))
else:
    log.warning("[ENV] IG_PASSWORD NO encontrada o vacia en este modulo.")

if IG_USERNAME and IG_PASSWORD:
    log.info("[Login] Credenciales detectadas → Iniciando login al conectar...")
else:
    log.warning("[Login] Modo invitado activado (sin credenciales) → funcionalidad limitada.")
# ── Constantes de diseño ──────────────────────────────────────────────────────────────────────────
# Color rosa Instagram (#E1306C) — borde izquierdo del embed
IG_COLOR = 0xE1306C

# Máximo de publicaciones a obtener por ciclo
MAX_POSTS_PER_CYCLE = 5

# Máximo de hashtags clicables a mostrar en el embed (evita saturar el mensaje)
MAX_HASHTAGS = 8


# ══════════════════════════════════════════════════════════════════════════════
# Cliente de Instagram — wrapper sobre instagrapi.Client
# ══════════════════════════════════════════════════════════════════════════════

class InstagramClient:
    """
    Wrapper thread-safe sobre instagrapi.Client con persistencia de sesión.

    Toda operación bloqueante de instagrapi se ejecuta en un executor
    para no bloquear el event loop de asyncio (compatible con discord.py).
    """

    def __init__(self) -> None:
        # Solo instanciar el cliente si instagrapi está disponible
        self._cl = Client() if INSTAGRAPI_OK else None
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
        if not INSTAGRAPI_OK or self._cl is None:
            return False
        if not SESSION_PATH.exists():
            return False
        try:
            self._cl.load_settings(SESSION_PATH)
            # Verificar que la sesión cargada sigue siendo válida
            self._cl.get_timeline_feed()
            self._logged_in = True
            log.info("[IG] ✅ Sesión restaurada desde %s", SESSION_PATH)
            return True
        except LoginRequired:
            log.warning("[IG] Sesión expirada, se necesita re-login.")
            return False
        except Exception as exc:
            log.warning("[IG] No se pudo restaurar la sesión: %s", exc)
            return False

    def _save_session(self) -> None:
        """Persiste la sesión actual en disco para futuros reinicios del bot."""
        if self._cl is None:
            return
        SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._cl.dump_settings(SESSION_PATH)
        log.debug("[IG] Sesión guardada en %s", SESSION_PATH)

    # ── Login ─────────────────────────────────────────────────────────────────────────────────────

    async def ensure_logged_in(self) -> bool:
        """
        Garantiza que el cliente este autenticado antes de hacer requests.

        Flujo:
        1. Si instagrapi no esta disponible → retorna False con log de error.
        2. Si ya esta autenticado → retorna True inmediatamente.
        3. Re-lee credenciales del entorno (fix para timing issues en Railway).
        4. Si hay sesion guardada valida → la restaura.
        5. Si no → hace login fresco con usuario/contrasena.
        """
        # Verificar que instagrapi este disponible
        if not INSTAGRAPI_OK or self._cl is None:
            log.error("[IG] instagrapi no disponible - no se puede autenticar.")
            return False

        async with self._lock:
            # Ya autenticado — nada que hacer
            if self._logged_in:
                return True

            # Re-leer las variables en tiempo de ejecucion para mayor robustez.
            # Si por timing issue la variable global quedo vacia al importar el
            # modulo, se re-lee directamente del entorno en el momento del login.
            ig_user = IG_USERNAME or os.getenv("IG_USERNAME", "").strip()
            ig_pass = IG_PASSWORD or os.getenv("IG_PASSWORD", "").strip()

            # Sin credenciales → modo invitado (acceso limitado)
            if not ig_user or not ig_pass:
                log.warning(
                    "[Login] Modo invitado activado (sin credenciales). "
                    "Configura IG_USERNAME e IG_PASSWORD en Railway."
                )
                return False

            log.info("[Login] Credenciales detectadas → Iniciando login como @%s...", ig_user)
            # CORRECCION: usar get_running_loop() en lugar del deprecado get_event_loop()
            loop = asyncio.get_running_loop()

            # 1. Intentar restaurar sesion guardada
            restored = await loop.run_in_executor(None, self._load_session)
            if restored:
                log.info("[Login] Sesion restaurada desde %s", SESSION_PATH)
                return True

            # 2. Login fresco con credenciales
            try:
                log.info("[Login] Iniciando sesion fresca como @%s ...", ig_user)
                await loop.run_in_executor(
                    None,
                    lambda: self._cl.login(ig_user, ig_pass),
                )
                self._logged_in = True
                await loop.run_in_executor(None, self._save_session)
                log.info("[Login] Login exitoso como @%s", ig_user)
                return True
            except BadPassword:
                log.error("[Login] Contrasena incorrecta para @%s. Revisa IG_PASSWORD.", ig_user)
            except ChallengeRequired:
                log.error("[Login] Se requiere verificacion (challenge/2FA). Resuelve manualmente.")
            except ReloginAttemptExceeded:
                log.error("[Login] Demasiados intentos de re-login. Espera antes de reintentar.")
            except Exception as exc:
                log.exception("[Login] Error inesperado durante el login: %s", exc)

            return False
    # ── Obtención de datos ────────────────────────────────────────────────────────────────────────

    async def get_user_info(self, username: str) -> Optional[object]:
        """
        Obtiene la información básica de un perfil público.
        Retorna None si hay error, usuario no existe o instagrapi no está disponible.
        """
        if not INSTAGRAPI_OK or self._cl is None:
            return None
        await self.ensure_logged_in()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: self._cl.user_info_by_username(username)
            )
        except UserNotFound:
            log.warning("[IG] Usuario @%s no encontrado.", username)
        except Exception as exc:
            log.exception("[IG] Error al obtener info de @%s: %s", username, exc)
        return None

    async def get_recent_medias(self, username: str, amount: int = 5) -> list:
        """
        Obtiene las publicaciones más recientes de una cuenta pública.
        Retorna lista vacía si hay error o instagrapi no está disponible.
        """
        if not INSTAGRAPI_OK or self._cl is None:
            return []
        await self.ensure_logged_in()
        loop = asyncio.get_running_loop()
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
            log.warning("[IG] No hay publicaciones para @%s.", username)
        except Exception as exc:
            log.exception("[IG] Error al obtener publicaciones de @%s: %s", username, exc)
        return []

    async def get_recent_stories(self, username: str) -> list:
        """
        Obtiene las Stories actuales de un usuario (requiere autenticación).
        Retorna lista vacía si hay error o instagrapi no está disponible.
        """
        if not INSTAGRAPI_OK or self._cl is None:
            return []
        await self.ensure_logged_in()
        loop = asyncio.get_running_loop()
        try:
            user_id = await loop.run_in_executor(
                None, lambda: self._cl.user_id_from_username(username)
            )
            return await loop.run_in_executor(
                None, lambda: self._cl.user_stories(user_id)
            )
        except LoginRequired:
            log.warning("[IG] Se requiere login para obtener Stories.")
            self._logged_in = False
        except Exception as exc:
            log.exception("[IG] Error al obtener Stories de @%s: %s", username, exc)
        return []

    @property
    def is_authenticated(self) -> bool:
        """Indica si el cliente tiene una sesión activa en Instagram."""
        return self._logged_in


# ══════════════════════════════════════════════════════════════════════════════
# Funciones auxiliares para construir embeds premium
# ══════════════════════════════════════════════════════════════════════════════

def _separar_caption(raw: str) -> tuple[str, list[str]]:
    """
    Separa el caption de Instagram en texto limpio y lista de hashtags.

    Retorna (texto_sin_hashtags, [lista_de_hashtags]).
    El texto limpio se muestra en el cuerpo; los hashtags como enlaces al final.
    """
    texto: list[str] = []
    tags: list[str] = []
    for palabra in raw.strip().split():
        if palabra.startswith("#"):
            tags.append(palabra)
        else:
            texto.append(palabra)
    return " ".join(texto).strip(), tags


def _formatear_numero(n: int) -> str:
    """
    Formatea un número en formato compacto K/M para las estadísticas.

    Ejemplos: 1234 → "1.2K" | 1500000 → "1.5M" | 999 → "999"
    """
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _construir_linea_stats(media: object) -> str:
    """
    Genera la línea de estadísticas en una sola línea compacta con formato K/M.

    Ejemplo de salida: ❤️ 1.2K  •  💬 56  •  👁️ 12K

    Solo se incluyen las métricas disponibles (las vistas son solo para Reels).
    """
    partes: list[str] = []

    likes = getattr(media, "like_count", 0) or 0
    if likes:
        partes.append(f"❤️ {_formatear_numero(likes)}")

    comentarios = getattr(media, "comment_count", 0) or 0
    if comentarios:
        partes.append(f"💬 {_formatear_numero(comentarios)}")

    # Las vistas aplican principalmente a Reels y videos
    vistas = getattr(media, "view_count", 0) or 0
    if vistas:
        partes.append(f"👁️ {_formatear_numero(vistas)}")

    return "  •  ".join(partes)


def build_media_embed(media: object, user_info: Optional[object] = None) -> discord.Embed:
    """
    Construye un embed premium de Discord para una publicación de Instagram.

    Diseño (estilo TweetShift/REALITY):
    ────────────────────────────────────────
    Author  : foto de perfil + "@username"
    Title   : "📸 Nueva publicación de @username"
    Descripción: caption limpio + stats + hashtags clicables
    Image   : imagen/thumbnail de la publicación
    Color   : rosa Instagram #E1306C
    Footer  : "InstaShift • Instagram • HH:MM UTC"
    """
    # URL de la publicación en Instagram
    shortcode = getattr(media, "code", None) or str(getattr(media, "pk", ""))
    ig_url = f"https://www.instagram.com/p/{shortcode}/"

    # Determinar tipo de contenido
    media_type = getattr(media, "media_type", 1)
    if media_type == 2:
        tipo_emoji = "🎥"
        tipo_texto = "Reel"
    elif getattr(media, "is_story", False):
        tipo_emoji = "📖"
        tipo_texto = "Story"
    else:
        tipo_emoji = "📸"
        tipo_texto = "Publicación"

    # Título: incluir el @usuario si está disponible
    username_display = ""
    if user_info:
        username_display = getattr(user_info, "username", "") or ""
    titulo = (
        f"{tipo_emoji} Nueva {tipo_texto.lower()} de @{username_display}"
        if username_display
        else f"{tipo_emoji} Nueva {tipo_texto.lower()} de Instagram"
    )

    # Separar caption de hashtags
    raw_caption = getattr(media, "caption_text", "") or ""
    caption_limpio, todos_los_tags = _separar_caption(raw_caption)
    if len(caption_limpio) > 1500:
        caption_limpio = caption_limpio[:1497] + "…"

    # Línea de estadísticas: ❤️ Likes • 💬 Comentarios • 👁️ Vistas
    linea_stats = _construir_linea_stats(media)

    # Hashtags clicables al final (máximo MAX_HASHTAGS)
    tags_display = todos_los_tags[:MAX_HASHTAGS]
    hashtags_clicables = ""
    if tags_display:
        hashtags_clicables = "  ".join(
            f"[{t}](https://www.instagram.com/explore/tags/{t.lstrip('#')}/)"
            for t in tags_display
        )
        if len(todos_los_tags) > MAX_HASHTAGS:
            hashtags_clicables += f"  *+{len(todos_los_tags) - MAX_HASHTAGS} más*"

    # Armar descripción: caption → stats → hashtags
    partes_desc: list[str] = []
    if caption_limpio:
        partes_desc.append(caption_limpio)
    if linea_stats:
        partes_desc.append(f"\n{linea_stats}")
    if hashtags_clicables:
        partes_desc.append(f"\n\n{hashtags_clicables}")
    descripcion_final = "".join(partes_desc)[:4096]

    # Construir el embed con color rosa Instagram #E1306C
    embed = discord.Embed(
        title=titulo,
        description=descripcion_final or None,
        url=ig_url,
        color=IG_COLOR,
        timestamp=getattr(media, "taken_at", None),
    )

    # Author: foto de perfil como icono circular junto al nombre
    if user_info:
        uname = getattr(user_info, "username", "instagram")
        nombre_completo = getattr(user_info, "full_name", "") or ""
        avatar_url = str(getattr(user_info, "profile_pic_url", "") or "")

        nombre_author = f"@{uname}  •  {nombre_completo}" if nombre_completo else f"@{uname}"

        # CORRECCION: discord.Embed.Empty fue eliminado en discord.py 2.0
        # Usar None o simplemente omitir icon_url cuando no hay avatar
        embed.set_author(
            name=nombre_author,
            url=f"https://www.instagram.com/{uname}/",
            icon_url=avatar_url or None,
        )

    # Imagen grande prominente de la publicación
    thumbnail_url: str = ""

    # Prioridad 1: thumbnail_url (reels y algunos posts)
    if hasattr(media, "thumbnail_url") and media.thumbnail_url:
        thumbnail_url = str(media.thumbnail_url)

    # Prioridad 2: image_versions2 (lista de candidatos, el primero es mayor resolución)
    if not thumbnail_url and hasattr(media, "image_versions2") and media.image_versions2:
        candidates = media.image_versions2.get("candidates", [])
        if candidates:
            thumbnail_url = str(candidates[0].get("url", ""))

    # Prioridad 3: resources del carrusel (primera imagen del álbum)
    if not thumbnail_url and hasattr(media, "resources") and media.resources:
        first_res = media.resources[0]
        if hasattr(first_res, "thumbnail_url") and first_res.thumbnail_url:
            thumbnail_url = str(first_res.thumbnail_url)

    if thumbnail_url:
        embed.set_image(url=thumbnail_url)

    # Footer limpio: marca + hora actual UTC
    hora_actual = datetime.now(timezone.utc).strftime("%H:%M UTC")
    embed.set_footer(text=f"InstaShift  •  Instagram  •  {hora_actual}")

    return embed


def build_view(media: object) -> discord.ui.View:
    """
    Crea la vista con el botón "Ver en Instagram".

    IMPORTANTE: Los botones con url= DEBEN usar ButtonStyle.link.
    Discord no permite combinar url= con ButtonStyle.primary ni otros estilos de color.
    El botón aparecerá visible y clicable en el mensaje de Discord.
    """
    shortcode = getattr(media, "code", None) or str(getattr(media, "pk", ""))
    ig_url = f"https://www.instagram.com/p/{shortcode}/"

    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Ver en Instagram",
            emoji="📸",
            url=ig_url,
            style=discord.ButtonStyle.link,  # OBLIGATORIO con url=
        )
    )
    return view


# ══════════════════════════════════════════════════════════════════════════════
# Cog principal del scraper de Instagram
# ══════════════════════════════════════════════════════════════════════════════

class InstagramScraperCog(commands.Cog, name="Instagram"):
    """
    Cog que gestiona el scraping de Instagram y los comandos relacionados.

    El nombre "Instagram" es importante: feeds.py lo busca con bot.cogs.get("Instagram")
    para ejecutar /checknow. No cambiar este nombre.

    Comandos registrados:
    - /preview         : previsualiza la última publicación de una cuenta
    - /instagram_status: muestra el estado de la sesión de Instagram
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        log.info("[Scraper] ⏳ Inicializando cog Instagram...")

        # Instancia del cliente de Instagram
        self.ig = InstagramClient()

        # Flag anti-spam: en el primer ciclo se marcan sin publicar
        self._primer_ciclo: bool = True

        # Iniciar el loop de feeds automáticamente al cargar el cog
        self.feed_loop.start()
        log.info("[Scraper] ✅ Cog Instagram cargado. FeedLoop iniciado (intervalo=%d min).", CHECK_INTERVAL)

    def cog_unload(self) -> None:
        """
        Se llama cuando el cog se descarga (bot cerrando o reload).
        Cancela el task loop para evitar memory leaks.
        """
        self.feed_loop.cancel()
        log.info("[Scraper] Cog Instagram descargado. FeedLoop cancelado.")

    # ── Task loop principal ─────────────────────────────────────────────────────────────────────────

    @tasks.loop(minutes=CHECK_INTERVAL)
    async def feed_loop(self) -> None:
        """
        Tarea periódica que se ejecuta cada CHECK_INTERVAL minutos.

        Ciclo de trabajo:
        1. Obtiene todos los feeds activos de la BD
        2. Asegura sesión de Instagram activa
        3. Para cada feed: obtiene medias nuevas y las publica en Discord
        4. En el primer ciclo solo marca (anti-spam) sin publicar
        """
        feeds = await get_all_active_feeds()
        if not feeds:
            return  # Sin feeds configurados, nada que hacer

        log.debug("[FeedLoop] Verificando %d feed(s)...", len(feeds))
        await self.ig.ensure_logged_in()

        for feed in feeds:
            try:
                await self._procesar_feed(feed)
            except Exception as exc:
                log.exception(
                    "[FeedLoop] Error inesperado en feed #%d (@%s): %s",
                    feed["id"], feed["instagram_account"], exc,
                )

        # Completar el primer ciclo (a partir de ahora sí se publican)
        if self._primer_ciclo:
            self._primer_ciclo = False
            log.info("[FeedLoop] ✅ Primer ciclo completado. Anti-spam activo a partir de ahora.")

    @feed_loop.before_loop
    async def before_feed_loop(self) -> None:
        """Espera a que el bot esté completamente conectado antes de iniciar."""
        await self.bot.wait_until_ready()
        log.info("[FeedLoop] ▶️ Bot listo. Iniciando loop de feeds (intervalo=%d min).", CHECK_INTERVAL)

    @feed_loop.error
    async def feed_loop_error(self, exc: Exception) -> None:
        """Captura errores del loop para que no se detenga silenciosamente."""
        log.exception("[FeedLoop] ❌ Error en el loop principal: %s", exc)

    # ── Procesamiento de feeds ───────────────────────────────────────────────────────────────────

    async def _procesar_feed(self, feed: dict) -> None:
        """
        Procesa un feed individual: obtiene publicaciones nuevas y las envía a Discord.

        Lógica anti-spam y anti-duplicados:
        1. Primer ciclo: marcar TODAS como vistas sin publicar (evita spam al arrancar).
        2. Ciclos siguientes: publicar solo las que no están en la BD.
        """
        account: str = feed["instagram_account"]
        feed_id: int = feed["id"]

        medias = await self.ig.get_recent_medias(account, amount=MAX_POSTS_PER_CYCLE)
        if not medias:
            return

        user_info = await self.ig.get_user_info(account)

        # Ordenar de más antiguo a más nuevo para publicar cronológicamente
        medias.sort(key=lambda m: getattr(m, "taken_at", 0))

        for media in medias:
            media_id = str(getattr(media, "pk", ""))

            # Anti-duplicados: saltar si ya fue publicada
            if await is_already_posted(feed_id, media_id):
                continue

            # Anti-spam: primer ciclo solo marca, no publica
            if self._primer_ciclo:
                await mark_as_posted(feed_id, media_id)
                continue

            # Obtener canal de destino
            channel_id: int = feed["channel_id"]
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                log.warning("[Feed #%d] Canal %s no encontrado. ¿Eliminado?", feed_id, channel_id)
                continue

            # Mención de rol opcional (si está configurado en el feed)
            role_id = feed.get("role_id")
            contenido: Optional[str] = f"<@&{role_id}>" if role_id else None

            # Construir embed premium y botón
            embed = build_media_embed(media, user_info)
            view = build_view(media)

            try:
                await channel.send(content=contenido, embed=embed, view=view)
                await mark_as_posted(feed_id, media_id)
                await update_last_media_id(feed_id, media_id)
                log.info("[Feed #%d] ✅ Publicado media %s de @%s", feed_id, media_id, account)
            except discord.Forbidden:
                log.error("[Feed #%d] ❌ Sin permisos en canal %s.", feed_id, channel_id)
            except discord.HTTPException as exc:
                log.error("[Feed #%d] ❌ Error HTTP de Discord: %s", feed_id, exc)

    # ── Comando /preview ───────────────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="preview",
        description="Previsualiza la última publicación de cualquier cuenta pública de Instagram.",
    )
    @app_commands.describe(username="Usuario de Instagram (ej: natgeo) sin el @")
    @app_commands.checks.cooldown(rate=1, per=15.0)
    async def preview(self, interaction: discord.Interaction, username: str) -> None:
        """Muestra la publicación más reciente sin necesidad de suscripción."""
        await interaction.response.defer(thinking=True)

        username = username.lstrip("@").strip()
        if not username:
            await interaction.followup.send("❌ Ingresa un nombre de usuario válido.", ephemeral=True)
            return

        if not INSTAGRAPI_OK:
            await interaction.followup.send(
                "❌ El módulo de Instagram no está disponible. Contacta al administrador.",
                ephemeral=True,
            )
            return

        await self.ig.ensure_logged_in()
        medias = await self.ig.get_recent_medias(username, amount=1)
        if not medias:
            await interaction.followup.send(
                f"❌ No se encontraron publicaciones para **@{username}**.\n"
                "Verifica que la cuenta sea pública y exista.",
                ephemeral=True,
            )
            return

        user_info = await self.ig.get_user_info(username)
        embed = build_media_embed(medias[0], user_info)
        view = build_view(medias[0])
        await interaction.followup.send(embed=embed, view=view)

    @preview.error
    async def preview_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Manejo de errores del comando /preview."""
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏳ Cooldown activo. Intenta en {error.retry_after:.1f}s.", ephemeral=True
            )

    # ── Comando /instagram_status ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="instagram_status",
        description="Muestra el estado de la sesión de Instagram del bot.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def instagram_status(self, interaction: discord.Interaction) -> None:
        """
        Informa si el bot está autenticado en Instagram, en modo invitado,
        o si instagrapi no está disponible. Requiere permiso Gestionar Servidor.
        """
        await interaction.response.defer(ephemeral=True)

        if not INSTAGRAPI_OK:
            embed = discord.Embed(
                title="❌ instagrapi no disponible",
                description=(
                    "La librería **instagrapi** no está instalada o tiene un error.\n"
                    "Revisa los logs del bot para más detalles."
                ),
                color=0xFF0000,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        is_auth = await self.ig.ensure_logged_in()

        if is_auth:
            embed = discord.Embed(
                title="✅ Sesión de Instagram activa",
                description=f"Autenticado como **@{IG_USERNAME}**",
                color=0x00B347,
            )
            embed.add_field(name="Archivo de sesión", value=f"`{SESSION_PATH}`", inline=False)
            embed.add_field(name="Intervalo de scraping", value=f"{CHECK_INTERVAL} minutos", inline=True)
        else:
            embed = discord.Embed(
                title="⚠️ Modo invitado (sin credenciales)",
                description=(
                    "No hay credenciales configuradas.\n"
                    "Establece **IG_USERNAME** e **IG_PASSWORD** en las variables de entorno."
                ),
                color=0xFFA500,
            )
            embed.set_footer(text="El modo invitado tiene acceso muy limitado a la API.")

        await interaction.followup.send(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────────
# Función setup() — OBLIGATORIA para load_extension()
# ─────────────────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    """
    Registra el cog Instagram en el bot.

    discord.py llama a esta función automáticamente al ejecutar load_extension().
    SIN esta función el cog NO se carga y los comandos NO aparecen en Discord.

    Nota: Si instagrapi no está instalado, el cog se carga igualmente
    pero el scraper no funcionará. Los logs mostrarán un error crítico.
    """
    log.info("[Setup] Registrando cog Instagram en el bot...")
    await bot.add_cog(InstagramScraperCog(bot))
    log.info("[Setup] ✅ Cog Instagram registrado correctamente.")
