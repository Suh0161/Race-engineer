"""
engineer/tts.py
Calls ElevenLabs API to convert a radio message to speech.
Returns the path to a temporary .mp3 file for playback.

════════════════════════════════════════════════════════════════
  ELEVEN V3 TTS STRATEGY  (researched Feb 2025 — ElevenLabs docs)
════════════════════════════════════════════════════════════════

MODEL: eleven_v3  (default)
  • Highest quality, most human-like delivery.
  • ~800ms latency. For F1 real-time you can switch to eleven_turbo_v2_5
    via ELEVENLABS_MODEL env var if latency is a problem.
  • V3 does NOT support SSML <break> tags — they are silently ignored or
    cause corruption. Use [audio tags] and punctuation instead.

VOICE SETTINGS IN V3:
  • stability:   Controls how closely voice adheres to reference audio.
                 LOW (0.20-0.35) = Creative  — most expressive, responds
                 best to audio tags. Prone to occasional hallucinations.
                 MID (0.45-0.55) = Natural   — balanced. Best default.
                 HIGH (0.70+)    = Robust    — stable but less responsive
                 to directional tags (similar to v2 behaviour).
                 → We use 0.35 (Creative/Natural boundary) to maximise
                   tag responsiveness while staying stable.

  • similarity_boost: 0.70-0.80. Same role as v2 — preserves voice
                 identity while allowing prosodic freedom. We use 0.75.

  • style:       0.0. Same as v2 — short utterances don't need external
                 style boost; the audio tags provide the emotion.

  • use_speaker_boost: True — essential for short clips.

  • speed:       0.90. Deliberate, radio-like pacing. Still valid in V3.

AUDIO TAGS (V3 only — sourced from official docs):
  Voice/emotion delivery tags:
    [sighs]  [exhales]  [excited]  [curious]  [sarcastic]
    [laughs] [whispers] [clears throat]
  Non-verbal sound tags:
    [exhales sharply]  [inhales deeply]
  Pause control:
    Ellipsis (…) = heavier pause/weight
    Em-dash (—)  = natural breath between thoughts
    Comma (,)    = light pause
    [short pause] [long pause]  ← explicit pause tags

F1 RADIO APPROPRIATE TAGS (what Kimi is trained to use):
  [sighs]          - after a tough moment ("lost the position. [sighs]")
  [exhales]        - calm exhale before a focused instruction
  [excited]        - for PB laps, big overtakes, SC restart
  [clears throat]  - natural human gap-filler at start of a long brief
  [exhales sharply]- at start of critical/urgent message for impact

WHAT DOESN'T WORK IN V3:
  • <break time="0.4s"/> — silently stripped or corrupts output. Use … or [short pause].
  • Style > 0 with audio tags — can conflict, introduces instability.
  • Too many tags per sentence — 1-2 per message maximum.
  • Tags incompatible with voice character (e.g. [whispering] on a loud voice).
════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("f1bot.tts")

_VOICE_ID    = os.getenv("ELEVENLABS_VOICE_ID", "")
_API_KEY     = os.getenv("ELEVENLABS_API_KEY", "")

# eleven_v3  = best quality, supports audio tags, ~800ms latency
# eleven_turbo_v2_5 = lower quality, ~400ms latency (set in .env if needed)
_ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_v3")
_IS_V3 = "v3" in _ELEVENLABS_MODEL

# ── Voice settings ────────────────────────────────────────────────────────────
# V3: stability 0.35 = Creative/Natural boundary — maximises audio tag
# responsiveness while avoiding hallucinations.
# V2: stability 0.38 was our tested sweet spot. Both work with these values.
_STABILITY        = float(os.getenv("TTS_STABILITY",        "0.35" if _IS_V3 else "0.38"))
_SIMILARITY_BOOST = float(os.getenv("TTS_SIMILARITY_BOOST", "0.75"))
_STYLE            = float(os.getenv("TTS_STYLE",            "0.0"))
_SPEAKER_BOOST    = os.getenv("TTS_SPEAKER_BOOST", "true").lower() in ("true", "1", "yes")
_SPEED            = float(os.getenv("TTS_SPEED",            "0.90"))

# Temp directory for audio files
_TEMP_DIR = Path(tempfile.gettempdir()) / "f1_engineer_bot"
_TEMP_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────────────────────
#  Text pipeline
# ─────────────────────────────────────────────────────────────

_ONES   = ["", "one", "two", "three", "four", "five", "six",
           "seven", "eight", "nine", "ten", "eleven", "twelve",
           "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
           "eighteen", "nineteen"]
_TENS   = ["", "", "twenty", "thirty", "forty", "fifty",
           "sixty", "seventy", "eighty", "ninety"]

def _int_to_words(n: int) -> str:
    """Convert integer 0-999 to English words."""
    if n < 0:
        return "minus " + _int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t = _TENS[n // 10]
        o = _ONES[n % 10]
        return t + ("-" + o if o else "")
    else:
        h = _ONES[n // 100] + " hundred"
        remainder = n % 100
        return h + (" " + _int_to_words(remainder) if remainder else "")


def _normalise_numbers(text: str) -> str:
    """Replace bare digit numbers with their word equivalents."""
    # Percentages: "83%" → "eighty-three percent"
    def _pct(m):
        try:
            return _int_to_words(int(m.group(1))) + " percent"
        except Exception:
            return m.group(0)
    text = re.sub(r'\b(\d{1,3})%', _pct, text)

    # Float seconds: "1.8s" → "one point eight seconds"
    def _float_s(m):
        try:
            parts = m.group(1).split(".")
            whole = _int_to_words(int(parts[0]))
            dec   = " ".join(_int_to_words(int(d)) for d in parts[1]) if len(parts) > 1 else ""
            return whole + (" point " + dec if dec else "") + " seconds"
        except Exception:
            return m.group(0)
    text = re.sub(r'\b(\d+\.\d+)s\b', _float_s, text)

    # Position "P3" → "P three" (keep the P prefix)
    def _pos(m):
        try:
            return "P " + _int_to_words(int(m.group(1)))
        except Exception:
            return m.group(0)
    text = re.sub(r'\bP(\d{1,2})\b', _pos, text)

    # Plain integers (standalone)
    def _plain(m):
        try:
            return _int_to_words(int(m.group(1)))
        except Exception:
            return m.group(0)
    text = re.sub(r'(?<![A-Za-z])(\d{1,3})(?!\d|\.)', _plain, text)

    return text


_FIXES = [
    # Common F1 shorthand → full spoken form
    (r'\bSC\b',  "safety car"),
    (r'\bVSC\b', "virtual safety car"),
    (r'\bDRS\b', "D R S"),
    # Box box: ensure double for urgency
    (r'\bbox box\b', "box, box"),
    # Numeric sector refs "S2" → "sector two"
    (r'\bS(\d)\b', lambda m: "sector " + _int_to_words(int(m.group(1)))),
]

def _apply_fixes(text: str) -> str:
    for pattern, replacement in _FIXES:
        if callable(replacement):
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        else:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _strip_ssml(text: str) -> str:
    """
    Remove any SSML <break> or XML tags from the text.
    V3 does NOT support SSML — they corrupt or are silently ignored.
    This sanitises any fallback messages that still contain V2-era break tags.
    """
    # Remove <break time="..."/> and any XML-style tags
    text = re.sub(r'<[^>]+>', '', text)
    return text


def _prepare_text(message: str) -> str:
    """
    Full pipeline: normalise numbers → fix shorthand → strip SSML (V3).
    NOTE: When Kimi AI is working, it already writes numbers as words
    and uses [audio tags] instead of SSML. This pipeline is mainly a
    safety net for hardcoded FALLBACK messages.
    """
    message = _normalise_numbers(message)
    message = _apply_fixes(message)
    if _IS_V3:
        # V3: strip any leftover SSML break tags (V2 artefacts)
        message = _strip_ssml(message)
    else:
        # V2: inject a single SSML break at the most natural pause point
        message = _add_natural_breaks_v2(message)
    return message


def _add_natural_breaks_v2(text: str) -> str:
    """
    V2 only: Insert ONE SSML break at the most natural pause point.
    We only add a single break to avoid the ElevenLabs instability bug
    where >2 break tags cause the model to rush the rest of the audio.
    """
    if "<break" in text or len(text) < 20:
        return text
    m = re.search(r'([.!?])\s+(?=\S)', text)
    if m:
        pos = m.end(1) + 1
        return text[: m.start(1) + 1] + ' <break time="0.45s"/> ' + text[pos:].lstrip()
    first_comma = text.find(",")
    if 8 < first_comma < len(text) - 15:
        return text[: first_comma + 1] + ' <break time="0.35s"/> ' + text[first_comma + 1 :].lstrip()
    return text


# ─────────────────────────────────────────────────────────────
#  Main TTS function
# ─────────────────────────────────────────────────────────────

async def generate_tts_audio(message: str, previous_text: str = "") -> Optional[str]:
    """
    Convert ``message`` to speech using ElevenLabs.

    Args:
        message:       The radio message text (may contain [audio tags] for V3).
        previous_text: Optional — text from the PREVIOUS generation call.
                       ElevenLabs uses this for prosodic continuity when messages
                       follow each other closely (e.g. queue of radio calls).
                       Leave blank for isolated messages.

    Returns the path to the generated .mp3 file, or None if TTS failed.
    """
    if not _API_KEY or not _VOICE_ID:
        log.warning("ElevenLabs API key or voice ID not configured — skipping TTS.")
        return None

    message = _prepare_text(message)
    log.debug("[TTS] Model: %s | Prepared: %s", _ELEVENLABS_MODEL, message)

    try:
        from elevenlabs import ElevenLabs, VoiceSettings

        client = ElevenLabs(api_key=_API_KEY)

        # speed is a valid VoiceSettings field in elevenlabs SDK >= 1.9
        try:
            voice_settings = VoiceSettings(
                stability=_STABILITY,
                similarity_boost=_SIMILARITY_BOOST,
                style=_STYLE,
                use_speaker_boost=_SPEAKER_BOOST,
                speed=_SPEED,
            )
        except TypeError:
            # Older SDK: speed not yet in VoiceSettings
            voice_settings = VoiceSettings(
                stability=_STABILITY,
                similarity_boost=_SIMILARITY_BOOST,
                style=_STYLE,
                use_speaker_boost=_SPEAKER_BOOST,
            )
            log.debug("SDK: speed not in VoiceSettings — skipping speed param")

        convert_kwargs: dict = {
            "voice_id":       _VOICE_ID,
            "text":           message,
            "model_id":       _ELEVENLABS_MODEL,
            "voice_settings": voice_settings,
            "output_format":  "mp3_44100_128",
        }

        # Add previous_text for prosodic continuity if provided
        if previous_text:
            convert_kwargs["previous_text"] = previous_text

        # Try with EL's own text normalization as a safety net for fallback messages
        try:
            convert_kwargs["apply_text_normalization"] = "on"
            audio_generator = client.text_to_speech.convert(**convert_kwargs)
        except TypeError:
            convert_kwargs.pop("apply_text_normalization", None)
            log.debug("SDK: apply_text_normalization not supported — skipping")
            audio_generator = client.text_to_speech.convert(**convert_kwargs)

        filename = _TEMP_DIR / f"radio_{uuid.uuid4().hex}.mp3"
        with open(filename, "wb") as f:
            for chunk in audio_generator:
                if chunk:
                    f.write(chunk)

        log.info("TTS audio saved: %s (%d bytes)", filename, filename.stat().st_size)
        return str(filename)

    except Exception as e:
        log.error("ElevenLabs TTS error: %s", e)
        return None


def cleanup_audio(file_path: str) -> None:
    """Delete a temporary audio file after playback."""
    try:
        path = Path(file_path)
        if path.exists():
            path.unlink()
            log.debug("Cleaned up audio file: %s", file_path)
    except Exception as e:
        log.warning("Failed to delete audio file %s: %s", file_path, e)
