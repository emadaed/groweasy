# GrowEasy Invoice – Production-Ready with WeasyPrint (Bookworm)
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install minimal system dependencies for WeasyPrint + Pillow + Postgres
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    # WeasyPrint core libraries
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libcairo-gobject2 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libharfbuzz0b \
    libfreetype6 \
    libfontconfig1 \
    # Pillow image libraries
    libjpeg62-turbo-dev \
    libpng-dev \
    libtiff-dev \
    libwebp-dev \
    zlib1g-dev \
    liblcms2-dev \
    # PostgreSQL
    libpq-dev \
    # XML/HTML
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy requirements for caching
COPY requirements.txt .

# Install Python packages
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD gunicorn --bind 0.0.0.0:$PORT \
    --workers 2 \
    --worker-class gevent \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    app:app