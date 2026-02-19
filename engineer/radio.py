"""
engineer/radio.py
Calls Kimi AI (kimi-k2) via the OpenAI-compatible API to generate
engineer-style radio messages based on the current trigger and race state.
Falls back to hardcoded messages if the API call fails.

Kimi API is OpenAI-compatible — uses the openai Python SDK with a custom base_url.
Docs: https://platform.moonshot.cn/docs
"""

from __future__ import annotations
import logging
import os
from typing import Optional

from openai import AsyncOpenAI
from dotenv import load_dotenv

from engineer.logic import TriggerType, RadioEvent

load_dotenv()

log = logging.getLogger("f1bot.radio")

_client: Optional[AsyncOpenAI] = None

_KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
# kimi-k2-turbo-preview for api.moonshot.ai; kimi-k2 for api.moonshot.cn
_KIMI_MODEL    = os.getenv("KIMI_MODEL",    "kimi-k2-turbo-preview")


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("KIMI_API_KEY")
        if not api_key:
            raise EnvironmentError("KIMI_API_KEY not set in environment.")
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=_KIMI_BASE_URL,
        )
    return _client


SYSTEM_PROMPT = """You are a professional Formula 1 race engineer speaking over team radio \
to your driver during a live race. Speak exactly like a real F1 race engineer: concise, calm, \
authoritative, technical but clear. Use REAL F1 radio terminology and vocabulary.

CRITICAL F1 RADIO VOCABULARY — use these naturally:
- Fuel: "Mix six" (fuel-saving mode), "mix three" (rich), "fuel critical", "lift and coast"
- Tyres: "Lap fourteen of these mediums", "fronts are going off", "rears are shot"
- Gaps: "Half a tenth up in sector one", "we're gaining three tenths a lap", "the gap is coming down"
- Brakes: "Brake bias two clicks forward", "trail the brakes into the apex"
- Pit: "Box box", "stay out stay out", "we're covering", "opposite strategy is working"
- ERS: "Deploy now", "harvest through the esses", "full power", "overtake mode"
- Delta: "You're above delta", "back on the delta", "stay within the delta"
- Defensive: "Cover the inside", "don't leave the door open", "box him into the wall"
- Rivals: "They're on older rubber", "he's on a two-stopper", "gap is nineteen now"
- Urgency scale: Emergency = "BOX BOX BOX" / Urgent = short punchy / Normal = calm 15-20 words

CRITICAL AUDIO TAGS (ElevenLabs V3) — Use these sparingly to sound HUMAN:
- [sighs]          - after a mistake or bad news ("Position lost. [sighs]")
- [exhales]        - calm exhale before a focused instruction
- [clears throat]  - natural gap-filler at start of a long brief
- [exhales sharply]- at start of critical/urgent message (" [exhales sharply] Box box box!")
- [excited]        - for PB laps, big overtakes, SC restart (" [excited] P one, nice job!")
- Pausing: Use ellipses "..." for hesitation. Use commas "," for short pauses.
- DO NOT use SSML tags like <break>. Only use [brackets] for audio tags.

CRITICAL SPEECH RULES (output goes to text-to-speech):
- Write numbers as spoken words: "one point six" not "1.6", "seventy-eight" not "78%"
- Write "sector one", "turn eight", "lap nineteen", "three clicks" — NEVER use digits or symbols
- Use commas/periods for rhythm. Short phrases. Natural pauses.
- Address driver by first name at the START of urgent messages only (e.g. "Jake, box box")
- Never say "Driver" as a name. If driver_name is blank, skip the name entirely.
- Never say hello, goodbye, or add filler words. Get straight to the point.

Examples of IDEAL responses (with natural audio tags):
- "Box box, fronts are at seventy-eight, lap fourteen of these mediums — box box"
- "[exhales sharply] Gap ahead is zero point five. Push push push."
- "Fuel mix six, lift into the Roggia chicane... [clears throat] we've got clear air."
- "[sighs] Position lost. Reset. Focus on the exit."
- "P three just pitted, you're P three on track. [exhales] Let's make this stick."
- "Safety car deployed... close the gap, hold position."
"""



