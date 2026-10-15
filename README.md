# LearnPath

A free web app that helps beginner programmers keep learning when no tutor is around. Learners take a short skill check, get a personal learning plan built from free online resources, and can ask an AI helper for hints when they get stuck.

Built by Group 5 (ST2A) with CompSAt at Ateneo de Manila University. Open source, so CompSAt can run and extend it after the project ends.

## How it works

1. **Sign in with Google.** No passwords to manage.
2. **Diagnose.** The learner picks a track, says how much time they have, and answers a few questions. Topics they already know get skipped.
3. **Plan.** The app orders the remaining topics so prerequisites come first and spreads them across weeks based on the learner's available hours.
4. **Learn.** Each topic links to vetted free resources, with tips from our team and a short fill-in-the-blank quiz.
5. **Get help.** The AI helper gives hints, never full answers, using only that topic's content. When the daily AI limit runs out, learners still get the tips and links.

Tracks: Python, JavaScript, HTML, CSS, Java, SQL.

## Tech stack

- Django with server-rendered templates
- Google login through django-allauth
- PostgreSQL on Neon, SQLite for local development
- Hosted on Render
- AI hints through free tiers of Groq and Cloudflare Workers AI

Everything runs on free tiers. The app costs nothing to host.

## Run it locally

You need Python 3.13 and Git.

```bash
git clone https://github.com/r1tsuuu/socsc14-opensource-learnpath.git
cd socsc14-opensource-learnpath
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # fill in the values (see below)
python manage.py migrate
python manage.py loaddata fixtures/content.json
python manage.py createsuperuser
python manage.py runserver
```

Open http://localhost:8000 for the app and http://localhost:8000/admin for content editing.

### Environment variables

| Variable | Needed for |
| --- | --- |
| `SECRET_KEY` | Django. Any long random string locally |
| `DEBUG` | `True` locally, `False` in production |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Production domain |
| `DATABASE_URL` | Production database. Leave blank locally to use SQLite |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google login |
| `GROQ_API_KEY`, `GROQ_MODEL`, `CF_ACCOUNT_ID`, `CF_API_TOKEN`, `CF_MODEL` | AI helper (optional locally) |
| `AI_DAILY_LIMIT` | AI messages per learner per day |

Ask a teammate for shared keys. Never commit `.env`.

## Deploying

`main` deploys to Render automatically.

- **Render web service** (free, Singapore): build command `./build.sh`, start command `gunicorn config.wsgi:application`. Python comes from `.python-version`.
- **Neon Postgres** (free, Singapore): put its connection string in Render as `DATABASE_URL`.
- Set `SECRET_KEY`, `DEBUG=False`, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in Render. The Render hostname is added to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` automatically; add custom domains yourself.
- Render free has no shell. Run admin commands from your laptop against Neon:

```bash
DATABASE_URL="<neon url>" python manage.py createsuperuser
DATABASE_URL="<neon url>" python manage.py loaddata fixtures/content.json
```

### Google login

1. In Google Cloud console, create an OAuth client (type: Web application).
2. Add these authorized redirect URIs:
   - `http://localhost:8000/accounts/google/login/callback/`
   - `https://<app>.onrender.com/accounts/google/login/callback/`
3. Put the client ID and secret in `.env` locally and in Render's environment.
4. Publish the OAuth consent screen to production, or only listed test users can log in.

The credentials live in settings only. Don't add a Google "Social application" in admin; allauth then finds two and login crashes.

## Adding or editing content

All tracks, topics, links, tips and quiz questions are edited in the admin page (`/admin`) with no coding. Ask a developer for an admin account.

To back up content from production:

```bash
DATABASE_URL="<production database url>" python manage.py dumpdata content --indent 2 > fixtures/content.json
```

## Running tests

```bash
python manage.py test
```

Tests also run on every pull request (GitHub Actions, `.github/workflows/tests.yml`).

## Contributing

1. Create a branch from `main` named `<your-name>/<feature>`.
2. Open a pull request. One teammate reviews it before merging.
3. `main` deploys to the live site automatically, so keep it working.

## Status

| Part | Status |
| --- | --- |
| Setup and deployment | Done (needs Render + Neon accounts connected) |
| Content admin | Done. Sample Python content in `fixtures/content.json` |
| Google login | Done |
| Diagnose | Done |
| Learning Plan | Done |
| Lessons and quiz | Done |
| AI helper | Not started |
| Reminder emails | Not started |
| Finish screen and Settings | Not started (M13) |

Update this table as parts are finished, and list known bugs below.

### Known issues

- Quizzes pass at 70%. With 2 or 3 items per topic that means every answer must be right (2 of 3 is 67%). Write 4 or more items for a topic if one mistake should still pass.
- Passing the quiz on the last topic doesn't lead anywhere special yet; the Finish screen is part of M13.
- Marking a topic done by hand records it as known (source "manual"), so a rebuilt plan skips it. Undo reverses that.

## Team

| Name | Role |
| --- | --- |
| Neil Biason | Critical path build, deployment |
| Christian | Frontend, integration, QA |
| Abdul Wahab | Testing, automation, handover, CompSAt contact |
| Eliza Casey | Content lead |

## License

MIT. See `LICENSE`.