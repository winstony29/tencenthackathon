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

Open http://localhost:8000/docs for the API. Run the frontend below for the app.

## Test

```bash
pytest
ruff check . && ruff format --check .
```

## Front end

See D-004 in `docs/DECISIONS.md`.

The first UI slice supports typed requests, browser recording against the ASR stub,
clarification, draft cards, related data, and validation. Signing is not connected yet.
Example buttons load labelled UI fixtures; they never execute a transaction.

On Windows, keep the backend running in one terminal:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, from the repo root:

```powershell
cd web
npm.cmd install
npm.cmd run dev
```

Open http://127.0.0.1:3000. The development server proxies `/api/` to FastAPI;
no CORS change is needed. Bun can run the same package scripts.

```powershell
npm.cmd run check
npm.cmd run build
npm.cmd run test:browser
```

Browser checks require both servers and Chrome (override `CHROME_PATH` for another
Chromium executable). Screenshots are written to ignored `graphify-out/`.
The production build writes `web/out/`; FastAPI mounting remains an integration task.
The backend currently serves only API routes; its `/` page is not mounted yet.