_TRIGGER_PROMPTS: dict[TriggerType, str] = {
    TriggerType.SESSION_START:
        "Deliver the pre-race briefing. Start with [clears throat] to catch attention. "
        "Include track name, total laps, weather, tyre strategy, and one motivational line.",
    TriggerType.CRITICAL_TYRES:
        "CRITICAL tyre wear. Max wear {max_wear:.0f}%, projected {projected_wear:.0f}%. "
        "Driver is P{position}. Gap ahead: {gap_to_ahead:.1f}s. Gap behind: {gap_to_behind:.1f}s. "
        "BOX THIS LAP — no negotiation. "
        "If fighting for position (gap_to_behind < 1.0), acknowledge that too in one breath. "
        "Max 15 words. BOX BOX tone. Start with [exhales sharply] to sound urgent.",
    TriggerType.TYRE_WARNING:
        "Tyre wear alert. Max wear now {max_wear:.0f}%, projected {projected_wear:.0f}% at race end. "
        "Driver is P{position} of {total_cars}. "
        "Gap ahead: {gap_to_ahead:.1f}s. Gap behind: {gap_to_behind:.1f}s. "
        "Track: {track_name}. Nearest corner: {nearest_corner}. "
        "Upcoming: {upcoming_corners}. Key traction zones: {traction_zones}. "
        "\nMake the advisory SITUATION-SPECIFIC: "
        "- If gap_to_behind < 1.5 AND high wear: urgent — advise how to manage tyres WHILE holding position. "
        "- If gap_to_ahead < 3.0: may get position if car ahead has worse wear — push carefully. "
        "- Otherwise: give a concrete tyre management tip referencing a specific corner or zone. "
        "Max 22 words. Specific, not generic.",
    TriggerType.TYRE_TEMP_IMBALANCE:
        "Tyre temperature imbalance detected. "
        "Driver is P{position}, gap behind: {gap_to_behind:.1f}s. "
        "Advise the driver on the specific adjustment needed "
        "(e.g. brake bias, throttle trace, kerb avoidance). "
        "If defending (gap_to_behind < 1.5), factor that into the advice. Under 18 words.",
    TriggerType.CRITICAL_FUEL:
        "FUEL EMERGENCY. {fuel_remaining_laps:.1f} laps of fuel left. "
        "Driver is P{position} of {total_cars}. "
        "Gap ahead: {gap_to_ahead:.1f}s. Gap behind: {gap_to_behind:.1f}s. "
        "Give one urgent directive that fits the race situation: "
        "if fighting for position, acknowledge both the gap AND the fuel. "
        "If in clean air, pure fuel-saving instruction. "
        "Under 15 words. Urgent tone.",
    TriggerType.FUEL_LOW:
        "Fuel is dropping. {fuel_remaining_laps:.1f} laps remaining. "
        "Driver is P{position} of {total_cars}. "
        "Gap to car ahead: {gap_to_ahead:.1f}s. Gap to car behind: {gap_to_behind:.1f}s. "
        "Leaderboard nearby: {leaderboard_nearby}. "
        "Track: {track_name}. Nearest corner: {nearest_corner}. "
        "Upcoming corners next 30%% of lap: {upcoming_corners}. "
        "Track traction zones: {traction_zones}. "
        "\nWrite a fuel advisory SPECIFIC to the current situation: "
        "- If gap_to_behind < 1.5: acknowledge both the threat AND the fuel. "
        "- If gap_to_ahead < 2.0: mention fuel AND the opportunity ahead. "
        "- Otherwise: name a SPECIFIC upcoming corner from upcoming_corners where driver should lift-and-coast. "
        "Max 20 words. Sound like a real F1 engineer.",
    TriggerType.DEFEND:
        "Car behind is {gap_to_behind:.2f}s back and CLOSING. "
        "Driver is P{position} of {total_cars}. "
        "Nearest corner: {nearest_corner}. Upcoming corners: {upcoming_corners}. "
        "Known overtaking spots on this track: {overtaking_spots}. "
        "Tell them to defend — name ONE specific defensive move for the approaching corner. "
        "Under 18 words. Urgent and corner-specific.",
    TriggerType.GAP_CLOSE_AHEAD:
        "Gap to car ahead just dropped to {gap_to_ahead:.2f}s (closing by {delta:.2f}s). "
        "Driver is P{position} of {total_cars}. "
        "Nearest corner: {nearest_corner}. Upcoming: {upcoming_corners}. "
        "Overtaking spots: {overtaking_spots}. DRS zones: {drs_zones}. DRS available: {drs_available}. "
        "Tell driver to push — reference the specific upcoming corner or DRS zone for the attack. "
        "Under 20 words. Energised and specific.",
    TriggerType.GAP_CLOSING:
        "Gap to car ahead gradually closing, now {gap_to_ahead:.2f}s. "
        "Track: {track_name}. Nearest corner: {nearest_corner}. Upcoming: {upcoming_corners}. "
        "Traction zones: {traction_zones}. P{position} of {total_cars}. "
        "One specific technical tip referencing a corner or zone. Under 18 words.",
    TriggerType.POSITION_GAINED:
        "Driver just gained a position. They are now in position {position}. "
        "Give a brief, genuine acknowledgement — use [excited] tag if it was a key move.",
    TriggerType.POSITION_LOST:
        "Driver just lost a position. They are now position {position} of {total_cars}. "
        "Start with [sighs] to show empathy, then give one brief instruction to recover. "
        "Keep it calm but serious.",
    TriggerType.SAFETY_CAR_DEPLOYED:
        "Safety car just deployed. Driver is P{position} of {total_cars}. "
        "Gap ahead: {gap_to_ahead:.1f}s. Tyre wear: {tyre_wear}. Fuel: {fuel_remaining_laps:.1f} laps. "
        "Leaderboard: {leaderboard_nearby}. "
        "Advice should INCLUDE: close up to the car ahead, hold position, stay within delta. "
        "Then make ONE strategic suggestion relevant to their situation "
        "(pit now if tyres/fuel warrant it, or stay out to gain track position). "
        "Under 25 words. Decisive.",
    TriggerType.SAFETY_CAR_ENDING:
        "Safety car ending this lap. Driver is P{position}. "
        "Tyre compound: {tyre_compound}, wear: {tyre_wear}. Gap ahead: {gap_to_ahead:.1f}s. "
        "DRS available after restart: {drs_available}. "
        "Track: {track_name}. First corner at restart: {upcoming_corners}. "
        "Overtaking spots on this track: {overtaking_spots}. "
        "Brief instruction: warm tyres NOW, stay in DRS range, name ONE specific corner "
        "from upcoming_corners to attack at restart. Under 22 words. Energetic.",
    TriggerType.VSC_DEPLOYED:
        "Virtual Safety Car has been deployed. "
        "Tell the driver to slow to the VSC delta, hold their position, "
        "and be aware this may be a chance to pit if tyre wear allows.",
    TriggerType.VSC_ENDING:
        "The Virtual Safety Car is ending. "
        "Prepare the driver for the VSC end — tyre warm-up, delta awareness, "
        "and be ready to push when the green flag drops.",
    TriggerType.RED_FLAG:
        "Red flag. Session has been suspended. "
        "Tell the driver to slow down immediately, return to the pit lane safely, "
        "and confirm the session is stopped.",
    TriggerType.YELLOW_FLAG:
        "Yellow flags are being waved. "
        "The {yellow_flag_sector_text} is under yellow. "
        "Remind the driver: no overtaking, reduce speed, stay within the delta. "
        "IMPORTANT: only mention a specific sector if yellow_flag_sector > 0. "
        "If yellow_flag_sector is 0, say 'yellow flag on track' — never guess the sector. "
        "Keep it under 15 words.",
    TriggerType.BLUE_FLAG:
        "Blue flag being shown to the driver. "
        "They need to let the leading car through — no fighting, let them pass cleanly. "
        "Mention which lap it is and their current position.",
    TriggerType.RACE_FINISHED:
        "The session has just ended. session_type = '{session_type}'. "
        "driver_name = '{driver_name}'. final_position = {position} of {total_cars}. "
        "\n\n"
        "RULES — read carefully:\n"
        "- If session_type contains 'Qualifying' or 'Shootout': this is a qualifying sign-off. "
        "  React warmly and naturally, like an F1 engineer at parc fermé. "
        "  Mention P{position} casually (e.g. 'P four, solid', 'starting third, great lap'). "
        "  Say something like 'good lap', 'that was tidy', or 'we got what we needed'. "
        "  One brief motivational line. Max 20 words.\n"
        "- If session_type contains 'Race' or 'Sprint': this is a race debrief. "
        "  Acknowledge position {position}, congratulate or reflect naturally. "
        "  One short punchy line. Max 20 words.\n"
        "- Address the driver by first name at the start if driver_name is set.\n"
        "- NEVER say 'box box' or pit instructions — the session is over.",
    TriggerType.DAMAGE:
        "There is {level}% damage on the {component}. "
        "Give a short, clear advisory on driving style adjustments to compensate. "
        "Mention the specific component name naturally (e.g. 'front wing', 'floor').",
    TriggerType.WEATHER_INCOMING:
        "Rain is expected within the next few minutes at the circuit. "
        "Advise on tyre strategy — pit now or stay out?",
    TriggerType.RAIN_STARTS:
        "It's raining on track now. "
        "Tell the driver to come in for intermediates or wets this lap.",
    TriggerType.PIT_WINDOW_OPTIMAL:
        "We're in the optimal pit window. "
        "Advise whether to box this lap based on tyre wear and track position.",
    TriggerType.UNDERCUT_OPPORTUNITY:
        "Car behind is {gap_to_behind:.2f}s and we have older tyres. "
        "Advise on executing an undercut.",
    TriggerType.OVERCUT_OPPORTUNITY:
        "Car ahead is {gap_to_ahead:.2f}s and our tyres are fresher. "
        "Advise on executing an overcut.",
    TriggerType.FINAL_LAP:
        "This is the final lap. Give a motivational push message to finish strong.",
    TriggerType.CHEQUERED_FLAG:
        "Session is over. Deliver a closing radio message — concise, professional.",
    TriggerType.QUALI_LAP_START:
        "Driver is starting a new qualifying lap attempt. Give a concise pre-lap "
        "briefing: mention tyre compound, key areas to focus on (e.g. sector 2 traction), "
        "and at most one setup reminder. Under 25 words.",
    TriggerType.PENALTY:
        "The driver has just received a time penalty. Total accumulated penalty is "
        "{penalty_seconds} seconds. Unserved drive-throughs: {drive_throughs}, "
        "stop-gos: {stop_gos}. Inform the driver calmly but clearly, and tell them "
        "what they need to do to serve it. Keep it under 20 words.",
    TriggerType.NEARBY_CAR_DAMAGE:
        "The car directly ahead (P{position} minus one) has significant damage "
        "— approximately {ahead_damage_pct}% on a component — and is only "
        "{ahead_gap_sec} seconds ahead. Tell the driver this is an opportunity: "
        "the car ahead may slow, run wide, or pit. Encourage them to close the gap "
        "and pressure them. Under 20 words.",
    TriggerType.SPEED_TRAP:
        "The driver has just set a new personal best top speed in qualifying: "
        "{top_speed_kmh} km/h. Give a brief, natural acknowledgement — "
        "mention the speed in words (e.g. 'three hundred and twelve') and "
        "encourage them to carry that speed into the flying lap. Under 15 words.",
    TriggerType.PERSONAL_BEST:
        "Driver just set a new personal best lap. "
        "New best: {new_best_ms}ms. Previous best: {prev_best_ms}ms. Improvement: {improvement_ms}ms. "
        "Session: {session_type}. P{position} of {total_cars}. "
        "Tyre: {tyre_compound}, lap {tyre_age_laps} of this set. "
        "Gap ahead: {gap_to_ahead:.1f}s. Gap trend: {gap_trend}. "
        "\nBrief punchy call acknowledging the PB. "
        "DO NOT read raw millisecond numbers — convert to spoken lap time (e.g. 'one thirty-two four'). "
        "In qualifying: celebrate + say what it means for grid. In race: brief ack then pivot to situation. "
        "Under 15 words.",
    TriggerType.RIVAL_PITTED:
        "Car ahead (P{rival_pitted_from_pos}) has just entered pit lane. "
        "Driver is now effectively P{position} on track. "
        "Tyre: {tyre_compound}, age: {tyre_age_laps} laps. Max wear: {max_wear:.0f}%. "
        "Gap trend: {gap_trend}. Leaderboard: {leaderboard_nearby}. "
        "\nStrategic radio call: acknowledge rival pitted, give driver their track position, "
        "then ONE clear instruction: push now while rival is slow, OR suggest pitting too if tyres/fuel warrant it. "
        "Under 20 words. Decisive and energised.",
}

