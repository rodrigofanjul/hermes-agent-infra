# Custom hermes-agent image: Playwright for the bank-sync scripts, plus the
# glue that installs/repairs the Mnemosyne memory provider at container start.
# See docs/superpowers/specs/2026-08-31-mnemosyne-memory-provider-design.md for
# why Mnemosyne exists here and docs/superpowers/specs/2026-10-17-hermes-v0.22-upgrade.md
# for what changed in the v0.22 upgrade.
#
# Upgrading hermes-agent: bump the FROM tag below. This image must be
# rebuilt (docker compose build), not pulled, after any change to this
# file or to the FROM tag.
#
# Either kind of change here requires recreating the hermes-agent-src volume
# after deploy — it's only populated from the image on first `up`, and this
# image's /opt/hermes (including the venv and browsers this file installs
# into) lives inside that volume. See README section 11 for the full
# procedure.
#
# Since v0.22 (tags are SemVer, no longer vYYYY.M.D):
#   - the image ships its own uv (under /opt/hermes/tools/uv-*/uv, not on PATH),
#     its venv has no pip, and it runs Python 3.14;
#   - the base image sets PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/tools and bakes
#     its own Chromium there for hermes's built-in browser tool. Running
#     `playwright install` into that same directory DELETES that baked
#     Chromium (stage2 then warns "baked Chromium binary is missing"), so we
#     keep our own Chromium separate in /opt/hermes/.playwright and point
#     PLAYWRIGHT_BROWSERS_PATH there for the scripts (same layout as before
#     v0.22; the compose AGENT_BROWSER_EXECUTABLE_PATH relies on it);
#   - Mnemosyne is no longer pip-installed here. It is a catalog plugin that
#     mnemosyne-bootstrap.sh installs into the persistent hermes-data volume on
#     first boot (see that script), with mnemosyne-ctor-fix.py as a stop-gap
#     for an upstream constructor bug.
FROM nousresearch/hermes-agent:v0.22.2

# Playwright + Chromium: gives dedicated scripts (invoked via hermes's
# code_execution tool, e.g. the Banco Galicia sync) a real browser to
# drive. This is NOT wired into hermes's own "browser" toolset — that
# toolset only knows how to talk to paid cloud backends (Browserbase,
# Browser-Use Cloud, Firecrawl), confirmed via `hermes plugins list`.
# See docs/superpowers/specs/2026-09-08-playwright-chromium-runtime-design.md.
#
# beautifulsoup4 is installed alongside it because every script that
# needs a real browser also needs to parse the HTML it renders — the
# banking site this is built for has no clean JSON API for most pages.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
RUN UV="$(find /opt/hermes/tools -maxdepth 2 -name uv -type f | head -n1)" \
 && test -x "$UV" \
 && "$UV" pip install --python /opt/hermes/.venv/bin/python playwright==1.62.0 beautifulsoup4==4.15.0
RUN /opt/hermes/.venv/bin/playwright install --with-deps chromium

# hermes-webui (a different uid) stages /opt/hermes read-only via rsync at
# startup. The v0.22.2 base image ships tools/.install.lock as root:root 0600,
# which makes that rsync fail ("Permission denied") and webui exit(1) on every
# version tried (0.52.379 and 0.52.458). Make anything unreadable readable.
RUN find /opt/hermes -type f ! -perm -o+r -exec chmod a+r {} +

# Last, so editing these scripts doesn't invalidate the Playwright layers above.
COPY mnemosyne-bootstrap.sh /usr/local/bin/mnemosyne-bootstrap.sh
COPY mnemosyne-ctor-fix.py /usr/local/bin/mnemosyne-ctor-fix.py
RUN chmod +x /usr/local/bin/mnemosyne-bootstrap.sh /usr/local/bin/mnemosyne-ctor-fix.py
