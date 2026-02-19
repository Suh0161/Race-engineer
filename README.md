# F1 25 Race Engineer

A Discord bot that acts like your personal F1 race engineer. It listens to live telemetry from **F1 25**, uses AI to generate situational radio messages, and speaks them into your voice channel in real-time.

It tracks tyre wear, fuel, gaps, sector times, and rivals to give you actual strategic advice, not just random chatter.

## How it works

1. **Telemetry**: Reads UDP packets from F1 25 (position, tyres, fuel, damage, etc.)
2. **Strategy**: Analyses the race state to trigger events (e.g. "Box now", "P3 is pitting", "Traffic ahead")
3. **AI Generation**: Uses **Kimi (Moonshot AI)** to generate a natural, concise radio message
4. **Voice**: Uses **ElevenLabs V3** to speak the message with realistic emotion (sighs, breathing, urgency)

## Prerequisites

- Python 3.11+
- FFmpeg (for audio streaming)
- F1 25 (PC/Console)
- API Keys: Discord, ElevenLabs, Moonshot (Kimi)

## Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/Suh0161/Race-engineer.git
   cd Race-engineer
   ```

2. Install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```

3. Configure `.env`:
   ```bash
   cp .env.example .env
   ```
   Fill in your tokens. You'll need an ElevenLabs voice ID (a British male voice works best).

4. Configure Game Telemetry:
   - **UDP Telemetry**: On
   - **UDP Format**: 2025
   - **UDP Port**: 20777
   - **UDP Send Rate**: 60Hz

## Running

```bash
python main.py
```

Invite the bot to your server, join a voice channel, and type `/join`. As soon as you hit the track, the engineer will radio in.

## Features

- **Real-time Tyre Monitoring**: Tracks wear per tyre and predicts life
- **Gap Tracking**: Tells you if you're catching the car ahead or being caught ("Gaining 0.3s a lap")
- **Pit Strategy**: Alerts when your rival pits ("P3 is in the pits, push now")
- **Damage Reports**: Notifies you of wing/floor damage
- **Qualifying**: Tracks personal bests and sector times
- **Dynamic Voice**: The engineer sounds stressed when you're crashing and calm when you're cruising
