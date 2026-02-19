"""
telemetry/listener.py
Async UDP listener for F1 25 telemetry.

Primary path:  f1-packets library (decode_packet)
Fallback path: lightweight raw struct parser (works without any library)

Player detection: uses m_playerCarIndex and m_secondaryPlayerCarIndex
from the PacketHeader to find which car indices belong to Player 1 and 2.
"""

from __future__ import annotations
import asyncio
import logging
import os
import struct
import time
from typing import Optional, Callable, Awaitable

from .state import GameState, game_state
from .parser import PacketParser

log = logging.getLogger("f1bot.listener")

UDP_PORT          = int(os.getenv("UDP_PORT", "20777"))
TELEMETRY_TIMEOUT = 30.0   # seconds before warning about lost telemetry

# ── Try to import f1-packets (optional accelerator) ──────────────────────────
_decode_packet = None
try:
    from f1_packets import decode_packet as _decode_packet   # package name: f1-packets
    log.info("f1-packets library loaded (primary decoder).")
except ImportError:
    try:
        from f1packets import decode_packet as _decode_packet  # alternative name
        log.info("f1packets library loaded (primary decoder).")
    except ImportError:
        try:
            from f1.packets import resolve as _decode_packet  # f1-packets 2025.x uses f1.packets
            log.info("f1-packets library loaded (f1.packets.resolve).")
        except ImportError:
            log.warning(
                "f1-packets library not found — raw struct fallback active. "
                "Install with: pip install f1-packets==2025.1.1"
            )

# ── PacketHeader layout (F1 25) ───────────────────────────────────────────────
# <H BB BB B Q f II BB = 29 bytes
# m_packetFormat u16, m_gameYear u8, m_gameMajorVersion u8, m_gameMinorVersion u8,
# m_packetVersion u8, m_packetId u8, m_sessionUID u64, m_sessionTime f32,
# m_frameIdentifier u32, m_overallFrameIdentifier u32,
# m_playerCarIndex u8, m_secondaryPlayerCarIndex u8
_HEADER_FMT  = "<HBBBBBQfIIBB"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)   # 29 bytes

PacketId = int  # type alias

PACKET_SESSION        = 1
PACKET_LAP_DATA       = 2
PACKET_EVENT          = 3
PACKET_PARTICIPANTS   = 4
PACKET_CAR_DAMAGE     = 10
PACKET_CAR_TELEMETRY  = 6
PACKET_CAR_STATUS     = 7


class _RawHeader:
    """Minimal parsed header from raw bytes."""
    __slots__ = (
        "packet_format", "game_year", "game_major", "game_minor",
        "packet_version", "packet_id", "session_uid", "session_time",
        "frame_id", "overall_frame_id", "player_car_index", "secondary_player_car_index",
    )

    def __init__(self, data: bytes):
        (
            self.packet_format, self.game_year, self.game_major, self.game_minor,
            self.packet_version, self.packet_id, self.session_uid, self.session_time,
            self.frame_id, self.overall_frame_id,
            self.player_car_index, self.secondary_player_car_index,
        ) = struct.unpack_from(_HEADER_FMT, data)


class TelemetryListener:
    """
    Listens on UDP port 20777 for F1 25 telemetry packets.

    Player detection (for duo career):
      - If PLAYER1_DISCORD_ID / PLAYER2_DISCORD_ID are set, the first
        session packet tells us which car indices belong to them via
        m_playerCarIndex (P1) and m_secondaryPlayerCarIndex (P2).
      - This is the correct method per the F1 25 UDP spec.
    """

    def __init__(
        self,
        gs: GameState,
        player_discord_ids: Optional[dict[int, str]] = None,
        on_telemetry_lost: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """
        Args:
            gs: Shared GameState instance.
            player_discord_ids: {0: discord_id_p1, 1: discord_id_p2}
                mapping of player slot (0=primary, 1=secondary) to Discord ID.
            on_telemetry_lost: async callback fired when telemetry is lost.
        """
        self.gs                  = gs
        self.parser              = PacketParser(gs)
        self._player_discord_ids = player_discord_ids or {}
        self.on_telemetry_lost   = on_telemetry_lost
        self._running            = False
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._watchdog_task: Optional[asyncio.Task]          = None
        self._player_indices_resolved                        = False

    async def start(self) -> None:
        log.info("Starting F1 25 telemetry listener on 0.0.0.0:%d", UDP_PORT)
        self._running = True

        loop = asyncio.get_event_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(self._on_packet_received),
            local_addr=("0.0.0.0", UDP_PORT),
        )

        self._watchdog_task = asyncio.create_task(self._watchdog())
        log.info("Telemetry listener started.")

    async def stop(self) -> None:
        self._running = False
        if self._transport:
            self._transport.close()
        if self._watchdog_task:
            self._watchdog_task.cancel()
        log.info("Telemetry listener stopped.")

    def _on_packet_received(self, data: bytes) -> None:
        """Called per datagram. Decode and delegate to parser."""
        if len(data) < _HEADER_SIZE:
            return

        try:
            header = _RawHeader(data)
        except struct.error:
            return

        # Auto-detect player car indices from the first valid header
        if not self._player_indices_resolved:
            self._resolve_player_indices(header)

        try:
            if _decode_packet is not None:
                # Use f1-packets library for structured packet objects
                packet = _decode_packet(data)
                if packet is not None:
                    self.parser.process(packet)
            else:
                # Raw fallback: pass a lightweight wrapper to the parser
                self.parser.process_raw(header, data)
        except Exception as e:
            log.debug("Packet processing error (ID=%d, %d bytes): %s",
                      header.packet_id, len(data), e)

        self.gs.last_packet_time = time.time()

    def _resolve_player_indices(self, header: _RawHeader) -> None:
        """
        On the first usable packet, map Discord IDs to their F1 car indices.
        m_playerCarIndex       = index of the primary player's car (Player 1)
        m_secondaryPlayerCarIndex = index of the secondary player's car (Player 2)
          255 = not present / not a multiplayer session
        """
        p1_idx = header.player_car_index
        p2_idx = header.secondary_player_car_index

        if 0 <= p1_idx <= 21:
            ps1 = self.gs.get_player(p1_idx)
            ps1.discord_id = self._player_discord_ids.get(0, "")
            log.info("Player 1 mapped → car index %d (discord: %s)",
                     p1_idx, ps1.discord_id or "unset")

        if 0 <= p2_idx <= 21:
            ps2 = self.gs.get_player(p2_idx)
            ps2.discord_id = self._player_discord_ids.get(1, "")
            log.info("Player 2 mapped → car index %d (discord: %s)",
                     p2_idx, ps2.discord_id or "unset")

        self._player_indices_resolved = True

    async def _watchdog(self) -> None:
        while self._running:
            await asyncio.sleep(10)
            elapsed = time.time() - self.gs.last_packet_time
            if elapsed > TELEMETRY_TIMEOUT:
                log.warning("Telemetry timeout: no packet for %.0fs", elapsed)
                if self.on_telemetry_lost:
                    try:
                        await self.on_telemetry_lost()
                    except Exception as e:
                        log.error("Telemetry-lost callback error: %s", e)


class _UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, callback: Callable[[bytes], None]):
        self._callback = callback

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._callback(data)

    def error_received(self, exc: Exception) -> None:
        log.warning("UDP error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            log.error("UDP connection lost: %s", exc)
