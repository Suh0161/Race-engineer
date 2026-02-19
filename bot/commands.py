"""
bot/commands.py
Discord slash commands for the F1 Race Engineer bot.
"""

from __future__ import annotations
import logging
import os
from datetime import date
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from database.models import (
    get_driver_profile, upsert_driver_profile,
    get_track_setup, upsert_track_setup,
    get_lap_history, get_session_laps,
)
import bot.state as bot_state

if TYPE_CHECKING:
    from bot.voice import VoiceManager

log = logging.getLogger("f1bot.commands")

PLAYER1_ID = os.getenv("PLAYER1_DISCORD_ID", "")
PLAYER2_ID = os.getenv("PLAYER2_DISCORD_ID", "")


def _ms_to_laptime(ms: int) -> str:
    """Convert milliseconds to M:SS.mmm format."""
    if not ms:
        return "N/A"
    minutes, rem = divmod(ms, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{int(minutes)}:{int(seconds):02d}.{int(millis):03d}"


class EngineerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────
    # VOICE — join / leave
    # ──────────────────────────────────────────────

    @app_commands.command(name="join", description="Bot joins your voice channel")
    async def join(self, interaction: discord.Interaction) -> None:
        if not interaction.user.voice:
            await interaction.response.send_message(
                "❌ You must be in a voice channel first.", ephemeral=True
            )
            return

        vm: VoiceManager = self.bot.voice_manager
        channel_id = interaction.user.voice.channel.id
        ok = await vm.connect(channel_id)
        msg = f"✅ Joined {interaction.user.voice.channel.name}" if ok else "❌ Failed to join voice channel."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="leave", description="Bot leaves the voice channel")
    async def leave(self, interaction: discord.Interaction) -> None:
        vm: VoiceManager = self.bot.voice_manager
        await vm.disconnect()
        await interaction.response.send_message("👋 Left the voice channel.", ephemeral=True)

    # ──────────────────────────────────────────────
    # PROFILE
    # ──────────────────────────────────────────────

    profile_group = app_commands.Group(name="profile", description="Driver profile commands")

    @profile_group.command(name="setup", description="Configure your driver profile")
    @app_commands.describe(
        driving_style="Your driving style",
        preferred_tyre="Your preferred starting tyre",
        brake_bias="Brake bias (50-60, default 56)",
        ers_mode="Your preferred ERS mode",
    )
    @app_commands.choices(
        driving_style=[
            app_commands.Choice(name="Aggressive", value="aggressive"),
            app_commands.Choice(name="Balanced",   value="balanced"),
            app_commands.Choice(name="Smooth",     value="smooth"),
        ],
        preferred_tyre=[
            app_commands.Choice(name="Soft",   value="soft"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Hard",   value="hard"),
        ],
        ers_mode=[
            app_commands.Choice(name="Harvesting", value="harvesting"),
            app_commands.Choice(name="Balanced",   value="balanced"),
            app_commands.Choice(name="Attack",     value="attack"),
        ],
    )
    async def profile_setup(
        self,
        interaction: discord.Interaction,
        driving_style: str = "balanced",
        preferred_tyre: str = "medium",
        brake_bias: int = 56,
        ers_mode: str = "balanced",
    ) -> None:
        discord_id  = str(interaction.user.id)
        driver_name = interaction.user.display_name
        brake_bias  = max(50, min(60, brake_bias))  # clamp

        await upsert_driver_profile(
            discord_id=discord_id,
            name=driver_name,
            driving_style=driving_style,
            preferred_tyre=preferred_tyre,
            preferred_brake_bias=brake_bias,
            preferred_ers_mode=ers_mode,
        )

        embed = discord.Embed(
            title="✅ Driver Profile Updated",
            colour=discord.Colour.green(),
        )
        embed.add_field(name="Driver",         value=driver_name,    inline=True)
        embed.add_field(name="Driving Style",  value=driving_style.title(), inline=True)
        embed.add_field(name="Preferred Tyre", value=preferred_tyre.title(), inline=True)
        embed.add_field(name="Brake Bias",     value=str(brake_bias), inline=True)
        embed.add_field(name="ERS Mode",       value=ers_mode.title(), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @profile_group.command(name="view", description="View your driver profile")
    async def profile_view(self, interaction: discord.Interaction) -> None:
        discord_id = str(interaction.user.id)
        profile    = await get_driver_profile(discord_id)

        if not profile:
            await interaction.response.send_message(
                "❌ No profile found. Use `/profile setup` first.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🏎️ Driver Profile — {profile['name']}",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Driving Style",  value=profile["driving_style"].title(), inline=True)
        embed.add_field(name="Preferred Tyre", value=profile["preferred_tyre"].title(), inline=True)
        embed.add_field(name="Brake Bias",     value=str(profile["preferred_brake_bias"]), inline=True)
        embed.add_field(name="ERS Mode",       value=profile["preferred_ers_mode"].title(), inline=True)
        embed.set_footer(text=f"Last updated: {profile['updated_at']}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ──────────────────────────────────────────────
    # SETUP RECOMMENDATION
    # ──────────────────────────────────────────────

    @app_commands.command(
        name="setup",
        description="Get a car setup recommendation for a track and condition",
    )
    @app_commands.describe(track="Track name (e.g. silverstone)", condition="Track condition (dry/wet)")
    async def setup_command(
        self,
        interaction: discord.Interaction,
        track: str,
        condition: str = "dry",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        profile    = await get_driver_profile(discord_id)
        saved      = await get_track_setup(discord_id, track)

        if profile is None:
            await interaction.followup.send(
                "❌ Please run `/profile setup` first.", ephemeral=True
            )
            return

        # Build recommended setup embed
        embed = discord.Embed(
            title=f"⚙️ Setup Recommendation — {track.title()} ({condition.title()})",
            colour=discord.Colour.gold(),
            description=(
                f"Based on your profile: **{profile['driving_style'].title()}** style, "
                f"**{profile['preferred_tyre'].title()}** tyre preference."
            ),
        )

        is_wet = condition.lower() == "wet"
        style  = profile["driving_style"]

        # Simple heuristic defaults tailored to driving style
        fw = 5 + (2 if style == "aggressive" else 0) + (1 if is_wet else 0)
        rw = 5 + (1 if style == "smooth" else 0) + (2 if is_wet else 0)
        bb = profile["preferred_brake_bias"]

        embed.add_field(name="🛞 Tyres",
                        value=f"Front: **{fw}** | Rear: **{rw}**", inline=False)
        embed.add_field(name="🔧 Suspension",
                        value="Front: **4** | Rear: **4** | ARB F: **5** / R: **5**",
                        inline=False)
        embed.add_field(name="📐 Ride Height",
                        value=f"Front: **{'22' if is_wet else '20'}** | Rear: **{'32' if is_wet else '30'}**",
                        inline=False)
        embed.add_field(name="🛑 Brakes",
                        value=f"Pressure: **100%** | Bias: **{bb}%**", inline=False)
        embed.add_field(name="⛽ Tyre Pressure",
                        value=f"Front: **{'22.0' if is_wet else '23.5'} psi** | Rear: **{'20.0' if is_wet else '21.5'} psi**",
                        inline=False)
        embed.add_field(name="🏎 Camber / Toe",
                        value="Camber: F **-2.50** / R **-1.00** | Toe: F **0.09** / R **0.32**",
                        inline=False)

        if saved:
            embed.set_footer(text="💾 You have a saved setup for this track — this is a fresh recommendation.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ──────────────────────────────────────────────
    # DEBRIEF
    # ──────────────────────────────────────────────

    @app_commands.command(name="debrief", description="Post-session performance debrief")
    async def debrief(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        discord_id  = str(interaction.user.id)
        today       = str(date.today())

        # Fetch today's laps (most recent track from game state)
        all_laps = await get_lap_history(discord_id, limit=50)
        if not all_laps:
            await interaction.followup.send("❌ No lap history found.")
            return

        # Group to most recent track+date
        latest_track = all_laps[0]["track_name"]
        session_laps = [l for l in all_laps if l["track_name"] == latest_track]

        valid_times = [l["lap_time_ms"] for l in session_laps if l["lap_time_ms"] and l["lap_time_ms"] > 0]
        best_lap    = min(valid_times) if valid_times else None
        avg_lap     = int(sum(valid_times) / len(valid_times)) if valid_times else None

        embed = discord.Embed(
            title=f"📊 Session Debrief — {latest_track.title()}",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Total Laps",  value=str(len(session_laps)),       inline=True)
        embed.add_field(name="Best Lap",    value=_ms_to_laptime(best_lap),     inline=True)
        embed.add_field(name="Average Lap", value=_ms_to_laptime(avg_lap),      inline=True)

        # Lap time consistency chart (text-based)
        if valid_times:
            min_t, max_t = min(valid_times), max(valid_times)
            chart_lines  = []
            for i, lap in enumerate(session_laps[:20], 1):
                t = lap["lap_time_ms"]
                if t and t > 0 and max_t > min_t:
                    bar_len = int(((t - min_t) / (max_t - min_t)) * 10)
                    bar     = "█" * (10 - bar_len) + "░" * bar_len
                else:
                    bar = "──────────"
                chart_lines.append(f"L{i:02d} {_ms_to_laptime(t)} {bar}")

            embed.add_field(
                name="📈 Consistency (less grey = faster)",
                value="```\n" + "\n".join(chart_lines) + "\n```",
                inline=False,
            )

        # Tyre strategy
        compounds_used = list({l["tyre_compound"] for l in session_laps if l["tyre_compound"]})
        embed.add_field(name="🛞 Tyres Used", value=", ".join(compounds_used) or "Unknown", inline=False)

        await interaction.followup.send(embed=embed)

    # ──────────────────────────────────────────────
    # LAP HISTORY
    # ──────────────────────────────────────────────

    @app_commands.command(name="history", description="View your lap history at a track")
    @app_commands.describe(track="Track name (e.g. silverstone)")
    async def history(self, interaction: discord.Interaction, track: str) -> None:
        discord_id = str(interaction.user.id)
        laps = await get_lap_history(discord_id, track_name=track, limit=20)

        if not laps:
            await interaction.response.send_message(
                f"❌ No lap history for **{track}**.", ephemeral=True
            )
            return

        lines = []
        for lap in laps:
            t     = _ms_to_laptime(lap["lap_time_ms"])
            cmpd  = lap["tyre_compound"] or "?"
            lap_n = lap["lap_number"]
            lines.append(f"L{lap_n:03d} | {t} | {cmpd}")

        embed = discord.Embed(
            title=f"🏁 Lap History — {track.title()}",
            description="```\n" + "\n".join(lines) + "\n```",
            colour=discord.Colour.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ──────────────────────────────────────────────
    # ENGINEER MUTE / UNMUTE
    # ──────────────────────────────────────────────

    engineer_group = app_commands.Group(name="engineer", description="Engineer radio settings")

    @engineer_group.command(name="mute", description="Mute radio messages")
    async def engineer_mute(self, interaction: discord.Interaction) -> None:
        self.bot.voice_manager.mute()
        await interaction.response.send_message("🔇 Radio muted.", ephemeral=True)

    @engineer_group.command(name="unmute", description="Unmute radio messages")
    async def engineer_unmute(self, interaction: discord.Interaction) -> None:
        self.bot.voice_manager.unmute()
        await interaction.response.send_message("🔊 Radio unmuted.", ephemeral=True)

    # ────────────────────────────────────────────
    # /join — join the user's current voice channel
    # /leave — disconnect the bot
    # ────────────────────────────────────────────

    @app_commands.command(
        name="join",
        description="Make the engineer join your voice channel and start monitoring telemetry",
    )
    async def join(self, interaction: discord.Interaction) -> None:
        # Defer immediately because voice connection can be slow
        await interaction.response.defer()

        # Check user is in a voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
                "❌ You need to be in a voice channel first!", ephemeral=True
            )
            return

        vc = interaction.user.voice.channel

        # Join voice channel
        ok = await self.bot.voice_manager.connect(channel_id=vc.id)
        if not ok:
            await interaction.followup.send(
                f"❌ Couldn't join **{vc.name}**. Check bot permissions.", ephemeral=True
            )
            return

        # Set the text/voice channel state
        bot_state.active_text_channel = interaction.channel
        bot_state.active_voice_channel = vc

        embed = discord.Embed(
            title="🎙️ Engineer Online",
            description=(
                f"Joined **{vc.name}** and monitoring telemetry.\n"
                f"Text updates will appear in {interaction.channel.mention}.\n\n"
                f"Use `/engineer mute` to silence radio. `/leave` to disconnect."
            ),
            colour=discord.Colour.from_str("#e10600"),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="leave",
        description="Disconnect the engineer from voice and stop monitoring",
    )
    async def leave(self, interaction: discord.Interaction) -> None:
        await self.bot.voice_manager.disconnect()
        bot_state.active_text_channel  = None
        bot_state.active_voice_channel = None
        await interaction.response.send_message(
            "🛑 Engineer offline. Use `/join` to reconnect.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EngineerCommands(bot))
