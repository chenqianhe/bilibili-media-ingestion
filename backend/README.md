# FastAPI Project - Backend

## Requirements

* [Docker](https://www.docker.com/).
* [uv](https://docs.astral.sh/uv/) for Python package and environment management.

## Docker Compose

Start the local development environment with Docker Compose following the guide in [../development.md](../development.md).

## General Workflow

By default, the dependencies are managed with [uv](https://docs.astral.sh/uv/), go there and install it.

From `./backend/` you can install all the dependencies with:

```console
$ uv sync
```

Then you can activate the virtual environment with:

```console
$ source .venv/bin/activate
```

Make sure your editor is using the correct Python virtual environment, with the interpreter at `backend/.venv/bin/python`.

Modify or add SQLModel models for data and SQL tables in `./backend/app/models.py`, API endpoints in `./backend/app/api/`, CRUD (Create, Read, Update, Delete) utils in `./backend/app/crud.py`.

## VS Code

There are already configurations in place to run the backend through the VS Code debugger, so that you can use breakpoints, pause and explore variables, etc.

The setup is also already configured so you can run the tests through the VS Code Python tests tab.

## Docker Compose Override

During development, you can change Docker Compose settings that will only affect the local development environment in the file `compose.override.yml`.

The changes to that file only affect the local development environment, not the production environment. So, you can add "temporary" changes that help the development workflow.

For example, the directory with the backend code is synchronized in the Docker container, copying the code you change live to the directory inside the container. That allows you to test your changes right away, without having to build the Docker image again. It should only be done during development, for production, you should build the Docker image with a recent version of the backend code. But during development, it allows you to iterate very fast.

There is also a command override that runs `fastapi run --reload` instead of the default `fastapi run`. It starts a single server process (instead of multiple, as would be for production) and reloads the process whenever the code changes. Have in mind that if you have a syntax error and save the Python file, it will break and exit, and the container will stop. After that, you can restart the container by fixing the error and running again:

```console
$ docker compose watch
```

There is also a commented out `command` override, you can uncomment it and comment the default one. It makes the backend container run a process that does "nothing", but keeps the container alive. That allows you to get inside your running container and execute commands inside, for example a Python interpreter to test installed dependencies, or start the development server that reloads when it detects changes.

To get inside the container with a `bash` session you can start the stack with:

```console
$ docker compose watch
```

and then in another terminal, `exec` inside the running container:

```console
$ docker compose exec backend bash
```

You should see an output like:

```console
root@7f2607af31c3:/app#
```

that means that you are in a `bash` session inside your container, as a `root` user, under the `/app` directory, this directory has another directory called "app" inside, that's where your code lives inside the container: `/app/app`.

There you can use the `fastapi run --reload` command to run the debug live reloading server.

```console
$ fastapi run --reload app/main.py
```

...it will look like:

```console
root@7f2607af31c3:/app# fastapi run --reload app/main.py
```

and then hit enter. That runs the live reloading server that auto reloads when it detects code changes.

Nevertheless, if it doesn't detect a change but a syntax error, it will just stop with an error. But as the container is still alive and you are in a Bash session, you can quickly restart it after fixing the error, running the same command ("up arrow" and "Enter").

...this previous detail is what makes it useful to have the container alive doing nothing and then, in a Bash session, make it run the live reload server.

## Backend tests

To test the backend run:

```console
$ bash ./scripts/test.sh
```

The tests run with Pytest, modify and add tests to `./backend/tests/`.

If you use GitHub Actions the tests will run automatically.

### Test running stack

If your stack is already up and you just want to run the tests, you can use:

```bash
docker compose exec backend bash scripts/tests-start.sh
```

That `/app/scripts/tests-start.sh` script just calls `pytest` after making sure that the rest of the stack is running. If you need to pass extra arguments to `pytest`, you can pass them to that command and they will be forwarded.

For example, to stop on first error:

```bash
docker compose exec backend bash scripts/tests-start.sh -x
```

### Host-based ingestion pytest

When you run `uv run pytest ...` directly from `./backend` on the host, the
ingestion-targeted tests now load the tracked root `.env.test` defaults
automatically. Those defaults point at a dedicated Postgres listener on
`localhost:55432`, while the gitignored root `.env.local` can continue to hold
optional live S3 credentials for `test_live_object_storage_smoke.py`.

Bring up the dedicated local test database with:

```bash
docker compose --profile test up -d test-db
```

Then run the host-based ingestion or smoke suites without exporting temporary
`POSTGRES_*` overrides:

```bash
cd backend
uv run pytest tests/services/test_live_object_storage_smoke.py -q
```

If you need local-only test DB overrides, create a root `.env.test.local`.
Explicit shell environment variables still win, so containerized
`docker compose exec backend ...` test runs continue to use the backend
container's `db:5432` settings. Host-based suites that share the dedicated
`localhost:55432` database should be run serially, because the current fixtures
clear the same tables between runs.

### Production environment checklist

The backend settings surface is defined in
`backend/app/core/config.py`. For a real deployment, treat these variables in
three groups:

- Required in every non-local environment:
  - `ENVIRONMENT=production`
  - `PROJECT_NAME`
  - `FRONTEND_HOST`
  - `BACKEND_PUBLIC_URL`
  - `SECRET_KEY`
  - `MEDIA_SIGNING_SECRET`
  - `FIRST_SUPERUSER`
  - `FIRST_SUPERUSER_PASSWORD`
  - `POSTGRES_SERVER`
  - `POSTGRES_PORT`
  - `POSTGRES_DB`
  - `POSTGRES_USER`
  - `POSTGRES_PASSWORD`
- Required for the full media pipeline:
  - `S3_ENDPOINT_URL`
  - `S3_ACCESS_KEY`
  - `S3_SECRET_KEY`
  - `S3_BUCKET`
  - optional `S3_REGION`
  - optional `S3_FORCE_PATH_STYLE`
- Optional depending on how you operate the system:
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_TLS`, `SMTP_SSL`, `SMTP_USER`,
    `SMTP_PASSWORD`, `EMAILS_FROM_EMAIL`, `EMAILS_FROM_NAME`
  - `SENTRY_DSN`
  - `BILIBILI_COOKIE_HEADER`
  - `YT_DLP_COOKIES_FILE`
  - `YT_DLP_COOKIES_FROM_BROWSER`
  - `YT_DLP_USER_AGENT`
  - `YT_DLP_IMPERSONATE`

The tracked root `.env` file is now organized around those groups and should be
treated as a template/defaults file rather than a place to commit real
production secrets.

Variables such as the Bilibili request throttling knobs, worker poll/stale
timeouts, and binary paths (`YT_DLP_BINARY`, `FFMPEG_BINARY`, `FFPROBE_BINARY`)
already have code defaults and normally do not need to be overridden unless
operations tuning is required.

Important deployment caveat: the current `compose.yml` still hardcodes
`POSTGRES_SERVER=db` for the backend, prestart job, and workers so local
containerized development keeps talking to the bundled Postgres service. If you
want to run the Compose stack against an external PostgreSQL instance, adjust
that wiring before treating the default Compose file as production-ready.

### Authenticated Bilibili access

Most metadata and source-download flows work against publicly accessible Bilibili
videos without extra credentials. For videos that need an authenticated session,
the ingestion stack now supports separate metadata and download credentials:

- Database-backed Netscape/Mozilla `cookies.txt`: saved from the admin console.
  Metadata crawlers derive a raw `Cookie` header from it, while `yt-dlp` reads
  the stored cookie file directly.
- Database-backed download user-agent: optional but recommended companion value
  for `yt-dlp`.
- `BILIBILI_COOKIE_HEADER`: legacy raw `Cookie` header fallback for metadata
  requests when no database cookies are stored.
- `YT_DLP_COOKIES_FILE`: path to a Netscape-format cookies file for
  `yt-dlp --cookies`.
- `YT_DLP_COOKIES_FROM_BROWSER`: value passed through to
  `yt-dlp --cookies-from-browser` such as `chrome`, `firefox`, or
  `chrome:Default`.
- `YT_DLP_USER_AGENT`: fallback user-agent string for `yt-dlp` when no
  database user-agent is stored.
- `YT_DLP_IMPERSONATE`: browser impersonation target passed through to
  `yt-dlp --impersonate`. `chrome` is the recommended default for Bilibili.

If both `YT_DLP_COOKIES_FILE` and `YT_DLP_COOKIES_FROM_BROWSER` are set, the
cookie file takes precedence.

The Docker image now installs a `yt-dlp` nightly build with `curl_cffi`, so the
containerized `download-worker` can use impersonation-capable requests out of
the box.

For the local compose stack, `compose.override.yml` mounts
`./backend/.secrets` into the `download-worker` container at
`/app/backend/.secrets`, so a common setup is:

```bash
# root .env.local
YT_DLP_COOKIES_FILE=/app/backend/.secrets/bilibili-cookies.txt
YT_DLP_USER_AGENT=Mozilla/5.0 (...)
YT_DLP_IMPERSONATE=chrome
```

Put the exported cookie file itself at
`backend/.secrets/bilibili-cookies.txt`. Browser cookie sync is mainly useful
for host-based worker runs like `uv run python -m app.workers.download_ingest`,
because the containerized `download-worker` does not have access to your host
browser profile unless you mount it yourself.

Superusers can also review the current status and save Bilibili Netscape
cookies plus an optional download user-agent from the frontend admin console.
The database-backed values take precedence over `BILIBILI_COOKIE_HEADER` and
the `YT_DLP_*` fallbacks from `.env.local`, and workers pick up the latest
saved value when they claim the next job.

`BILIBILI_COOKIE_HEADER` is now only a metadata fallback for comments,
danmaku, subtitles, and metadata crawls. Source downloads use the stored
Netscape cookies or `YT_DLP_*` fallbacks together with the configured
user-agent and impersonation settings. Requested `fetch_comments`,
`fetch_danmaku`, and `fetch_subtitles` work through the metadata worker and
persist into PostgreSQL; `fetch_subtitles` is enabled by default again. When
`create_hls=true`, media processing now also emits a `proxy_mp4`, HLS master
manifest, media playlist, and HLS segment assets instead of failing the job.

### Bilibili ingestion coverage

The current ingestion stack is intentionally split into a media pipeline and an
auxiliary-data pipeline:

- Video metadata prefers `/x/web-interface/wbi/view` and falls back to the
  legacy `/x/web-interface/view` endpoint when needed.
- Tags are fetched from `/x/tag/archive/tags`.
- Comments prefer `/x/v2/reply/wbi/main`, fall back to `/x/v2/reply`, and use
  `/x/v2/reply/reply` for child replies.
- Comment parsing now collects `top.admin`, `top.upper`, `top_replies`, `hots`,
  regular `replies`, nested replies, and fetched child replies into one flat
  persisted comment set. Persisted comment rows now use an internal UUID key,
  while the Bilibili reply identity is tracked with a `(bvid, rpid)` unique
  constraint.
- Comment images under `content.pictures` are normalized into
  `video_comment_images`; those rows now link to comments by `comment_id`, and
  when object storage is configured they are also uploaded as `comment_image`
  `MediaAsset` rows.
- Danmaku still comes from `https://comment.bilibili.com/{cid}.xml`, which means
  the current implementation captures the XML pool, not a full historical
  danmaku archive.
- Subtitles still come from `/x/player/v2` plus the referenced subtitle JSON, so
  only tracks exposed by the player are persisted.
- Source video downloads still run through `yt-dlp`, with upload and media
  processing handled by the downstream workers.

### Bilibili anti-abuse controls

The shared Bilibili HTTP client now applies a small set of configurable
stability controls that are used by metadata, comments, and comment-image
downloads:

- Browser-like `User-Agent`, `Accept-Language`, and video `Referer` headers.
- WBI signing with cached image/sub keys and automatic refresh when Bilibili
  rejects a signature.
- Host-scoped minimum request interval via
  `BILIBILI_REQUEST_MIN_INTERVAL_SECONDS`.
- Additional random jitter via `BILIBILI_REQUEST_JITTER_SECONDS`.
- WBI key cache TTL via `BILIBILI_WBI_KEY_CACHE_TTL_SECONDS`.
- Empty-page retry for WBI comment pagination via
  `BILIBILI_COMMENT_EMPTY_PAGE_RETRY_ATTEMPTS`.
- Metadata-worker inter-job delay via
  `METADATA_WORKER_INTER_JOB_DELAY_SECONDS` to space out consecutively queued
  video ingests.

These controls improve stability, but they do not turn the crawler into a
guaranteed full mirror of all Bilibili data.

### Read APIs for ingested media and auxiliary data

The authenticated backend now exposes the main ingestion read surface through
these routes:

- `GET /api/v1/ingest/jobs` lists recent ingest jobs visible to the current
  user; superusers can also filter by requester.
- `GET /api/v1/videos/` lists cataloged videos, and `GET /api/v1/videos/{bvid}`
  returns detail metadata for one video.
- `GET /api/v1/videos/{bvid}/assets` returns all stored `MediaAsset` rows for a
  video. Use `?asset_type=comment_image` to filter to comment-image assets only.
- `GET /api/v1/videos/{bvid}/comments` returns persisted comments ordered by
  newest comment time first, with `limit` and `offset` pagination. Each comment
  includes normalized image entries, storage status, and a nested asset summary
  when the image has a backing `MediaAsset`.
- `GET /api/v1/videos/{bvid}/danmaku` returns persisted danmaku rows with
  optional `cid`, `source`, and `history_date` filters.
- `GET /api/v1/videos/{bvid}/subtitles` returns persisted subtitle rows with
  optional `cid` and `lang` filters.
- `GET /api/v1/media/assets/{asset_id}` returns a single media asset descriptor.
- `POST /api/v1/media/assets/{asset_id}/signed-url` and
  `GET /api/v1/media/assets/{asset_id}/download` provide the existing signed
  download flow.
- `POST /api/v1/media/assets/{asset_id}/playback-url` and
  `GET /api/v1/media/assets/{asset_id}/playback` provide app-proxied playback
  for direct media bytes plus HLS manifest rewriting.

Run `alembic upgrade head` after pulling backend schema changes. The repository
now keeps a single squashed baseline migration for the current pre-production
schema.

### Test Coverage

When the tests are run, a file `htmlcov/index.html` is generated, you can open it in your browser to see the coverage of the tests.

## Migrations

The historical Alembic chain has been intentionally squashed into a single
baseline migration:

- `backend/app/alembic/versions/20260420_01_initialize_current_schema.py`

This is acceptable because the project has not been connected to a production
database yet.

If you already have a local database created from the removed old revision
chain, the cleanest path is to drop and recreate that local database once and
then rerun:

```console
$ alembic upgrade head
```

As during local development your app directory is mounted as a volume inside the
container, you can also run Alembic commands inside the container and commit the
resulting migration file back to the repo.

* Start an interactive session in the backend container:

```console
$ docker compose exec backend bash
```

* Alembic is already configured to import your SQLModel models from `./backend/app/models.py`.

* If you still want to keep using Alembic revisions, after changing the schema,
  create a new revision inside the container, e.g.:

```console
$ alembic revision --autogenerate -m "Add column last_name to User model"
```

* Commit the resulting migration file(s) to git.

* After creating the revision, run the migration in the database (this is what will actually change the database):

```console
$ alembic upgrade head
```

If you do not want to use Alembic at all during this pre-production phase, you
can switch to direct table creation by uncommenting the lines in
`./backend/app/core/db.py` that end in:

```python
SQLModel.metadata.create_all(engine)
```

and comment the line in the file `scripts/prestart.sh` that contains:

```console
$ alembic upgrade head
```

If you choose to squash the schema again before production, generate a new
baseline against an empty local database rather than trying to preserve a long
pre-production revision history.

## Email Templates

The email templates are in `./backend/app/email-templates/`. Here, there are two directories: `build` and `src`. The `src` directory contains the source files that are used to build the final email templates. The `build` directory contains the final email templates that are used by the application.

Before continuing, ensure you have the [MJML extension](https://github.com/mjmlio/vscode-mjml) installed in your VS Code.

Once you have the MJML extension installed, you can create a new email template in the `src` directory. After creating the new email template and with the `.mjml` file open in your editor, open the command palette with `Ctrl+Shift+P` and search for `MJML: Export to HTML`. This will convert the `.mjml` file to a `.html` file and now you can save it in the build directory.
