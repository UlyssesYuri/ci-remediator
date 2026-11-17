# ci-remediator

Public intake API for an autonomous CI remediator. Accepts GitHub
`workflow_run` webhooks, extracts failed runs, stores one row per run in
Postgres, and sends one message per run to SQS. It does not repair anything
yet — that is the future worker's job.

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

## Local Postgres

```bash
docker run -d --name ci-remediator-pg \
  -e POSTGRES_USER=ci -e POSTGRES_PASSWORD=ci -e POSTGRES_DB=ci_remediator \
  -p 5432:5432 postgres:16
export DATABASE_URL=postgresql://ci:ci@127.0.0.1:5432/ci_remediator
python scripts/local_postgres_check.py
psql "$DATABASE_URL" -c "select full_name, github_run_id, status from runs;"
docker rm -f ci-remediator-pg
```

## Local SQS

```bash
docker run -d --name ci-remediator-sqs -p 9324:9324 softwaremill/elasticmq-native
export DATABASE_URL=sqlite:///./ci-remediator.db
export SQS_ENDPOINT_URL=http://127.0.0.1:9324
export SQS_QUEUE_URL=http://127.0.0.1:9324/000000000000/ci-remediator
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
python scripts/local_sqs_check.py
# Expect: first status 200, second status 200, message_count 1
docker rm -f ci-remediator-sqs
```

## Railway

- Create a service from this repo.
- Builder: root `Dockerfile`.
- Start command: leave empty (uses the Dockerfile `CMD`).
- Enable public networking.
- Healthcheck path: `/health`.
- Variables: `DATABASE_URL`, `AWS_REGION`, `SQS_QUEUE_URL`,
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`. Never set
  `SQS_ENDPOINT_URL` — that is only for the local elasticmq queue.
- Tables are created on startup (`Base.metadata.create_all`); there is no
  migration tool.

## Webhook on agent-demo-inventory

- Content type: `application/json`
- Event: `workflow_run` only
- URL: `https://<name>.up.railway.app/webhook` (no `www` prefix — the
  certificate is only valid for `*.up.railway.app`)

## Behavior

`POST /webhook` returns `200 {"accepted": true}` for every delivery,
including pings, empty bodies, invalid JSON, and non-failure conclusions.

An accepted failure (`action == "completed"` and `conclusion` in `failure`,
`timed_out`, `startup_failure`) is parsed and persisted as one row keyed on
`(full_name, github_run_id)`, then sent to SQS. `sqs_message_id` is recorded
on the row so a GitHub redelivery never sends a second message.

Every failure path still returns 200, with a warning in the logs:

| What happened | Deploy Logs | Row | Recovery |
|---|---|---|---|
| Database write fails | `WARNING: persist failed` | none | fix the database, then Redeliver |
| `SQS_QUEUE_URL` unset | not-set warning + the body | `sqs_message_id` null | set the variable, then Redeliver |
| `SendMessage` fails | `WARNING: SendMessage failed` | `sqs_message_id` null | Redeliver — the next delivery sends |
| Sent | the body | `sqs_message_id` set | done, waiting for a worker |

## Worker

Run from the repo root with `.env` sourced:

```bash
set -a && source .env && set +a
python -m worker.sqs_worker
```

The worker long-polls SQS one message at a time. For each one it downloads
`logs_url` once and stores the zip in `AWS_S3_BUCKET`, then runs a LangGraph
agent: it clones `clone_url` at `head_sha`, runs `claude -p` to edit the
code, runs the repo's allowlisted test command, and retries up to three
times. A passing fix opens one pull request, ready for review; three
failures (or a passing gate with no edit) set the run to `needs_manual`.
It never merges. The message is deleted only after the graph returns
without raising — a crash or an infrastructure error leaves it on the
queue to retry.

Worker-only variables (the API on Railway never reads these):

| Variable | Value |
|---|---|
| `DATABASE_URL`, `AWS_REGION`, `SQS_QUEUE_URL` | Same as Railway |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | A key that can receive/delete on the queue and read/write the bucket |
| `AWS_S3_BUCKET` | The log bucket name |
| `GITHUB_TOKEN` | Fine-grained PAT: Actions read, Contents read/write, Pull requests read/write |
| `ANTHROPIC_API_KEY` | For `claude -p` |

A missing database or queue is a warning, not a crash — the worker retries
every 20 seconds instead of exiting.
