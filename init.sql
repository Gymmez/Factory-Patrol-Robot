CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS sensors (
    sensorid INTEGER PRIMARY KEY,
    sensorname TEXT NOT NULL UNIQUE,
    sensordescrp TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    time TIMESTAMPTZ NOT NULL,
    sensorid INTEGER NOT NULL REFERENCES sensors(sensorid),
    value DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (time, sensorid)
);

SELECT create_hypertable('metrics', 'time', if_not_exists => TRUE);

-- These names must match exactly what the Pi/ESP32 sends in /api/ingest,
-- and what /api/sensors + sensors.html expect: battery, voltage,
-- temperature, distance, humidity, light.
INSERT INTO sensors (sensorid, sensorname, sensordescrp) VALUES
    (1, 'battery',     'Battery level (%)'),
    (2, 'voltage',     'Battery voltage (V)'),
    (3, 'temperature', 'Temperature (°C)'),
    (4, 'distance',    'Obstacle distance (cm)'),
    (5, 'humidity',    'Relative humidity (%)'),
    (6, 'light',       'Light sensor (1 = light, 0 = dark)')
ON CONFLICT (sensorname) DO NOTHING;
