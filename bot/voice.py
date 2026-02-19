"""
bot/voice.py
Handles joining/leaving voice channels and playing TTS audio files.
Maintains a queue of pending messages (max 2) and never interrupts
a currently-playing message.
"""

from __future__ import annotations
import asyncio
import logging
import os
from typing import Optional

import discord

import bot.state as bot_state
from engineer.tts import cleanup_audio

log = logging.getLogger("f1bot.voice")

VOICE_CHANNEL_ID = int(os.getenv("DISCORD_VOICE_CHANNEL_ID", "0"))
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")  # Path to ffmpeg.exe, or "ffmpeg" to use PATH


class VoiceManager:
    """Manages the bot's voice connection and audio queue."""

    MAX_QUEUE  = 2
    RECONNECT_DELAY = 5.0

    # Triggers with priority value <= this threshold can interrupt current playback.
    # Matches TriggerType enum: CRITICAL_FUEL=1, CRITICAL_TYRES=2, DAMAGE=3,
    # RED_FLAG=4, SC=5, SC_ENDING=6, VSC=7, VSC_ENDING=8, BLUE_FLAG=9, YELLOW_FLAG=10
    # PENALTY=11. Anything <= 11 pre-empts routine chatter.
    INTERRUPT_THRESHOLD = 11

    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot        = bot
        self.vc: Optional[discord.VoiceClient] = None
        self._queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue(maxsize=self.MAX_QUEUE)
        self._muted     = False
        self._play_task: Optional[asyncio.Task] = None
        # Track the priority of what is currently playing/queued
        self._current_priority: int = 999   # 999 = nothing playing
        self._pending_priority: int = 999   # lowest item in the queue

    # ──────────────────────────────────────────
    # Connection management
    # ──────────────────────────────────────────

    async def connect(self, channel_id: Optional[int] = None) -> bool:
        """Join a voice channel. Falls back to configured channel or active state."""
        target_id = channel_id or VOICE_CHANNEL_ID
        
        # If no explicit target, try the one from state (reconnect scenario)
        if not target_id and bot_state.active_voice_channel:
             target_id = bot_state.active_voice_channel.id

        if not target_id:
            log.error("No voice channel ID configured.")
            return False

        channel = self.bot.get_channel(target_id)
        if not isinstance(channel, discord.VoiceChannel):
            log.error("Channel %s is not a voice channel.", target_id)
            return False

        # Try to connect/move
        try:
            if self.vc and self.vc.is_connected():
                # Already connected — try moving
                try:
                    await self.vc.move_to(channel)
                except Exception:
                    # Move failed — kill connection and retry fresh
                    await self.cleanup_connection()
                    self.vc = await channel.connect(reconnect=True)
            else:
                # Not connected — fresh connect
                # Ensure clean slate first
                await self.cleanup_connection()
                self.vc = await channel.connect(reconnect=True)

            log.info("Connected to voice channel: %s", channel.name)
            return True

        except discord.errors.ConnectionClosed as e:
            if e.code == 4006:
                log.warning("Voice connection interrupted (4006) — retrying...")
            else:
                log.warning("Voice connection closed (%s) — retrying...", e.code)
            await self.cleanup_connection()
            await asyncio.sleep(1.0)
            return False

        except Exception as e:
            log.error("Failed to connect to voice channel: %s", e)
            # Last ditch cleanup
            await self.cleanup_connection()
            return False

    async def cleanup_connection(self) -> None:
        """Forcefully clean up any existing voice client state."""
        if self.vc:
            try:
                await self.vc.disconnect(force=True)
            except Exception:
                pass
            self.vc = None

    async def disconnect(self) -> None:
        """Leave the voice channel."""
        await self.cleanup_connection()
        log.info("Disconnected from voice channel.")

    async def ensure_connected(self) -> bool:
        """Reconnect if the bot has been disconnected but should be online."""
        if self.vc and self.vc.is_connected():
            return True
            
        # Only reconnect if we *expect* to be connected
        if not bot_state.active_voice_channel and not VOICE_CHANNEL_ID:
            return False

        log.warning("Voice connection lost — attempting reconnect.")
        return await self.connect()

    # ──────────────────────────────────────────
    # Audio playback
    # ──────────────────────────────────────────

    async def start_playback_loop(self) -> None:
        """Background task that continuously processes the audio queue."""
        while True:
            try:
                file_path, message_text, priority = await self._queue.get()
                if self._muted:
                    log.debug("Muted — skipping audio: %s", message_text)
                    cleanup_audio(file_path)
                    self._queue.task_done()
                    continue

                # Re-connect if needed
                if not await self.ensure_connected():
                    await asyncio.sleep(self.RECONNECT_DELAY)
                    self._queue.task_done()
                    continue

                # Brief poll: if something is still finishing, give it a moment
                if self.vc.is_playing():
                    await asyncio.sleep(0.3)

                self._current_priority = priority
                await self._play_file(file_path)
                self._current_priority = 999   # done playing
                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Playback loop error: %s", e)
                await asyncio.sleep(1)

    async def _play_file(self, file_path: str) -> None:
        """Play an audio file and wait for it to complete."""
        if not self.vc or not self.vc.is_connected():
            log.warning("Cannot play — not connected.")
            cleanup_audio(file_path)
            return

        done_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def after_play(error: Optional[Exception]) -> None:
            if error:
                log.error("Playback error: %s", error)
            cleanup_audio(file_path)
            loop.call_soon_threadsafe(done_event.set)

        try:
            source = discord.FFmpegPCMAudio(
                file_path,
                executable=FFMPEG_PATH,
                options="-vn -ar 48000 -ac 2",
            )
            self.vc.play(source, after=after_play)
            await done_event.wait()
        except Exception as e:
            log.error("Error during audio playback: %s", e)
            cleanup_audio(file_path)

    async def queue_message(self, file_path: str, message_text: str = "",
                            priority: int = 999) -> bool:
        """
        Add a radio message to the playback queue.
        Returns False if the queue is full (message dropped).
        """
        if self._queue.full():
            log.debug("Audio queue full — dropping message: %s", message_text[:40])
            cleanup_audio(file_path)
            return False

        self._pending_priority = min(self._pending_priority, priority)
        await self._queue.put((file_path, message_text, priority))
        log.debug("Queued audio (P%d): %s", priority, message_text[:60])
        return True

    async def interrupt_and_speak(self, file_path: str, message_text: str,
                                   priority: int) -> None:
        """
        Pre-empt: stop current playback, drain the queue, then play immediately.
        Used when a high-priority event fires while something else is playing.
        """
        log.info("[INTERRUPT] P%d pre-empts P%d — %s",
                 priority, self._current_priority, message_text[:60])

        # Stop whatever is playing right now
        if self.vc and self.vc.is_playing():
            self.vc.stop()   # triggers after_play callback, which cleans up audio
            await asyncio.sleep(0.15)  # brief gap so Discord registers the stop

        # Drain all pending items from the queue (they're stale now)
        _cleared = self._clear_queue()
        if _cleared:
            log.debug("[INTERRUPT] Drained %d stale queued messages.", _cleared)

        # Reset priorities and play the new urgent message immediately
        self._current_priority = priority
        self._pending_priority = 999
        await self._play_file(file_path)
        self._current_priority = 999

    def _clear_queue(self) -> int:
        """Drain all items from the queue. Returns number of items cleared."""
        count = 0
        while not self._queue.empty():
            try:
                fp, _, _p = self._queue.get_nowait()
                cleanup_audio(fp)
                self._queue.task_done()
                count += 1
            except asyncio.QueueEmpty:
                break
        self._pending_priority = 999
        return count

    # ──────────────────────────────────────────
    # Mute / unmute
    # ──────────────────────────────────────────

    def mute(self) -> None:
        self._muted = True
        log.info("Radio messages muted.")

    def unmute(self) -> None:
        self._muted = False
        log.info("Radio messages unmuted.")

    @property
    def is_muted(self) -> bool:
        return self._muted

    async def speak_text(self, text: str, priority: int = 999) -> bool:
        """
        Generate TTS audio from text and route to playback.
        - If priority <= INTERRUPT_THRESHOLD AND is higher-priority than what's
          currently playing or queued, calls interrupt_and_speak() to cut through.
        - Otherwise queues normally.
        Returns True if successfully handled.
        """
        from engineer.tts import generate_tts_audio
        file_path = await generate_tts_audio(text)
        if not file_path:
            log.warning("TTS failed — could not speak: %s", text[:60])
            return False

        # Decide: interrupt or queue?
        is_urgent   = priority <= self.INTERRUPT_THRESHOLD
        can_preempt = priority < self._current_priority and priority < self._pending_priority

        if is_urgent and can_preempt:
            await self.interrupt_and_speak(file_path, text, priority)
        else:
            return await self.queue_message(file_path, text, priority)
        return True
