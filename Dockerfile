# Multi-Arch: amd64 fuer Umbrel-PCs, arm64 fuer den Raspberry Pi.
# Bauen mit:
#   docker buildx build --platform linux/amd64,linux/arm64 \
#     -t ghcr.io/saftcpu/satscope:0.1.0 --push .
# Danach IMMER pruefen, dass beide Architekturen gelistet sind:
#   docker buildx imagetools inspect ghcr.io/saftcpu/satscope:0.1.0
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/

# Nicht als root laufen; die ID passt zu den Volume-Rechten aus pre-start.
RUN useradd -u 1000 -m satscope || true
USER 1000:1000

CMD ["python", "-m", "satscope.web"]
