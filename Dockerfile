# Custom hermes-agent image with the Mnemosyne external memory provider
# baked in at a pinned version. See docs/superpowers/specs/2026-08-31-
# mnemosyne-memory-provider-design.md for why this exists and what it
# does NOT do (the actual plugin wiring happens at container startup,
# in mnemosyne-bootstrap.sh — this Dockerfile only installs the package).
#
# Upgrading hermes-agent: bump the FROM tag below. This image must be
# rebuilt (docker compose build), not pulled, after any change to this
# file or to the FROM tag.
#
# Either kind of change here (FROM tag OR the mnemosyne-hermes version
# below) requires recreating the hermes-agent-src volume after deploy —
# it's only populated from the image on first `up`, and this image's
# /opt/hermes (including the venv this RUN installs into) lives inside
# that volume. See README section 11 for the full procedure; skipping
# it is exactly what crash-looped the first Mnemosyne deploy.
FROM nousresearch/hermes-agent:v2026.8.31

RUN uv pip install --python /opt/hermes/.venv/bin/python mnemosyne-hermes==0.5.0

COPY mnemosyne-bootstrap.sh /usr/local/bin/mnemosyne-bootstrap.sh
RUN chmod +x /usr/local/bin/mnemosyne-bootstrap.sh

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
RUN uv pip install --python /opt/hermes/.venv/bin/python playwright==1.62.0 beautifulsoup4==4.15.0
RUN /opt/hermes/.venv/bin/playwright install --with-deps chromium
