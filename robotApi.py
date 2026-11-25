from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import time
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

load_dotenv()

MJPEG_STREAM_URL = os.getenv(
    "MJPEG_STREAM_URL",
    "http://127.0.0.1:5000/video_feed",  # default value if not set in .env file
)

# remembers the last camera check so we don't hit the camera on every page refresh
CAMERA_CHECK_EVERY = 10  # seconds
camera_cache = {"connected": False, "checked_at": 0.0}

# no new data for this many seconds = robot is offline
OFFLINE_AFTER = 15


def build_async_database_url(raw_url: str | None) -> str:
    if not raw_url:
        raise ValueError("DATABASE_URL is missing or empty.")
    cleaned = raw_url.strip()
    if cleaned.startswith("postgresql+asyncpg://"):
        return cleaned
    if cleaned.startswith("postgresql://"):
        return cleaned.replace("postgresql://", "postgresql+asyncpg://", 1)
    if cleaned.startswith("postgres://"):
        return cleaned.replace("postgres://", "postgresql+asyncpg://", 1)
    return cleaned


class IngestPayload(BaseModel):
    readings: dict[str, float] = Field(..., min_length=1)


async def check_database_status(engine: AsyncEngine) -> tuple[bool, str, dict[str, Any]]:
    try:
        async with engine.connect() as conn:
            sensor_res = await conn.execute(
                text("SELECT count(*), string_agg(sensorname, ', ' ORDER BY sensorid) AS names FROM sensors")
            )
            sensor_row = sensor_res.one()
            sensor_count = int(sensor_row.count or 0)
            sensor_names = sensor_row.names or "none"

            # approximate_row_count is a fast estimate (count(*) scans the whole table)
            metrics_res = await conn.execute(
                text(
                    "SELECT GREATEST(approximate_row_count('metrics'), 0) AS total_rows, "
                    "max(time) AS last_time FROM metrics"
                )
            )
            metrics_row = metrics_res.one()
            total_metrics = int(metrics_row.total_rows or 0)
            last_time = metrics_row.last_time

        last_time_str = last_time.isoformat() if last_time else "no rows yet"
        detail = (
            f"Connected | Sensors: {sensor_count} ({sensor_names}) | "
            f"Metrics rows (approx): {total_metrics} | Last write: {last_time_str}"
        )
        info = {
            "sensor_count": sensor_count,
            "sensor_names": sensor_names.split(", ") if sensor_row.names else [],
            "metrics_rows": total_metrics,
            "last_write": last_time.isoformat() if last_time else None,
        }
        return True, detail, info
    except Exception as exc:
        return False, f"Disconnected: {exc}", {}


async def load_sensor_map(engine: AsyncEngine) -> dict[str, int]:
    # sensor names -> ids, e.g. {"battery": 1, "voltage": 2}
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT sensorid, sensorname FROM sensors"))
        return {row.sensorname: int(row.sensorid) for row in rows}


async def is_camera_connected() -> bool:
    # only check the camera again if the last check is older than 10 seconds
    if time.time() - camera_cache["checked_at"] < CAMERA_CHECK_EVERY:
        return camera_cache["connected"]

    connected = False
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            async with client.stream("GET", MJPEG_STREAM_URL) as response:
                connected = response.status_code == 200
    except httpx.RequestError:
        pass

    camera_cache["connected"] = connected
    camera_cache["checked_at"] = time.time()
    return connected


@asynccontextmanager
async def lifespan(app: FastAPI):
    raw_db_url = os.getenv("DATABASE_URL")
    try:
        async_db_url = build_async_database_url(raw_db_url)
    except ValueError as exc:
        print(f"[DB] Configuration error: {exc}")
        raise

    engine = create_async_engine(async_db_url, pool_pre_ping=True)
    app.state.engine = engine

    is_ok, detail, _ = await check_database_status(engine)
    app.state.db_connected = is_ok
    if is_ok:
        print(f"[DB] Startup check passed -> {detail}")
    else:
        print(f"[DB] Startup check failed -> {detail}")

    # load the sensor list once (it never changes while the app runs)
    app.state.sensor_map = {}
    try:
        app.state.sensor_map = await load_sensor_map(engine)
    except Exception as exc:
        print(f"[DB] Could not load sensors: {exc}")

    try:
        yield
    finally:
        await engine.dispose()
        print("[DB] Engine disposed cleanly.")


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# serves files from the static/ folder (the home page background image lives there)
app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")


# basic health check used by "docker compose ps" / monitoring
@app.get("/health")
async def health(request: Request):
    db_ok, db_detail, db_info = await check_database_status(request.app.state.engine)
    request.app.state.db_connected = db_ok
    return {
        "status": "ok" if db_ok else "degraded",
        "database": {
            "connected": db_ok,
            "detail": db_detail,
            **db_info,
        },
    }


# home page: shows the camera stream + db status
@app.get("/", name="home")
async def home(request: Request):
    db_ok, db_detail, db_info = await check_database_status(request.app.state.engine)
    request.app.state.db_connected = db_ok
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active_page": "home",
            "db_ok": db_ok,
            "db_detail": db_detail,
            "db_info": db_info,
            "sensor_count": db_info.get("sensor_count"),
        },
    )


