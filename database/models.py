"""
database/models.py
CRUD helpers for all database tables.
"""

import logging
from typing import Optional
import aiosqlite
from .db import get_db

log = logging.getLogger("f1bot.models")


# ──────────────────────────────────────────────
# DRIVER PROFILES
# ──────────────────────────────────────────────

async def get_driver_profile(discord_id: str) -> Optional[aiosqlite.Row]:
    """Fetch a driver profile by Discord ID."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM drivers WHERE discord_id = ?", (discord_id,)
    ) as cursor:
        return await cursor.fetchone()


async def upsert_driver_profile(
    discord_id: str,
    name: str,
    driving_style: str = "balanced",
    preferred_tyre: str = "medium",
    preferred_brake_bias: int = 56,
    preferred_ers_mode: str = "balanced",
) -> None:
    """Insert or update a driver profile."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO drivers
            (discord_id, name, driving_style, preferred_tyre, preferred_brake_bias, preferred_ers_mode)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(discord_id) DO UPDATE SET
            name = excluded.name,
            driving_style = excluded.driving_style,
            preferred_tyre = excluded.preferred_tyre,
            preferred_brake_bias = excluded.preferred_brake_bias,
            preferred_ers_mode = excluded.preferred_ers_mode,
            updated_at = datetime('now')
        """,
        (discord_id, name, driving_style, preferred_tyre, preferred_brake_bias, preferred_ers_mode),
    )
    await db.commit()
    log.debug("Upserted driver profile for %s", discord_id)


# ──────────────────────────────────────────────
# TRACK SETUPS
# ──────────────────────────────────────────────

async def get_track_setup(discord_id: str, track_name: str) -> Optional[aiosqlite.Row]:
    """Fetch a setup for a given driver and track."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM track_setups WHERE discord_id = ? AND track_name = ?",
        (discord_id, track_name.lower()),
    ) as cursor:
        return await cursor.fetchone()


async def upsert_track_setup(discord_id: str, track_name: str, **kwargs) -> None:
    """Insert or update a track setup. Pass setup fields as keyword arguments."""
    allowed = {
        "front_wing", "rear_wing", "on_throttle", "off_throttle",
        "front_camber", "rear_camber", "front_toe", "rear_toe",
        "front_suspension", "rear_suspension", "front_anti_roll_bar",
        "rear_anti_roll_bar", "front_ride_height", "rear_ride_height",
        "brake_pressure", "brake_bias", "front_tyre_pressure",
        "rear_tyre_pressure", "ballast", "fuel_load",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return

    db = await get_db()
    # Build upsert dynamically
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{col} = excluded.{col}" for col in fields)

    await db.execute(
        f"""
        INSERT INTO track_setups (discord_id, track_name, {cols})
        VALUES (?, ?, {placeholders})
        ON CONFLICT(discord_id, track_name) DO UPDATE SET
            {updates},
            updated_at = datetime('now')
        """,
        (discord_id, track_name.lower(), *fields.values()),
    )
    await db.commit()
    log.debug("Upserted setup for %s at %s", discord_id, track_name)


# ──────────────────────────────────────────────
# LAP HISTORY
# ──────────────────────────────────────────────

async def add_lap_history(
    discord_id: str,
    track_name: str,
    lap_number: int,
    lap_time_ms: Optional[int] = None,
    tyre_compound: Optional[str] = None,
    sector1_ms: Optional[int] = None,
    sector2_ms: Optional[int] = None,
    sector3_ms: Optional[int] = None,
    finish_position: Optional[int] = None,
) -> None:
    """Record a completed lap to the database."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO lap_history
            (discord_id, track_name, lap_number, lap_time_ms, tyre_compound,
             sector1_ms, sector2_ms, sector3_ms, finish_position)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (discord_id, track_name.lower(), lap_number, lap_time_ms,
         tyre_compound, sector1_ms, sector2_ms, sector3_ms, finish_position),
    )
    await db.commit()


async def get_lap_history(
    discord_id: str,
    track_name: Optional[str] = None,
    limit: int = 50,
) -> list[aiosqlite.Row]:
    """Retrieve lap history, optionally filtered by track."""
    db = await get_db()
    if track_name:
        async with db.execute(
            """
            SELECT * FROM lap_history
            WHERE discord_id = ? AND track_name = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (discord_id, track_name.lower(), limit),
        ) as cursor:
            return await cursor.fetchall()
    else:
        async with db.execute(
            """
            SELECT * FROM lap_history
            WHERE discord_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (discord_id, limit),
        ) as cursor:
            return await cursor.fetchall()


async def get_session_laps(discord_id: str, track_name: str, session_date: str) -> list[aiosqlite.Row]:
    """Retrieve all laps for a given player in a specific session."""
    db = await get_db()
    async with db.execute(
        """
        SELECT * FROM lap_history
        WHERE discord_id = ? AND track_name = ? AND session_date = ?
        ORDER BY lap_number ASC
        """,
        (discord_id, track_name.lower(), session_date),
    ) as cursor:
        return await cursor.fetchall()
