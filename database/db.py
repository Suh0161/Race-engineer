"""
database/db.py
SQLite database connection and schema initialisation using aiosqlite.
"""

import asyncio
import logging
import os
import aiosqlite
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("f1bot.database")

DB_PATH = os.getenv("DATABASE_PATH", "f1_engineer.db")

_connection: aiosqlite.Connection | None = None


CREATE_DRIVERS = """
CREATE TABLE IF NOT EXISTS drivers (
    discord_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    driving_style   TEXT NOT NULL DEFAULT 'balanced',
    preferred_tyre  TEXT NOT NULL DEFAULT 'medium',
    preferred_brake_bias INTEGER NOT NULL DEFAULT 56,
    preferred_ers_mode    TEXT NOT NULL DEFAULT 'balanced',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_TRACK_SETUPS = """
CREATE TABLE IF NOT EXISTS track_setups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id          TEXT NOT NULL,
    track_name          TEXT NOT NULL,
    front_wing          INTEGER DEFAULT 5,
    rear_wing           INTEGER DEFAULT 5,
    on_throttle         INTEGER DEFAULT 50,
    off_throttle        INTEGER DEFAULT 50,
    front_camber        REAL DEFAULT -2.50,
    rear_camber         REAL DEFAULT -1.00,
    front_toe           REAL DEFAULT 0.09,
    rear_toe            REAL DEFAULT 0.32,
    front_suspension    INTEGER DEFAULT 4,
    rear_suspension     INTEGER DEFAULT 4,
    front_anti_roll_bar INTEGER DEFAULT 5,
    rear_anti_roll_bar  INTEGER DEFAULT 5,
    front_ride_height   INTEGER DEFAULT 20,
    rear_ride_height    INTEGER DEFAULT 30,
    brake_pressure      INTEGER DEFAULT 100,
    brake_bias          INTEGER DEFAULT 56,
    front_tyre_pressure REAL DEFAULT 23.5,
    rear_tyre_pressure  REAL DEFAULT 21.5,
    ballast             INTEGER DEFAULT 8,
    fuel_load           REAL DEFAULT 100.0,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(discord_id, track_name)
);
"""

CREATE_LAP_HISTORY = """
CREATE TABLE IF NOT EXISTS lap_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      TEXT NOT NULL,
    track_name      TEXT NOT NULL,
    session_date    TEXT NOT NULL DEFAULT (date('now')),
    lap_number      INTEGER NOT NULL,
    lap_time_ms     INTEGER,
    tyre_compound   TEXT,
    sector1_ms      INTEGER,
    sector2_ms      INTEGER,
    sector3_ms      INTEGER,
    finish_position INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db() -> None:
    """Initialise the database and create tables if they do not exist."""
    global _connection
    log.info("Initialising SQLite database at: %s", DB_PATH)
    _connection = await aiosqlite.connect(DB_PATH)
    _connection.row_factory = aiosqlite.Row
    await _connection.execute("PRAGMA journal_mode=WAL;")
    await _connection.execute("PRAGMA foreign_keys=ON;")
    await _connection.execute(CREATE_DRIVERS)
    await _connection.execute(CREATE_TRACK_SETUPS)
    await _connection.execute(CREATE_LAP_HISTORY)
    await _connection.commit()
    log.info("Database initialised successfully.")


async def get_db() -> aiosqlite.Connection:
    """Return the active database connection, initialising if needed."""
    global _connection
    if _connection is None:
        await init_db()
    return _connection


async def close_db() -> None:
    """Close the database connection gracefully."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
        log.info("Database connection closed.")
