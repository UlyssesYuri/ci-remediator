FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install --no-install-recommends -y git curl ca-certificates make \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 appuser
# claude refuses --dangerously-skip-permissions as root, so it is installed
# as appuser.
USER appuser
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH=/home/appuser/.local/bin:$PATH
COPY --chown=appuser:appuser . .
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
