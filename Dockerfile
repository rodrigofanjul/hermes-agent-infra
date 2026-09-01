# Custom hermes-agent image with the Mnemosyne external memory provider
# baked in at a pinned version. See docs/superpowers/specs/2026-08-31-
# mnemosyne-memory-provider-design.md for why this exists and what it
# does NOT do (the actual plugin wiring happens at container startup,
# in mnemosyne-bootstrap.sh — this Dockerfile only installs the package).
#
# Upgrading hermes-agent: bump the FROM tag here AND in the comment
# below, in the same commit. This image must be rebuilt (docker compose
# build), not pulled, after any change to this file or to the FROM tag.
FROM nousresearch/hermes-agent:v2026.8.31

RUN uv pip install --python /opt/hermes/.venv/bin/python mnemosyne-hermes==0.5.0

COPY mnemosyne-bootstrap.sh /usr/local/bin/mnemosyne-bootstrap.sh
RUN chmod +x /usr/local/bin/mnemosyne-bootstrap.sh
