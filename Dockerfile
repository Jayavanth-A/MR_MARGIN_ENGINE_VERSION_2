FROM python:3.11-slim

# Install system dependencies (FFmpeg and FFprobe)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY engine/ ./engine/
COPY frontend/ ./frontend/
COPY scripts/ ./scripts/
COPY main.py .
COPY server.py .
COPY README.md .
COPY .env.example .

# Create working directories
RUN mkdir -p output cache sample_data

EXPOSE 8000

# Default command launches web dashboard server
CMD ["python", "main.py", "--serve", "--port", "8000"]
