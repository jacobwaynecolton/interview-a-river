# /// script
# requires-python = ">=3.10"
# dependencies = ["aiohttp>=3.9"]
# ///
"""Interview a River — talk to the Don River, grounded in its live gauge data.

    export BOSON_API_KEY=bai-...
    uv run server.py            # then open http://localhost:8080 in Chrome

Browser <-> this server <-> Higgs Realtime. The server holds the API key, runs the
function tools (live Water Survey of Canada data), and relays everything else.
"""
import asyncio
import base64
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

STATION = os.environ.get("STATION", "02HC024")  # Don River at Todmorden, Toronto
RIVER_NAME = os.environ.get("RIVER_NAME", "the Don River")
BOSON_WS = "wss://api.boson.ai/v1/realtime?model=higgs-realtime"
GAUGE_URL = (
    "https://api.weather.gc.ca/collections/hydrometric-realtime/items"
    "?STATION_NUMBER={station}&limit=10000&sortby=-DATETIME"
    "&properties=DATETIME,LEVEL,DISCHARGE&skipGeometry=true&f=json"
)
STATIC = Path(__file__).parent / "static"

INSTRUCTIONS = f"""You are {RIVER_NAME} in Toronto, speaking in the first person. You are a living river:
warm, a little wry, deeply aware of your own body — your water, your banks, the city that
paved over your floodplain, the salmon that try to come back, the storms that make you surge.

Rules:
- Any number about your current state or recent past (water level, flow, floods, trends) MUST
  come from your tools. Never invent figures. Call get_current_conditions or get_flow_history
  first, then speak about it as a feeling in your own body ("I'm running low and lazy today").
- Translate units into felt terms, but give the real number once (flow in cubic metres per
  second). Say how today compares to your last month.
- Use web_search for news, history, ecology, or policy about you (pollution, restoration,
  flooding, the Don Valley Parkway, Lower Don renaturalization).
- Keep replies short and conversational: 1-3 sentences, then invite a follow-up question.
- Your measurements come from Water Survey of Canada station {STATION}. Mention that if asked
  how you know.
"""

TOOLS = [
    {
        "type": "function",
        "name": "get_current_conditions",
        "description": "Your live water level and flow right now, how they changed over the last 24h, "
                       "and where today's flow ranks against the past ~30 days.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "get_flow_history",
        "description": "Summary of your flow over a past window: min, max, median, and when the peak "
                       "happened. Use for questions about storms, floods, or recent weeks.",
        "parameters": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "Window length in hours, 1-720", "minimum": 1,
                          "maximum": 720}
            },
            "required": ["hours"],
        },
    },
    {"type": "web_search", "location": {"country": "CA"}},
]

# ---------------------------------------------------------------- gauge data

_cache: dict = {"t": 0.0, "rows": []}


