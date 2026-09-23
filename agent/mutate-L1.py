"""Revert one session-L1 guard at a time and check that the suite notices.

Run by `agent/accept-L1.sh` as check 11. Each mutation is a plausible edit
inside the current API, applied to a scratch copy of the tree; at least one test
must go red for each, and the tests that do should be the tests that name it.

The six the spec names, then the guards the port added on the way:

  - `M1` the source writes a contact row itself — the reference `save_profile()`
    shape, and the layering breach the whole port exists to unwind.
  - `M2` the teardown runs on success and failure but not on cancellation —
    i.e. not on the deadline, which is the path the reference box leaked on.
  - `M3` and `M3b` let a second scrape start: `M3` removes the process lock,
    `M3b` the running-row check. Two guards, each proved alone; `M3c` drops
    `max_instances=1` from the schedule.
  - `M4` puts a behavioural field on `contacts`.
  - `M5` lets a blocklisted bidder through.
  - `M6` gives the daily trigger its own hardcoded zone; `M6b` does it at the
    registration in `main.py` instead.
  - `S1` reads the panel as soon as any marker is present — the stale-panel
    defect, bidder 2 with bidder 1's phone.
  - `S2` reads the phone from the whole page — the table's figure becomes the
    phoneless bidder's number.
  - `S3` removes the selector-drift failure: a changed page is a successful
    scrape of nobody again.
  - `S4` trusts the teardown's own word for whether it ran.
  - `S5` stops the teardown at the first failed step, so the driver outlives a
    context that would not close.
  - `S6` discards what was read when the deadline fires.
  - `S7` skips screening; `S8` lets an unanswered lookup through the gate.
  - `S9` removes the profile-directory guard.
  - `S10` stops telling a misread (one number, two names) from a repeat.
  - `S11`-`S18` revert what the fresh-context review found: the teardown's
    per-step bound, opening a row by name, early drift detection, the
    all-phoneless failure, `incomplete`, the cross-run profile conflict, the
    misfire grace, and the named remedy for an unwritable profile directory.

## Preconditions, before any patch

Every `.py`/`.html`/`.json` under `app/`, `tests/` and `alembic/` in the scratch
tree is compared byte for byte with the repo (`SCRATCH VERIFIED PRISTINE`), and
every anchor is resolved (`ANCHORS VERIFIED`). Either failing exits 2: a
harness on a dirty tree or with a stale anchor reports nothing true.

Nothing here drives a browser or reaches the network: the suite uses the fake
portal, and `conftest.py` blanks every credential.

    python3 agent/mutate-L1.py <scratch-tree> <python>
"""

import pathlib
import re
import subprocess
import sys

SCRATCH = pathlib.Path(sys.argv[1])
PY = sys.argv[2]
REPO = pathlib.Path(__file__).resolve().parent.parent

TARGETS = ["tests/test_bidder_source.py", "tests/test_bidder_scrape_runs.py",
           "tests/test_migrations.py"]

LA = "app/sources/liveauctioneers.py"
BASE = "app/sources/auction_scraper_base.py"
SVC = "app/services/bidder_scrape.py"
MAIN = "app/main.py"
CONTACT = "app/models/contact.py"

