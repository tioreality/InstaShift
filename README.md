<div align="center">

# 📸 InstaShift

**Mirror any public Instagram account to your Discord server — Posts, Reels & Stories, almost in real-time.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![discord.py](https://img.shields.io/badge/discord.py-2.3%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io)
[![instagrapi](https://img.shields.io/badge/instagrapi-2.x-E1306C?logo=instagram&logoColor=white)](https://subzeroid.github.io/instagrapi/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## ✨ Features

- **Auto-posting** — Polls Instagram feeds every 10 minutes and sends new Posts, Reels, and Stories straight to Discord channels or threads
- **Elegant embeds** — Profile picture, large image, clean caption, separated hashtags, like/comment counts, and a direct "Ver en Instagram" button
- **Persistent session** — Logs in once with instagrapi and persists the session to `ig_session.json` — no repeated logins
- **Anti-duplicate / anti-spam** — Tracks posted media IDs per feed; skips all existing content on the first cycle
- **Multi-server** — Each guild manages its own subscriptions independently
- **Role mentions** — Optionally ping a role on every new post
- **Thread support** — Post to a specific thread instead of the parent channel
- **Guest mode** — Works without credentials for public accounts (limited)
- **/preview** — Preview any account's latest post without subscribing
- **Docker-ready** — Lean multi-stage `Dockerfile` included

---

## 🗂️ Project Structure

```
instashift/
├── bot/
│   ├── __init__.py            # Package marker + version
│   ├── main.py                # Bot entry-point & setup_hook
│   ├── database.py            # Async SQLite wrapper (aiosqlite)
│   ├── cogs/
│   │   ├── __init__.py
│   │   ├── feeds.py           # /follow /unfollow /list /dashboard /checknow /sync
│   │   └── instagram_scraper.py  # Feed-loop task + /preview /instagram_status
│   └── utils/
│       └── __init__.py        # Utility helpers (extend as needed)
├── .env.example               # Environment variable template
├── .gitignore
├── Dockerfile
├── README.md
├── requirements.txt
└── run.sh                     # Local launcher script
```

---

## ⚙️ Configuration (.env)

Copy `.env.example` to `.env` and fill in your values:

```env
# Discord
DISCORD_TOKEN=your_discord_bot_token_here
GUILD_ID=                        # Leave empty for production (global commands)

# Instagram
IG_USERNAME=your_instagram_username
IG_PASSWORD=your_instagram_password

# Optional tweaks
DB_PATH=instashift.db
SESSION_PATH=ig_session.json
CHECK_INTERVAL=10                 # Minutes between feed checks
LOG_LEVEL=INFO
```

> ⚠️ **Never commit your `.env` file.** It's in `.gitignore` by default.

---

## 🚀 Installation & Deployment

### Local (bash)

```bash
git clone https://github.com/YOUR_USERNAME/InstaShift.git
cd InstaShift
cp .env.example .env
# Fill in DISCORD_TOKEN + IG_USERNAME + IG_PASSWORD in .env
bash run.sh
```

### Manual (venv)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

### Docker

```bash
docker build -t instashift .
docker run -d \
  --name instashift \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  instashift
```

### Railway

1. Fork this repo
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add environment variables in the Railway dashboard
4. Deploy — Railway auto-detects the `Dockerfile`

### Replit

1. Import repo into Replit
2. Add secrets (env vars) via **Secrets** panel
3. Set run command: `python -m bot.main`
4. Keep-alive with [UptimeRobot](https://uptimerobot.com) if needed

### VPS (systemd)

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
```

---

## 🤖 Bot Setup (Discord Developer Portal)

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Create a new application → **Bot** tab → **Add Bot**
3. Copy the **Token** → set as `DISCORD_TOKEN`
4. Enable **Server Members Intent** and **Message Content Intent** if needed
5. OAuth2 → URL Generator → select `bot` + `applications.commands`
6. Required permissions: **Send Messages**, **Embed Links**, **View Channels**
7. Invite the bot to your server with the generated URL

---

## 📋 Commands

| Command | Description | Permission |
|---------|-------------|------------|
| `/follow` | Subscribe a channel to an Instagram account | Manage Server |
| `/unfollow` | Remove a subscription | Manage Server |
| `/list` | List all active feeds in this server | Manage Server |
| `/dashboard` | Rich embed overview of all feeds | Manage Server |
| `/checknow` | Force an immediate feed check | Manage Server |
| `/preview @user` | Preview the latest post from any account | Everyone |
| `/instagram_status` | Check Instagram session health | Manage Server |
| `/sync` | Re-sync slash commands | Administrator |
| `/sync clear` | Remove all guild slash commands | Administrator |

---

## 🗄️ Database Schema

InstaShift uses **SQLite** (via aiosqlite) with two tables:

```sql
feeds (
  id                 INTEGER PRIMARY KEY,
  guild_id           INTEGER,
  instagram_account  TEXT,
  channel_id         INTEGER,
  thread_id          INTEGER,     -- optional
  role_id            INTEGER,     -- optional mention
  last_media_id      TEXT,
  active             INTEGER DEFAULT 1,
  created_at         TEXT
)

posted_media (
  id         INTEGER PRIMARY KEY,
  feed_id    INTEGER REFERENCES feeds(id),
  media_id   TEXT,
  posted_at  TEXT
)
```

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Commit your changes: `git commit -m "feat: add your feature"`
4. Push and open a Pull Request

Please follow the existing code style (type hints, docstrings, logging) and keep commits focused.

---

## 📄 License

MIT © InstaShift Contributors

---

<div align="center">
Made with ❤️ and <code>discord.py</code> + <code>instagrapi</code>
</div>