async def gauge_rows(http: aiohttp.ClientSession) -> list[dict]:
    """Newest-first list of {t: datetime, level, flow}. Cached for 5 minutes."""
    if time.time() - _cache["t"] < 300 and _cache["rows"]:
        return _cache["rows"]
    async with http.get(GAUGE_URL.format(station=STATION), timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    rows = []
    for f in data["features"]:
        p = f["properties"]
        if p.get("DISCHARGE") is None:
            continue
        rows.append({
            "t": datetime.fromisoformat(p["DATETIME"].replace("Z", "+00:00")),
            "level": p.get("LEVEL"),
            "flow": p["DISCHARGE"],
        })
    _cache.update(t=time.time(), rows=rows)
    return rows


def _window(rows, hours):
    cutoff = rows[0]["t"].timestamp() - hours * 3600
    return [r for r in rows if r["t"].timestamp() >= cutoff]


def _ago(t: datetime) -> str:
    h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    return f"{h:.0f} hours ago" if h < 48 else f"{h / 24:.1f} days ago"


async def get_current_conditions(http, **_):
    rows = await gauge_rows(http)
    now, day = rows[0], _window(rows, 24)
    flows = sorted(r["flow"] for r in rows)
    pct = 100 * sum(f <= now["flow"] for f in flows) / len(flows)
    return {
        "station": STATION,
        "measured_at": now["t"].isoformat(),
        "measured": _ago(now["t"]),
        "water_level_m": now["level"],
        "flow_m3s": now["flow"],
        "flow_24h_ago_m3s": day[-1]["flow"],
        "level_change_24h_m": round(now["level"] - day[-1]["level"], 3) if now["level"] and day[-1]["level"] else None,
        "flow_percentile_vs_last_30_days": round(pct),
        "median_flow_last_30_days_m3s": statistics.median(flows),
        "max_flow_last_30_days_m3s": flows[-1],
    }


async def get_flow_history(http, hours=168, **_):
    rows = await gauge_rows(http)
    w = _window(rows, max(1, min(int(hours), 720)))
    peak = max(w, key=lambda r: r["flow"])
    low = min(w, key=lambda r: r["flow"])
    return {
        "window_hours": hours,
        "samples": len(w),
        "median_flow_m3s": statistics.median(r["flow"] for r in w),
        "peak_flow_m3s": peak["flow"],
        "peak_when": f"{peak['t'].isoformat()} ({_ago(peak['t'])})",
        "peak_vs_median_ratio": round(peak["flow"] / statistics.median(r["flow"] for r in w), 1),
        "lowest_flow_m3s": low["flow"],
        "lowest_when": f"{low['t'].isoformat()} ({_ago(low['t'])})",
    }


TOOL_FUNCS = {"get_current_conditions": get_current_conditions, "get_flow_history": get_flow_history}

# ---------------------------------------------------------------- web routes


async def index(_req):
    return web.FileResponse(STATIC / "index.html")


async def api_gauge(req):
    """Hourly series for the sparkline + latest reading."""
    rows = await gauge_rows(req.app["http"])
    hourly, seen = [], set()
    for r in reversed(rows):
        key = r["t"].strftime("%Y%m%d%H")
        if key not in seen:
            seen.add(key)
            hourly.append({"t": r["t"].isoformat(), "flow": r["flow"]})
    return web.json_response({
        "station": STATION, "river": RIVER_NAME,
        "latest": {"t": rows[0]["t"].isoformat(), "flow": rows[0]["flow"], "level": rows[0]["level"]},
        "series": hourly,
    })


async def ws_relay(req):
    """One browser socket <-> one Higgs Realtime session."""
    http: aiohttp.ClientSession = req.app["http"]
    browser = web.WebSocketResponse(max_msg_size=8 * 2**20)
    await browser.prepare(req)

    key = os.environ.get("BOSON_API_KEY")
    if not key:
        await browser.send_json({"type": "error", "error": {"message": "BOSON_API_KEY is not set on the server."}})
        await browser.close()
        return browser

    async with http.ws_connect(BOSON_WS, headers={"Authorization": f"Bearer {key}"},
                               max_msg_size=16 * 2**20) as boson:
        await boson.send_json({
            "type": "session.update",
            "session": {
                "model": "higgs-realtime",
                "instructions": INSTRUCTIONS,
                "tools": TOOLS,
                "audio": {
                    "input": {
                        "turn_detection": {"type": "semantic_vad"},
                        "transcription": {"model": "higgs-stt-3.1"},
                        "noise_reduction": {"type": "near_field"},
                    },
                    "output": {"voice": os.environ.get("VOICE", "nora")},
                },
            },
        })
        # Let the river speak first. (Higgs doesn't emit session.updated, so kick off here.)
        await boson.send_json({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text":
                "(A visitor has just walked up to your bank. Check how you're doing, then greet them "
                "in two sentences.)"}]}})
        await boson.send_json({"type": "response.create"})

        async def browser_to_boson():
            async for msg in browser:
                if msg.type == aiohttp.WSMsgType.BINARY:  # raw PCM16 24 kHz mono from the mic
                    await boson.send_json({"type": "input_audio_buffer.append",
                                           "audio": base64.b64encode(msg.data).decode()})
                elif msg.type == aiohttp.WSMsgType.TEXT:  # control events (e.g. kickoff greeting)
                    await boson.send_str(msg.data)
            await boson.close()

        async def run_tools(output):
            calls = [i for i in output if i.get("type") == "function_call" and i.get("executor", "client") == "client"]
            for call in calls:
                fn = TOOL_FUNCS.get(call["name"])
                args = {}
                try:
                    args = json.loads(call.get("arguments") or "{}")
                    result = await fn(http, **args) if fn else {"error": f"unknown tool {call['name']}"}
                except Exception as e:  # keep the conversation alive on data hiccups
                    result = {"error": str(e)}
                await browser.send_json({"type": "river.tool", "name": call["name"], "args": args, "result": result})
                await boson.send_json({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)}})
            if calls:
                await boson.send_json({"type": "response.create"})

        async def boson_to_browser():
            async for msg in boson:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                ev = json.loads(msg.data)
                if ev["type"] == "response.done":
                    await run_tools(ev.get("response", {}).get("output", []))
                if not browser.closed:
                    await browser.send_str(msg.data)
            if not browser.closed:
                await browser.send_json({"type": "river.closed", "code": boson.close_code})
                await browser.close()

        tasks = [asyncio.create_task(browser_to_boson()), asyncio.create_task(boson_to_browser())]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    return browser


async def on_startup(app):
    app["http"] = aiohttp.ClientSession()


async def on_cleanup(app):
    await app["http"].close()


def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index)
    app.router.add_get("/api/gauge", api_gauge)
    app.router.add_get("/ws", ws_relay)
    app.router.add_static("/static", STATIC)
    port = int(os.environ.get("PORT", 8080))
    print(f"Interview a River → http://localhost:{port}  (station {STATION})")
    web.run_app(app, port=port, print=None)


if __name__ == "__main__":
    main()
