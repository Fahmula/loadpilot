FROM python:3.15-slim

# Runtime libraries WeasyPrint needs to render invoice PDFs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pinned Python dependencies first so this layer is cached between code changes
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application files
COPY app.py /app/app.py
COPY loadpilot /app/loadpilot
COPY entry.sh /app/entry.sh
COPY templates /app/templates
COPY static /app/static

RUN chmod +x /app/entry.sh

# Version is passed in by the release workflow from the git tag
ARG LOADPILOT_VERSION=dev
ENV LOADPILOT_VERSION=${LOADPILOT_VERSION} \
    LOADPILOT_DATA_DIR=/config \
    PYTHONUNBUFFERED=1

EXPOSE 8000

VOLUME /config

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["/app/entry.sh"]
