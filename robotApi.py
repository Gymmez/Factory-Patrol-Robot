import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

app = FastAPI()
templates = Jinja2Templates(directory="templates")
load_dotenv()
MJPEG_STREAM_URL = os.getenv(
    "MJPEG_STREAM_URL",
    "http://127.0.0.1:5000/video_feed" ,#default value if not set in .env file
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/", name="home")
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"active_page": "home"},
    )


@app.get("/sensors", name="sensors")
async def sensors(request: Request):
    return templates.TemplateResponse(
        request,
        "sensors.html",
        {"active_page": "sensors"},
    )


@app.get("/api/sensors")
async def sensor_values():
    connected = False
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            async with client.stream("GET", MJPEG_STREAM_URL) as response:
                connected = response.status_code == 200
    except httpx.RequestError:
        pass

    return {
        "battery": None,
        "voltage": None,
        "temperature": None,
        "distance": None,
        "humidity": None,
        "light": None,
        "connected": connected,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/stream", name="stream")
async def stream(request: Request):
    return templates.TemplateResponse(
        request,
        "stream.html",
        {"active_page": "stream"},
    )


@app.get("/video_feed", name="video_feed")
async def video_feed():
    client = httpx.AsyncClient(timeout=None)
    upstream_context = client.stream("GET", MJPEG_STREAM_URL)
    try:
        upstream = await upstream_context.__aenter__()
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(502, "Unable to reach the robot video stream") from exc

    if upstream.status_code != 200:
        await upstream_context.__aexit__(None, None, None)
        await client.aclose()
        raise HTTPException(upstream.status_code, "Unable to reach the robot video stream")

    async def mjpeg_stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream_context.__aexit__(None, None, None)
            await client.aclose()

    return StreamingResponse(
        mjpeg_stream(),
        media_type=upstream.headers.get("content-type", "multipart/x-mixed-replace"),
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

@app.get("/control", name="control")
async def control(request: Request):
    return templates.TemplateResponse(
        request,
        "control.html",
        {"active_page": "control"},
    )


@app.post("/move/{direction}")
async def move_robot(direction: str):
    print(f"ROBOT COMMAND RECEIVED: {direction.upper()}")
    return {"status": "ok", "message": f"Moving {direction}"}
