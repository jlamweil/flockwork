FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pytest
RUN useradd -m -u 1004 worker
USER worker
ENV HOME=/home/worker
WORKDIR /work