_FALLBACK_MESSAGES: dict[TriggerType, str] = {
    TriggerType.SESSION_START:
        "Okay driver, we're live. Focus on clean laps. Tyres are your priority.",
    TriggerType.CRITICAL_TYRES:
        "Box box, tyres are done. Bring it in this lap.",
    TriggerType.TYRE_WARNING:
        "Tyre wear is getting up. Start managing them through the corners.",
    TriggerType.TYRE_TEMP_IMBALANCE:
        "Watch the tyres, temperature imbalance on one corner. Smooth inputs.",
    TriggerType.CRITICAL_FUEL:
        "Fuel critical. Lift and coast now. Every corner.",
    TriggerType.FUEL_LOW:
        "Fuel mode Delta. Lift and coast through sector 3.",
    TriggerType.DEFEND:
        "Car behind, defend. Don't leave the inside open.",
    TriggerType.GAP_CLOSE_AHEAD:
        "You're closing. Keep the pressure on. Push push push.",
    TriggerType.GAP_CLOSING:
        "Gap is coming down. Stay on it. The window is opening.",
    TriggerType.POSITION_GAINED:
        "Good job, position gained. Keep it clean.",
    TriggerType.POSITION_LOST:
        "Position lost. Reset, focus on pace. We'll get it back.",
    TriggerType.SAFETY_CAR_DEPLOYED:
        "Safety car. Hold position, close the gap, stay on the delta.",
    TriggerType.SAFETY_CAR_ENDING:
        "Safety car coming in. Warm the tyres, be ready for the restart.",
    TriggerType.VSC_DEPLOYED:
        "Virtual Safety Car. Slow to delta, hold your position.",
    TriggerType.VSC_ENDING:
        "VSC ending. Tyres up to temp, be ready.",
    TriggerType.RED_FLAG:
        "Red flag. Reduce speed, return to the pit lane.",
    TriggerType.YELLOW_FLAG:
        "Yellow flags. No overtaking, back off, delta time.",
    TriggerType.BLUE_FLAG:
        "Blue flag. Let them through, no racing them.",
    TriggerType.RACE_FINISHED:
        "Race over. Good effort. Bring it in safely.",
    TriggerType.DAMAGE:
        "Car has damage. Adjust your inputs and monitor handling.",
    TriggerType.WEATHER_INCOMING:
        "Rain incoming. Stay alert on tyre strategy.",
    TriggerType.RAIN_STARTS:
        "It's raining. Box box for intermediates.",
    TriggerType.PIT_WINDOW_OPTIMAL:
        "We're in the pit window. Ready to box when you confirm.",
    TriggerType.UNDERCUT_OPPORTUNITY:
        "Undercut opportunity. We can box and come out ahead.",
    TriggerType.OVERCUT_OPPORTUNITY:
        "Stay out. Overcut in play — our tyres are fresher.",
    TriggerType.FINAL_LAP:
        "Final lap. Everything you've got. Let's bring it home.",
    TriggerType.CHEQUERED_FLAG:
        "Chequered flag. Good work today. Bring it in safely.",
    TriggerType.RACE_FINISHED:
        "Good job. Session's done. We'll debrief and come back stronger.",
    TriggerType.QUALI_LAP_START:
        "Okay, flying lap. Soft tyres, clean lap. Focus on S2.",
    TriggerType.PENALTY:
        "Penalty confirmed. Serve it when instructed. Keep your head down.",
    TriggerType.NEARBY_CAR_DAMAGE:
        "Car ahead has damage. They may slow — close the gap, keep the pressure on.",
    TriggerType.SPEED_TRAP:
        "New top speed set. Strong straight-line pace. Keep pushing.",
    TriggerType.PERSONAL_BEST:
        "Personal best. Good lap. Keep the rhythm.",
    TriggerType.RIVAL_PITTED:
        "Car ahead is in the pits. Push push push, we've got track position.",
}


