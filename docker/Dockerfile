FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for building Python packages and for health checks
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libmariadb-dev \
        pkg-config \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY docker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY collector /app/collector
COPY api /app/api
COPY grafana /app/grafana

# Default to collector; override CMD for API (e.g. python -m api)
CMD ["python", "-m", "collector.collector"]
