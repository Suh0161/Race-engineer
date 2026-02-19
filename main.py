"""
main.py
Entry point for the F1 25 Race Engineer Discord Bot.
Starts the Discord bot and UDP telemetry listener concurrently.
"""

from __future__ import annotations
import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# Environment
# ──────────────────────────────────────────────
load_dotenv()

DISCORD_TOKEN    = os.getenv("DISCORD_BOT_TOKEN", "")
PLAYER1_ID       = os.getenv("PLAYER1_DISCORD_ID", "")
PLAYER2_ID       = os.getenv("PLAYER2_DISCORD_ID", "")

# ──────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)

log_formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# Rotating file handler
file_handler = logging.handlers.RotatingFileHandler(
    "logs/f1_engineer.log",
    maxBytes=5_000_000,   # 5 MB
    backupCount=3,
    encoding="utf-8",
)
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

log = logging.getLogger("f1bot.main")

# ──────────────────────────────────────────────
# Bot subclass
# ──────────────────────────────────────────────

class F1EngineerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content  = True
        intents.members           = True
        intents.voice_states      = True
        super().__init__(command_prefix="!", intents=intents)

        from bot.voice import VoiceManager
        self.voice_manager = VoiceManager(self)
        self._telemetry_listener = None

    async def setup_hook(self) -> None:
        """Called once after login, before starting the bot's event loop."""
        # Initialise database
        from database.db import init_db
        await init_db()

        # Load cogs (discord.py calls setup() in each module)
        for ext in ("bot.commands", "bot.events"):
            await self._load_ext(ext)

        # ──────────────────────────────────────────────
        # CRITICAL: Force clean voice state on startup
        # ──────────────────────────────────────────────
        # This prevents "WebSocket closed with 4006" loops caused by zombie sessions.
        # If Discord thinks we're connected but we aren't, this clears it.
        for vc in self.voice_clients:
            try:
                log.info("Cleaning up zombie voice connection: %s", vc.channel)
                await vc.disconnect(force=True)
            except Exception as e:
                log.warning("Failed to clean up voice: %s", e)
        
        # Also ensure our manager state is clean
        if self.voice_manager.vc:
             await self.voice_manager.disconnect()


        # Start playback loop
        asyncio.create_task(self.voice_manager.start_playback_loop())

        # Start UDP telemetry listener
        from telemetry.listener import TelemetryListener
        from telemetry.state import game_state

        # Map player slot (0=primary, 1=secondary) → Discord IDs
        # Car indices are auto-detected from m_playerCarIndex / m_secondaryPlayerCarIndex
        # in the first received F1 25 packet header — no need to hardcode car 0/1.
        player_discord_ids: dict[int, str] = {}
        if PLAYER1_ID:
            player_discord_ids[0] = PLAYER1_ID
            log.info("Player 1 Discord ID: %s", PLAYER1_ID)
        if PLAYER2_ID:
            player_discord_ids[1] = PLAYER2_ID
            log.info("Player 2 Discord ID: %s", PLAYER2_ID)

        self._telemetry_listener = TelemetryListener(
            gs=game_state,
            player_discord_ids=player_discord_ids,
            on_telemetry_lost=self._on_telemetry_lost,
        )
        asyncio.create_task(self._telemetry_listener.start())
        log.info("Setup complete. Bot ready.")

    async def _load_ext(self, module: str) -> None:
        """Load a cog from its module, with error logging."""
        try:
            await self.load_extension(module)
            log.info("Loaded extension: %s", module)
        except Exception as e:
            log.exception("Failed to load extension %s: %s", module, e)

    async def _on_telemetry_lost(self) -> None:
        """Callback when telemetry has not been received for 30 seconds."""
        # Events cog handles the actual message — this is a secondary hook.
        log.warning("Telemetry lost callback fired from listener.")

    async def close(self) -> None:
        log.info("Shutting down bot...")
        if self._telemetry_listener:
            await self._telemetry_listener.stop()
        from database.db import close_db
        await close_db()
        await self.voice_manager.disconnect()
        await super().close()


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main() -> None:
    if not DISCORD_TOKEN:
        log.error("DISCORD_BOT_TOKEN is not set in .env — cannot start.")
        sys.exit(1)

    bot = F1EngineerBot()

    try:
        asyncio.run(bot.start(DISCORD_TOKEN))
    except KeyboardInterrupt:
        log.info("Bot interrupted by user.")
    except discord.LoginFailure as e:
        log.error("Discord login failed: %s", e)
        sys.exit(1)
    except Exception as e:
        log.exception("Unexpected error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
