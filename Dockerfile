FROM python:3.14-slim

# Install system dependencies required by WeasyPrint for PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libgirepository-1.0-1 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    jinja2 \
    python-multipart \
    pydantic \
    aiosqlite \
    google-genai \
    python-dotenv \
    pypdf \
    weasyprint

# Copy the application files
COPY app.py /app/app.py
COPY loadpilot /app/loadpilot
COPY entry.sh /app/entry.sh
COPY requirements.txt /app/requirements.txt
COPY templates /app/templates
COPY static /app/static

RUN mkdir -p /config/logs static/bols static/invoices

RUN chmod +x /app/entry.sh

EXPOSE 8000

VOLUME /config

ENTRYPOINT ["/app/entry.sh"]