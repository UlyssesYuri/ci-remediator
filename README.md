# ci-remediator

Public intake API for an autonomous CI remediator. Today this repo only
accepts GitHub `workflow_run` webhooks, extracts failed runs, and prints the
message a future worker will consume. It does not repair anything yet.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
curl http://127.0.0.1:8000/health
```

## Run the image

```bash
docker build -t ci-remediator .
docker run --rm -e PORT=8000 -p 8000:8000 ci-remediator
```

## Railway

- Create a service from this repo.
- Builder: root `Dockerfile`.
- Start command: leave empty (uses the Dockerfile `CMD`).
- Enable public networking.
- Healthcheck path: `/health`.

## Webhook on agent-demo-inventory

- Content type: `application/json`
- Event: `workflow_run` only
- URL: `https://<name>.up.railway.app/webhook` (no `www` prefix — the
  certificate is only valid for `*.up.railway.app`)

## Behavior

`POST /webhook` returns `200 {"accepted": true}` for every delivery,
including pings, empty bodies, invalid JSON, and non-failure conclusions.

An accepted failure (`action == "completed"` and `conclusion` in `failure`,
`timed_out`, `startup_failure`) is parsed, passed through the `persist_run`
placeholder, then printed as the SQS message via `enqueue_run`. That line
includes `full_name`, `clone_url`, and `logs_url`. Real Postgres and SQS are
not connected yet.
