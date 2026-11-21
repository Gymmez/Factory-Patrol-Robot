# 🤖 Za3tar Robot Project

This project shows a robot camera stream and sensor pages in a web browser.

You do not need to install Python. Docker will run everything for you.

## 📦 What You Need

Install these programs:

1. [Docker Desktop](https://www.docker.com/products/docker-desktop/)
2. [Git](https://git-scm.com/downloads)
3. A working camera or phone camera stream

After installing Docker Desktop, open it and wait until it says Docker is
running.

## 1️⃣ Download the Project

Open a terminal:

- **Windows:** open PowerShell
- **Mac:** open Terminal
- **Linux:** open Terminal

Run:

```bash
git clone <repository-url>
cd Robot
```

Ask the person who gave you the project for the repository URL.

## 2️⃣ Enter the Camera Stream Address

This project needs the address of the camera's MJPEG stream.

The address commonly looks like:

```text
http://192.168.1.50:8080/video
```

Do not use `localhost` or `127.0.0.1`. These addresses refer to the
Docker container, not your camera.

### 📝 Create the settings file

Copy `.env.example` to `.env`:

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**Mac and Linux:**

```bash
cp .env.example .env
```

Open `.env` in a text editor and replace the address:

```dotenv
MJPEG_STREAM_URL=http://192.168.1.50:8080/video
```

Save the file.

## 3️⃣ Start the Project

In the project folder, run:

```bash
docker compose up -d --build
```

The first start may take a while. Docker must download the database and build
the application. It can take 10 minutes or longer depending on your internet
connection.

Do not close Docker Desktop while building or starting the project.

## 4️⃣ Open the Website

Open a browser and visit:

```text
http://localhost:8000
```

Useful pages:

- Home: http://localhost:8000
- Sensors: http://localhost:8000/sensors
- Camera stream: http://localhost:8000/stream
- Robot controls: http://localhost:8000/control
- Health check: http://localhost:8000/health
- API documentation: http://localhost:8000/docs

## 5️⃣ Change the Camera Address

To use another camera address:

1. Open `.env`.
2. Change `MJPEG_STREAM_URL`.
3. Save the file.
4. Run:

```bash
docker compose up -d --force-recreate api
```

Refresh the website.

## ✅ Check Whether It Is Working

Run:

```bash
docker compose ps
```

You should see the `api` and `db` containers running.

For the health check:

```bash
curl http://localhost:8000/health
```

A successful response is:

```json
{"status":"ok"}
```

If the camera is not visible, confirm that:

- The camera address is correct.
- The address works from a normal browser or phone.
- The address is reachable from the computer running Docker.
- The address ends with the correct video path.

## 🛑 Stop the Project

Keep the saved data and stop the containers:

```bash
docker compose down
```

Start it again later:

```bash
docker compose up -d
```

## 🔄 Reset Everything

This permanently deletes all saved database data.

```bash
docker compose down -v
docker compose up -d --build
```

Only use this command when a complete reset is wanted.

## 🧰 If Something Goes Wrong

### ⚠️ The project does not start

Check that Docker Desktop is running, then run:

```bash
docker compose up --build
```

Reading the messages in the terminal can show whether Docker is downloading
or building something.

### 📹 The camera does not appear

Open the camera address directly in a browser.

Example:

```text
http://192.168.1.50:8080/video
```

If that address does not open in the browser, the application cannot receive
the stream.

### 🔍 Show recent errors

```bash
docker compose logs api
docker compose logs db
```
