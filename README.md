<div align="center">

<img src="https://img.icons8.com/fluency/96/instagram-new.png" width="80" alt="InstaShift">

# InstaShift

### El bot de Discord que convierte cualquier perfil público de Instagram en un feed automático dentro de tus canales.

<br>

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![discord.py](https://img.shields.io/badge/discord.py-2.3%2B-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discordpy.readthedocs.io)
[![instagrapi](https://img.shields.io/badge/instagrapi-2.x-E1306C?style=for-the-badge&logo=instagram&logoColor=white)](https://subzeroid.github.io/instagrapi/)
[![SQLite](https://img.shields.io/badge/SQLite-aiosqlite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![Mantenimiento](https://img.shields.io/badge/Mantenimiento-activo-brightgreen?style=for-the-badge)]()

<br>

> **InstaShift** publica automáticamente Posts, Reels y Stories de cualquier cuenta pública de Instagram directamente en canales o hilos de Discord — casi en tiempo real.

</div>

---

## ✨ Características

| Función | Descripción |
|---------|-------------|
| 📸 **Publicaciones automáticas** | Detecta y publica Posts, Reels y Stories en menos de 10 minutos |
| 🎨 **Embeds premium** | Foto de perfil, imagen grande, caption limpia, hashtags clicables, estadísticas y botón de enlace |
| 🔄 **Sesión persistente** | Login único con instagrapi. La sesión se guarda y renueva automáticamente |
| 🛡️ **Anti-duplicados** | Sistema de control por `feed_id + media_id`. Nunca publica lo mismo dos veces |
| 🌐 **Multi-servidor** | Cada servidor gestiona sus propias suscripciones de forma independiente |
| 📢 **Menciones de rol** | Opcional: menciona un rol de Discord en cada nueva publicación |
| 🧵 **Soporte de hilos** | Publica en un hilo específico en lugar del canal principal |
| 👤 **Modo invitado** | Funciona sin credenciales para cuentas públicas (funcionalidad limitada) |
| 🔍 **Comando /preview** | Previsualiza cualquier cuenta sin necesidad de suscripción |
| 🐳 **Docker-ready** | Imagen Docker multi-stage lista para producción |

---

## 🎨 Diseño del embed

Cada publicación de Instagram aparece en Discord con este diseño:

```
┌─────────────────────────────────────────────────┐
│ 🖼️ @usuario_de_instagram • Nombre Completo       │
├─────────────────────────────────────────────────┤
│  📸 Publicación                                  │
│                                                 │
│  [Imagen grande prominente]                     │
│                                                 │
│  Caption limpia del post sin hashtags duplicados│
│  separados…                                     │
│                                                 │
│  ❤️ 12,345  •  💬 234  •  👁️ 45,678             │
│                                                 │
│  [#hashtag1](link)  [#hashtag2](link)  ...      │
│                                                 │
│  Footer: InstaShift • Instagram    [fecha/hora] │
├─────────────────────────────────────────────────┤
│  [📸 Ver en Instagram]                          │
└─────────────────────────────────────────────────┘
Color de acento: rosa Instagram #E1306C
```

---

## 📁 Estructura del proyecto

```
InstaShift/
├── bot/
│   ├── __init__.py                 # Versión del paquete
│   ├── main.py                     # Punto de entrada + configuración del bot
│   ├── database.py                 # Capa de datos SQLite asíncrona (aiosqlite)
│   ├── cogs/
│   │   ├── __init__.py
│   │   ├── feeds.py                # /follow /unfollow /list /dashboard /checknow /sync
│   │   └── instagram_scraper.py   # Tarea periódica + embeds + /preview + /instagram_status
│   └── utils/
│       └── __init__.py             # Utilidades reutilizables
├── .env.example                    # Plantilla de configuración
├── .gitignore
├── Dockerfile                      # Imagen Docker multi-stage
├── README.md
├── requirements.txt
└── run.sh                          # Script de lanzamiento local
```

---

## ⚙️ Configuración del entorno (.env)

Copia `.env.example` a `.env` y completa los valores:

```env
# ── Discord ──────────────────────────────────────────────────────────────────
# Token del bot (desde https://discord.com/developers/applications)
DISCORD_TOKEN=tu_token_aqui

# ID del servidor para modo desarrollo (comandos instantáneos)
# Dejar vacío en producción (comandos globales)
GUILD_ID=

# ── Instagram ─────────────────────────────────────────────────────────────────
# Cuenta para autenticación (mejora límites de API y permite Stories)
IG_USERNAME=tu_usuario_de_instagram
IG_PASSWORD=tu_contraseña_de_instagram

# ── Base de datos ─────────────────────────────────────────────────────────────
DB_PATH=instashift.db           # Ruta del archivo SQLite
SESSION_PATH=ig_session.json    # Archivo de sesión persistente de Instagram

# ── Configuración ─────────────────────────────────────────────────────────────
CHECK_INTERVAL=10               # Minutos entre cada verificación de feeds
LOG_LEVEL=INFO                  # DEBUG | INFO | WARNING | ERROR
```

> ⚠️ **Nunca subas tu archivo `.env` a GitHub.** Ya está incluido en `.gitignore`.

---

## 🚀 Instalación y despliegue

### 🖥️ Local (bash)

```bash
# 1. Clonar el repositorio
git clone https://github.com/tioreality/InstaShift.git
cd InstaShift

# 2. Configurar el entorno
cp .env.example .env
# Editar .env con tu editor favorito y completar DISCORD_TOKEN, IG_USERNAME, IG_PASSWORD

# 3. Lanzar el bot (crea venv y instala dependencias automáticamente)
bash run.sh
```

### 🐍 Manual (venv)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

### 🐳 Docker

```bash
# Construir la imagen
docker build -t instashift .

# Ejecutar el contenedor con volumen persistente
docker run -d \
  --name instashift \
  --restart unless-stopped \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  instashift

# Ver logs en tiempo real
docker logs -f instashift
```

### 🚂 Railway

1. Haz fork de este repositorio
2. Crea un nuevo proyecto en [railway.app](https://railway.app) → **Deploy from GitHub repo**
3. Agrega las variables de entorno en el panel de Railway
4. Railway detecta el `Dockerfile` automáticamente y despliega

### 🔁 Replit

1. Importa el repositorio en Replit
2. Agrega los secretos (variables de entorno) en el panel **Secrets**
3. Establece el comando de inicio: `python -m bot.main`
4. Mantén el bot activo con [UptimeRobot](https://uptimerobot.com) si es necesario

### 🖧 VPS (systemd)

```ini
# /etc/systemd/system/instashift.service
[Unit]
Description=InstaShift Discord Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/instashift
EnvironmentFile=/opt/instashift/.env
ExecStart=/opt/instashift/.venv/bin/python -m bot.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now instashift
sudo journalctl -fu instashift    # Ver logs
```

---

## 🤖 Configuración del bot en Discord

1. Ve a [discord.com/developers/applications](https://discord.com/developers/applications)
2. Crea una nueva aplicación → pestaña **Bot** → **Add Bot**
3. Copia el **Token** → pégalo como `DISCORD_TOKEN` en tu `.env`
4. Activa los **Privileged Gateway Intents** si los necesitas
5. Ve a **OAuth2 → URL Generator** → selecciona `bot` + `applications.commands`
6. Permisos necesarios: **Enviar mensajes**, **Insertar enlaces**, **Ver canales**
7. Invita el bot al servidor con la URL generada

---

## 📋 Comandos

| Comando | Descripción | Permiso requerido |
|---------|-------------|:-----------------:|
| `/follow @usuario` | Suscribir un canal a una cuenta de Instagram | Gestionar servidor |
| `/unfollow @usuario` | Eliminar una suscripción | Gestionar servidor |
| `/list` | Ver todas las suscripciones activas | Gestionar servidor |
| `/dashboard` | Panel de control visual con todos los feeds | Gestionar servidor |
| `/checknow` | Forzar verificación inmediata de feeds | Gestionar servidor |
| `/preview @usuario` | Previsualizar último post sin suscribirse | Cualquiera |
| `/instagram_status` | Ver estado de la sesión de Instagram | Gestionar servidor |
| `/sync` | Re-sincronizar comandos slash | Administrador |
| `/sync clear` | Eliminar todos los comandos del servidor | Administrador |

### 📌 Ejemplos de uso

```
/follow username:nasa channel:#astronomia role:@Noticias
/follow username:natgeo channel:#naturaleza thread:#fotos-hilo

/unfollow username:nasa channel:#astronomia

/preview username:apple

/dashboard
/checknow
/instagram_status
```

---

## 🗄️ Esquema de base de datos

InstaShift usa **SQLite** asíncrono (via aiosqlite) con dos tablas:

```sql
-- Suscripciones por servidor
feeds (
    id                 INTEGER PRIMARY KEY,
    guild_id           INTEGER,         -- ID del servidor de Discord
    instagram_account  TEXT,            -- @usuario de Instagram
    channel_id         INTEGER,         -- canal de destino
    thread_id          INTEGER,         -- hilo opcional
    role_id            INTEGER,         -- rol a mencionar (opcional)
    last_media_id      TEXT,            -- último contenido visto
    active             INTEGER DEFAULT 1,
    created_at         TEXT
)

-- Registro anti-duplicados
posted_media (
    id         INTEGER PRIMARY KEY,
    feed_id    INTEGER REFERENCES feeds(id) ON DELETE CASCADE,
    media_id   TEXT,               -- ID único de la publicación en Instagram
    posted_at  TEXT
)
```

---

## 🔧 Arquitectura técnica

```
Discord Gateway
      │
      ▼
 InstaShift (Bot)
      │
      ├── setup_hook()
      │     ├── init_db()           ← Crea tablas SQLite
      │     ├── load_extension()    ← Carga cogs
      │     └── tree.sync()         ← Registra slash commands
      │
      ├── FeedsCog
      │     └── /follow /unfollow /list /dashboard /checknow /sync
      │
      └── InstagramScraperCog
            ├── feed_loop (cada 10 min)
            │     ├── get_all_active_feeds()   ← SQLite
            │     ├── get_recent_medias()       ← instagrapi → Instagram
            │     ├── is_already_posted()       ← Anti-duplicados
            │     ├── build_media_embed()       ← Embed premium
            │     └── channel.send()            ← Discord
            ├── /preview
            └── /instagram_status
```

---

## 🤝 Contribuir

Las contribuciones son bienvenidas. Por favor:

1. Haz un **fork** del repositorio
2. Crea una rama de funcionalidad: `git checkout -b feat/nueva-funcionalidad`
3. Escribe código limpio con **comentarios en español**
4. Haz commit: `git commit -m "feat: descripción clara del cambio"`
5. Abre un **Pull Request** con descripción detallada

**Estilo de código:**
- Comentarios y docstrings en **español neutro**
- Mensajes del bot en **español neutro**
- Type hints en todas las funciones
- Logging con contexto claro

---

## 📄 Licencia

MIT © InstaShift Contributors

---

<div align="center">

Hecho con ❤️ usando `discord.py` + `instagrapi` + `aiosqlite`

**[⭐ Dale una estrella si te fue útil](https://github.com/tioreality/InstaShift)**

</div>
