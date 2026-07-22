# WARF — Knowledge Preservation Framework

> **"Employees change. Experience stays."**

WARF is an AI-powered knowledge management platform that preserves institutional
knowledge and prevents organizational brain drain. It captures meeting
discussions, transcribes them, generates structured summaries, and extracts
decisions and action items using Large Language Models (LLMs).

An integrated **Retrieval-Augmented Generation (RAG)** assistant lets users query
historical meetings and get contextual, evidence-based answers with cited sources.

Developed as a graduation project during the *Artificial Intelligence Model
Building & Development* Bootcamp at **Tuwaiq Academy | أكاديمية طويق**.

---

## Core Capabilities

| Feature | Description |
|---|---|
| **WARF Meet** | Meetings with auto-transcription and intelligent summaries. |
| **AI Meeting Engine** | Summarizes transcripts and extracts decisions, action items, and risks via an LLM. |
| **WARF Assistant** | A RAG chatbot that answers questions from the meeting knowledge base, citing sources and prioritizing the most recent information. |
| **Decision Support** | Tracks decisions and generates executive reports. |
| **Face Verification** | Optional attendance/identity verification (see notes below). |

---

## Tech Stack

- **Backend:** Django 5 (Python)
- **Database:** PostgreSQL (Neon) in production; SQLite for local development
- **LLM:** Amazon Bedrock — Claude Sonnet (via the Converse API)
- **Retrieval:** Keyword-based RAG over `KnowledgeDocument` / `KnowledgeChunk`
- **Static files:** WhiteNoise
- **Server:** Gunicorn
- **Hosting:** Render (web service) + Neon (managed Postgres)

---

## Project Structure

```
Warf_System/
├── accounts/      # Custom user model, profiles, face verification
├── meetings/      # Meetings, attendees, AI meeting engine
│   └── services/ai_meeting_engine/   # Bedrock summarizer + decision extractor
├── minutes/       # Meeting minutes
├── tasks/         # Tasks and submissions
├── records/       # Knowledge documents & chunks (RAG store)
├── archive/       # Archived entries
├── assistant/     # WARF Assistant chatbot (RAG + Bedrock)
├── core/          # Settings, URLs, WSGI
├── templates/     # HTML templates
└── static/        # CSS / assets
```

---

## Local Setup

```bash
git clone https://github.com/Ali-Arishi/Warf_System.git
cd Warf_System

pip install -r requirements.txt

# create a .env file (see below), then:
python manage.py migrate
python manage.py createsuperuser
python manage.py import_seed_knowledge seed_data/all_meetings.jsonl   # loads the RAG knowledge base
python manage.py runserver
```

Open http://127.0.0.1:8000

---

## Environment Variables

Create a `.env` file (local) or set these on your host (Render):

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DJANGO_DEBUG` | `True` locally, `False` in production |
| `DATABASE_URL` | Postgres connection string (Neon). Omit locally to use SQLite. |
| `AWS_ACCESS_KEY_ID` | AWS key for Bedrock |
| `AWS_SECRET_ACCESS_KEY` | AWS secret for Bedrock |
| `AWS_REGION` | e.g. `us-east-1` |
| `BEDROCK_MODEL_ID` | Bedrock model / inference-profile ID (e.g. a Claude Sonnet profile) |

> The AI features degrade gracefully: if Bedrock is unreachable, the assistant
> still returns the relevant knowledge content, and the app keeps running.

---

## Deployment (Render)

1. **New Web Service** → connect this GitHub repo
2. **Build Command:** `pip install -r requirements.txt`
3. **Start Command:** `gunicorn core.wsgi:application --bind 0.0.0.0:$PORT`
4. Add the environment variables above
5. Run migrations and import the knowledge base against the production database

The database (Neon) and LLM (Bedrock) are reached over HTTPS, so the app can be
hosted anywhere.

---

## Notes

- **Face verification** uses `deepface`, which pulls in TensorFlow (~1.8 GB).
  It is **not** installed by default so the app fits serverless size limits; the
  feature degrades gracefully when the library is absent. To enable it locally,
  run `pip install deepface`.
- The RAG retrieval is currently keyword-based. A future improvement is semantic
  search using embeddings.

---

## Team

| Name | Role |
|---|---|
| Ibrahem Altowalah | Data Science |
| Abdulwahab Ibraheem Almutlak | AI Engineer |
| Norah Ibrahim Alhabib | Business Intelligence |
| Joud Abdullah Alkhaldi | AI Software Engineer |
| Ali Yahya Arishi | AI Engineer |

**Associated with:** Tuwaiq Academy | أكاديمية طويق (Jan 2026)

🔗 Project site: https://jaldwehi.github.io/Warf_Team/
