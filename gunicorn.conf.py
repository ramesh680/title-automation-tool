# Loaded automatically by gunicorn from the working directory, so it applies
# even when Render's Start Command is plain `gunicorn app:app` (Render does
# not read the Procfile -- the logs showed the default sync worker).
#
# ONE worker: jobs live in an in-memory store that every poll must reach,
# and a second worker would double memory on the 512 MB instance.
# Threads: background jobs run in their own threads; these serve the page,
# uploads and the 1.5 s progress polls without blocking each other.
import os

bind = "0.0.0.0:" + os.getenv("PORT", "10000")
workers = 1
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "8"))
timeout = 120
graceful_timeout = 30
keepalive = 5
