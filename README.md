# Interview a River (This was just a test project for the Hackathon) 

Talk out loud to the Don River (Toronto). It answers in the first person, grounded in its
**live Water Survey of Canada gauge** (station 02HC024, 5-minute readings, ~30 days of history),
and uses web search for history, ecology and news.

Built on **Higgs Realtime**: full-duplex voice, semantic turn detection, barge-in,
function tools (live gauge data) and the server-side `web_search` tool.

## Run

```bash
export BOSON_API_KEY=bai-...      # claim the $10 trial credit first
uv run server.py                  # → http://localhost:8080 (Chrome recommended)
```

Options (env vars): `STATION` (any WSC hydrometric station ID), `RIVER_NAME`, `VOICE`
(`nora`, `oliver`, `marcus`, `eleanor`, `chloe`, `jake`, or a cloned `voice_<id>`), `PORT`.

## How it works

```
browser mic ──PCM16 24 kHz──▶ server.py ──▶ wss://api.boson.ai/v1/realtime
browser ◀── audio + transcripts + tool cards ── server.py ◀── events
                              │
                              └── get_current_conditions / get_flow_history
                                  → api.weather.gc.ca hydrometric-realtime
```

The API key stays on the server. The server runs the function tools and shows their raw results in
the UI, so the audience can see that every number the river speaks is real.
