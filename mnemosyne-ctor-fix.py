#!/opt/hermes/.venv/bin/python
"""Idempotent stop-gap for mnemosyne-hermes 0.7.3/0.7.4 under Hermes >= 0.22.

Those releases cannot construct MnemosyneMemoryProvider() when HERMES_HOME is
set: __init__ writes self._beam through _write_slot() before self._bindings
exists, which fails closed ("no binding for the current home"). Upstream fixed
it in mnemosyne-oss/mnemosyne PR #1155 (merged to main 2026-10-11), but no
release containing the fix existed on PyPI as of 2026-10-17 (tracked in
issues #1114 / #1178). The two replacements below are exactly that PR's
change to integrations/hermes/src/mnemosyne_hermes/__init__.py.

Safe to leave in place after upstream ships a fix: the patch is only applied
when constructing the provider actually fails, and it is reverted if the
patched file still does not construct. Always exits 0 so a problem here never
turns into a crash loop; failures are logged loudly instead.
"""
import glob
import os
import shutil
import subprocess
import sys

DATA = os.environ.get("HERMES_HOME", "/opt/data")

OLD_CTOR_HEAD = '    def __init__(self):\n        self._beam: Optional[Any] = None'
NEW_CTOR_HEAD = '    def __init__(self):\n        # P1b (2026-09-20 incident): ambient attributes replaced by a home-keyed\n        # bindings dict resolved AT CALL TIME from the turn\'s scope, with the\n        # last-initialized binding as out-of-turn fallback (cron/teardown keep\n        # legacy behavior exactly). _beam/_agent_identity/_session_id are\n        # properties over these slots; see _binding_slot below. The dict must\n        # exist before any property write in this constructor.\n        self._bindings: Dict[str, Dict[str, Any]] = {\n            "default": {"beam": None, "agent_identity": "", "session_id": "hermes_default"},\n        }\n        self._ambient_key = "default"\n        # Raw home string of the home currently being initialized, or None.\n        # Routes binding writes to the home under construction even when no\n        # turn scope exists; cleared in the finally of _initialize_locked so\n        # it can never outlive the init that set it.\n        self.__dict__["_init_home"]: Optional[str] = None\n        # Seed property-backed slots on the ambient binding without resolving\n        # turn-scoped HERMES_HOME — __init__ runs before initialize() exists.\n        _ambient = self._bindings[self._ambient_key]\n        _ambient["beam"] = None'
OLD_LATE_BLOCK = '        # P1b (2026-09-20 incident): ambient attributes replaced by a home-keyed\n        # bindings dict resolved AT CALL TIME from the turn\'s scope, with the\n        # last-initialized binding as out-of-turn fallback (cron/teardown keep\n        # legacy behavior exactly). _beam/_agent_identity/_session_id are\n        # properties over these slots; see _binding_slot below.\n        self._bindings: Dict[str, Dict[str, Any]] = {\n            "default": {"beam": None, "agent_identity": "", "session_id": "hermes_default"},\n        }\n        self._ambient_key = "default"\n        # Raw home string of the home currently being initialized, or None.\n        # Routes binding writes to the home under construction even when no\n        # turn scope exists; cleared in the finally of _initialize_locked so\n        # it can never outlive the init that set it.\n        self.__dict__["_init_home"]: Optional[str] = None\n'
NEW_LATE_BLOCK = ""

PROBE = (
    "import sys; sys.path.insert(0, '/opt/hermes'); "
    "import mnemosyne_hermes as m; m.MnemosyneMemoryProvider(); print('CTOR_OK')"
)


def log(msg):
    print("[mnemosyne-ctor-fix] " + msg, flush=True)


def constructs(venv_python):
    try:
        r = subprocess.run(
            [venv_python, "-c", PROBE],
            capture_output=True, text=True, timeout=180,
            env=dict(os.environ, HERMES_HOME=DATA),
        )
    except Exception as exc:  # noqa: BLE001
        log("probe could not run: %r" % (exc,))
        return False
    return r.returncode == 0 and "CTOR_OK" in r.stdout


def main():
    pattern = os.path.join(
        DATA, "installs", "*", "environments", "*", "venv", "lib", "python*",
        "site-packages", "mnemosyne_hermes", "__init__.py",
    )
    targets = sorted(glob.glob(pattern))
    if not targets:
        log("no mnemosyne_hermes install found under %s/installs - nothing to do" % DATA)
        return
    for target in targets:
        venv_python = target.split("/lib/python")[0] + "/bin/python"
        if constructs(venv_python):
            log("provider constructs fine (%s) - no patch needed" % target)
            continue
        with open(target, encoding="utf-8") as fh:
            src = fh.read()
        if src.count(OLD_CTOR_HEAD) != 1 or src.count(OLD_LATE_BLOCK) != 1:
            log("ERROR: constructor fails but the file does not match the known "
                "0.7.3/0.7.4 layout - NOT patching %s. Mnemosyne memory will be "
                "unavailable until mnemosyne-hermes is fixed upstream." % target)
            continue
        backup = target + ".orig-prepatch"
        if not os.path.exists(backup):
            shutil.copy2(target, backup)
        patched = src.replace(OLD_CTOR_HEAD, NEW_CTOR_HEAD).replace(OLD_LATE_BLOCK, NEW_LATE_BLOCK)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(patched)
        if constructs(venv_python):
            log("applied upstream PR #1155 constructor fix to %s" % target)
        else:
            shutil.copy2(backup, target)
            log("ERROR: patched file still does not construct - restored original %s. "
                "Mnemosyne memory will be unavailable." % target)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log("ERROR: unexpected failure: %r" % (exc,))
    sys.exit(0)
