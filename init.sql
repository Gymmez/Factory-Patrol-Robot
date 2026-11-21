CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS sensors (
    sensorid INTEGER PRIMARY KEY,
    sensorname TEXT NOT NULL,
    sensordescrp TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    time TIMESTAMPTZ NOT NULL,
    sensorid INTEGER NOT NULL REFERENCES sensors(sensorid),
    value DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (time, sensorid)
);

CREATE TABLE IF NOT EXISTS detections (
    time TIMESTAMPTZ NOT NULL,
    detection TEXT NOT NULL,
    image_path TEXT,
    mode TEXT,
    sensorid INTEGER NOT NULL REFERENCES sensors(sensorid),
    PRIMARY KEY (time, sensorid)
);

SELECT create_hypertable('metrics', 'time', if_not_exists => TRUE);
SELECT create_hypertable('detections', 'time', if_not_exists => TRUE);
