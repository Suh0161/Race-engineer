"""
bot/events.py
Discord event handlers: on_ready, on_voice_state_update, etc.
Also drives the engineer logic evaluation loop that monitors race state.
"""

from __future__ import annotations
import asyncio
import logging
import os
from datetime import date
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

import bot.state as bot_state
from telemetry.state import game_state
from engineer.logic import EngineerLogic, TriggerType
from engineer.radio import generate_radio_message
from database.models import add_lap_history, get_session_laps

if TYPE_CHECKING:
    from bot.voice import VoiceManager

log = logging.getLogger("f1bot.events")

EVAL_INTERVAL   = 3.0    # seconds between trigger evaluations


# Cooldown between "Telemetry lost" Discord messages (avoid spam when game is closed)
TELEMETRY_LOST_COOLDOWN = int(os.getenv("TELEMETRY_LOST_COOLDOWN", "600"))   # seconds; 0 = post once only
TELEMETRY_LOST_WARN = os.getenv("TELEMETRY_LOST_WARN", "true").lower() in ("true", "1", "yes")

class EngineerEvents(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self.logic   = EngineerLogic()
        self._telemetry_warned = False
        self._last_telemetry_warn_time: float = 0   # when we last posted the warning
        self._last_lap: dict[int, int] = {}   # car_idx → last saved lap number

    # ────────────────────────────────────────
    # on_ready
    # ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        log.info("Bot logged in as %s (ID: %s)", self.bot.user, self.bot.user.id)
        
        # ──────────────────────────────────────────────
        # CLEANUP: Force disconnect any lingering voice sessions
        # ──────────────────────────────────────────────
        for vc in self.bot.voice_clients:
            try:
                await vc.disconnect(force=True)
                log.info("Cleaned up zombie voice session in %s", vc.channel)
            except Exception:
                pass

        # Start background tasks
        self.evaluate_loop.start()
        self.telemetry_watchdog.start()
        # Try auto-join if a fallback voice channel ID is configured
        fallback_vc_id = int(os.getenv("DISCORD_VOICE_CHANNEL_ID", "0"))
        fallback_tc_id = int(os.getenv("DISCORD_TEXT_CHANNEL_ID", "0"))
        if fallback_vc_id:
            await self.bot.voice_manager.connect(channel_id=fallback_vc_id)
            if fallback_tc_id:
                ch = self.bot.get_channel(fallback_tc_id)
                if ch:
                    bot_state.active_text_channel = ch
        else:
            log.info("No fallback voice channel set — use /join in Discord to connect.")
        # Sync slash commands
        try:
            synced = await self.bot.tree.sync()
            log.info("Synced %d slash commands.", len(synced))
        except Exception as e:
            log.error("Failed to sync slash commands: %s", e)

    # ────────────────────────────────────────
    # Engineer evaluation loop
    # ────────────────────────────────────────

    @tasks.loop(seconds=EVAL_INTERVAL)
    async def evaluate_loop(self) -> None:
        """Evaluate triggers for every registered player every N seconds."""
        vm: VoiceManager = self.bot.voice_manager

        for car_idx, ps in game_state.players.items():
            events = self.logic.evaluate(ps)
            for event in events:
                try:
                    message_text = await generate_radio_message(event)
                    await vm.speak_text(message_text, priority=event.priority)
                except Exception as e:
                    log.error("Error processing event %s: %s", event.trigger.name, e)

            # Persist completed laps to DB
            await self._maybe_save_lap(ps)

    @evaluate_loop.before_loop
    async def before_evaluate(self) -> None:
        await self.bot.wait_until_ready()

    # ────────────────────────────────────────
    # Persist lap history
    # ────────────────────────────────────────

    async def _maybe_save_lap(self, ps) -> None:
        """Save a lap to history when the driver crosses into a new lap."""
        if not ps.discord_id or ps.current_lap <= 1:
            return
        lap_to_save = ps.current_lap - 1   # save the completed lap
        last_saved  = self._last_lap.get(ps.car_index, 0)
        if lap_to_save <= last_saved:
            return

        self._last_lap[ps.car_index] = lap_to_save
        try:
            await add_lap_history(
                discord_id=ps.discord_id,
                track_name=ps.track_name,
                lap_number=lap_to_save,
                lap_time_ms=ps.last_lap_time_ms or None,
                tyre_compound=ps.tyre_compound_name,
                sector1_ms=ps.sector1_ms or None,
                sector2_ms=ps.sector2_ms or None,
            )
            log.debug("Saved lap %d for %s at %s", lap_to_save, ps.driver_name, ps.track_name)
        except Exception as e:
            log.error("Failed to save lap history: %s", e)

    # ────────────────────────────────────────
    # Telemetry watchdog
    # ────────────────────────────────────────

    @tasks.loop(seconds=10)
    async def telemetry_watchdog(self) -> None:
        import time
        if not TELEMETRY_LOST_WARN:
            return
        now = time.time()
        elapsed = now - game_state.last_packet_time
        if elapsed > 30:
            # Post only once per disconnect, or at most every COOLDOWN seconds
            should_post = (
                not self._telemetry_warned
                or (TELEMETRY_LOST_COOLDOWN > 0 and (now - self._last_telemetry_warn_time) >= TELEMETRY_LOST_COOLDOWN)
            )
            if should_post:
                self._telemetry_warned = True
                self._last_telemetry_warn_time = now
                log.warning("Telemetry lost for %.0f seconds.", elapsed)
                await self._post_text(
                    "⚠️ **Telemetry lost.** Check F1 25 UDP settings:\n"
                    "> Settings → Telemetry → UDP **On**, Format **2025**, Port **20777**, "
                    "IP = this PC's IP address."
                )
        else:
            self._telemetry_warned = False

    @telemetry_watchdog.before_loop
    async def before_watchdog(self) -> None:
        await self.bot.wait_until_ready()

    # ────────────────────────────────────────
    # Chequered flag handler (called from main.py on event packet)
    # ────────────────────────────────────────

    async def on_chequered_flag(self) -> None:
        """Called when F1 25 sends a chequered flag event."""
        for car_idx, ps in game_state.players.items():
            event = self.logic.on_chequered_flag(ps)
            if event:
                message_text = await generate_radio_message(event)
                await self.bot.voice_manager.speak_text(message_text)

        # Post debrief embed to text channel
        await self._post_session_debrief()

    async def _post_session_debrief(self) -> None:
        """Build and post a dual-player debrief embed after the race."""
        embed = discord.Embed(
            title="🏁 Post-Race Debrief",
            colour=discord.Colour.gold(),
        )

        for car_idx, ps in game_state.players.items():
            if not ps.discord_id:
                continue
            laps = await get_session_laps(ps.discord_id, ps.track_name, str(date.today()))
            if not laps:
                continue

            valid  = [l["lap_time_ms"] for l in laps if l["lap_time_ms"] and l["lap_time_ms"] > 0]
            best   = min(valid) if valid else None
            avg    = int(sum(valid) / len(valid)) if valid else None
            p_name = ps.driver_name
            pos    = ps.current_position

            def ms_to_str(ms: int | None) -> str:
                if not ms:
                    return "N/A"
                m, r = divmod(ms, 60_000)
                s, mi = divmod(r, 1000)
                return f"{int(m)}:{int(s):02d}.{int(mi):03d}"

            embed.add_field(
                name=f"🏎️ {p_name} — P{pos}",
                value=(
                    f"Laps: **{len(laps)}**\n"
                    f"Best: **{ms_to_str(best)}**\n"
                    f"Avg: **{ms_to_str(avg)}**\n"
                    f"Tyres: **{ps.tyre_compound_name}**"
                ),
                inline=True,
            )

        await self._post_text(embed=embed)

    async def _post_text(self, message: str = None, embed: discord.Embed = None) -> None:
        channel = bot_state.active_text_channel
        if not channel:
            log.debug("No active text channel — use /join to set one.")
            return
        try:
            if embed:
                await channel.send(embed=embed)
            elif message:
                await channel.send(message)
        except Exception as e:
            log.error("Failed to post to text channel: %s", e)

    # ────────────────────────────────────────
    # Voice state reconnect
    # ────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EngineerEvents(bot))