MUTATIONS = {
    "M1 the source writes a contact row itself, as the reference save_profile() did": [
        (LA, "                found.append(to_record(parse_profile(text, name, panel)))\n",
             "                found.append(to_record(parse_profile(text, name, panel)))\n"
             "                from app.core.database import SessionLocal\n"
             "                from app.models.contact import Contact\n"
             "                _db = SessionLocal()\n"
             "                _rec = found[-1]\n"
             "                if _rec.phone:\n"
             "                    from app.sms.phone import normalize\n"
             "                    _p = normalize(_rec.phone)\n"
             "                    if not _db.query(Contact).filter(Contact.phone == _p).first():\n"
             "                        _db.add(Contact(phone=_p, full_name=_rec.full_name,\n"
             "                                        source='liveauctioneers', attributes={}))\n"
             "                        _db.commit()\n"
             "                _db.close()\n")],
    "M2 the teardown is skipped on the deadline path — success and failure close "
    "the browser, cancellation does not": [
        (BASE, "            return self.records\n"
               "        except Exception:\n"
               "            await self._debug_screenshot(\"scrape-fail\")\n"
               "            raise\n"
               "        finally:\n"
               "            # Every way out, including cancellation by `run()`'s deadline.\n"
               "            await self.shutdown()\n",
               "            await self.shutdown()\n"
               "            return self.records\n"
               "        except Exception:\n"
               "            await self._debug_screenshot(\"scrape-fail\")\n"
               "            await self.shutdown()\n"
               "            raise\n")],
    "M3 the process lock is gone — a second run in the same process starts": [
        (SVC, "    if not _RUN_LOCK.acquire(blocking=False):\n",
              "    _RUN_LOCK.acquire(blocking=False)\n    if False:\n")],
    "M3b the running-row check is gone — a run in another process is invisible": [
        (SVC, "        if _running_elsewhere(db, platform, timeout):\n",
              "        if False:\n")],
    "M3c the schedule may start a second copy of itself": [
        (MAIN, "            id=bidder_scrape.JOB_ID, replace_existing=True, max_instances=1,\n",
               "            id=bidder_scrape.JOB_ID, replace_existing=True, max_instances=3,\n")],
    "M4 a behavioural field is put on contacts": [
        (CONTACT, "    last_messaged_at = Column(String(50), nullable=True)\n",
                  "    last_messaged_at = Column(String(50), nullable=True)\n"
                  "    items_won = Column(Integer, nullable=True)\n")],
    "M5 a blocklisted bidder is let through": [
        (SVC, "    kept = [r for r in kept if r.phone not in blocked]\n",
              "    kept = list(kept)\n")],
    "M6 the daily trigger keeps its own zone": [
        (SVC, "                       minute=settings.BIDDER_SCRAPE_MINUTE, timezone=clock.ZONE)\n",
              "                       minute=settings.BIDDER_SCRAPE_MINUTE,\n"
              "                       timezone=\"America/New_York\")\n")],
    "M6b main.py registers the job with its own hardcoded zone": [
        (MAIN, "            bidder_scrape.daily_job, bidder_scrape.trigger(),\n",
               "            bidder_scrape.daily_job,\n"
               "            CronTrigger(hour=9, minute=0, timezone=\"America/New_York\"),\n")],
    "S1 the panel is read as soon as any marker shows — the previous bidder's": [
        (LA, "            if text != before and any(m in text for m in PROFILE_MARKERS):\n",
             "            if any(m in text for m in PROFILE_MARKERS):\n")],
    "S2 the phone is read from the whole page, table included": [
        (LA, "    for line in panel:\n        match = _PHONE.search(line)\n",
             "    for line in lines:\n        match = _PHONE.search(line)\n")],
    "S3 a page that yielded nothing from its rows is a successful scrape of nobody": [
        (BASE, "            if self.rows_seen and not self.records:\n",
               "            if False:\n")],
    "S4 cleanup_ran trusts the teardown's own word": [
        (SVC, "        run.cleanup_ran = 0 if (source.browser_open()\n"
              "                                or source.shutdown_clean is False) else 1\n",
              "        run.cleanup_ran = 0 if source.shutdown_clean is False else 1\n")],
    "S5 the teardown stops at the first failed step; the driver outlives the context": [
        (BASE, "                clean = False\n",
               "                clean = False\n                self.shutdown_clean = clean\n"
               "                return\n")],
    "S6 the deadline discards what was already read": [
        (SVC, "    except asyncio.TimeoutError:\n        timed_out = True\n",
              "    except asyncio.TimeoutError:\n        timed_out = True\n"
              "        source.records = []\n")],
    "S7 screening is skipped even when it is on": [
        (SVC, "    if kept and screening_enabled():\n", "    if False:\n")],
    "S8 an unanswered lookup passes the gate": [
        (SVC, "        passed = [r for r in kept if outcome[\"results\"].get(r.phone)\n"
              "                  in lookup_service.PROMOTABLE_LINE_TYPES]\n",
              "        passed = [r for r in kept if outcome[\"results\"].get(r.phone)\n"
              "                  != \"landline\"]\n")],
    "S9 a profile directory inside the project is used anyway": [
        (SVC, "        if problem:\n            raise RuntimeError(problem)\n", "")],
    "S11 the teardown steps lose their bound — a close() that hangs holds the "
    "deadline open indefinitely": [
        (BASE, "                await asyncio.wait_for(getattr(target, method)(),\n"
               "                                       timeout=self.SHUTDOWN_STEP_SECONDS)\n",
               "                await getattr(target, method)()\n")],
    "S12 the row is opened by its name, so a second namesake is never read": [
        (LA, "                try:\n"
             "                    await (await row.query_selector_all(\"td\"))[3].click(timeout=CLICK_MS)\n"
             "                except Exception:                     # noqa: BLE001\n"
             "                    await self.page.get_by_text(name, exact=True).first.click(timeout=CLICK_MS)\n",
             "                await self.page.get_by_text(name, exact=True).first.click(timeout=CLICK_MS)\n")],
    "S13 a changed panel is only noticed after every row has missed": [
        (LA, "                            self._panel_misses >= EARLY_DRIFT_ROWS:\n",
             "                            self._panel_misses >= 10 ** 6:\n")],
    "S14 a list where nobody has a phone is a successful run creating nobody": [
        (BASE, "            if not any((r.phone or \"\").strip() for r in self.records):\n",
               "            if False:\n")],
    "S15 a run that stopped paging early reports completed": [
        (SVC, "            shortfall = _shortfall(run)\n", "            shortfall = None\n")],
    "S16 a shared number's profile is overwritten by whoever came first today": [
        (SVC, "        if (row is not None and row.platform_username and record.username\n",
              "        if (False and row is not None and row.platform_username and record.username\n")],
    "S17 a 09:00 firing missed by a stalled loop skips the day": [
        (MAIN, "            coalesce=True, misfire_grace_time=3600,\n",
               "            coalesce=True,\n")],
    "S18 an unwritable profile directory fails with a bare PermissionError": [
        (SVC, "        _check_writable(source.profile_dir())\n", "")],
    "S10 one number read for two names is filed as a repeat, not a conflict": [
        (SVC, "            if same:\n                run.repeats += 1\n",
              "            if True:\n                run.repeats += 1\n")],
}

