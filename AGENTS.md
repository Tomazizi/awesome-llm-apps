# Base44 Development Environment

## What this repo is

Awesome LLM Apps — a collection of 100+ open-source AI agent and RAG apps. There is no single application; each subdirectory is an independent app.

## What runs in the preview

The **AI Travel Agent** (`starter_ai_agents/ai_travel_agent/travel_agent.py`) — a Streamlit app featured in the README quick start. It renders a full UI immediately (title, API-key input fields, destination/duration form, generate/download buttons). API keys (OpenAI + SerpAPI) are entered in the UI itself, not as environment variables, so the app boots and is fully visible without any external credentials. Generating an itinerary requires the user to paste their own keys into the form.

## How it runs

- `docker-compose.base44.yml` uses `python:3.12-slim`, bind-mounts the repo at `/app`, installs `requirements.txt` on startup, and runs `streamlit run travel_agent.py` on port 3000 with poll-based file watching (needed for bind mounts).
- No external secrets are required to boot. The app does not read any env-var credentials.

## Switching to a different app

To run a different app from this collection, change `working_dir` and the `streamlit run` target in `docker-compose.base44.yml` to point at the desired subdirectory and entry file. Most starter agents are single-file Streamlit apps with their own `requirements.txt`.

## Verification

- Health check: `GET /_stcore/health` returns `ok`.
- The main page at `/` shows the travel planner form.
