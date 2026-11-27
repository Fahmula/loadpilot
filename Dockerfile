# Compile the Tailwind/daisyUI stylesheet. CSS is the same on every platform,
# so this stage always runs natively on the build machine.
FROM --platform=$BUILDPLATFORM debian:trixie-slim AS css
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY scripts/build-css.sh scripts/build-css.sh
COPY assets assets
COPY templates templates
COPY loadpilot loadpilot
RUN scripts/build-css.sh

FROM python:3.14-slim

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
COPY --from=css /src/static/css/app.css /app/static/css/app.css

RUN chmod +x /app/entry.sh

# The app runs as a regular user, who can't write __pycache__ into /app; compile it now instead
RUN python -m compileall -q /app/app.py /app/loadpilot

# Version is passed in by the release workflow from the git tag.
# PUID/PGID: the user LoadPilot runs as and the owner of /config (entry.sh drops root to it).
ARG LOADPILOT_VERSION=dev
ENV LOADPILOT_VERSION=${LOADPILOT_VERSION} \
    LOADPILOT_DATA_DIR=/config \
    PUID=1000 \
    PGID=1000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

VOLUME /config

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["/app/entry.sh"]
