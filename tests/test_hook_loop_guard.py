"""The Stop hook's loop guard actually arms.

`.claude/hooks/verify-gate.sh` is what stops an agent that thinks it is finished
when the gate says otherwise. It has a guard: after MAX_GATE_ATTEMPTS bounces it
writes an escalation and lets the agent stop, rather than bouncing forever.

That guard had never fired. The hook parsed its stdin with `json.loads`, and a
transcript containing a raw control character raises `JSONDecodeError: Invalid
control character at: line 1 column 567`. The `read` that followed then assigned
nothing, so `STOP_ACTIVE` was empty rather than "true", the MAX_GATE_ATTEMPTS
branch was unreachable, and a gate failure bounced indefinitely — burning tokens
until a human noticed. The same failed parse left the attempt counter on a fixed
path shared by every session, which is how a fresh run was told "Attempt 22 of
4". See decisions/001-gate-interpreter-path.md.

The bug survived because the only path that exercises it is the one nobody wants
to hit. This file hits it.

Every run happens inside a throwaway sandbox with its own agent.config.sh, its
own decisions directory and a stub notify.sh. That is not tidiness: the real
`agent/notify.sh` sends an SMS through the carrier when NOTIFY_TO and an API key
are in the environment, and the real DECISIONS_DIR is the escalation queue the
Stop hook itself reads — a test writing a `*.open.md` into it would silently
block every future session from stopping.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "verify-gate.sh"

MAX_ATTEMPTS = 2

# A raw \x01 and a raw newline inside a JSON string. json.dumps would escape
# both, so the payload is assembled by hand — an escaped \n is exactly what does
# NOT reproduce the bug.
CONTROL_CHARACTER_PAYLOAD = (
    '{{"session_id": "{session}", "stop_hook_active": {active}, '
    '"last_message": "gate output\x01with a control character\nand a raw newline"}}'
)


def _payload(session: str, active: bool) -> str:
    return CONTROL_CHARACTER_PAYLOAD.format(
        session=session, active="true" if active else "false")


def test_the_payload_this_file_uses_really_is_the_broken_kind():
    """The premise, asserted before anything leans on it.

    If a future edit escapes these characters the tests below would still pass
    while proving nothing about the failure they exist for.
    """
    raw = _payload("premise", True)
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert json.loads(raw, strict=False)["stop_hook_active"] is True


@pytest.fixture
def sandbox(tmp_path):
    """A fake repo root the hook can run against without touching this one."""
    root = tmp_path / "repo"
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "agent").mkdir()
    (root / "decisions").mkdir()

    shutil.copy(HOOK, root / ".claude" / "hooks" / "verify-gate.sh")

    # git init so `git rev-parse --show-toplevel` resolves to the sandbox rather
    # than to whatever repo TMPDIR might sit inside on someone else's machine.
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    (root / "agent.config.sh").write_text(
        'PROJECT_NAME="hook-smoke"\n'
        f'DECISIONS_DIR="{root}/decisions"\n'
        # A gate that always fails, cheaply. The hook eval's this.
        'GATE_CMD="printf \'scripted gate failure\\n\'; exit 1"\n'
        f'MAX_GATE_ATTEMPTS={MAX_ATTEMPTS}\n'
    )

    # Stands in for agent/notify.sh, which posts to the carrier's API for real
    # when a credential is in the environment.
    notify = root / "agent" / "notify.sh"
    notify.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$1" >> "$(dirname "$0")/notified.log"\n')
    notify.chmod(0o755)

    return root


def _run(sandbox, payload: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Counters land inside the sandbox and go away with it.
    env["TMPDIR"] = str(sandbox / "tmp")
    (sandbox / "tmp").mkdir(exist_ok=True)
    # Belt and braces on top of the stub notify.sh: no credential, nothing to
    # send with, even if a future edit reaches for the real script.
    for key in ("TELNYX_API_KEY", "NOTIFY_TO", "AGENT_NOTIFY_TO", "TELNYX_FROM"):
        env.pop(key, None)

    return subprocess.run(
        ["bash", str(sandbox / ".claude" / "hooks" / "verify-gate.sh")],
        input=payload, capture_output=True, text=True, cwd=sandbox, env=env,
    )


def test_a_control_character_no_longer_disarms_the_loop_guard(sandbox):
    """The whole bug, end to end.

    Under the old parse every one of these returns 2 forever: STOP_ACTIVE never
    becomes "true", so the escalation branch cannot be reached and the agent is
    told "you are not done" indefinitely.
    """
    session = "sess-control-char"

    first = _run(sandbox, _payload(session, active=True))
    assert first.returncode == 2, "a failing gate must block the stop"
    assert f"Attempt 1 of {MAX_ATTEMPTS}" in first.stderr, first.stderr

    second = _run(sandbox, _payload(session, active=True))
    assert second.returncode == 0, (
        "the loop guard did not arm: the hook is still bouncing the agent after "
        f"{MAX_ATTEMPTS} attempts instead of escalating"
    )

    escalations = list((sandbox / "decisions").glob("gate-stuck-*.open.md"))
    assert escalations, "the guard fired but wrote no escalation for a human"
    assert session in escalations[0].read_text(), (
        "the escalation names no session — the id was never parsed"
    )
    assert (sandbox / "agent" / "notified.log").exists(), "nobody was paged"


def test_a_passing_gate_ends_the_run_and_clears_the_counter(sandbox):
    """The ordinary case, and proof the counter file is removed on success."""
    (sandbox / "agent.config.sh").write_text(
        'PROJECT_NAME="hook-smoke"\n'
        f'DECISIONS_DIR="{sandbox}/decisions"\n'
        'GATE_CMD="true"\n'
        f'MAX_GATE_ATTEMPTS={MAX_ATTEMPTS}\n'
    )
    result = _run(sandbox, _payload("sess-green", active=True))
    assert result.returncode == 0
    assert not list((sandbox / "decisions").glob("*.open.md"))


def test_the_counter_is_scoped_to_a_session(sandbox):
    """A fresh session starts at attempt 1.

    The counter file used to be a single fixed path, because the session id
    never parsed. It survived across runs, and a brand-new session was told
    "Attempt 22 of 4" about failures it had not caused.
    """
    first = _run(sandbox, _payload("sess-one", active=True))
    assert "Attempt 1 of" in first.stderr

    other = _run(sandbox, _payload("sess-two", active=True))
    assert "Attempt 1 of" in other.stderr, (
        "a different session inherited another session's attempt count"
    )

    same_again = _run(sandbox, _payload("sess-one", active=True))
    assert same_again.returncode == 0, (
        "the same session's second failure did not reach the escalation branch, "
        "so its counter is not persisting either"
    )


def test_a_missing_session_id_is_treated_as_a_fresh_run(sandbox):
    """Rather than falling back to the shared path that caused the bug.

    The cost is that an unidentified run never accumulates a count. That is the
    right side to fail on: inheriting a stranger's count is what produced
    "Attempt 22 of 4", and a run nobody can identify is one nobody can attribute
    a bounce to either.
    """
    for _ in range(MAX_ATTEMPTS + 1):
        result = _run(sandbox, '{"stop_hook_active": true}')
        assert result.returncode == 2
        assert "Attempt 1 of" in result.stderr, result.stderr


def test_unparseable_stdin_does_not_take_the_hook_down(sandbox):
    """Whatever arrives, the hook still runs the gate and returns a verdict.

    Includes the shapes that are legal JSON but not the expected object — a bare
    list, a nested session_id — because `read` splitting an unexpected value into
    the wrong fields is how the original bug stayed silent.
    """
    for payload in ("", "not json at all", "[1, 2, 3]", '{"session_id": {"nested": 1}}',
                    '{"session_id": "has spaces in it", "stop_hook_active": true}',
                    '{"session_id": "../../escape", "stop_hook_active": true}'):
        result = _run(sandbox, payload)
        assert result.returncode == 2, f"hook died on {payload!r}: {result.stderr[-300:]}"
        assert "scripted gate failure" in result.stderr
        assert "Attempt 1 of" in result.stderr, (
            f"{payload!r} produced a usable session key it should have rejected"
        )


def test_the_hook_survives_a_parser_that_never_prints(sandbox):
    """python3 missing, or dying before its print, must not wedge the counter.

    Without the empty-value fallback, SESSION_ID is "" and ATTEMPT_FILE becomes a
    bare directory path — the counter write then fails and the guard is back to
    never arming, for a different reason.
    """
    stub_bin = sandbox / "stub-bin"
    stub_bin.mkdir()
    broken = stub_bin / "python3"
    broken.write_text("#!/usr/bin/env bash\nexit 1\n")
    broken.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    env["TMPDIR"] = str(sandbox / "tmp")
    (sandbox / "tmp").mkdir(exist_ok=True)
    for key in ("TELNYX_API_KEY", "NOTIFY_TO", "AGENT_NOTIFY_TO", "TELNYX_FROM"):
        env.pop(key, None)

    result = subprocess.run(
        ["bash", str(sandbox / ".claude" / "hooks" / "verify-gate.sh")],
        input=_payload("sess-no-python", active=True),
        capture_output=True, text=True, cwd=sandbox, env=env,
    )
    assert result.returncode == 2, result.stderr[-400:]
    assert "Attempt 1 of" in result.stderr, result.stderr[-400:]


def test_an_open_escalation_lets_the_agent_stop(sandbox):
    """A human is the blocker; bouncing the agent achieves nothing."""
    (sandbox / "decisions" / "003-something.open.md").write_text("# open question\n")
    result = _run(sandbox, _payload("sess-blocked", active=True))
    assert result.returncode == 0
    assert "scripted gate failure" not in result.stderr, (
        "the gate ran anyway, so an escalated session still burns a gate run"
    )