# ── Preconditions ────────────────────────────────────────────────────────────
files = sorted({path for muts in MUTATIONS.values() for path, _, _ in muts})
SOURCE = sorted(str(f.relative_to(REPO)) for top in ("app", "tests", "alembic")
                for f in (REPO / top).rglob("*")
                if f.suffix in (".py", ".html", ".mjs", ".json")
                and "__pycache__" not in f.parts and "node_modules" not in f.parts)
dirty = [p for p in SOURCE if not (SCRATCH / p).exists()
         or (SCRATCH / p).read_bytes() != (REPO / p).read_bytes()]
if dirty:
    print("SCRATCH TREE IS NOT PRISTINE — every verdict would sit on a leftover edit:")
    for path in dirty[:20]:
        print(f"   -> {path} differs from the repo")
    sys.exit(2)
print(f"SCRATCH VERIFIED PRISTINE ({len(SOURCE)} source files under app/, tests/ and "
      f"alembic/ byte-identical to the repo)")

unresolved = [(name.split()[0], path, old.splitlines()[0][:60])
              for name, patches in MUTATIONS.items() for path, old, _ in patches
              if (SCRATCH / path).read_text().count(old) != 1]
if unresolved:
    print("MUTATION ANCHORS DO NOT RESOLVE EXACTLY ONCE — fix the harness, not the code:")
    for entry in unresolved:
        print(f"   -> {entry[0]} in {entry[1]}: {entry[2]!r}")
    sys.exit(2)
print(f"ANCHORS VERIFIED ({sum(len(p) for p in MUTATIONS.values())} patches across "
      f"{len(MUTATIONS)} mutations, each resolving exactly once)")

pristine = {p: (SCRATCH / p).read_text() for p in files}
survivors = []
for name, patches in MUTATIONS.items():
    for path, old, new in patches:
        f = SCRATCH / path
        f.write_text(f.read_text().replace(old, new, 1))
    result = subprocess.run(
        [PY, "-m", "pytest", *TARGETS, "-q", "--tb=no", "-p", "no:cacheprovider"],
        cwd=SCRATCH, capture_output=True, text=True,
        env={"PATH": f"{pathlib.Path(PY).parent}:/usr/bin:/bin:/usr/local/bin",
             "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "HOME": str(pathlib.Path.home())})
    failed = re.findall(r"^FAILED (\S+)", result.stdout, re.M)
    errors = re.findall(r"^ERROR (\S+)", result.stdout, re.M)
    verdict = "CAUGHT" if (failed or errors) else "*** NOT CAUGHT ***"
    print(f"\n{name}\n  {verdict}  ({len(failed)} failed, {len(errors)} errors)")
    for t in (failed + errors)[:5]:
        print(f"    {t.split('::')[-1]}")
    if len(failed) + len(errors) > 5:
        print(f"    ... and {len(failed) + len(errors) - 5} more")
    if not failed and not errors:
        survivors.append(name)
        tail = [l for l in result.stdout.splitlines() if "passed" in l or "error" in l]
        print("    " + (tail[-1] if tail else result.stdout[-200:]))
    for path, text in pristine.items():
        (SCRATCH / path).write_text(text)

print(f"\n{len(MUTATIONS)} mutations, {len(MUTATIONS) - len(survivors)} caught, "
      f"{len(survivors)} survived")
for name in survivors:
    print(f"   -> survived: {name}")
sys.exit(1 if survivors else 0)
