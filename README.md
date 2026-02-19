# 🏎️ F1 25 Race Engineer Discord Bot

A fully-functional AI race engineer Discord bot that listens to live UDP telemetry from **F1 25**, generates contextual radio messages using Claude AI, converts them to speech via ElevenLabs, and delivers them live into a Discord voice channel — just like a real F1 race engineer.

Supports **duo career mode** with separate state tracking and profiles for two players.

---

## 📋 Prerequisites

### 1. Python 3.11+
Download from [python.org](https://python.org). Verify: `python --version`

### 2. FFmpeg
Required for audio streaming into Discord voice channels.

**Windows:**
1. Download FFmpeg from [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
2. Extract and add the `bin` folder to your system PATH
3. Verify: `ffmpeg -version`

**Linux/Mac:** `sudo apt install ffmpeg` or `brew install ffmpeg`

### 3. F1 25 (PC)
The game must be running with UDP telemetry enabled (see below).

---

## ⚙️ F1 25 UDP Telemetry Setup

In **F1 25**, go to:
> **Settings → Telemetry → UDP Telemetry Settings**

| Setting | Value |
|---|---|
| UDP Telemetry | **On** |
| UDP Format | **2025** |
| UDP Broadcast Mode | **Off** |
| UDP IP Address | IP address of the PC running this bot |
| UDP Port | **20777** |
| UDP Send Rate | **60Hz** (recommended) |

---

## 🚀 Installation

### 1. Clone / Download the project
```bash
cd f1_engineer_bot
```

### 2. Create and activate a virtual environment
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

---

## 🔑 Environment Setup

1. Copy the example env file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and fill in all values:

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Your Discord bot token from [discord.com/developers](https://discord.com/developers) |
| `DISCORD_VOICE_CHANNEL_ID` | Right-click your voice channel → Copy ID |
| `DISCORD_TEXT_CHANNEL_ID` | Right-click your text channel → Copy ID |
| `PLAYER1_DISCORD_ID` | Right-click Player 1's Discord profile → Copy ID |
| `PLAYER2_DISCORD_ID` | Right-click Player 2's Discord profile → Copy ID |
| `ANTHROPIC_API_KEY` | From [console.anthropic.com](https://console.anthropic.com) |
| `ELEVENLABS_API_KEY` | From [elevenlabs.io](https://elevenlabs.io) |
| `ELEVENLABS_VOICE_ID` | Choose a British male voice from ElevenLabs voice library |
| `UDP_PORT` | `20777` (default) |
| `UDP_FORMAT` | `2025` |

### Creating a Discord Bot
1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Create a new application → Bot tab → Create Bot
3. Enable **Message Content Intent**, **Server Members Intent**, **Presence Intent**
4. Under OAuth2 → URL Generator, select: `bot`, `applications.commands`
5. Bot Permissions: `Send Messages`, `Connect`, `Speak`, `Use Slash Commands`
6. Copy the generated URL and use it to invite the bot to your server

---

## ▶️ Running the Bot

```bash
python main.py
```

The bot will:
1. Connect to Discord and join the configured voice channel
2. Start listening for F1 25 UDP telemetry on port 20777
3. Begin monitoring your race data and sending radio messages

---

## 👤 Setting Up Driver Profiles

Use the `/profile setup` slash command in Discord to configure your:
- **Driving style**: Aggressive / Balanced / Smooth
- **Preferred tyre**: Soft / Medium / Hard
- **Brake bias**: Numeric value (default 56)
- **ERS mode**: Harvesting / Balanced / Attack

View your profile with `/profile view`.

---

## 🎮 Slash Commands

| Command | Description |
|---|---|
| `/join` | Bot joins your current voice channel |
| `/leave` | Bot leaves the voice channel |
| `/profile setup` | Configure your driver profile |
| `/profile view` | View your current driver profile |
| `/setup [track] [condition]` | Get a car setup recommendation |
| `/debrief` | Post-session performance debrief |
| `/history [track]` | View your lap history at a track |
| `/engineer mute` | Mute radio messages |
| `/engineer unmute` | Unmute radio messages |

---

## 📁 Project Structure

```
f1_engineer_bot/
├── main.py                  # Entry point
├── bot/
│   ├── commands.py          # Slash commands
│   ├── voice.py             # Voice channel + audio queue
│   └── events.py            # Discord event handlers
├── telemetry/
│   ├── listener.py          # Async UDP listener
│   ├── parser.py            # Packet parser
│   └── state.py             # Live race state
├── engineer/
│   ├── logic.py             # Trigger thresholds + cooldowns
│   ├── radio.py             # Claude AI message generation
│   └── tts.py               # ElevenLabs TTS
├── database/
│   ├── db.py                # SQLite connection
│   └── models.py            # Table definitions + queries
├── logs/                    # Log files
├── .env                     # Your API keys (never commit!)
├── .env.example             # Template
└── requirements.txt
```

---

## 🔧 Troubleshooting

- **"Telemetry lost" warning**: Verify F1 25 UDP settings and that the bot's PC IP is correct
- **No audio in voice**: Check FFmpeg is installed and in PATH
- **Bot not responding to slash commands**: Wait a few minutes for Discord to register commands, or re-invite the bot
- **ElevenLabs errors**: Check your API key and that you have quota remaining

---

## 📝 Logs

All events and errors are logged to `logs/f1_engineer.log` with timestamps.