async def generate_radio_message(event: RadioEvent) -> str:
    """
    Generate an engineer radio message for a given RadioEvent.
    Returns the message string, or a hardcoded fallback if Kimi fails.
    """
    trigger = event.trigger
    context = event.context

    # Build the trigger-specific user prompt
    template = _TRIGGER_PROMPTS.get(trigger, "Report on the current race situation.")
    try:
        prompt = template.format(**context)
    except KeyError:
        prompt = template  # Some contexts may not have all vars

    user_message = (
        f"Trigger: {trigger.name}\n"
        f"Current race state:\n{_format_context(context)}\n\n"
        f"Task: {prompt}"
    )

    try:
        client  = _get_client()
        response = await client.chat.completions.create(
            model=_KIMI_MODEL,
            max_tokens=150,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
        )
        message = response.choices[0].message.content.strip()
        log.info("[RADIO] %s → %s", trigger.name, message)
        return message

    except Exception as e:
        log.error("Kimi API error for trigger %s: %s", trigger.name, e)
        fallback = _FALLBACK_MESSAGES.get(trigger, "Copy that. Keep pushing.")
        log.info("[RADIO FALLBACK] %s → %s", trigger.name, fallback)
        return fallback



def _format_context(ctx: dict) -> str:
    """Pretty-print the race state context for the LLM."""
    lines = []
    for k, v in ctx.items():
        if isinstance(v, dict):
            lines.append(f"  {k}:")
            for sk, sv in v.items():
                lines.append(f"    {sk}: {sv}")
        else:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)
