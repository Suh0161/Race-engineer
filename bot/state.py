"""
bot/state.py

Runtime bot state — tracks the active text channel and voice channel.
These are set by /join and read by the engineer event loop.
"""

from __future__ import annotations
from typing import Optional
import discord

# Set by /join, cleared by /leave
active_text_channel: Optional[discord.TextChannel] = None
active_voice_channel: Optional[discord.VoiceChannel] = None
