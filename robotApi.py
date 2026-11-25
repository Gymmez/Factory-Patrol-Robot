from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
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

            metrics_res = await conn.execute(
                text("SELECT count(*) AS total_rows, max(time) AS last_time FROM metrics")
            )
            metrics_row = metrics_res.one()
            total_metrics = int(metrics_row.total_rows or 0)
            last_time = metrics_row.last_time

        last_time_str = last_time.isoformat() if last_time else "no rows yet"
        detail = (
            f"Connected | Sensors: {sensor_count} ({sensor_names}) | "
            f"Metrics rows: {total_metrics} | Last write: {last_time_str}"
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

    try:
        yield
    finally:
        await engine.dispose()
        print("[DB] Engine disposed cleanly.")


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


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

    # pull the latest reading for each sensor out of the database
    try:
        async with engine.connect() as conn:
            sensors = await conn.execute(text("SELECT sensorid, sensorname FROM sensors"))
            for sensor in sensors.all():
                row = (await conn.execute(
                    text("SELECT value, time FROM metrics WHERE sensorid = :id ORDER BY time DESC LIMIT 1"),
                    {"id": sensor.sensorid},
                )).first()
                if row is not None:
                    readings[sensor.sensorname] = row.value
                    latest_time = max(latest_time, row.time) if latest_time else row.time
    except Exception as exc:
        # database read failed, just keep readings as None instead of crashing
        print(f"[sensors] DB read failed: {exc}")

    # check if the robot's camera stream is currently reachable
    connected = False
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            async with client.stream("GET", MJPEG_STREAM_URL) as response:
                connected = response.status_code == 200
    except httpx.RequestError:
        pass

    # send everything back to the webpage
    return {
        **readings,
        "connected": connected,
        "updated_at": latest_time.isoformat() if latest_time else datetime.now(timezone.utc).isoformat(),
    }


# saves new sensor readings sent by the robot (ESP32/Pi) into the metrics table
@app.post("/api/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_readings(payload: IngestPayload, request: Request):
    engine: AsyncEngine = request.app.state.engine
    now_utc = datetime.now(timezone.utc)

    try:
        async with engine.begin() as conn:
            sensor_rows = await conn.execute(text("SELECT sensorid, sensorname FROM sensors"))
            sensor_map: dict[str, int] = {
                row.sensorname: int(row.sensorid) for row in sensor_rows
            }

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
    except HTTPException:
        raise
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