FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        patch \
        wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY gaugemodel.sh /app/gaugemodel.sh
RUN chmod +x /app/gaugemodel.sh \
    && /app/gaugemodel.sh

COPY . /app

EXPOSE 8000

CMD ["uvicorn", "robotApi:app", "--host", "0.0.0.0", "--port", "8000"]
