"""
telemetry/parser.py
Translates raw f1-packets packet objects into updates on the GameState.
Supports both m_camelCase (legacy) and snake_case (f1.packets) attribute names.
"""

from __future__ import annotations
import logging
import time
from typing import Any

from .state import GameState, PlayerState, CarSnapshot, TyreWear, CarDamage, WeatherForecast

log = logging.getLogger("f1bot.parser")


def _attr(obj: Any, *names: str, default: Any = 0) -> Any:
    """Get attribute trying m_camelCase then snake_case (f1-packets compatibility)."""
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


class PacketParser:
    """Stateful parser that maps F1 25 packet data onto the shared GameState."""

    def __init__(self, game_state: GameState):
        self.gs = game_state

    def process(self, packet: Any) -> None:
        """Dispatch a decoded f1-packets packet to the appropriate handler."""
        try:
            ptype = type(packet).__name__
            handler = _HANDLERS.get(ptype)
            if handler:
                handler(self, packet)
            self.gs.last_packet_time = time.time()
        except Exception as e:
            log.debug("Error processing packet %s: %s", type(packet).__name__, e)

    def process_raw(self, header: Any, data: bytes) -> None:
        """
        Fallback raw processing path used when f1-packets is not installed.
        Currently a no-op stub — instruct the user to install f1-packets for
        full packet parsing support.  The listener still tracks last_packet_time
        so the watchdog doesn't fire false alarms.
        """
        # No-op: raw struct parsing of all packets is out of scope without the library.
        # The listener already updates gs.last_packet_time after calling this.
        pass

    # ──────────────────────────────────────────
    # SESSION DATA  (PacketSessionData)
    # ──────────────────────────────────────────
    def _handle_session(self, pkt: Any) -> None:
        uid = _attr(pkt, "m_sessionUID") or (getattr(pkt.header, "session_uid", 0) if hasattr(pkt, "header") else 0)
        self.gs.session_uid = uid
        session_type = _attr(pkt, "m_sessionType", "session_type")
        track_id = _attr(pkt, "m_trackId", "track_id")
        total_laps = _attr(pkt, "m_totalLaps", "total_laps")
        weather = _attr(pkt, "m_weather", "weather")
        for idx, ps in self.gs.players.items():
            ps.session_type       = session_type
            ps.track_name         = _TRACK_NAMES.get(track_id, f"Track{track_id}")
            ps.total_laps         = total_laps
            ps.weather            = weather
            ps.safety_car_status  = _attr(pkt, "m_safetyCarStatus", "safety_car_status")
            ps.track_length_m     = float(_attr(pkt, "m_trackLength", "track_length") or 0.0)

        # Parse weather forecast (m_weatherForecastSamples or weather_forecast_samples)
        samples = _attr(pkt, "m_weatherForecastSamples", "weather_forecast_samples")
        if samples is None:
            samples = []
        forecasts = []
        for fc in samples:
            f = WeatherForecast(
                session_num=_attr(fc, "m_sessionType", "session_type"),
                weather=_attr(fc, "m_weather", "weather"),
                rain_percentage=_attr(fc, "m_rainPercentage", "rain_percentage"),
                time_offset=_attr(fc, "m_timeOffset", "time_offset"),
            )
            forecasts.append(f)

        for ps in self.gs.players.values():
            ps.weather_forecast = forecasts

        # ── Marshal zones → which sector currently has a yellow flag?
        # m_marshalZones: list of zones with m_zoneStart (0.0-1.0) and m_zoneFlag
        # Flag values: 0=unknown, 1=green, 2=blue, 3=yellow, 4=red
        # We approximate sector by track fraction: S1=0.0-0.35, S2=0.35-0.67, S3=0.67-1.0
        zones = _attr(pkt, "m_marshalZones", "marshal_zones")
        yellow_sector = 0
        if zones:
            for zone in zones:
                flag = _attr(zone, "m_zoneFlag", "zone_flag")
                if flag == 3:  # yellow
                    start = float(_attr(zone, "m_zoneStart", "zone_start") or 0.0)
                    if start < 0.35:
                        yellow_sector = 1
                    elif start < 0.67:
                        yellow_sector = 2
                    else:
                        yellow_sector = 3
                    break  # use the first yellow zone found
        for ps in self.gs.players.values():
            ps.yellow_flag_sector = yellow_sector

    # ──────────────────────────────────────────
    # LAP DATA  (PacketLapData)
    # ──────────────────────────────────────────
    def _handle_lap_data(self, pkt: Any) -> None:
        lap_data = _attr(pkt, "m_lapData", "lap_data")
        if lap_data is None:
            return

        # ── Pass 1: build leaderboard snapshot for ALL 20 cars ──────────────────
        for car_idx, lap in enumerate(lap_data):
            if car_idx not in self.gs.all_cars:
                self.gs.all_cars[car_idx] = CarSnapshot()
            snap = self.gs.all_cars[car_idx]
            snap.position    = _attr(lap, "m_carPosition", "car_position")
            snap.current_lap = _attr(lap, "m_currentLapNum", "current_lap_num")
            snap.pit_status  = _attr(lap, "m_pitStatus", "pit_status")

            # Gap to race leader (F1 24/25 split fields, legacy fallback)
            raw_lead_ms  = _attr(lap, "m_deltaToRaceLeaderMSPart",      "delta_to_race_leader_ms_part")
            raw_lead_min = _attr(lap, "m_deltaToRaceLeaderMinutesPart", "delta_to_race_leader_minutes_part")
            if raw_lead_ms is None:
                raw_lead_ms  = _attr(lap, "m_deltaToRaceLeaderInMS", default=0)
                raw_lead_min = 0
            snap.gap_to_leader_sec = (
                int(raw_lead_ms or 0) + int(raw_lead_min or 0) * 60_000
            ) / 1000.0

        # ── Pass 2: update player cars with full lap data + penalties ───────────
        for car_idx, lap in enumerate(lap_data):
            if car_idx not in self.gs.players:
                continue
            ps: PlayerState = self.gs.players[car_idx]
            ps.prev_position    = ps.current_position
            ps.current_position = _attr(lap, "m_carPosition", "car_position")

            prev_lap       = ps.current_lap
            ps.current_lap = _attr(lap, "m_currentLapNum", "current_lap_num")

            # Reset per-lap speed tracker when lap number advances
            if ps.current_lap != prev_lap:
                ps.max_speed_this_lap = 0.0

            ps.current_lap_time_ms = _attr(lap, "m_currentLapTimeInMS", "current_lap_time_in_ms")
            ps.last_lap_time_ms    = _attr(lap, "m_lastLapTimeInMS",    "last_lap_time_in_ms")
            ps.best_lap_time_ms    = _attr(lap, "m_bestLapTimeInMS",    "best_lap_time_in_ms")
            ps.sector              = _attr(lap, "m_sector", "sector") + 1
            ps.pit_status          = _attr(lap, "m_pitStatus", "pit_status")
            ps.pit_limiter_status  = _attr(lap, "m_pitLaneTimerActive", "pit_lane_timer_active")
            ps.vehicle_fia_flags   = _attr(lap, "m_vehicleFiaFlags", "vehicle_fia_flags", default=-1)

            # Tyre age fallback: detect pit completion from pit_status 1/2→0
            # m_tyresAgeLaps (F1 24+) is preferred; this handles older UDP versions.
            if ps.prev_pit_status in (1, 2) and ps.pit_status == 0:
                ps.tyre_change_lap = ps.current_lap
                log.debug("[PARSER] Tyre change detected on lap %d (car %d)", ps.current_lap, car_idx)
            ps.prev_pit_status = ps.pit_status
            # Compute age from change lap if m_tyresAgeLaps not available
            if ps.tyre_age_laps == 0 and ps.tyre_change_lap > 0:
                ps.tyre_age_laps = max(0, ps.current_lap - ps.tyre_change_lap)

            # Track position (for corner awareness)
            raw_dist = _attr(lap, "m_lapDistance", "lap_distance")
            if raw_dist is not None:
                ps.lap_distance_m = float(raw_dist)

            # Penalties
            ps.penalty_seconds  = int(_attr(lap, "m_penalties",                       "penalties"))
            ps.num_drive_through = int(_attr(lap, "m_numUnservedDriveThroughPens",   "num_unserved_drive_through_pens"))
            ps.num_stop_go       = int(_attr(lap, "m_numUnservedStopGoPens",          "num_unserved_stop_go_pens"))

            # ── Sector times (F1 24/25 split MSPart + MinutesPart)
            s1_ms  = _attr(lap, "m_sector1TimeMSPart",      "sector1_time_ms_part")
            s1_min = _attr(lap, "m_sector1TimeMinutesPart", "sector1_time_minutes_part")
            if not hasattr(lap, "m_sector1TimeMSPart") and not hasattr(lap, "sector1_time_ms_part"):
                s1_ms = _attr(lap, "m_sector1TimeInMS", default=0); s1_min = 0
            ps.sector1_ms = int(s1_ms or 0) + int(s1_min or 0) * 60_000

            s2_ms  = _attr(lap, "m_sector2TimeMSPart",      "sector2_time_ms_part")
            s2_min = _attr(lap, "m_sector2TimeMinutesPart", "sector2_time_minutes_part")
            if not hasattr(lap, "m_sector2TimeMSPart") and not hasattr(lap, "sector2_time_ms_part"):
                s2_ms = _attr(lap, "m_sector2TimeInMS", default=0); s2_min = 0
            ps.sector2_ms = int(s2_ms or 0) + int(s2_min or 0) * 60_000

            # ── Gaps (split fields)
            raw_ahead_ms  = _attr(lap, "m_deltaToCarInFrontMSPart",   "delta_to_car_in_front_ms_part")
            raw_ahead_min = _attr(lap, "m_deltaToCarInFrontMinutesPart", "delta_to_car_in_front_minutes_part")
            if raw_ahead_ms is None:
                raw_ahead_ms = _attr(lap, "m_deltaToCarInFrontInMS", default=0); raw_ahead_min = 0
            ps.prev_gap_to_ahead = ps.gap_to_ahead
            ps.gap_to_ahead = (int(raw_ahead_ms or 0) + int(raw_ahead_min or 0) * 60_000) / 1000.0

            raw_behind_ms  = _attr(lap, "m_deltaToCarBehindMSPart",   "delta_to_car_behind_ms_part")
            raw_behind_min = _attr(lap, "m_deltaToCarBehindMinutesPart", "delta_to_car_behind_minutes_part")
            if raw_behind_ms is None:
                raw_behind_ms = _attr(lap, "m_deltaToCarBehindInMS", default=0); raw_behind_min = 0
            ps.gap_to_behind = (int(raw_behind_ms or 0) + int(raw_behind_min or 0) * 60_000) / 1000.0

            ps.last_updated = time.time()

    # ──────────────────────────────────────────
    # CAR TELEMETRY  (PacketCarTelemetryData)
    # ──────────────────────────────────────────
    def _handle_car_telemetry(self, pkt: Any) -> None:
        tel_data = _attr(pkt, "m_carTelemetryData", "car_telemetry_data")
        if tel_data is None:
            return
        for car_idx, tel in enumerate(tel_data):
            if car_idx not in self.gs.players:
                continue
            ps = self.gs.players[car_idx]
            ps.drs_activated     = _attr(tel, "m_drs", "drs")

            # Speed trap: track current speed + session best
            speed = float(_attr(tel, "m_speed", "speed", default=0))
            ps.current_speed_kmh = speed
            if speed > ps.max_speed_this_lap:
                ps.max_speed_this_lap = speed

            # Tyre inner temps (m_tyresInnerTemperature or tyres_inner_temperature)
            ti = _attr(tel, "m_tyresInnerTemperature", "tyres_inner_temperature")
            if ti is not None and len(ti) >= 4:
                ps.tyre_inner_temp = TyreWear(
                    rl=ti[0], rr=ti[1], fl=ti[2], fr=ti[3]
                )

    # ──────────────────────────────────────────
    # CAR STATUS  (PacketCarStatusData)
    # ──────────────────────────────────────────
    def _handle_car_status(self, pkt: Any) -> None:
        status_data = _attr(pkt, "m_carStatusData", "car_status_data")
        if status_data is None:
            return
        for car_idx, status in enumerate(status_data):
            if car_idx not in self.gs.players:
                continue
            ps = self.gs.players[car_idx]
            ps.fuel_remaining      = _attr(status, "m_fuelInTank", "fuel_in_tank", default=0.0)
            ps.fuel_remaining_laps = _attr(status, "m_fuelRemainingLaps", "fuel_remaining_laps", default=0.0)
            ps.fuel_mix            = _attr(status, "m_fuelMix", "fuel_mix", default=1)
            ps.ers_store_energy    = _attr(status, "m_ersStoreEnergy", "ers_store_energy", default=0.0)
            ps.ers_pct             = min(100.0, ps.ers_store_energy / 40_000.0)  # 4MJ max
            ps.ers_deploy_mode     = int(_attr(status, "m_ersDeployMode", "ers_deploy_mode", default=0))
            ps.drs_allowed         = _attr(status, "m_drsAllowed", "drs_allowed")
            ps.tyre_compound_visual= _attr(status, "m_visualTyreCompound", "visual_tyre_compound")
            ps.vehicle_fia_flags   = _attr(status, "m_vehicleFiaFlags", "vehicle_fia_flags", default=-1)
            ps.last_updated        = time.time()

            # Tyre age: m_tyresAgeLaps gives laps on current set directly (F1 24+)
            raw_age = _attr(status, "m_tyresAgeLaps", "tyres_age_laps")
            if raw_age is not None:
                ps.tyre_age_laps = int(raw_age)

    # ──────────────────────────────────────────
    # CAR DAMAGE  (PacketCarDamageData)
    # ──────────────────────────────────────────
    def _handle_car_damage(self, pkt: Any) -> None:
        dmg_data = _attr(pkt, "m_carDamageData", "car_damage_data")
        if dmg_data is None:
            return
        for car_idx, dmg in enumerate(dmg_data):
            fl_wing = int(_attr(dmg, "m_frontLeftWingDamage",  "front_left_wing_damage"))
            fr_wing = int(_attr(dmg, "m_frontRightWingDamage", "front_right_wing_damage"))
            rear    = int(_attr(dmg, "m_rearWingDamage",  "rear_wing_damage"))
            floor_d = int(_attr(dmg, "m_floorDamage",     "floor_damage"))
            diff    = int(_attr(dmg, "m_diffuserDamage",  "diffuser_damage"))
            pods    = int(_attr(dmg, "m_sidepodDamage",   "sidepod_damage"))
            worst   = max(fl_wing, fr_wing, rear, floor_d, diff, pods)

            # Update the all_cars snapshot (so nearby-damage logic can check any car)
            if car_idx not in self.gs.all_cars:
                self.gs.all_cars[car_idx] = CarSnapshot()
            self.gs.all_cars[car_idx].max_damage = worst

            # Full damage breakdown only for player cars
            if car_idx not in self.gs.players:
                continue
            ps = self.gs.players[car_idx]
            tw = _attr(dmg, "m_tyresWear", "tyres_wear")
            if tw is not None and len(tw) >= 4:
                vals = [float(tw[i]) for i in range(4)]
                if all(0 <= v <= 1 for v in vals):
                    vals = [v * 100 for v in vals]
                ps.tyre_wear = TyreWear(rl=vals[0], rr=vals[1], fl=vals[2], fr=vals[3])
            ps.damage = CarDamage(
                front_wing = max(fl_wing, fr_wing),
                rear_wing  = rear,
                floor      = floor_d,
                diffuser   = diff,
                sidepods   = pods,
            )

    # ──────────────────────────────────────────
    # PARTICIPANTS  (PacketParticipantsData)
    # ──────────────────────────────────────────
    def _handle_participants(self, pkt: Any) -> None:
        participants = _attr(pkt, "m_participants", "participants")
        if participants is None:
            return
        num_active = _attr(pkt, "m_numActiveCars", "num_active_cars")
        for car_idx, part in enumerate(participants):
            if car_idx not in self.gs.players:
                continue
            ps = self.gs.players[car_idx]
            if num_active is not None:
                ps.total_participants = int(num_active)
            raw_name = _attr(part, "m_name", "name") or ""
            if isinstance(raw_name, bytes):
                raw_name = raw_name.decode("utf-8", errors="ignore")
            if raw_name.strip():
                first_name = raw_name.strip().split()[0].capitalize()
                ps.driver_name = first_name

    # ──────────────────────────────────────────
    # EVENT PACKETS  (PacketEventData) — race finish, flags etc.
    # ──────────────────────────────────────────
    def _handle_event(self, pkt: Any) -> None:
        code = _attr(pkt, "m_eventStringCode", "event_string_code", default=b"")
        if isinstance(code, bytes):
            code = code.decode("utf-8", errors="ignore").strip()
        code = code.upper().rstrip("\x00")  # strip null bytes

        log.debug("Event packet: %s", code)

        if code in ("CHQF", "SEND"):
            # CHQF = Chequered Flag, SEND = Session End
            for ps in self.gs.players.values():
                ps.race_finished = True



