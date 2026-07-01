# Tesserae, official Docker image.
#
# Base: Microsoft's Playwright Python image. It ships Chromium plus
# every X / fontconfig / NSS library Playwright's headless browser
# needs, pre-installed and at the right version for the bundled
# Playwright Python package. Installing that on a vanilla python:slim
# image is a 200-line apt-get litany that breaks every time Debian
# renames a package, the Playwright image is maintained by the
# people who own the version coupling, so it's the right base for a
# renderer that launches a browser to compose dashboards.
#
# Size: ~970 MB compressed to pull, ~2.5 GB on disk uncompressed.
# Most of that is Chromium and its sandboxes, worth it for a self-
# hosted appliance that needs to render real web pages.

# Pin the Playwright minor that matches our pyproject constraint
# (playwright>=1.60,<1.61). Bumping the image tag and the constraint
# together keeps Chromium and the Playwright Python client in lockstep,
# the bundled chromium revision (e.g. ``chromium_headless_shell-1223``
# for 1.60) is what the matching Python wheel goes looking for at
# launch. A mismatch boots the container fine but errors at first
# render with "Executable doesn't exist at /ms-playwright/...".
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

# Where Playwright looks for installed browsers. The base image puts
# Chromium here; setting it explicitly so any later `playwright install`
# call lands in the same spot rather than user-home (which we drop).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Flag the runtime so Settings → System hides the in-app self-update
# card and shows a `docker pull` hint instead. The updater would
# refuse here regardless (git pull inside a layered filesystem would
# lose the next image rebuild), this just stops us advertising a
# button that doesn't apply.
ENV TESSERAE_IN_DOCKER=1

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy the whole source tree, Tesserae's loaders resolve plugins/,
# renderers/, devices/, hardware/, templates/, and static/ from
# REPO_ROOT, so the install needs to leave the source tree in place
# rather than just copying app/ into site-packages.
COPY pyproject.toml /app/
COPY app/        /app/app/
COPY plugins/    /app/plugins/
COPY renderers/  /app/renderers/
COPY devices/    /app/devices/
COPY hardware/   /app/hardware/
COPY schema/     /app/schema/
COPY templates/  /app/templates/
COPY static/     /app/static/

# Editable install: ``pip install -e .`` is what install.sh + install.ps1
# do for the same reason, pyproject.toml's
# ``tool.setuptools.packages.find`` excludes data/ plugins/ renderers/
# devices/, so a regular install would land app/ in site-packages and
# REPO_ROOT (resolved from app_factory.__file__) would point at
# site-packages where none of those folders live. Editable keeps
# REPO_ROOT = /app so the loaders find their content.
RUN pip install -e /app

# ``gosu`` is what the entrypoint uses to drop privileges after fixing
# the bind-mount ownership. It's a single ~2 MB static binary; the
# obvious alternative ``su-exec`` only ships on Alpine. The base
# image's ``setpriv`` works too but lacks gosu's standard semantics.
#
# ``fonts-noto-color-emoji`` is the de-facto Linux colour-emoji font.
# Widgets that paint country flags (f1_next, sky_*) and any other
# Unicode emoji need this to render properly inside the headless
# Chromium that drives the composer, without it, flag emojis fall
# back to regional-indicator letter pairs in boxes. ~12 MB on top
# of the existing image, paid once for every emoji widget.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true

# Persistent state, settings.json, pages, schedules, events DB, render
# cache, gallery photos, backups. Tesserae's default data_root is
# REPO_ROOT/data, which inside the image is /app/data. Mounting a
# host path or named volume here is all docker-compose has to do.
VOLUME ["/app/data"]

# Entrypoint script starts as root, chowns /app/data to pwuser (uid
# 1001) so a host-side bind mount with the wrong UID still works, then
# re-execs the command under ``gosu pwuser`` so the actual Tesserae
# process runs unprivileged. Without this, ``docker compose up`` on a
# fresh host creates ./data as the host user (typically uid 1000) and
# Tesserae's first ``mkdir(data/plugins)`` EPERMs. Defence in depth,
# not a widget sandbox, widgets execute in the same Python process
# and can read anything pwuser can read (tracked separately as issue
# #3). This just stops a container escape from landing in root.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && \
    mkdir -p /app/data && \
    chown -R pwuser:pwuser /app

# NOTE: no ``USER pwuser`` here. The entrypoint runs as root just long
# enough to fix /app/data ownership, then drops to pwuser via gosu
# before exec'ing the CMD.

# HTTP admin / renders endpoint. The embedded MQTT broker (if the
# user enables it via Settings → Server → MQTT) listens on 1883 -
# that port only matters if the operator publishes it from compose.
EXPOSE 8765

ENTRYPOINT ["docker-entrypoint.sh"]
# `tesserae` is the console script declared in pyproject [project.scripts].
# Defaults to waitress on 0.0.0.0:8765.
CMD ["tesserae", "--host", "0.0.0.0", "--port", "8765"]