# sensors page: just renders the template, real values come from /api/sensors below
@app.get("/sensors", name="sensors")
async def sensors(request: Request):
    db_ok, db_detail, db_info = await check_database_status(request.app.state.engine)
    request.app.state.db_connected = db_ok
    return templates.TemplateResponse(
        request,
        "sensors.html",
        {
            "active_page": "sensors",
            "db_ok": db_ok,
            "db_detail": db_detail,
            "db_info": db_info,
            "sensor_count": db_info.get("sensor_count"),
        },
    )


# returns the latest reading for every sensor, used by the sensors/home pages
@app.get("/api/sensors")
async def sensor_values(request: Request):
    engine: AsyncEngine = request.app.state.engine

    # default to "no data yet" for every sensor
    readings: dict[str, float | None] = {
        "battery": None, "voltage": None, "temperature": None,
        "distance": None, "humidity": None, "light": None,
    }
    latest_time = None

    # one query: for each sensor, take the row with the newest time
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT DISTINCT ON (s.sensorid) s.sensorname, m.value, m.time
                    FROM sensors s
                    JOIN metrics m ON m.sensorid = s.sensorid
                    ORDER BY s.sensorid, m.time DESC
                    """
                )
            )
            for row in result.all():
                readings[row.sensorname] = row.value
                if latest_time is None or row.time > latest_time:
                    latest_time = row.time
    except Exception as exc:
        # database read failed, just keep readings as None instead of crashing
        print(f"[sensors] DB read failed: {exc}")

    # check if the robot's camera stream is reachable (cached for 10 seconds)
    connected = await is_camera_connected()

    # robot is online only if we got data recently
    robot_online = False
    if latest_time is not None:
        age = (datetime.now(timezone.utc) - latest_time).total_seconds()
        robot_online = age <= OFFLINE_AFTER

    # send everything back to the webpage
    return {
        **readings,
        "connected": connected,
        "robot_online": robot_online,
        "updated_at": latest_time.isoformat() if latest_time else datetime.now(timezone.utc).isoformat(),
    }


# saves new sensor readings sent by the robot (ESP32/Pi) into the metrics table
@app.post("/api/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_readings(payload: IngestPayload, request: Request):
    engine: AsyncEngine = request.app.state.engine
    now_utc = datetime.now(timezone.utc)

    # use the sensor list loaded at startup (reload it if it was empty)
    sensor_map: dict[str, int] = request.app.state.sensor_map
    if not sensor_map:
        try:
            sensor_map = await load_sensor_map(engine)
            request.app.state.sensor_map = sensor_map
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not load sensors: {exc}",
            ) from exc

    unknown_keys = [name for name in payload.readings if name not in sensor_map]
    if unknown_keys:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Unknown sensor name(s)",
                "unknown_sensors": unknown_keys,
                "allowed_sensors": sorted(sensor_map.keys()),
            },
        )

    insert_rows = [
        {
            "time": now_utc,
            "sensorid": sensor_map[sensor_name],
            "value": float(value),
        }
        for sensor_name, value in payload.readings.items()
    ]

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO metrics (time, sensorid, value)
                    VALUES (:time, :sensorid, :value)
                    """
                ),
                insert_rows,
            )

        return {
            "inserted": len(insert_rows),
            "timestamp": now_utc.isoformat(),
            "readings": payload.readings,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database write failed: {exc}",
        ) from exc


# test route: writes a dummy row then deletes it, to confirm the db can be written to
@app.post("/api/db-check")
async def verify_db_write(request: Request):
    engine: AsyncEngine = request.app.state.engine
    now_utc = datetime.now(timezone.utc)
    try:
        async with engine.begin() as conn:
            first_sensor = await conn.execute(
                text("SELECT sensorid, sensorname FROM sensors ORDER BY sensorid LIMIT 1")
            )
            sensor = first_sensor.first()
            if not sensor:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Connected, but sensors table is empty.",
                )

            inserted = await conn.execute(
                text(
                    """
                    INSERT INTO metrics (time, sensorid, value)
                    VALUES (:time, :sensorid, :value)
                    RETURNING time, sensorid, value
                    """
                ),
                {"time": now_utc, "sensorid": sensor.sensorid, "value": -999.0},
            )
            row = inserted.mappings().one()

            await conn.execute(
                text("DELETE FROM metrics WHERE time = :time AND sensorid = :sensorid AND value = :value"),
                {"time": now_utc, "sensorid": sensor.sensorid, "value": -999.0},
            )

        return {
            "db_read_write_ok": True,
            "verified_sensor": sensor.sensorname,
            "test_row_written_and_cleaned": {
                "time": row["time"].isoformat(),
                "sensorid": row["sensorid"],
                "value": row["value"],
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"DB write check failed: {exc}",
        ) from exc


# full-screen camera stream page
@app.get("/stream", name="stream")
async def stream(request: Request):
    return templates.TemplateResponse(
        request,
        "stream.html",
        {"active_page": "stream"},
    )


# pulls the MJPEG stream from the robot's camera and passes it on to the browser
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


# control page: the buttons that send move commands
@app.get("/control", name="control")
async def control(request: Request):
    return templates.TemplateResponse(
        request,
        "control.html",
        {"active_page": "control"},
    )


# receives a move command from the control page (not wired to real hardware yet)
@app.post("/move/{direction}")
async def move_robot(direction: str):
    print(f"ROBOT COMMAND RECEIVED: {direction.upper()}")
    return {"status": "ok", "message": f"Moving {direction}"}
