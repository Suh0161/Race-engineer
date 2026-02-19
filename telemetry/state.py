"""
telemetry/state.py
Live race state data classes. Updated every packet cycle by the parser.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class TyreWear:
    fl: float = 0.0   # Front Left %
    fr: float = 0.0   # Front Right %
    rl: float = 0.0   # Rear Left %
    rr: float = 0.0   # Rear Right %

    def max_wear(self) -> float:
        return max(self.fl, self.fr, self.rl, self.rr)

    def __repr__(self) -> str:
        return f"TyreWear(FL={self.fl:.1f}%, FR={self.fr:.1f}%, RL={self.rl:.1f}%, RR={self.rr:.1f}%)"


@dataclass
class CarDamage:
    front_wing: int = 0     # 0-100 damage %
    rear_wing: int = 0
    floor: int = 0
    diffuser: int = 0
    sidepods: int = 0

    def any_critical(self, threshold: int = 20) -> bool:
        return any(v >= threshold for v in [
            self.front_wing, self.rear_wing, self.floor, self.diffuser, self.sidepods
        ])


@dataclass
class WeatherForecast:
    session_num: int = 0
    weather: int = 0        # 0=clear, 1=light cloud, 2=overcast, 3=light rain, 4=heavy rain, 5=storm
    rain_percentage: int = 0
    time_offset: int = 0    # minutes into future

    @property
    def is_wet(self) -> bool:
        return self.weather >= 3


@dataclass
class CarSnapshot:
    """
    Lightweight state for any of the 20 cars on track.
    Populated by the parser from PacketLapData + PacketCarDamageData.
    Used for leaderboard building and nearby-car damage detection.
    """
    position: int = 0
    current_lap: int = 0
    gap_to_leader_sec: float = 0.0   # seconds behind race leader (0.0 = leader)
    pit_status: int = 0              # 0=on track, 1=pitting, 2=pit area
    max_damage: int = 0              # 0-100: worst component damage %


@dataclass
class PlayerState:
    """All live state for one driver/player."""
    car_index: int = 0
    discord_id: str = ""
    driver_name: str = ""              # first name from Participants packet

    # Session basics
    session_type: int = 0       # 0=unknown, 1=Practice1, 2=Prac2, 3=Prac3, 4=ShortPrac
                                 # 5=Q1, 6=Q2, 7=Q3, 8=ShortQ, 9=OSQ
                                 # 10=Race, 11=Race2, 12=TimeTrial
    track_name: str = ""
    total_laps: int = 0

    # Position / gap
    current_position: int = 0
    total_participants: int = 0
    gap_to_ahead: float = 0.0   # seconds
    gap_to_behind: float = 0.0  # seconds
    prev_gap_to_ahead: float = 0.0  # for delta calculations
    prev_position: int = 0

    # Lap data
    current_lap: int = 0
    pit_status: int = 0      # 0=none, 1=pitting, 2=in pit area
    current_lap_time_ms: int = 0
    last_lap_time_ms: int = 0
    best_lap_time_ms: int = 0
    sector: int = 1          # 1, 2, 3
    sector1_ms: int = 0
    sector2_ms: int = 0
    lap_distance_m: float = 0.0    # metres from start/finish (m_lapDistance)
    track_length_m: float = 0.0    # full lap length in metres (from session packet)

    # Tyres
    tyre_compound_visual: int = 0  # 16=Soft, 17=Med, 18=Hard, 7=Inter, 8=Wet
    tyre_wear: TyreWear = field(default_factory=TyreWear)
    tyre_inner_temp: TyreWear = field(default_factory=TyreWear)  # reuse for temps
    tyre_age_laps: int = 0         # laps on current tyre set (tracked from pit stops)
    tyre_change_lap: int = 0       # lap number when current tyres were fitted
    prev_pit_status: int = 0       # for detecting pit completion (0→1/2→0 cycle)

    # Fuel
    fuel_remaining: float = 0.0    # kg
    fuel_mix: int = 0              # 0=lean, 1=standard, 2=rich, 3=max
    fuel_remaining_laps: float = 0.0

    # ERS
    ers_store_energy: float = 0.0   # joules — convert to % of 4MJ
    ers_pct: float = 0.0            # ERS store as 0-100% (4MJ = 100%)
    ers_deploy_mode: int = 0        # 0=None, 1=Medium, 2=Overtake, 3=Hotlap
    drs_allowed: int = 0            # 0=not allowed, 1=unknown, 2=allowed
    drs_activated: int = 0          # 0=off, 1=on

    # Damage
    damage: CarDamage = field(default_factory=CarDamage)

    # Weather
    weather: int = 0               # current weather code
    weather_forecast: list[WeatherForecast] = field(default_factory=list)

    # Pit
    pit_limiter_status: int = 0
    vehicle_fia_flags: int = -1  # -1=invalid, 0=none, 1=green, 2=blue, 3=yellow, 4=red

    # Safety car / session status
    safety_car_status: int = 0  # 0=none, 1=full SC, 2=VSC, 3=formation lap SC
    race_finished: bool = False  # set True when session result packet arrives

    # Flag sector: which sector currently has a yellow flag
    # 0=none, 1=sector 1, 2=sector 2, 3=sector 3
    # Populated by marshal zone parsing in the session packet.
    yellow_flag_sector: int = 0

    # Penalties
    penalty_seconds: int = 0         # accumulated time penalty (seconds)
    num_drive_through: int = 0       # unserved drive-through penalties
    num_stop_go: int = 0             # unserved stop-go penalties

    # Speed trap (qualifying)
    current_speed_kmh: float = 0.0   # updated every telemetry packet
    best_speed_kmh: float = 0.0      # personal best top speed this session
    max_speed_this_lap: float = 0.0  # reset each lap — used to detect new session best at lap boundary

    # Timestamps for telemetry freshness
    last_updated: float = field(default_factory=time.time)

    # ──────────────────────────────────────────
    # Computed helpers
    # ──────────────────────────────────────────

    @property
    def ers_percent(self) -> float:
        """ERS store as 0-100% of the 4 MJ maximum."""
        return min(100.0, (self.ers_store_energy / 4_000_000.0) * 100.0)

    @property
    def tyre_compound_name(self) -> str:
        # F1 25 visual tyre compound IDs (confirmed from EA spec)
        mapping = {
            16: "Soft", 17: "Medium", 18: "Hard",
            7:  "Inter", 8: "Wet",
            # C1-C5 slick compounds (some tracks)
            19: "C1", 20: "C2", 21: "C3", 22: "C4", 23: "C5",
        }
        return mapping.get(self.tyre_compound_visual, "Unknown")

    @property
    def is_in_race(self) -> bool:
        # F1 25: 13=Race, 14=Sprint Race, 15=Race3; legacy: 10=Race, 11=Race2
        return self.session_type in (10, 11, 13, 14, 15)

    @property
    def is_in_qualifying(self) -> bool:
        return self.session_type in (5, 6, 7, 8, 9)

    @property
    def is_in_practice(self) -> bool:
        return self.session_type in (1, 2, 3, 4)

    @property
    def approaching_rain(self) -> Optional[WeatherForecast]:
        """Return first forecast that predicts rain, if within 5 laps (approx 15 min)."""
        for fc in self.weather_forecast:
            if fc.is_wet and fc.time_offset <= 15:
                return fc
        return None

    def is_final_lap(self) -> bool:
        return self.total_laps > 0 and self.current_lap >= self.total_laps


@dataclass
class GameState:
    """
    Top-level game state holding both player states and session-wide info.
    Keyed by car_index (0 or 1 for duo career).

    all_cars: snapshot of ALL 20 cars for leaderboard and nearby-damage logic.
    Keyed by car_index (0-19).
    """
    players: dict[int, PlayerState] = field(default_factory=dict)
    all_cars: dict[int, "CarSnapshot"] = field(default_factory=dict)
    session_uid: int = 0
    last_packet_time: float = field(default_factory=time.time)

    def get_player(self, car_index: int) -> PlayerState:
        if car_index not in self.players:
            self.players[car_index] = PlayerState(car_index=car_index)
        return self.players[car_index]

    def get_player_by_discord(self, discord_id: str) -> Optional[PlayerState]:
        for p in self.players.values():
            if p.discord_id == discord_id:
                return p
        return None


# Global singleton — shared across all modules
game_state: GameState = GameState()
