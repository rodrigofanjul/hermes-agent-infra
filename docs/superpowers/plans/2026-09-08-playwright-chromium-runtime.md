# Playwright + Chromium Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Playwright (with the Chromium browser) and `beautifulsoup4` to the custom `hermes` image, so future dedicated scripts (starting with the Banco Galicia sync) can drive a real browser and parse HTML — validated empirically before touching the live compose, given two prior production incidents with this same Dockerfile.

**Architecture:** Extend the existing `Dockerfile` (already used for Mnemosyne) with two more `RUN` steps: install `playwright` + `beautifulsoup4` via `uv pip`, then `playwright install --with-deps chromium`. Bump `shm_size` for the `hermes` service in `docker-compose.yml` to avoid Chromium crashes on the default 64MB `/dev/shm`. Validate with a standalone, isolated build+run on the server (same pattern as the Mnemosyne plan's Task 3) before wiring anything into the live compose.

**Tech Stack:** Docker, `uv` (Python package manager already used in this image), Playwright (Python), Chromium, Debian 13 (trixie, the base image's OS).

**Reference:** Design doc at `docs/superpowers/specs/2026-09-08-playwright-chromium-runtime-design.md` — read it first for full rationale (why hermes's own "browser" toolset can't be used, what was confirmed about the base image's user/apt-get availability).

---

### Task 1: Add Playwright, beautifulsoup4, and Chromium to the Dockerfile

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\Dockerfile`

- [ ] **Step 1: Read the current file to find the exact insertion point**

```bash
cd E:\Repositorios\hermes-agent-infra
cat Dockerfile
```

Expected: the file currently ends with these lines (after the `mnemosyne-hermes` install and the `mnemosyne-bootstrap.sh` COPY/chmod):

```dockerfile
RUN uv pip install --python /opt/hermes/.venv/bin/python mnemosyne-hermes==0.5.0

COPY mnemosyne-bootstrap.sh /usr/local/bin/mnemosyne-bootstrap.sh
RUN chmod +x /usr/local/bin/mnemosyne-bootstrap.sh
```

- [ ] **Step 2: Append the new RUN steps at the end of the file**

Add this to the end of `Dockerfile`:

```dockerfile

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
RUN uv pip install --python /opt/hermes/.venv/bin/python playwright==1.62.0 beautifulsoup4
RUN /opt/hermes/.venv/bin/playwright install --with-deps chromium
```

- [ ] **Step 3: Verify the full file reads correctly**

```bash
cat Dockerfile
```

Expected: the two new `RUN` lines appear after the Mnemosyne block, with the comment above them, and nothing else in the file changed.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "Add Playwright, Chromium, and beautifulsoup4 to the hermes image"
```

---

### Task 2: Increase shm_size for the hermes service

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\docker-compose.yml`

- [ ] **Step 1: Find the `hermes` service block**

```bash
grep -n "shm_size\|container_name: hermes$" docker-compose.yml
```

Expected: `container_name: hermes` is found; `shm_size` is not found anywhere in the file yet (this is the first time it's being added).

- [ ] **Step 2: Add `shm_size` right after `container_name: hermes`**

Find this line in the `hermes` service block:

```yaml
    container_name: hermes
```

Add `shm_size: "1gb"` immediately after it, so it reads:

```yaml
    container_name: hermes
    shm_size: "1gb"
```

- [ ] **Step 3: Verify the change with a diff**

```bash
git diff docker-compose.yml
```

Expected: exactly one line added (`shm_size: "1gb"`), nothing else changed. Confirm indentation matches the surrounding lines (4 spaces, same as `container_name:`).

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "Increase shm_size for hermes service to support Chromium"
```

---

### Task 3: Validate the build and Chromium launch in isolation on the server

This is the highest-risk step (same reasoning as the Mnemosyne plan's Task 3): confirm the image actually builds and Chromium actually launches, in a throwaway location on the server, before this touches the real `hermes-agent-infra` deployment.

**Files:** none (server-side, throwaway build directory)

- [ ] **Step 1: Copy the current Dockerfile and mnemosyne-bootstrap.sh to a scratch directory on Server B**

```bash
ssh opc@oracle-us-west "mkdir -p /tmp/playwright-validate"
scp E:\Repositorios\hermes-agent-infra\Dockerfile opc@oracle-us-west:/tmp/playwright-validate/Dockerfile
scp E:\Repositorios\hermes-agent-infra\mnemosyne-bootstrap.sh opc@oracle-us-west:/tmp/playwright-validate/mnemosyne-bootstrap.sh
```

- [ ] **Step 2: Write a minimal test script that exercises both Playwright and beautifulsoup4**

```bash
ssh opc@oracle-us-west "cat > /tmp/playwright-validate/test_browser.py <<'EOF'
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

with sync_playwright() as p:
    browser = p.chromium.launch(args=[\"--no-sandbox\"])
    page = browser.new_page()
    page.set_content(\"<html><body><h1>hello</h1></body></html>\")
    html = page.content()
    browser.close()

soup = BeautifulSoup(html, \"html.parser\")
h1 = soup.find(\"h1\")
assert h1 is not None and h1.text == \"hello\", \"BeautifulSoup did not parse the rendered page correctly\"
print(\"CHROMIUM_AND_BS4_OK\")
EOF"
```

- [ ] **Step 3: Build the image standalone**

```bash
ssh opc@oracle-us-west "cd /tmp/playwright-validate && sudo docker build -t playwright-validate:latest . 2>&1 | tail -80"
```

Expected: build completes with exit code 0. If `playwright install --with-deps chromium` fails here (e.g. an `apt-get` package name mismatch for Debian 13), STOP — do not proceed to Task 4. Report the exact error; it means the Playwright version needs re-checking against what it supports on this specific Debian release.

- [ ] **Step 4: Copy the test script into a throwaway container and run it**

```bash
ssh opc@oracle-us-west "sudo docker run --rm -v /tmp/playwright-validate/test_browser.py:/tmp/test_browser.py playwright-validate:latest /opt/hermes/.venv/bin/python /tmp/test_browser.py"
```

Expected: the last line of output is `CHROMIUM_AND_BS4_OK`. If it fails with a Chromium launch error (e.g. missing shared library), the `--with-deps` step in the Dockerfile didn't install everything Chromium needs on this specific Debian version — report the exact error rather than guessing at a fix.

- [ ] **Step 5: Clean up the scratch image and directory**

```bash
ssh opc@oracle-us-west "sudo docker rmi playwright-validate:latest; rm -rf /tmp/playwright-validate"
```

- [ ] **Step 6: Decision point**

If Step 4 printed `CHROMIUM_AND_BS4_OK`, proceed to Task 4. If it failed, STOP and report back with the exact error — do not guess at Dockerfile fixes without seeing the failure.

---

### Task 4: Deploy and verify

**Files:** none (deployment + verification only)

- [ ] **Step 1: Push to trigger the Coolify webhook**

```bash
cd E:\Repositorios\hermes-agent-infra
git push
```

- [ ] **Step 2: Poll the deployment queue until it finishes**

```bash
ssh root@oracle-us-east "for i in \$(seq 1 30); do st=\$(docker exec coolify-db psql -U coolify -d coolify -tAc \"SELECT status FROM application_deployment_queues WHERE application_id = '16' ORDER BY created_at DESC LIMIT 1;\"); echo \$st; if [ \"\$st\" != 'in_progress' ] && [ \"\$st\" != 'queued' ]; then break; fi; sleep 5; done"
```

Expected: ends with `finished`.

- [ ] **Step 3: Find the new container and check its health**

```bash
ssh opc@oracle-us-west "sudo docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep '^hermes-e'"
```

Expected: one line, status eventually reaching `Up ... (healthy)` (poll every ~5s for up to a minute if it still says `(health: starting)`). If it's `Restarting`, this is a NEW failure mode not seen in Task 3's isolated test (since that test used a throwaway container without the real compose environment, volumes, or `mnemosyne-bootstrap.sh` as the actual command) — check logs with `sudo docker logs <container-name> --tail 80` and report back rather than guessing.

**Important — recreate `hermes-agent-src` if the container is unhealthy or if this is being deployed after any prior deploy that didn't already rebuild the image from scratch:** this Dockerfile change adds new content to `/opt/hermes/.venv` (site-packages) and to wherever `playwright install` puts its browser binaries — both inside the volume-backed `/opt/hermes` directory. If `hermes-agent-src` already existed from a previous deploy, Docker will NOT repopulate it from this new image, and Playwright/Chromium will be invisible to any script that runs. If Step 3 shows the container unhealthy, or if you want to be certain the new packages are actually present regardless of health status, do this before further debugging:

```bash
ssh opc@oracle-us-west "sudo docker ps -a --format '{{.Names}}' | grep -E '^hermes-e|^hermes-webui'"
```

Then for each of the two names found:

```bash
ssh opc@oracle-us-west "sudo docker stop <hermes-container-name> <hermes-webui-container-name>"
ssh opc@oracle-us-west "sudo docker rm <hermes-container-name> <hermes-webui-container-name>"
ssh opc@oracle-us-west "sudo docker volume ls | grep hermes-agent-src"
```

(Confirm the exact volume name from that last command's output — it has a UUID prefix, e.g. `e3eeyshcsvecewtkagam3np2_hermes-agent-src` — then:)

```bash
ssh opc@oracle-us-west "sudo docker volume rm <exact-volume-name>"
cd E:\Repositorios\hermes-agent-infra
git commit --allow-empty -m "Trigger redeploy after recreating hermes-agent-src volume for Playwright"
git push
```

Then repeat Steps 2 and 3 of this task.

- [ ] **Step 4: Confirm Playwright and Chromium are actually present in the running container**

```bash
ssh opc@oracle-us-west "sudo docker exec <container-name> /opt/hermes/.venv/bin/python -c \"from playwright.sync_api import sync_playwright; from bs4 import BeautifulSoup; print('IMPORTS_OK')\""
```

(Replace `<container-name>` with the current name from Step 3's output.)
Expected: `IMPORTS_OK`.

- [ ] **Step 5: Confirm Chromium actually launches inside the real running container**

```bash
ssh opc@oracle-us-west "sudo docker exec <container-name> /opt/hermes/.venv/bin/python -c \"
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(args=['--no-sandbox'])
    page = b.new_page()
    page.goto('about:blank')
    print('LAUNCH_OK title=' + page.title())
    b.close()
\""
```

Expected: `LAUNCH_OK title=`. If this fails while Task 3's standalone test passed, the difference is the real container's environment (volumes, non-default user context from `mnemosyne-bootstrap.sh`, or resource limits) — report the exact error rather than guessing.

- [ ] **Step 6: Confirm the existing cron jobs and Mnemosyne provider are unaffected**

```bash
ssh opc@oracle-us-west "sudo docker exec <container-name> /opt/hermes/.venv/bin/hermes memory status"
```

Expected: still shows `mnemosyne (local) ← active` (this change should not have touched anything Mnemosyne-related, but confirm rather than assume, given this Dockerfile has already had two unrelated production incidents in this project).

```bash
ssh opc@oracle-us-west "sudo docker exec <container-name> python3 -c \"
import json
d = json.load(open('/opt/data/cron/jobs.json'))
for j in d['jobs']:
    print(j['name'], '-', j.get('last_status'))
\""
```

Expected: the three existing jobs (`Resumen diario de gastos`, `Recordatorios diarios - pagos y actividades`, `Carga cierres de tarjeta en Sheet`) are still listed.

---

## Rollback

If Task 4 fails and cannot be fixed quickly:

```bash
cd E:\Repositorios\hermes-agent-infra
git revert HEAD~2..HEAD
git push
```

(Adjust the range if extra recreate-volume commits were made in between — revert back to the commit before Task 1's Dockerfile change.) This restores the Dockerfile and compose to their pre-Playwright state. Recreate `hermes-agent-src` again after this revert, same procedure as Task 4 Step 3, since the image content is changing again. `hermes-data` (config, memory, cron) is untouched throughout.
