# DCTA: voice banking agent

Tencent Cloud Hackathon 2026 · DBS case study (DCTA), Scenario 1: voice payments.
Speak a banking command; the AI drafts it, a second model checks it, and money moves only after you sign it on your phone.

Background: `docs/BRIEF.md`. Coding assistants: read `AGENTS.md` first.

## Run locally

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in your keys
uvicorn app.main:app --reload
```

Open http://localhost:8000 for the app, or http://localhost:8000/docs for the API.

## Test

```bash
pytest
ruff check . && ruff format --check .
```

## Front end

See D-004 in `docs/DECISIONS.md`.
