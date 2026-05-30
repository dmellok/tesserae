# Tesserae — official Docker image.
#
# Base: Microsoft's Playwright Python image. It ships Chromium plus
# every X / fontconfig / NSS library Playwright's headless browser
# needs, pre-installed and at the right version for the bundled
# Playwright Python package. Installing that on a vanilla python:slim
# image is a 200-line apt-get litany that breaks every time Debian
# renames a package — the Playwright image is maintained by the
# people who own the version coupling, so it's the right base for a
# renderer that launches a browser to compose dashboards.
#
# Size: ~970 MB compressed to pull, ~2.5 GB on disk uncompressed.
# Most of that is Chromium and its sandboxes — worth it for a self-
# hosted appliance that needs to render real web pages.

# Pin the Playwright minor that matches our pyproject constraint
# (playwright>=1.42,<2). Bumping the image tag and the constraint
# together keeps Chromium and the Playwright Python client in lockstep.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

# Where Playwright looks for installed browsers. The base image puts
# Chromium here; setting it explicitly so any later `playwright install`
# call lands in the same spot rather than user-home (which we drop).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Flag the runtime so Settings → System hides the in-app self-update
# card and shows a `docker pull` hint instead. The updater would
# refuse here regardless (git pull inside a layered filesystem would
# lose the next image rebuild) — this just stops us advertising a
# button that doesn't apply.
ENV TESSERAE_IN_DOCKER=1

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy the whole source tree — Tesserae's loaders resolve plugins/,
# renderers/, devices/, templates/, and static/ from REPO_ROOT, so
# the install needs to leave the source tree in place rather than
# just copying app/ into site-packages.
COPY pyproject.toml /app/
COPY app/        /app/app/
COPY plugins/    /app/plugins/
COPY renderers/  /app/renderers/
COPY devices/    /app/devices/
COPY schema/     /app/schema/
COPY templates/  /app/templates/
COPY static/     /app/static/

# Editable install: ``pip install -e .`` is what install.sh + install.ps1
# do for the same reason — pyproject.toml's
# ``tool.setuptools.packages.find`` excludes data/ plugins/ renderers/
# devices/, so a regular install would land app/ in site-packages and
# REPO_ROOT (resolved from app_factory.__file__) would point at
# site-packages where none of those folders live. Editable keeps
# REPO_ROOT = /app so the loaders find their content.
RUN pip install -e /app

# Persistent state — settings.json, pages, schedules, events DB, render
# cache, gallery photos, backups. Tesserae's default data_root is
# REPO_ROOT/data, which inside the image is /app/data. Mounting a
# host path or named volume here is all docker-compose has to do.
VOLUME ["/app/data"]

# Run as the unprivileged ``pwuser`` (uid 1001) that the Playwright
# base image already provisions for Chromium. Reusing it avoids the
# gid/uid 1001 collision a fresh useradd would hit, and pwuser has
# the right `/ms-playwright` permissions Chromium needs at run time.
# Defence in depth, not a widget sandbox — widgets execute in the
# same Python process and can read anything pwuser can read (tracked
# separately as issue #3). This just stops a container escape from
# landing in root.
RUN mkdir -p /app/data && chown -R pwuser:pwuser /app
USER pwuser

# HTTP admin / renders endpoint. The embedded MQTT broker (if the
# user enables it via Settings → Server → MQTT) listens on 1883 —
# that port only matters if the operator publishes it from compose.
EXPOSE 8000

# `tesserae` is the console script declared in pyproject [project.scripts].
# Defaults to waitress on 0.0.0.0:8000.
CMD ["tesserae", "--host", "0.0.0.0", "--port", "8000"]
