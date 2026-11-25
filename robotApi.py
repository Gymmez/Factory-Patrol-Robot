import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from contextlib import asynccontextmanager
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import create_async_engine

from typing import Annotated
from pydantic import BaseModel, Field


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://", 1)
MJPEG_STREAM_URL = os.getenv(
    "MJPEG_STREAM_URL",
    "http://127.0.0.1:5000/video_feed",  # default value if not set in .env file
)


@asynccontextmanager
async def lifespan(app):
    app.state.engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    yield
    await app.state.engine.dispose()


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


class IngestPayload(BaseModel):
    time: datetime | None = None
    readings: dict[str, Annotated[float, Field(allow_inf_nan=False)]] = Field(min_length=1)


@app.post("/api/ingest", status_code=201)
async def ingest(payload: IngestPayload, request: Request):
    when = payload.time or datetime.now(timezone.utc)

    lookup = text("SELECT sensorname, sensorid FROM sensors WHERE sensorname IN :names") \
        .bindparams(bindparam("names", expanding=True))

    async with request.app.state.engine.begin() as conn:
        ids = dict((await conn.execute(lookup, {"names": list(payload.readings)})).all())

        unknown = payload.readings.keys() - ids.keys()
        if unknown:
            raise HTTPException(422, f"Unknown sensor(s): {', '.join(sorted(unknown))}")

        await conn.execute(
            text("""INSERT INTO metrics (time, sensorid, value) VALUES (:t, :s, :v)
                    ON CONFLICT (time, sensorid) DO UPDATE SET value = EXCLUDED.value"""),
            [{"t": when, "s": ids[name], "v": value} for name, value in payload.readings.items()],
        )

    return {"status": "ok", "stored": len(payload.readings)}


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
async def sensor_values(request: Request):
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT DISTINCT ON (s.sensorname) s.sensorname, m.value
            FROM metrics m JOIN sensors s USING (sensorid)
            WHERE m.time > now() - interval '15 seconds'
            ORDER BY s.sensorname, m.time DESC
        """))).all()

    latest = {name: round(value, 2) for name, value in rows}
    keys = ("battery", "voltage", "temperature", "distance", "humidity", "light")

    return {
        **{key: latest.get(key) for key in keys},
        "connected": bool(latest),
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
