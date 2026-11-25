import requests, random, time

while True:
    data = {"readings": {
        "voltage": round(random.uniform(23.5, 24.5), 1),
        "temperature": round(random.uniform(30, 35), 1),
        "battery": random.randint(80, 90),
    }}
    print(requests.post("http://localhost:8000/api/ingest", json=data).status_code)
    time.sleep(1)