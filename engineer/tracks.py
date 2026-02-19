"""
engineer/tracks.py
Static per-circuit track knowledge used to give the LLM spatial awareness:
  - Which sector the driver is in
  - Which corner they're approaching
  - DRS zone locations
  - Key overtaking spots and traction zones

Corner positions are fractions of lap length (0.0 = start/finish, 1.0 = back to S/F).
Sector boundaries are also expressed as fractions.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Corner:
    frac: float   # 0.0–1.0 lap fraction
    name: str     # e.g. "T1 (Copse)" or "T8–9 (Maggotts-Becketts-Chapel)"


@dataclass
class TrackInfo:
    full_name:        str
    s1_end:           float       # fraction where Sector 1 ends
    s2_end:           float       # fraction where Sector 2 ends
    corners:          list[Corner] = field(default_factory=list)
    drs_zones:        list[str]   = field(default_factory=list)   # human-readable
    overtaking_spots: str = ""    # key braking/pass zones
    traction_zones:   str = ""    # traction-critical exits
    notes:            str = ""    # 1-2 sentence engineer briefing for LLM


# ──────────────────────────────────────────────────────────────────────────────
# Track database — keyed by the track_name strings used in _TRACK_NAMES
# ──────────────────────────────────────────────────────────────────────────────

TRACK_DB: dict[str, TrackInfo] = {

    "Sakhir (Bahrain)": TrackInfo(
        full_name="Bahrain International Circuit",
        s1_end=0.31, s2_end=0.60,
        corners=[
            Corner(0.02, "T1 hairpin"), Corner(0.13, "T4 hairpin"),
            Corner(0.35, "T8–9 sweepers"), Corner(0.46, "T10"),
            Corner(0.52, "T11 chicane"), Corner(0.75, "T14 hairpin"),
        ],
        drs_zones=["Main straight (after T15)", "Between T3 and T4"],
        overtaking_spots="T1 and T4 hairpins, T11 chicane",
        traction_zones="T10–T12 exit sequence, T14–T15",
        notes=(
            "Desert circuit with high tyre deg. Key overtaking at T1 and T4 hairpins "
            "and the T11 chicane. DRS detection after T15. Traction out of T11 critical "
            "for main-straight speed."
        ),
    ),

    "Melbourne": TrackInfo(
        full_name="Albert Park Circuit",
        s1_end=0.28, s2_end=0.58,
        corners=[
            Corner(0.02, "T1–2"), Corner(0.13, "T6"), Corner(0.22, "T9–10"),
            Corner(0.38, "T11–12"), Corner(0.55, "T15–16"), Corner(0.78, "T3 chicane"),
        ],
        drs_zones=["Main straight", "Back straight between T11 and T15"],
        overtaking_spots="T1 braking zone, T11–12 chicane, T15 hairpin",
        traction_zones="T3 exit, T16 exit onto main straight",
        notes=(
            "Semi-street circuit with limited overtaking. T1 braking zone and T15 hairpin "
            "are the key attack points. Walls are close — smooth inputs essential. "
            "DRS active on main straight and back section."
        ),
    ),

    "Catalunya": TrackInfo(
        full_name="Circuit de Barcelona-Catalunya",
        s1_end=0.27, s2_end=0.60,
        corners=[
            Corner(0.03, "T1"), Corner(0.10, "T3 Repsol"), Corner(0.22, "T5 Seat"),
            Corner(0.35, "T9 La Caixa hairpin"), Corner(0.48, "T10"),
            Corner(0.62, "T13–14 Campsa"), Corner(0.80, "T16 Banc de Sabadell"),
        ],
        drs_zones=["Main straight (after T16)", "Straight after T9"],
        overtaking_spots="T1 braking zone — almost the only realistic overtake",
        traction_zones="T9 La Caixa exit, T16 exit (key for S/F straight speed)",
        notes=(
            "Notoriously hard to overtake — T1 is the primary attacking point. "
            "Tyre management crucial, especially rear deg. T9 hairpin exit and T16 exit "
            "set up the main straight DRS. Following cars suffer heavily in dirty air."
        ),
    ),

    "Monaco": TrackInfo(
        full_name="Circuit de Monaco",
        s1_end=0.33, s2_end=0.67,
        corners=[
            Corner(0.03, "T1 Sainte Devote"), Corner(0.10, "T5 Massenet"),
            Corner(0.14, "T6 Casino Square"), Corner(0.22, "T8 Mirabeau Haute"),
            Corner(0.28, "T10 Grand Hotel Hairpin"), Corner(0.42, "T12 Portier"),
            Corner(0.60, "T16 Swimming Pool"), Corner(0.73, "T18 La Rascasse"),
            Corner(0.87, "T19 Antony Noghes"),
        ],
        drs_zones=["Pit straight (very limited)"],
        overtaking_spots="Almost none — pit stop differentials and safety car periods only",
        traction_zones="Grand Hotel Hairpin, La Rascasse, Sainte Devote",
        notes=(
            "Extreme precision circuit. No real overtaking — track position everything. "
            "Barriers are centimetres away. Smooth throttle application critical at Grand Hotel "
            "Hairpin and La Rascasse. Braking references are the kerbs themselves."
        ),
    ),

    "Montreal": TrackInfo(
        full_name="Circuit Gilles Villeneuve",
        s1_end=0.30, s2_end=0.63,
        corners=[
            Corner(0.05, "T1–2 hairpin"), Corner(0.18, "T6 Island hairpin"),
            Corner(0.32, "T8"), Corner(0.48, "T10"), Corner(0.65, "T12"),
            Corner(0.80, "T13 Wall of Champions hairpin"),
        ],
        drs_zones=["Main straight", "Back straight before T13"],
        overtaking_spots="T1 braking zone, T10, T13 Wall of Champions hairpin",
        traction_zones="T13 exit, T6 exit",
        notes=(
            "Stop-start street circuit. T13 Wall of Champions is the main overtaking spot. "
            "Braking very late under DRS. Safety car common due to wall proximity. "
            "Snap oversteer risk at T13 — smooth hands."
        ),
    ),

    "Silverstone": TrackInfo(
        full_name="Silverstone Circuit",
        s1_end=0.33, s2_end=0.67,
        corners=[
            Corner(0.02, "T1–2 Abbey"), Corner(0.07, "T4 Farm"),
            Corner(0.18, "T6 Copse"), Corner(0.24, "T8 Maggotts"),
            Corner(0.26, "T9 Becketts"), Corner(0.29, "T10 Chapel"),
            Corner(0.44, "T13 Stowe"), Corner(0.52, "T15 Vale"),
            Corner(0.58, "T16–17 Club"), Corner(0.75, "T18 Luffield"),
            Corner(0.88, "T19 Woodcote"),
        ],
        drs_zones=["Wellington straight (T3–T6)", "Hanger straight (T13–T15)"],
        overtaking_spots="T1 Abbey braking zone, T5 Farm, Stowe (T13–15 under DRS)",
        traction_zones="Chapel (T10) — key to Hanger straight speed, Woodcote exit",
        notes=(
            "High-speed flowing circuit. Maggotts–Becketts–Chapel is the defining sequence. "
            "Chapel exit traction determines Hanger straight speed and Stowe braking opportunity. "
            "DRS at Wellington and Hanger. Tyre deg high on rear due to fast corners."
        ),
    ),

    "Spa": TrackInfo(
        full_name="Circuit de Spa-Francorchamps",
        s1_end=0.25, s2_end=0.55,
        corners=[
            Corner(0.02, "T1 La Source"), Corner(0.06, "T4 Eau Rouge"),
            Corner(0.09, "T5 Raidillon"), Corner(0.20, "T7 Pouhon"),
            Corner(0.36, "T9 Malmedy"), Corner(0.48, "T14 Blanchimont"),
            Corner(0.72, "T18 Bus Stop chicane"), Corner(0.82, "T20 Campus"),
        ],
        drs_zones=["Kemmel straight (after Raidillon)", "Main straight"],
        overtaking_spots="T1 La Source, T18 Bus Stop chicane under DRS",
        traction_zones="Raidillon exit, Pouhon, Bus Stop exit",
        notes=(
            "Iconic fast circuit. Eau Rouge/Raidillon is flat or near-flat — "
            "any lift there costs significant lap time. Bus Stop chicane the main overtaking zone. "
            "Kemmel DRS: one of the longest full-throttle sections in F1. Weather can change by sector."
        ),
    ),

    "Monza": TrackInfo(
        full_name="Autodromo Nazionale di Monza",
        s1_end=0.30, s2_end=0.65,
        corners=[
            Corner(0.06, "T1–2 Rettifilio chicane"), Corner(0.22, "T4–5 Roggia chicane"),
            Corner(0.40, "T8 Lesmo 1"), Corner(0.45, "T9 Lesmo 2"),
            Corner(0.58, "T11 Ascari chicane"), Corner(0.78, "T11A Parabolica"),
        ],
        drs_zones=["Main straight", "Back straight (before Ascari)"],
        overtaking_spots="T1 Rettifilio under DRS, T4 Roggia, T11A Parabolica",
        traction_zones="Parabolica exit — critical for main straight top speed",
        notes=(
            "Power circuit — lowest downforce in the season. Braking is violent at T1 and T4 chicanes. "
            "Parabolica exit traction is THE most important corner of the lap for top speed. "
            "Slipstream and DRS make T1 the primary overtaking zone. Lift and coast at one chicane is possible."
        ),
    ),

    "Singapore": TrackInfo(
        full_name="Marina Bay Street Circuit",
        s1_end=0.32, s2_end=0.65,
        corners=[
            Corner(0.02, "T1"), Corner(0.08, "T3"), Corner(0.18, "T5"),
            Corner(0.35, "T10 Singapore Sling area"), Corner(0.48, "T14"),
            Corner(0.62, "T18"), Corner(0.72, "T20"), Corner(0.85, "T23"),
        ],
        drs_zones=["Pit straight", "Short section between T14–T17"],
        overtaking_spots="T7 and T14 braking zones, T23 final hairpin",
        traction_zones="T10 exit, T23 exit — both set up DRS straights",
        notes=(
            "Slowest and most physical circuit in F1. Walls everywhere — precision essential. "
            "Overtaking rare in clear air; safety car periods are the opportunity. "
            "Extreme tyre and brake heat. Night race — reference points different under lights."
        ),
    ),

    "Suzuka": TrackInfo(
        full_name="Suzuka Circuit",
        s1_end=0.35, s2_end=0.68,
        corners=[
            Corner(0.03, "T1"), Corner(0.08, "T3–5 S-curves"), Corner(0.14, "T7 Dunlop"),
            Corner(0.22, "T9 Degner 1"), Corner(0.27, "T11 Hairpin"),
            Corner(0.42, "T13 Spoon"), Corner(0.56, "T16 130R"),
            Corner(0.65, "T17–18 Casio chicane"),
        ],
        drs_zones=["Main straight"],
        overtaking_spots="T1 braking zone (under DRS), occasional moves at T11 hairpin",
        traction_zones="130R (T16) — flat or near-flat, high commitment. Spoon exit (T13).",
        notes=(
            "Technical figure-of-eight layout. 130R defines bravery — flat is faster but risky. "
            "The S-curves and Esses reward smooth inputs. Spoon is the key traction zone for "
            "the back straight and chicane braking. Main straight + DRS = T1 attack."
        ),
    ),

    "Abu Dhabi": TrackInfo(
        full_name="Yas Marina Circuit",
        s1_end=0.33, s2_end=0.65,
        corners=[
            Corner(0.02, "T1"), Corner(0.18, "T5"), Corner(0.37, "T9"),
            Corner(0.48, "T11"), Corner(0.60, "T14"), Corner(0.73, "T17"),
            Corner(0.87, "T21"),
        ],
        drs_zones=["Main straight", "Marina section (T12–T14)"],
        overtaking_spots="T1 hairpin, T9, T17 long hairpin",
        traction_zones="T9 exit, T21 exit onto main straight",
        notes=(
            "Renovated 2021 layout — much faster, outright overtaking still difficult. "
            "T9 and T17 are the main braking zones. Marina section is technical. "
            "Final race of the season — managing championship implications as well as race strategy."
        ),
    ),

    "Texas (COTA)": TrackInfo(
        full_name="Circuit of the Americas",
        s1_end=0.37, s2_end=0.68,
        corners=[
            Corner(0.03, "T1 (blind apex)"), Corner(0.09, "T3–9 infield esses"),
            Corner(0.32, "T11 back straight hairpin"), Corner(0.44, "T12"),
            Corner(0.56, "T15–16 esses"), Corner(0.68, "T18 hairpin"),
            Corner(0.78, "T20"), Corner(0.92, "T19 Esses exit"),
        ],
        drs_zones=["Main straight", "Back straight before T11"],
        overtaking_spots="T1 (long braking zone under DRS), T11 hairpin, T18 hairpin",
        traction_zones="T9 exit, T18 exit — both set up long straights",
        notes=(
            "Varied layout with flowing esses and heavy-braking hairpins. "
            "T1 has the longest braking zone in F1 — critical not to over-slow or miss the apex. "
            "T11 and T18 are secondary overtaking zones. Kerbs can be used aggressively in esses."
        ),
    ),

    "Brazil (Interlagos)": TrackInfo(
        full_name="Autódromo José Carlos Pace (Interlagos)",
        s1_end=0.25, s2_end=0.60,
        corners=[
            Corner(0.03, "T1 Senna S"), Corner(0.08, "T2 Senna S exit"),
            Corner(0.18, "T4 Descida do Lago"), Corner(0.30, "T6–7"),
            Corner(0.48, "T8 Bico de Pato"), Corner(0.56, "T11 Mergulhão"),
            Corner(0.60, "T12 Junção"), Corner(0.78, "T14 Subida dos Boxes"),
        ],
        drs_zones=["Main straight", "Short back straight"],
        overtaking_spots="T1 Senna S, T4 Descida do Lago, T12 Junção",
        traction_zones="Junção exit (T12) — critical for main straight acceleration",
        notes=(
            "Anti-clockwise, high-altitude circuit. Junção (T12) exit is the most important "
            "traction zone — sets up the main straight. Senna S can be taken in one fluid motion "
            "for maximum exit speed. Tyre deg less severe due to short lap. Unpredictable weather."
        ),
    ),

    "Austria (Red Bull Ring)": TrackInfo(
        full_name="Red Bull Ring",
        s1_end=0.40, s2_end=0.67,
        corners=[
            Corner(0.13, "T1 (uphill braking)"), Corner(0.20, "T2"),
            Corner(0.32, "T3"), Corner(0.42, "T4 Remus"),
            Corner(0.52, "T6"), Corner(0.62, "T8"),
            Corner(0.72, "T9–10 Rindt"), Corner(0.88, "T11 Power Horse"),
        ],
        drs_zones=["Main straight", "Short straight before T3"],
        overtaking_spots="T1 (only realistic pass), occasional T3 and T4",
        traction_zones="T4 exit, T10 exit — short circuit, every exit matters",
        notes=(
            "Short, power-sensitive circuit on a hillside. T1 is blind and uphill — "
            "braking reference is the bridge. Engine and ERS deployment crucial on short straights. "
            "Very limited overtaking outside T1 — track position and qualifying critical."
        ),
    ),

    "Mexico": TrackInfo(
        full_name="Autódromo Hermanos Rodríguez",
        s1_end=0.32, s2_end=0.62,
        corners=[
            Corner(0.04, "T1–4 stadium section"), Corner(0.22, "T5 Reta Opuesta"),
            Corner(0.30, "T7–8"), Corner(0.42, "T12 Esses first"),
            Corner(0.50, "T13 Esses second"), Corner(0.65, "T16 Horquilla hairpin"),
            Corner(0.80, "T17"), Corner(0.92, "T19 Foro Sol"),
        ],
        drs_zones=["Main straight", "Reta Opuesta back straight"],
        overtaking_spots="T1 stadium entry, T16 Horquilla hairpin, T19 Foro Sol",
        traction_zones="Foro Sol (T19) exit — sets up main straight DRS. Horquilla exit.",
        notes=(
            "High altitude reduces downforce and engine power — DRS effect much stronger. "
            "The stadium section at T1 is one of F1's best atmospheres but hard to overtake. "
            "Foro Sol stadium hairpin (T19) is the last main corner — "
            "traction here defines main straight speed and the DRS opportunity."
        ),
    ),

    "Azerbaijan (Baku)": TrackInfo(
        full_name="Baku City Circuit",
        s1_end=0.23, s2_end=0.55,
        corners=[
            Corner(0.04, "T1"), Corner(0.15, "T8 Castle section"),
            Corner(0.32, "T15 (before long straight)"), Corner(0.54, "T16"),
            Corner(0.75, "T20 final hairpin"), Corner(0.90, "T21"),
        ],
        drs_zones=["Castle straight (2.2km monster)"],
        overtaking_spots="T1 and T2 under DRS, T20 final hairpin",
        traction_zones="T20 hairpin exit — feeds the longest DRS zone in F1",
        notes=(
            "Street circuit with the longest straight (2.2km) in F1. "
            "T20 hairpin exit traction is the single most critical moment — "
            "it sets up the 2.2km DRS blast to T1. Castle section is very narrow and fast. "
            "Safety car virtually guaranteed. Walls = immediate retirement."
        ),
    ),

    "Zandvoort": TrackInfo(
        full_name="Circuit Zandvoort",
        s1_end=0.35, s2_end=0.68,
        corners=[
            Corner(0.04, "T1 Tarzan hairpin"), Corner(0.12, "T3 Gerlach"),
            Corner(0.22, "T5 Hugenholtzbocht"), Corner(0.42, "T9 Scheivlak"),
            Corner(0.52, "T11 Hugenholtz"), Corner(0.65, "T14 Marlboro hairpin (banked)"),
            Corner(0.78, "T16 Arie Luyendyk"), Corner(0.90, "T17"),
        ],
        drs_zones=["Main straight"],
        overtaking_spots="T1 Tarzan — only realistic passing zone",
        traction_zones="T14 banked hairpin, T1 exit",
        notes=(
            "Classic Dutch dunes circuit. Tarzan hairpin is virtually the only overtaking opportunity. "
            "T14 is a unique banked hairpin — the banking provides extra grip. "
            "Track position and safety car timing are everything here."
        ),
    ),

    "Jeddah": TrackInfo(
        full_name="Jeddah Corniche Circuit",
        s1_end=0.32, s2_end=0.65,
        corners=[
            Corner(0.03, "T1"), Corner(0.10, "T4"), Corner(0.22, "T7"),
            Corner(0.38, "T13"), Corner(0.50, "T17"), Corner(0.62, "T22"),
            Corner(0.78, "T27"), Corner(0.92, "T30"),
        ],
        drs_zones=["Main straight", "Multiple zones across the long straights"],
        overtaking_spots="T1, T13, T27 under DRS",
        traction_zones="T27 exit and T30 — feeds into main straight",
        notes=(
            "Fastest street circuit in F1. Wall-to-wall barriers at 250+ km/h. "
            "Second longest straight only behind Baku. Multiple DRS zones create many "
            "overtaking opportunities. Walls are the margins — "
            "commitment and precision are both required simultaneously."
        ),
    ),

    "Miami": TrackInfo(
        full_name="Miami International Autodrome",
        s1_end=0.38, s2_end=0.68,
        corners=[
            Corner(0.04, "T1"), Corner(0.12, "T3–4 sweeper"),
            Corner(0.24, "T7"), Corner(0.48, "T11–14 hairpin sequence"),
            Corner(0.65, "T17 hard braking"), Corner(0.82, "T19"),
        ],
        drs_zones=["Main straight", "Second DRS zone in back section"],
        overtaking_spots="T1, T11 hairpin, T17",
        traction_zones="T14 exit, T19 exit",
        notes=(
            "Newer circuit around the Hard Rock Stadium. Multiple overtaking opportunities. "
            "T11–14 sequence is a hairpin complex — the exit traction feeds the second DRS zone. "
            "Track surface can be slippery, especially in early laps as rubber goes down."
        ),
    ),

    "Las Vegas": TrackInfo(
        full_name="Las Vegas Strip Circuit",
        s1_end=0.30, s2_end=0.60,
        corners=[
            Corner(0.08, "T1"), Corner(0.22, "T5"), Corner(0.38, "T10"),
            Corner(0.48, "T11 Casino hairpin"), Corner(0.57, "T14"),
            Corner(0.72, "T17"), Corner(0.85, "T19"),
        ],
        drs_zones=["The Strip main straight (1.9km)", "Back section"],
        overtaking_spots="T12 Casino hairpin, T1 under DRS on The Strip",
        traction_zones="T12 Casino hairpin exit — feeds The Strip 1.9km blast",
        notes=(
            "Night race on the Las Vegas Strip. 1.9km main straight rivals Baku. "
            "T12 Casino hairpin exit is the key traction zone — feeds the main DRS blast. "
            "Track can be slippery early (cold asphalt at night). Dramatic setting, high speeds."
        ),
    ),

    "Losail (Qatar)": TrackInfo(
        full_name="Losail International Circuit",
        s1_end=0.30, s2_end=0.65,
        corners=[
            Corner(0.04, "T1"), Corner(0.08, "T2"), Corner(0.18, "T6"),
            Corner(0.38, "T10"), Corner(0.50, "T12"), Corner(0.58, "T14"),
            Corner(0.68, "T16 final complex"),
        ],
        drs_zones=["Main straight", "Back section"],
        overtaking_spots="T1 (main DRS zone), T10",
        traction_zones="T16 final corner — key traction zone for main straight",
        notes=(
            "Night race on a motorcycle circuit. Flowing, high-speed layout. "
            "Track evolution through weekend is massive — rubber goes down lap by lap. "
            "Tyre deg can be severe. Wind direction affects cornering balance significantly."
        ),
    ),

    "Shanghai": TrackInfo(
        full_name="Shanghai International Circuit",
        s1_end=0.35, s2_end=0.68,
        corners=[
            Corner(0.05, "T1–2 hairpin"), Corner(0.22, "T6"),
            Corner(0.38, "T8"), Corner(0.52, "T11"),
            Corner(0.65, "T13 hairpin"), Corner(0.82, "T14–16 back section"),
        ],
        drs_zones=["Main straight", "Back straight"],
        overtaking_spots="T14 hairpin, T1 under DRS",
        traction_zones="T16 exit, T2 exit — both feed long straights",
        notes=(
            "The first back straight is one of the longest in F1. "
            "T14 hairpin at the end of the back straight is the prime overtaking zone. "
            "The T1–2 complex is a slow 180-degree hairpin — late braking critical. "
            "Tyre wear varies significantly across compounds."
        ),
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# Public helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_track_info(track_name: str) -> Optional[TrackInfo]:
    """Return the TrackInfo for a given track name, or None if not in database."""
    return TRACK_DB.get(track_name)


def track_context(track_name: str, lap_frac: float) -> dict:
    """
    Build a context dict describing the current track position and circuit facts.

    Args:
        track_name: from PlayerState.track_name (e.g. "Monza")
        lap_frac:   fraction of lap completed (m_lapDistance / track_length_m), 0.0–1.0

    Returns a dict ready to merge into _build_context().
    """
    info = get_track_info(track_name)
    if info is None:
        return {
            "track_notes":      "",
            "overtaking_spots": "",
            "traction_zones":   "",
            "drs_zones":        "",
            "current_sector":   None,
            "nearest_corner":   None,
            "upcoming_corners": [],
        }

    # Clamp fraction to valid range
    frac = max(0.0, min(1.0, lap_frac))

    # Sector from fraction
    if frac <= info.s1_end:
        sector = 1
    elif frac <= info.s2_end:
        sector = 2
    else:
        sector = 3

    # Nearest corner behind the car (last passed)
    nearest = None
    for c in reversed(info.corners):
        if c.frac <= frac + 0.01:   # small lookahead so "at the corner" counts
            nearest = c.name
            break
    if nearest is None and info.corners:
        nearest = info.corners[-1].name   # past final corner, approaching S/F

    # Upcoming corners in the next ~30% of lap
    upcoming = [
        c.name for c in info.corners
        if frac < c.frac <= frac + 0.30
    ]

    return {
        "track_notes":      info.notes,
        "overtaking_spots": info.overtaking_spots,
        "traction_zones":   info.traction_zones,
        "drs_zones":        ", ".join(info.drs_zones),
        "current_sector":   sector,
        "nearest_corner":   nearest,
        "upcoming_corners": upcoming,
    }