_HANDLERS: dict[str, Any] = {
    "PacketSessionData":     PacketParser._handle_session,
    "PacketLapData":         PacketParser._handle_lap_data,
    "PacketCarTelemetryData":PacketParser._handle_car_telemetry,
    "PacketCarStatusData":   PacketParser._handle_car_status,
    "PacketCarDamageData":   PacketParser._handle_car_damage,
    "PacketParticipantsData":PacketParser._handle_participants,
    "PacketEventData":       PacketParser._handle_event,
}


# ──────────────────────────────────────────────
# F1 25 track ID → name mapping
# ──────────────────────────────────────────────
_TRACK_NAMES: dict[int, str] = {
    0:  "Melbourne",
    1:  "Paul Ricard",
    2:  "Shanghai",
    3:  "Bahrain",
    4:  "Catalunya",
    5:  "Monaco",
    6:  "Montreal",
    7:  "Silverstone",
    8:  "Hockenheim",
    9:  "Hungaroring",
    10: "Spa-Francorchamps",
    11: "Monza",
    12: "Singapore",
    13: "Suzuka",
    14: "Yas Marina",
    15: "Austin",
    16: "Interlagos",
    17: "Austria",
    18: "Sochi",
    19: "Mexico City",
    20: "Baku",
    21: "Sakhir Short",
    22: "Silverstone Short",
    23: "Austin Short",
    24: "Suzuka Short",
    25: "Hanoi",
    26: "Zandvoort",
    27: "Imola",
    28: "Portimao",
    29: "Jeddah",
    30: "Miami",
    31: "Las Vegas",
    32: "Losail",
    33: "São Paulo Short",
}
