"""
engineer/logic.py

Threshold-based triggers that decide WHEN to fire a radio message.
Each trigger type has its own cooldown measured in laps or seconds.
A priority queue (max 2 items) prevents message spam.
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Optional

from telemetry.state import PlayerState, game_state as _gs
from engineer.tracks import track_context as _track_context

log = logging.getLogger("f1bot.logic")


# ──────────────────────────────────────────────────────────────────────────────
# Trigger taxonomy & priority
# ──────────────────────────────────────────────────────────────────────────────

class TriggerType(IntEnum):
    # Highest priority first
    CRITICAL_FUEL         = 1
    CRITICAL_TYRES        = 2
    DAMAGE                = 3
    RED_FLAG              = 4   # Red flag — session stopped
    SAFETY_CAR_DEPLOYED   = 5   # Full SC out
    SAFETY_CAR_ENDING     = 6   # SC coming in
    VSC_DEPLOYED          = 7   # Virtual Safety Car deployed
    VSC_ENDING            = 8   # VSC ending
    BLUE_FLAG             = 9   # Being lapped / lapping
    YELLOW_FLAG           = 10  # Yellow sector / caution
    PENALTY               = 11  # Time penalty received
    RAIN_STARTS           = 12
    WEATHER_INCOMING      = 13
    FUEL_LOW              = 14
    TYRE_WARNING          = 15
    TYRE_TEMP_IMBALANCE   = 16
    NEARBY_CAR_DAMAGE     = 17  # Car directly ahead has significant damage (opportunity)
    DEFEND                = 18
    FINAL_LAP             = 19
    RACE_FINISHED         = 20  # Race / session result confirmed
    GAP_CLOSING           = 21
    UNDERCUT_OPPORTUNITY  = 22
    OVERCUT_OPPORTUNITY   = 23
    PIT_WINDOW_OPTIMAL    = 24
    POSITION_GAINED       = 25
    POSITION_LOST         = 26
    GAP_CLOSE_AHEAD       = 27
    SESSION_START         = 28
    CHEQUERED_FLAG        = 29
    QUALI_LAP_START       = 30
    SPEED_TRAP            = 31  # New personal best top speed (qualifying only)
    PERSONAL_BEST         = 32  # Set a new personal best lap time
    RIVAL_PITTED          = 33  # Car directly ahead pitted — gap opportunity


# ──────────────────────────────────────────────────────────────────────────────
# Session type groupings  (confirmed F1 25 m_sessionType values)
# Source: EA UDP spec + community verification
# ──────────────────────────────────────────────────────────────────────────────
#
# 0  = Unknown
# 1  = Practice 1      4 = Short Practice
# 2  = Practice 2      5 = Q1
# 3  = Practice 3      6 = Q2
#                      7 = Q3
#                      8 = Short Qualifying
#                      9 = OSQ (One-Shot Qualifying)
#                      10 = Sprint Shootout SQ1 (F1 25)
#                      11 = Sprint Shootout SQ2 (F1 25)
#                      12 = Sprint Shootout SQ3 (F1 25)
# 13 = Race
# 14 = Race 2 (Sprint Race — ~100km / 11-19 laps depending on circuit)
# 15 = Race 3
# 16 = Time Trial
#
# NOTE: Some community sources document 10/11/12 as Race/Race2/Race3 and
# 13 as Time Trial (F1 23 numbering). F1 25 introduced Sprint Shootout IDs
# which shifted the race block. We handle BOTH mappings to be resilient.

_PRACTICE_SESSIONS      = {1, 2, 3, 4}          # P1, P2, P3, Short Practice
_QUALIFYING_SESSIONS    = {5, 6, 7, 8, 9}        # Q1–Q3, Short Q, OSQ
_SPRINT_SHOOTOUT        = {10, 11, 12}           # Sprint Shootout SQ1/SQ2/SQ3
_SPRINT_RACE_SESSIONS   = {14}                   # Sprint Race (~100km, no mandatory stops)
_RACE_SESSIONS          = {13, 15}               # Full Race, Race 3
_TIME_TRIAL             = {16}

# Combined qualifying (standard + sprint shootout) for trigger purposes
_ALL_QUALIFYING         = _QUALIFYING_SESSIONS | _SPRINT_SHOOTOUT
# All sessions where the car is actually "racing" (not practice/quali/TT)
_ALL_RACE_LIKE          = _RACE_SESSIONS | _SPRINT_RACE_SESSIONS

# ── Race distance classification from total_laps ──────────────────────────────
# F1 25 race distance options and approximate lap counts (varies by track):
#   Quickfire  = ~3 laps
#   Very Short = ~5 laps
#   Short      = 25% → ~10-15 laps (track dependent)
#   Medium     = 35% → ~15-20 laps
#   Long       = 50% → ~22-30 laps
#   Full       = 100% → ~44-57 laps
#   Sprint Race = ~11-19 laps (fixed ~100km, scaled wear)

def _race_distance_category(total_laps: int) -> str:
    """Classify total_laps into a distance category string."""
    if total_laps <= 3:   return "quickfire"   # 3 laps
    if total_laps <= 6:   return "very_short"  # 5 laps
    if total_laps <= 18:  return "short"       # 25-35%
    if total_laps <= 30:  return "medium"      # 50%
    return "full"                              # 75-100%

# Practice: safety-relevant + tyre/fuel awareness only
_PRACTICE_TRIGGERS = {
    TriggerType.SESSION_START,
    TriggerType.CRITICAL_TYRES, TriggerType.TYRE_WARNING, TriggerType.TYRE_TEMP_IMBALANCE,
    # Note: FUEL_LOW intentionally excluded from practice — too noisy, fuel always appears low
    TriggerType.CRITICAL_FUEL,
    TriggerType.DAMAGE,
    TriggerType.RAIN_STARTS,    TriggerType.WEATHER_INCOMING,
    TriggerType.CHEQUERED_FLAG, TriggerType.RACE_FINISHED,
    TriggerType.RED_FLAG,       TriggerType.YELLOW_FLAG,
    TriggerType.SAFETY_CAR_DEPLOYED, TriggerType.SAFETY_CAR_ENDING,
    TriggerType.VSC_DEPLOYED,   TriggerType.VSC_ENDING,
    TriggerType.BLUE_FLAG,      TriggerType.PENALTY,
}

# Qualifying (Q1-Q3, Short Q, OSQ) + Sprint Shootout (SQ1-SQ3):
_QUALI_TRIGGERS = {
    TriggerType.SESSION_START,  TriggerType.QUALI_LAP_START,
    # Tyres matter in quali (helps manage outlap warm-up vs purples)
    TriggerType.CRITICAL_TYRES, TriggerType.TYRE_WARNING, TriggerType.TYRE_TEMP_IMBALANCE,
    # FUEL_LOW and CRITICAL_FUEL intentionally excluded from qualifying:
    # Qualifying cars always load minimal fuel, so fuel_remaining_laps is always
    # ~1-2 laps. Alerting on this every 90s is useless and spammy.
    TriggerType.DAMAGE,
    TriggerType.RAIN_STARTS,    TriggerType.WEATHER_INCOMING,
    TriggerType.CHEQUERED_FLAG, TriggerType.RACE_FINISHED,
    TriggerType.RED_FLAG,       TriggerType.YELLOW_FLAG,
    TriggerType.SAFETY_CAR_DEPLOYED, TriggerType.SAFETY_CAR_ENDING,
    TriggerType.VSC_DEPLOYED,   TriggerType.VSC_ENDING,
    TriggerType.BLUE_FLAG,      TriggerType.PENALTY,
    TriggerType.SPEED_TRAP,     # new personal best top speed this session
    TriggerType.PERSONAL_BEST,  # new best lap time this session
}

# Sprint Race (~100km, no mandatory pit stops):
_SPRINT_TRIGGERS = {
    TriggerType.SESSION_START,
    TriggerType.CRITICAL_TYRES, TriggerType.TYRE_WARNING, TriggerType.TYRE_TEMP_IMBALANCE,
    TriggerType.CRITICAL_FUEL,  TriggerType.FUEL_LOW,
    TriggerType.DAMAGE,
    TriggerType.RAIN_STARTS,    TriggerType.WEATHER_INCOMING,
    TriggerType.DEFEND,
    TriggerType.GAP_CLOSE_AHEAD, TriggerType.GAP_CLOSING,
    TriggerType.POSITION_GAINED, TriggerType.POSITION_LOST,
    TriggerType.FINAL_LAP,      TriggerType.CHEQUERED_FLAG, TriggerType.RACE_FINISHED,
    TriggerType.RED_FLAG,       TriggerType.YELLOW_FLAG,
    TriggerType.SAFETY_CAR_DEPLOYED, TriggerType.SAFETY_CAR_ENDING,
    TriggerType.VSC_DEPLOYED,   TriggerType.VSC_ENDING,
    TriggerType.BLUE_FLAG,      TriggerType.PENALTY,
    TriggerType.NEARBY_CAR_DAMAGE,
    # Intentionally excluded: UNDERCUT, OVERCUT, PIT_WINDOW (sprints have no mandatory stop)
}

# Full Race: all triggers active
_RACE_TRIGGERS = (
    set(TriggerType)
    - {TriggerType.QUALI_LAP_START, TriggerType.SPEED_TRAP}  # not relevant mid-race
)

# Time Trial: personal performance only
_TIME_TRIAL_TRIGGERS = {
    TriggerType.SESSION_START,
    TriggerType.CRITICAL_TYRES, TriggerType.TYRE_WARNING, TriggerType.TYRE_TEMP_IMBALANCE,
    TriggerType.CRITICAL_FUEL,
    TriggerType.DAMAGE,
    TriggerType.CHEQUERED_FLAG, TriggerType.RACE_FINISHED,
}


def _allowed_triggers(session_type: int) -> set[TriggerType]:
    """
    Return the set of trigger types permitted for the given session type.

    Handles F1 25 IDs (13=Race, 14=Sprint Race, 16=Time Trial) AND
    legacy F1 23/24 IDs (10=Race, 11=Race2, 12=Race3/TT) for resilience.
    """
    # ── F1 25 confirmed session IDs ──
    if session_type in _RACE_SESSIONS:         return _RACE_TRIGGERS
    if session_type in _SPRINT_RACE_SESSIONS:  return _SPRINT_TRIGGERS
    if session_type in _ALL_QUALIFYING:        return _QUALI_TRIGGERS
    if session_type in _PRACTICE_SESSIONS:     return _PRACTICE_TRIGGERS
    if session_type in _TIME_TRIAL:            return _TIME_TRIAL_TRIGGERS

    # ── Fallback: legacy F1 23/24 IDs ──
    if session_type == 10:  return _RACE_TRIGGERS      # old Race
    if session_type == 11:  return _SPRINT_TRIGGERS    # old Race 2
    if session_type == 12:  return _TIME_TRIAL_TRIGGERS  # old Race 3 / TT

    log.warning("Unknown session_type=%d — defaulting to practice triggers", session_type)
    return _PRACTICE_TRIGGERS


@dataclass
class RadioEvent:
    trigger: TriggerType
    car_index: int
    context: dict          # extra data for the LLM prompt
    priority: int = 5      # lower = higher priority
    created_at: float = field(default_factory=time.time)


# ──────────────────────────────────────────────────────────────────────────────
# Per-player trigger state
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerTriggerState:
    """Tracks cooldowns and session flags for one player."""
    # cooldown_until[TriggerType] = monotonic time when trigger is allowed again
    cooldown_until: dict[TriggerType, float] = field(default_factory=dict)

    # Track state for deltas
    last_trigger_lap: dict[TriggerType, int] = field(default_factory=dict)
    session_start_fired: bool = False
    final_lap_fired: bool     = False
    chequered_fired: bool     = False
    race_finished_fired: bool = False
    damage_fired: set[str]    = field(default_factory=set)
    last_safety_car_status: int = -1   # -1 = not yet seen
    last_fia_flag: int          = -1   # -1 = not yet seen

    # Penalty tracking
    last_penalty_seconds: int = 0      # detect increases

    # Speed trap (qualifying)
    best_speed_this_session: float = 0.0   # personal best so far this session

    # Nearby damage: track which car-ahead indices we've already alerted on
    nearby_damage_alerted_cars: set[int] = field(default_factory=set)

    # Intent tracking: only re-fire when the situation has MEANINGFULLY CHANGED,
    # not just when the cooldown clock expires.
    # Fuel: re-alert when fuel drops at least 0.3 laps since last alert
    last_fuel_alert_laps: float = -1.0
    # Tyres: re-alert when max wear increases by at least 5% since last alert
    last_tyre_alert_wear: float = 0.0
    # Defend: re-alert only when the gap behind is actively shrinking
    last_defend_gap: float = 999.0

    # PERSONAL_BEST: track best lap so we can detect new PBs
    last_best_lap_ms: int = 0

    # RIVAL_PITTED: track which position we last saw a rival ahead pitting from
    last_rival_pit_position: int = 0

    # Gap trend: lightweight EWMA (exponential weighted moving average) of
    # gap_to_ahead change per evaluation cycle. Positive = gaining on car ahead.
    gap_trend: float = 0.0   # seconds per eval cycle (smoothed)

    def is_ready(self, trigger: TriggerType, now: Optional[float] = None) -> bool:
        now = now or time.monotonic()
        return now >= self.cooldown_until.get(trigger, 0.0)

    def set_cooldown(self, trigger: TriggerType, seconds: float) -> None:
        self.cooldown_until[trigger] = time.monotonic() + seconds


# Per-trigger cooldown table (seconds)
_COOLDOWNS: dict[TriggerType, float] = {
    TriggerType.CRITICAL_FUEL:          90.0,
    TriggerType.CRITICAL_TYRES:         60.0,
    TriggerType.DAMAGE:                 999_999,
    TriggerType.RED_FLAG:               999_999,  # once per red flag period
    TriggerType.SAFETY_CAR_DEPLOYED:    999_999,  # once per SC deployment
    TriggerType.SAFETY_CAR_ENDING:      999_999,
    TriggerType.VSC_DEPLOYED:           999_999,
    TriggerType.VSC_ENDING:             999_999,
    TriggerType.BLUE_FLAG:              60.0,
    TriggerType.YELLOW_FLAG:            120.0,
    TriggerType.PENALTY:                45.0,
    TriggerType.RAIN_STARTS:            300.0,
    TriggerType.WEATHER_INCOMING:       240.0,
    TriggerType.FUEL_LOW:               240.0,    # max once per ~4 minutes in race
    TriggerType.TYRE_WARNING:           120.0,
    TriggerType.TYRE_TEMP_IMBALANCE:    150.0,
    TriggerType.NEARBY_CAR_DAMAGE:      180.0,
    TriggerType.DEFEND:                 60.0,
    TriggerType.FINAL_LAP:              999_999,
    TriggerType.RACE_FINISHED:          999_999,
    TriggerType.GAP_CLOSING:            60.0,
    TriggerType.UNDERCUT_OPPORTUNITY:   120.0,
    TriggerType.OVERCUT_OPPORTUNITY:    120.0,
    TriggerType.PIT_WINDOW_OPTIMAL:     180.0,
    TriggerType.POSITION_GAINED:        30.0,
    TriggerType.POSITION_LOST:          30.0,
    TriggerType.GAP_CLOSE_AHEAD:        60.0,
    TriggerType.SESSION_START:          999_999,
    TriggerType.CHEQUERED_FLAG:         999_999,
    TriggerType.QUALI_LAP_START:        60.0,
    TriggerType.SPEED_TRAP:             90.0,
    TriggerType.PERSONAL_BEST:          999_999,  # once per PB (event-driven, not timed)
    TriggerType.RIVAL_PITTED:           120.0,    # don't spam if rival pits again quickly
}


# ──────────────────────────────────────────────────────────────────────────────
# Main Logic Engine
# ──────────────────────────────────────────────────────────────────────────────

class EngineerLogic:
    """
    Checks the current PlayerState against all thresholds.
    Returns a list of RadioEvents to fire (max 2, sorted by priority).
    """

    MAX_QUEUE = 2

    # On bot restart, stale game state (old tyre wear, low fuel, existing damage)
    # would trigger a burst of 5+ messages within seconds of reconnect.
    # The grace period suppresses non-critical triggers for the first N seconds.
    STARTUP_GRACE_SECONDS = 25.0

    # Only these triggers fire during the startup grace period:
    _GRACE_ALLOWED = {
        TriggerType.RED_FLAG,
        TriggerType.SAFETY_CAR_DEPLOYED,
        TriggerType.VSC_DEPLOYED,
        TriggerType.SESSION_START,
        TriggerType.PENALTY,
    }

    def __init__(self):
        # Per-car trigger state
        self._state: dict[int, PlayerTriggerState] = {}
        self._started_at: float = time.monotonic()    # for startup grace filter

    def _get_state(self, car_idx: int) -> PlayerTriggerState:
        if car_idx not in self._state:
            self._state[car_idx] = PlayerTriggerState()
        return self._state[car_idx]

    def evaluate(self, ps: PlayerState) -> list[RadioEvent]:
        """
        Evaluate all triggers for a single player and return pending events.
        Only triggers appropriate for the current session type are fired.
        """
        events: list[RadioEvent] = []
        ts = self._get_state(ps.car_index)
        now = time.monotonic()

        # Skip if session type is unknown
        if ps.session_type == 0:
            return []

        # ── POST-SESSION SILENCE
        # Once the session-end message has fired, suppress ALL further output.
        # This stops the bot yapping about fuel/tyres after the chequered flag.
        if ts.race_finished_fired:
            return []

        allowed = _allowed_triggers(ps.session_type)

        # Startup grace: suppress non-critical triggers for STARTUP_GRACE_SECONDS
        # after bot start to avoid burst-firing stale game state on reconnect.
        in_grace = (now - self._started_at) < self.STARTUP_GRACE_SECONDS

        # Helper: only emit event if trigger is allowed for this session
        def emit(trigger: TriggerType, context: dict, priority: int | None = None) -> None:
            if trigger not in allowed:
                return
            # During grace period, silently drop non-critical triggers
            if in_grace and trigger not in self._GRACE_ALLOWED:
                log.debug("[GRACE] Suppressing %s during startup grace period", trigger.name)
                return
            events.append(RadioEvent(
                trigger=trigger,
                car_index=ps.car_index,
                context=context,
                priority=priority if priority is not None else int(trigger),
            ))

        # ── SESSION START
        if not ts.session_start_fired and ps.current_lap <= 1:
            ts.session_start_fired = True
            emit(TriggerType.SESSION_START, self._build_context(ps))

        # ── QUALIFYING: announce each new flying lap attempt
        if ps.session_type in _QUALIFYING_SESSIONS:
            # Fire QUALI_LAP_START whenever a new in-lap begins (lap > 1)
            if (ps.current_lap > 1
                    and ts.last_trigger_lap.get(TriggerType.QUALI_LAP_START, 0) < ps.current_lap
                    and ts.is_ready(TriggerType.QUALI_LAP_START, now)):
                ts.last_trigger_lap[TriggerType.QUALI_LAP_START] = ps.current_lap
                ts.set_cooldown(TriggerType.QUALI_LAP_START, 60.0)
                emit(TriggerType.QUALI_LAP_START, self._build_context(ps))

        # ── TYRES  (projection-based — works for any race length)
        wear     = ps.tyre_wear
        max_wear = wear.max_wear()
        proj     = _project_wear(ps)   # estimated max wear at race end

        # CRITICAL: projected to be undriveable by end, OR already very worn now
        crit_threshold = max(60.0, 100.0 - (ps.total_laps * 0.5))
        crit_threshold = min(crit_threshold, 85.0)  # never higher than 85% absolute
        if (proj >= 95 or max_wear >= crit_threshold) and ts.is_ready(TriggerType.CRITICAL_TYRES, now):
            ts.set_cooldown(TriggerType.CRITICAL_TYRES, _COOLDOWNS[TriggerType.CRITICAL_TYRES])
            emit(TriggerType.CRITICAL_TYRES,
                 {**self._build_context(ps), "max_wear": max_wear, "projected_wear": round(proj, 1)})

        # WARNING: projected to reach uncomfortable levels
        # Intent-aware: only re-fire when wear has increased ≥5% since last alert.
        elif proj >= 78 and ts.is_ready(TriggerType.TYRE_WARNING, now):
            wear_grew = max_wear - ts.last_tyre_alert_wear >= 5.0
            if wear_grew:
                ts.set_cooldown(TriggerType.TYRE_WARNING, _COOLDOWNS[TriggerType.TYRE_WARNING])
                ts.last_tyre_alert_wear = max_wear
                emit(TriggerType.TYRE_WARNING,
                     {**self._build_context(ps), "max_wear": max_wear, "projected_wear": round(proj, 1)})

        # Tyre temperature imbalance (any tyre > 20°C hotter than average)
        temps = [ps.tyre_inner_temp.fl, ps.tyre_inner_temp.fr,
                 ps.tyre_inner_temp.rl, ps.tyre_inner_temp.rr]
        if any(t > 0 for t in temps):
            avg_temp = sum(temps) / 4
            if max(temps) - avg_temp > 20 and ts.is_ready(TriggerType.TYRE_TEMP_IMBALANCE, now):
                ts.set_cooldown(TriggerType.TYRE_TEMP_IMBALANCE,
                                _COOLDOWNS[TriggerType.TYRE_TEMP_IMBALANCE])
                emit(TriggerType.TYRE_TEMP_IMBALANCE,
                     {**self._build_context(ps), "temps": temps})

        # ── FUEL  (intent-aware: only re-fire when fuel has dropped meaningfully)
        # CRITICAL: under half a lap — genuine emergency, always fire on cooldown
        if ps.fuel_remaining_laps < 0.5 and ts.is_ready(TriggerType.CRITICAL_FUEL, now):
            ts.set_cooldown(TriggerType.CRITICAL_FUEL, _COOLDOWNS[TriggerType.CRITICAL_FUEL])
            ts.last_fuel_alert_laps = ps.fuel_remaining_laps
            emit(TriggerType.CRITICAL_FUEL, self._build_context(ps))

        # LOW: only re-fire when fuel has dropped ≥ 0.3 laps since the last alert.
        # Prevents the bot saying "1.4 laps remaining" → 90s later → "1.3 laps remaining" forever.
        elif ps.fuel_remaining_laps < 2.0 and ts.is_ready(TriggerType.FUEL_LOW, now):
            fuel_dropped = ts.last_fuel_alert_laps < 0 or (
                ts.last_fuel_alert_laps - ps.fuel_remaining_laps >= 0.3
            )
            if fuel_dropped:
                ts.set_cooldown(TriggerType.FUEL_LOW, _COOLDOWNS[TriggerType.FUEL_LOW])
                ts.last_fuel_alert_laps = ps.fuel_remaining_laps
                emit(TriggerType.FUEL_LOW, self._build_context(ps))

        # ── GAPS & POSITION  (race only — filtered by emit())
        # DEFEND — intent-aware: only fire when the threat is ACTIVELY APPROACHING.
        # gap_to_behind < 1.0 is the threshold, but also require the gap is shrinking
        # (last_defend_gap > current gap) so we don't spam "defend" when statically close.
        if 0 < ps.gap_to_behind < 1.0 and ts.is_ready(TriggerType.DEFEND, now):
            approaching = ps.gap_to_behind < ts.last_defend_gap - 0.05  # closing by >50ms
            if approaching:
                ts.set_cooldown(TriggerType.DEFEND, _COOLDOWNS[TriggerType.DEFEND])
                ts.last_defend_gap = ps.gap_to_behind
                emit(TriggerType.DEFEND, self._build_context(ps))
        else:
            # Reset when car is no longer close, so next approach triggers cleanly
            ts.last_defend_gap = ps.gap_to_behind if ps.gap_to_behind > 0 else 999.0

        delta_ahead = ps.prev_gap_to_ahead - ps.gap_to_ahead
        if delta_ahead > 0.3 and ps.gap_to_ahead > 0 and ts.is_ready(TriggerType.GAP_CLOSE_AHEAD, now):
            ts.set_cooldown(TriggerType.GAP_CLOSE_AHEAD, _COOLDOWNS[TriggerType.GAP_CLOSE_AHEAD])
            emit(TriggerType.GAP_CLOSE_AHEAD,
                 {**self._build_context(ps), "delta": delta_ahead})

        if ps.prev_position > 0 and ps.current_position < ps.prev_position:
            if ts.is_ready(TriggerType.POSITION_GAINED, now):
                ts.set_cooldown(TriggerType.POSITION_GAINED, _COOLDOWNS[TriggerType.POSITION_GAINED])
                emit(TriggerType.POSITION_GAINED, self._build_context(ps))

        elif ps.prev_position > 0 and ps.current_position > ps.prev_position:
            if ts.is_ready(TriggerType.POSITION_LOST, now):
                ts.set_cooldown(TriggerType.POSITION_LOST, _COOLDOWNS[TriggerType.POSITION_LOST])
                emit(TriggerType.POSITION_LOST, self._build_context(ps))

        # ── SAFETY CAR / VSC  (detect status change each eval cycle)
        sc = ps.safety_car_status
        prev_sc = ts.last_safety_car_status
        if sc != prev_sc and prev_sc != -1:           # state change detected
            if sc == 1:   # Full SC deployed
                ts.set_cooldown(TriggerType.SAFETY_CAR_DEPLOYED, _COOLDOWNS[TriggerType.SAFETY_CAR_DEPLOYED])
                emit(TriggerType.SAFETY_CAR_DEPLOYED, self._build_context(ps))
            elif sc == 2: # VSC deployed
                ts.set_cooldown(TriggerType.VSC_DEPLOYED, _COOLDOWNS[TriggerType.VSC_DEPLOYED])
                emit(TriggerType.VSC_DEPLOYED, self._build_context(ps))
            elif sc == 0 and prev_sc == 1:  # SC ending
                ts.set_cooldown(TriggerType.SAFETY_CAR_ENDING, _COOLDOWNS[TriggerType.SAFETY_CAR_ENDING])
                emit(TriggerType.SAFETY_CAR_ENDING, self._build_context(ps))
            elif sc == 0 and prev_sc == 2:  # VSC ending
                ts.set_cooldown(TriggerType.VSC_ENDING, _COOLDOWNS[TriggerType.VSC_ENDING])
                emit(TriggerType.VSC_ENDING, self._build_context(ps))
        ts.last_safety_car_status = sc

        # ── FIA FLAGS  (vehicle_fia_flags from lap / car status data)
        flag = ps.vehicle_fia_flags
        if flag != ts.last_fia_flag and flag >= 0:  # valid flag change
            if flag == 4 and ts.is_ready(TriggerType.RED_FLAG, now):
                ts.set_cooldown(TriggerType.RED_FLAG, _COOLDOWNS[TriggerType.RED_FLAG])
                emit(TriggerType.RED_FLAG, self._build_context(ps))
            elif flag == 3 and ts.is_ready(TriggerType.YELLOW_FLAG, now):
                ts.set_cooldown(TriggerType.YELLOW_FLAG, _COOLDOWNS[TriggerType.YELLOW_FLAG])
                _sector_names = {1: "sector one", 2: "sector two", 3: "sector three"}
                _sector_text  = _sector_names.get(ps.yellow_flag_sector, "this sector")
                emit(TriggerType.YELLOW_FLAG, {
                    **self._build_context(ps),
                    "yellow_flag_sector_text": _sector_text,
                })
            elif flag == 2 and ts.is_ready(TriggerType.BLUE_FLAG, now):
                ts.set_cooldown(TriggerType.BLUE_FLAG, _COOLDOWNS[TriggerType.BLUE_FLAG])
                emit(TriggerType.BLUE_FLAG, self._build_context(ps))
        ts.last_fia_flag = flag

        # ── DAMAGE  (always relevant — every session)
        dmg = ps.damage
        _COMPONENT_NAMES = {
            "front_wing": "front wing",
            "rear_wing":  "rear wing",
            "floor":      "floor",
            "diffuser":   "diffuser",
            "sidepods":   "sidepods",
        }
        for component_key, value in [
            ("front_wing", dmg.front_wing),
            ("rear_wing",  dmg.rear_wing),
            ("floor",      dmg.floor),
            ("diffuser",   dmg.diffuser),
            ("sidepods",   dmg.sidepods),
        ]:
            if value >= 20 and component_key not in ts.damage_fired:
                ts.damage_fired.add(component_key)
                emit(TriggerType.DAMAGE,
                     {**self._build_context(ps),
                      "component": _COMPONENT_NAMES[component_key],
                      "level": value})

        # ── WEATHER
        incoming = ps.approaching_rain
        if incoming and ts.is_ready(TriggerType.WEATHER_INCOMING, now):
            ts.set_cooldown(TriggerType.WEATHER_INCOMING, _COOLDOWNS[TriggerType.WEATHER_INCOMING])
            emit(TriggerType.WEATHER_INCOMING,
                 {**self._build_context(ps), "forecast": incoming})

        if ps.weather >= 3 and ts.is_ready(TriggerType.RAIN_STARTS, now):
            ts.set_cooldown(TriggerType.RAIN_STARTS, _COOLDOWNS[TriggerType.RAIN_STARTS])
            emit(TriggerType.RAIN_STARTS, self._build_context(ps))

        # ── PIT STRATEGY
        events.extend(self._check_pit_strategy(ps, ts, now))

        # ── FINAL LAP
        if ps.is_final_lap() and not ts.final_lap_fired:
            ts.final_lap_fired = True
            events.append(RadioEvent(TriggerType.FINAL_LAP, ps.car_index,
                                     {**self._build_context(ps)}, TriggerType.FINAL_LAP))

        # ── RACE FINISHED (set by PacketEventData CHQF/SEND)
        if ps.race_finished and not ts.race_finished_fired:
            ts.race_finished_fired = True
            events.append(RadioEvent(TriggerType.RACE_FINISHED, ps.car_index,
                                     {**self._build_context(ps)}, TriggerType.RACE_FINISHED))

        # ── PENALTY
        if ps.penalty_seconds > ts.last_penalty_seconds:
            # Only fire if penalty has genuinely increased (not just first read after 0)
            if ts.last_penalty_seconds > 0 or ps.penalty_seconds >= 5:
                if ts.is_ready(TriggerType.PENALTY, now):
                    ts.set_cooldown(TriggerType.PENALTY, _COOLDOWNS[TriggerType.PENALTY])
                    emit(TriggerType.PENALTY, {
                        **self._build_context(ps),
                        "penalty_seconds":  ps.penalty_seconds,
                        "drive_throughs":   ps.num_drive_through,
                        "stop_gos":         ps.num_stop_go,
                    })
            ts.last_penalty_seconds = ps.penalty_seconds

        # ── NEARBY CAR DAMAGE  (race/sprint only — filtered by emit)
        # Fire when the car directly ahead has >= 40% damage on any component.
        # This signals they may slow, pit, or run wide — an attacking opportunity.
        if ps.current_position > 1 and ps.gap_to_ahead < 3.0:
            car_ahead = self._get_car_at_position(ps.current_position - 1)
            if car_ahead is not None and car_ahead.max_damage >= 40:
                # Use car's position as a key so we alert once per unique damaged car
                alert_key = car_ahead.position
                if alert_key not in ts.nearby_damage_alerted_cars:
                    if ts.is_ready(TriggerType.NEARBY_CAR_DAMAGE, now):
                        ts.set_cooldown(TriggerType.NEARBY_CAR_DAMAGE,
                                        _COOLDOWNS[TriggerType.NEARBY_CAR_DAMAGE])
                        ts.nearby_damage_alerted_cars.add(alert_key)
                        emit(TriggerType.NEARBY_CAR_DAMAGE, {
                            **self._build_context(ps),
                            "ahead_damage_pct": car_ahead.max_damage,
                            "ahead_gap_sec":    round(ps.gap_to_ahead, 2),
                        })
            elif car_ahead is not None and car_ahead.max_damage < 20:
                # Car is repaired / pitted — clear the alert key so we can re-alert next time
                ts.nearby_damage_alerted_cars.discard(car_ahead.position)

        # ── SPEED TRAP  (qualifying only — filtered by emit)
        # Fire when the driver completes a lap and has set a new session best top speed.
        # We use current_lap > 1 to avoid false fires on the out-lap before the first flying lap.
        if ps.is_in_qualifying and ps.current_lap > 1:
            # max_speed_this_lap is reset by the parser at each lap boundary.
            # If it just reset, the previous lap's max speed is in ps.best_speed_kmh
            # (we update best_speed_kmh below when a new PB is found)
            speed_to_check = ps.max_speed_this_lap
            if (speed_to_check > ts.best_speed_this_session + 2.0
                    and speed_to_check > 200.0   # ignore slow outlaps
                    and ts.is_ready(TriggerType.SPEED_TRAP, now)):
                ts.set_cooldown(TriggerType.SPEED_TRAP, _COOLDOWNS[TriggerType.SPEED_TRAP])
                ts.best_speed_this_session = speed_to_check
                emit(TriggerType.SPEED_TRAP, {
                    **self._build_context(ps),
                    "top_speed_kmh": round(speed_to_check, 1),
                })

        # ── GAP TREND (EWMA update) — compute smoothed rate of change on gap to car ahead.
        # Alpha=0.25 means recent readings count ~4x more than old ones.
        # Positive gap_trend = we are GAINING on the car ahead (gap shrinking).
        _gap_alpha = 0.25
        raw_gain = (ps.prev_gap_to_ahead - ps.gap_to_ahead)  # positive = closing
        ts.gap_trend = _gap_alpha * raw_gain + (1 - _gap_alpha) * ts.gap_trend

        # ── PERSONAL BEST  (qualifying + race)
        if (ps.best_lap_time_ms > 0
                and ps.best_lap_time_ms < ts.last_best_lap_ms
                and ts.last_best_lap_ms > 0
                and ts.is_ready(TriggerType.PERSONAL_BEST, now)):
            ts.set_cooldown(TriggerType.PERSONAL_BEST, _COOLDOWNS[TriggerType.PERSONAL_BEST])
            prev_ms = ts.last_best_lap_ms
            improvement_ms = prev_ms - ps.best_lap_time_ms
            emit(TriggerType.PERSONAL_BEST, {
                **self._build_context(ps),
                "new_best_ms":       ps.best_lap_time_ms,
                "prev_best_ms":      prev_ms,
                "improvement_ms":    improvement_ms,
            })
        # Always update last known best (also handles cold start: set initial value)
        if ps.best_lap_time_ms > 0:
            ts.last_best_lap_ms = ps.best_lap_time_ms

        # ── RIVAL PITTED  (race + sprint — filtered by emit)
        # Fires when the car immediately ahead in the standings enters the pit lane.
        # This is a big strategic moment — driver temporarily gains a position.
        if ps.current_position > 1 and ps.gap_to_ahead < 5.0:
            car_ahead = self._get_car_at_position(ps.current_position - 1)
            if (car_ahead is not None
                    and car_ahead.pit_status in (1, 2)  # pitting or in pit area
                    and ts.last_rival_pit_position != ps.current_position - 1
                    and ts.is_ready(TriggerType.RIVAL_PITTED, now)):
                ts.set_cooldown(TriggerType.RIVAL_PITTED, _COOLDOWNS[TriggerType.RIVAL_PITTED])
                ts.last_rival_pit_position = ps.current_position - 1
                emit(TriggerType.RIVAL_PITTED, {
                    **self._build_context(ps),
                    "rival_pitted_from_pos": ps.current_position - 1,
                    "gap_trend_per_cycle":   round(ts.gap_trend, 3),
                })
            elif car_ahead is not None and car_ahead.pit_status == 0:
                # Rival rejoined — reset so we can fire again if they pit again
                ts.last_rival_pit_position = 0

        # Sort by priority and cap at MAX_QUEUE
        events.sort(key=lambda e: e.priority)
        return events[:self.MAX_QUEUE]

    def on_chequered_flag(self, ps: PlayerState) -> RadioEvent:
        ts = self._get_state(ps.car_index)
        if not ts.chequered_fired:
            ts.chequered_fired = True
            return RadioEvent(TriggerType.CHEQUERED_FLAG, ps.car_index,
                              self._build_context(ps), TriggerType.CHEQUERED_FLAG)

    def reset_session(self, car_index: int) -> None:
        """Reset per-session flags when a new session begins."""
        self._state[car_index] = PlayerTriggerState()

    # ──────────────────────────────────────────
    # Pit strategy helpers
    # ──────────────────────────────────────────

    def _check_pit_strategy(
        self, ps: PlayerState, ts: PlayerTriggerState, now: float
    ) -> list[RadioEvent]:
        events = []
        if not ps.is_in_race or ps.total_laps == 0:
            return events

        laps_remaining = ps.total_laps - ps.current_lap
        max_wear       = ps.tyre_wear.max_wear()
        proj           = _project_wear(ps)
        race_progress  = ps.current_lap / ps.total_laps

        # ── PIT WINDOW: use projected wear and race position
        # Scale the "wear is meaningful" threshold to race length:
        #   short race:  start worrying at projected 50%+
        #   full race:   start worrying at projected 45%+
        pit_concern_threshold = max(35.0, 90.0 / max(ps.total_laps, 1) * 10)
        pit_concern_threshold = min(pit_concern_threshold, 50.0)
        if (proj >= pit_concern_threshold and 0.25 < race_progress < 0.70
                and ts.is_ready(TriggerType.PIT_WINDOW_OPTIMAL, now)):
            ts.set_cooldown(TriggerType.PIT_WINDOW_OPTIMAL,
                            _COOLDOWNS[TriggerType.PIT_WINDOW_OPTIMAL])
            events.append(RadioEvent(TriggerType.PIT_WINDOW_OPTIMAL, ps.car_index,
                                     {**self._build_context(ps),
                                      "projected_wear": round(proj, 1)},
                                     TriggerType.PIT_WINDOW_OPTIMAL))

        # ── UNDERCUT: car behind within 2s, our tyres are wearing faster
        # For short races use a lower absolute threshold proportional to total laps
        undercut_wear_min = max(30.0, 50.0 * ps.total_laps / 50)
        undercut_wear_min = min(undercut_wear_min, 50.0)
        if (0 < ps.gap_to_behind < 2.0 and max_wear > undercut_wear_min
                and ts.is_ready(TriggerType.UNDERCUT_OPPORTUNITY, now)):
            ts.set_cooldown(TriggerType.UNDERCUT_OPPORTUNITY,
                            _COOLDOWNS[TriggerType.UNDERCUT_OPPORTUNITY])
            events.append(RadioEvent(TriggerType.UNDERCUT_OPPORTUNITY, ps.car_index,
                                     self._build_context(ps),
                                     TriggerType.UNDERCUT_OPPORTUNITY))

        # ── OVERCUT: car ahead within 2.5s, our tyres are fresher
        overcut_wear_max = max(20.0, 40.0 * ps.total_laps / 50)
        overcut_wear_max = min(overcut_wear_max, 40.0)
        if (0 < ps.gap_to_ahead < 2.5 and max_wear < overcut_wear_max
                and ts.is_ready(TriggerType.OVERCUT_OPPORTUNITY, now)):
            ts.set_cooldown(TriggerType.OVERCUT_OPPORTUNITY,
                            _COOLDOWNS[TriggerType.OVERCUT_OPPORTUNITY])
            events.append(RadioEvent(TriggerType.OVERCUT_OPPORTUNITY, ps.car_index,
                                     self._build_context(ps),
                                     TriggerType.OVERCUT_OPPORTUNITY))

        return events

    # ──────────────────────────────────────────
    # Leaderboard + nearby-car helpers
    # ──────────────────────────────────────────

    def _get_car_at_position(self, target_pos: int):
        """
        Return the CarSnapshot of whichever car is currently at `target_pos`.
        O(20) scan of all_cars — fine for F1's fixed 20-car field.
        Returns None if target_pos not found or all_cars is empty.
        """
        for snap in _gs.all_cars.values():
            if snap.position == target_pos:
                return snap
        return None

    def _build_leaderboard_context(self, ps: PlayerState) -> dict:
        """
        Build a compact leaderboard summary focused around the player's position.
        Returns a dict with:
          - positions P-2 through P+3 (relative to player)
          - each entry: position, gap_to_leader_sec, pit_status
          - summary string for the LLM (e.g. 'P4: +5.2s | P5(you) | P6: +1.1s behind')
        """
        my_pos = ps.current_position
        if my_pos == 0 or not _gs.all_cars:
            return {}

        # Sort all cars by position
        sorted_cars = sorted(
            (s for s in _gs.all_cars.values() if s.position > 0),
            key=lambda s: s.position
        )
        if not sorted_cars:
            return {}

        # Pick cars P-2 to P+3 around the player
        nearby: list[str] = []
        for snap in sorted_cars:
            rel = snap.position - my_pos
            if -2 <= rel <= 3:
                gap_str = f"+{snap.gap_to_leader_sec:.1f}s" if snap.gap_to_leader_sec > 0 else "leader"
                pit_str = " (pitting)" if snap.pit_status in (1, 2) else ""
                you_str = " ←you" if snap.position == my_pos else ""
                nearby.append(f"P{snap.position}: {gap_str}{pit_str}{you_str}")

        return {
            "leaderboard_nearby": "  |  ".join(nearby) if nearby else "N/A",
        }

    # ──────────────────────────────────────────
    # Context builder for LLM
    # ──────────────────────────────────────────

    def _build_context(self, ps: PlayerState) -> dict:
        # Fuel mix name for radio readability
        _FUEL_MIX = {0: "lean", 1: "standard", 2: "rich", 3: "max"}
        _ERS_MODE  = {0: "none", 1: "medium", 2: "overtake", 3: "hotlap"}

        # Gap trend: human description for the LLM
        ts = self._get_state(ps.car_index)
        gap_trend = ts.gap_trend
        if abs(gap_trend) < 0.01:
            gap_trend_desc = "stable"
        elif gap_trend > 0:
            gap_trend_desc = f"gaining {gap_trend:.2f}s/cycle on car ahead"
        else:
            gap_trend_desc = f"losing {abs(gap_trend):.2f}s/cycle to car ahead"

        max_wear = max(ps.tyre_wear.fl, ps.tyre_wear.fr,
                       ps.tyre_wear.rl, ps.tyre_wear.rr)

        ctx = {
            "driver_name":       ps.driver_name,
            "track_name":        ps.track_name,
            "session_type":      _SESSION_NAMES.get(ps.session_type, "Unknown"),
            "lap":               ps.current_lap,
            "total_laps":        ps.total_laps,
            "position":          ps.current_position,
            "total_cars":        ps.total_participants,
            "tyre_compound":     ps.tyre_compound_name,
            "tyre_age_laps":     ps.tyre_age_laps,
            "tyre_wear":         {
                "fl": round(ps.tyre_wear.fl, 1),
                "fr": round(ps.tyre_wear.fr, 1),
                "rl": round(ps.tyre_wear.rl, 1),
                "rr": round(ps.tyre_wear.rr, 1),
            },
            "max_wear":          round(max_wear, 1),
            "fuel_remaining_laps": round(ps.fuel_remaining_laps, 1),
            "fuel_kg":           round(ps.fuel_remaining, 2),
            "fuel_mix":          _FUEL_MIX.get(ps.fuel_mix, "standard"),
            "ers_pct":           round(ps.ers_pct, 1),
            "ers_mode":          _ERS_MODE.get(ps.ers_deploy_mode, "none"),
            "gap_to_ahead":      round(ps.gap_to_ahead, 3),
            "gap_to_behind":     round(ps.gap_to_behind, 3),
            "gap_trend":         gap_trend_desc,
            "weather":           _WEATHER_NAMES.get(ps.weather, "Unknown"),
            "drs_available":     bool(ps.drs_allowed),
            "safety_car":        _SC_STATUS_NAMES.get(ps.safety_car_status, "None"),
            "penalty_seconds":   ps.penalty_seconds,
            "yellow_flag_sector": ps.yellow_flag_sector,
            "damage": {
                "front_wing": ps.damage.front_wing,
                "rear_wing":  ps.damage.rear_wing,
                "floor":      ps.damage.floor,
                "diffuser":   ps.damage.diffuser,
                "sidepods":   ps.damage.sidepods,
            },
        }
        # Merge in leaderboard context (nearby positions + gaps)
        ctx.update(self._build_leaderboard_context(ps))
        # Merge in track-position context (sector, corners, DRS zones, notes)
        lap_frac = (
            ps.lap_distance_m / ps.track_length_m
            if ps.track_length_m > 0 else 0.0
        )
        ctx.update(_track_context(ps.track_name, lap_frac))
        return ctx

# ──────────────────────────────────────────────
# Tyre wear projection helper
# ──────────────────────────────────────────────

def _project_wear(ps: PlayerState) -> float:
    """
    Estimate the maximum tyre wear at the end of the race based on
    current wear rate per lap.

    Example:
      5-lap sprint, lap 2, max wear 18% →
        rate = 18 / 2 = 9%/lap
        projected = 18 + (9 × 3 remaining) = 45%  ← triggers warning at 78% proj
      50-lap race, lap 10, max wear 22% →
        rate = 22 / 10 = 2.2%/lap
        projected = 22 + (2.2 × 40 remaining) = 110% ← triggers warning immediately

    Returns current wear if fewer than 2 laps have been completed
    (not enough data to calculate a reliable rate).
    """
    laps_done      = max(ps.current_lap - 1, 0)   # completed laps
    laps_remaining = max(ps.total_laps - ps.current_lap, 0)
    max_wear       = ps.tyre_wear.max_wear()

    # Need at least 2 data points; also skip if race data unavailable
    if laps_done < 2 or ps.total_laps == 0 or max_wear == 0:
        return max_wear

    wear_rate  = max_wear / laps_done          # %/lap
    projected  = max_wear + (wear_rate * laps_remaining)
    return min(projected, 100.0)               # cap at 100%


# ──────────────────────────────────────────────
# Name mappings
# ──────────────────────────────────────────────

_SESSION_NAMES = {
    0:  "Unknown",
    1:  "Practice 1",     2: "Practice 2",   3: "Practice 3",
    4:  "Short Practice",
    5:  "Q1",             6: "Q2",           7: "Q3",
    8:  "Short Qualifying", 9: "OSQ",
    10: "Sprint Shootout SQ1",
    11: "Sprint Shootout SQ2",
    12: "Sprint Shootout SQ3",
    13: "Race",
    14: "Sprint Race",
    15: "Race 3",
    16: "Time Trial",
}

_WEATHER_NAMES = {
    0: "Clear", 1: "Light Cloud", 2: "Overcast",
    3: "Light Rain", 4: "Heavy Rain", 5: "Storm",
}

_SC_STATUS_NAMES = {
    0: "None",
    1: "Full Safety Car",
    2: "Virtual Safety Car",
    3: "Formation Lap SC",
}

