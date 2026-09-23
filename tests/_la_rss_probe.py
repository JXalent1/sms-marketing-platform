"""Peak RSS of a real-browser fixture run. Not collected by pytest; run by hand
or by `agent/accept-L1.sh` check 8:

    .venv/bin/python -m tests._la_rss_probe [--bidders N]

**Why a real browser and not the suite's fake.** The memory risk is Chromium —
the reference box reached 1.6 GB on leaked drivers — and a fake Playwright
weighs nothing. So this drives headless Chromium through the unchanged
`LiveAuctioneersSource` and `bidder_scrape.run_scrape()`, against a page built
from `tests/fixtures/liveauctioneers/bidders.json`.

**Nothing leaves the machine.** A page route registered after the source's own
answers every request itself: the portal URL is fulfilled with the fixture page
and everything else is aborted. Every request is counted, and the probe fails
if any request was neither fulfilled nor aborted by it.

What is measured: the summed RSS of this process and every descendant (the
node driver and each Chromium process), sampled every 100 ms. Summing RSS
counts shared pages once per process, so the figure **overstates** real use —
the conservative direction for a question about headroom.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

_TMP = tempfile.mkdtemp(prefix="la-rss-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/probe.db"
os.environ["SMS_PROVIDER"] = "console"
for key in ("LA_USERNAME", "LA_PASSWORD", "LA_HOUSE_ID", "STRIPE_SECRET_KEY"):
    os.environ[key] = ""

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Bidders</title>
<style>body{font:14px sans-serif} td{padding:2px 6px} #panel{position:fixed;right:0;top:0;width:320px}
.opts div{padding:4px}</style></head><body>
<h1>Bidders</h1>
<div class="css-1a-control"><div class="css-2b-singleValue" id="sv">Oct 01, 2026 - Marine Sale</div>
<div class="css-3c-indicatorContainer" id="ind">&#9662;</div></div><div class="opts" id="opts"></div>
<div><button>Approved</button> <button id="reg">Registered</button></div>
<table><tbody id="rows"></tbody></table><div id="foot"></div><div id="pager"></div>
<aside id="panel"></aside>
<script>
const B = __BIDDERS__, SIZE = 120; let page = 1, shown = false;
const opts = ["All Upcoming Auctions", "Oct 01, 2026 - Marine Sale"];
document.getElementById("ind").onclick = () => {
  document.getElementById("opts").innerHTML = opts.map((o, i) =>
    `<div id="react-select-2-option-${i}" class="css-9-option">${o}</div>`).join("");
  document.querySelectorAll("#opts div").forEach(d => d.onclick = () => {
    document.getElementById("sv").textContent = d.textContent;
    document.getElementById("opts").innerHTML = ""; });
};
function panel(b) {
  const L = [b.name, b.username, b.phone, b.address, b.location, "Member Since", b.member_since,
    "Card on File", b.card_on_file, "Auctions Attended", String(b.auctions_attended),
    "Tax Exemption", b.tax_exemption, "Registrations", "Bids Placed: 0"];
  if (b.analytics) { const a = b.analytics; L.push("Bidder Analytics", "Bids Placed",
    String(a.bids_placed), "Items Won", String(a.items_won), "Payment Rate", a.payment_rate,
    "Avg Hammer Price", a.avg_hammer, "Dispute History", a.dispute_history); }
  return L.filter(x => x).map(x => `<div>${x}</div>`).join("");
}
function render() {
  if (!shown) return;
  const slice = B.slice((page - 1) * SIZE, page * SIZE);
  document.getElementById("rows").innerHTML = slice.map(b =>
    `<tr><td>&#9733;</td><td>&#9744;</td><td>Paddle ref 3055550199</td><td class="n">${b.name}</td>` +
    `<td>Fort Lauderdale</td></tr>`).join("");
  document.querySelectorAll("td.n").forEach(td => td.onclick = () => {
    const b = B.find(x => x.name === td.textContent);
    setTimeout(() => { document.getElementById("panel").innerHTML = panel(b); }, 60); });
  const s = (page - 1) * SIZE + 1;
  document.getElementById("foot").textContent = `${s}-${s + slice.length - 1} of ${B.length}`;
  const n = Math.ceil(B.length / SIZE);
  document.getElementById("pager").innerHTML = Array.from({length: n}, (_, i) =>
    `<button aria-label="Page ${i + 1}">${i + 1}</button>`).join("");
  document.querySelectorAll("#pager button").forEach((btn, i) =>
    btn.onclick = () => { page = i + 1; document.getElementById("panel").innerHTML = ""; render(); });
}
document.getElementById("reg").onclick = () => { shown = true; render(); };
</script></body></html>"""


def tree_rss_kb(root_pid: int) -> dict:
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss=,comm="],
                         capture_output=True, text=True).stdout
    procs = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            procs[int(parts[0])] = (int(parts[1]), int(parts[2]),
                                    parts[3] if len(parts) > 3 else "")
    members, frontier = {root_pid}, [root_pid]
    while frontier:
        parent = frontier.pop()
        for pid, (ppid, _, _) in procs.items():
            if ppid == parent and pid not in members:
                members.add(pid)
                frontier.append(pid)
    # The `ps` this function just spawned is a child too; it is the measuring
    # instrument, not the thing measured.
    return {pid: procs[pid] for pid in members
            if pid in procs and os.path.basename(procs[pid][2]) != "ps"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bidders", type=int, default=0,
                        help="repeat the fixture to this many bidders (0 = as is)")
    args = parser.parse_args()

    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    from alembic import command
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    command.upgrade(cfg, "head")

    from app.core.database import SessionLocal
    from app.models.contact import Contact
    from app.services import bidder_scrape
    from app.sources.liveauctioneers import LiveAuctioneersSource

    with open(os.path.join(HERE, "fixtures", "liveauctioneers", "bidders.json")) as fh:
        bidders = json.load(fh)["bidders"]
    if args.bidders > len(bidders):
        base = list(bidders)
        for i in range(len(base), args.bidders):
            b = dict(base[i % len(base)], name=f"Scaled Bidder {i}",
                     username=f"scaled{i:05d}", phone=f"(954) 71{i // 10000}-{i % 10000:04d}")
            bidders.append(b)
    html = PAGE.replace("__BIDDERS__", json.dumps(bidders))
    seen = {"fulfilled": 0, "aborted": 0, "requests": 0}

    class Probe(LiveAuctioneersSource):
        async def initialize(self):
            await super().initialize()
            # Registered after the source's own route, so it runs first.
            async def serve(route, request):
                seen["requests"] += 1
                if request.url.startswith("https://partners.liveauctioneers.com/"):
                    seen["fulfilled"] += 1
                    await route.fulfill(status=200, content_type="text/html", body=html)
                else:
                    seen["aborted"] += 1
                    await route.abort()
            await self.page.route("**/*", serve)

    source = Probe(profile_root=os.path.join(_TMP, "profiles"), username="probe",
                   password="probe", house_id="0000")
    source.ROW_PAUSE_SECONDS = 0
    samples, stop = [], threading.Event()
    me = os.getpid()
    baseline = sum(r for _, r, _ in tree_rss_kb(me).values())

    def sample():
        while not stop.is_set():
            procs = tree_rss_kb(me)
            samples.append((sum(r for _, r, _ in procs.values()), len(procs)))
            time.sleep(0.1)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.monotonic()
    db = SessionLocal()
    try:
        run = bidder_scrape.run_scrape(db, source, timeout_seconds=1800)
    finally:
        stop.set()
        sampler.join(2)
    elapsed = time.monotonic() - started
    time.sleep(1.0)
    after = tree_rss_kb(me)
    contacts = db.query(Contact).count()
    db.close()

    peak_kb, peak_procs = max(samples) if samples else (0, 0)
    report = {
        "bidders_on_page": len(bidders), "status": run.status, "error": run.error,
        "contacts_created": run.contacts_created, "contacts_in_db": contacts,
        "cleanup_ran": run.cleanup_ran, "seconds": round(elapsed, 1),
        "baseline_mb": round(baseline / 1024), "peak_tree_mb": round(peak_kb / 1024),
        "peak_browser_mb": round((peak_kb - baseline) / 1024),
        "peak_processes": peak_procs, "processes_after": len(after),
        "requests": seen, "platform": sys.platform,
    }
    print(json.dumps(report, indent=1))
    ok = (run.status == "completed" and run.cleanup_ran == 1 and len(after) == 1
          and seen["requests"] == seen["fulfilled"] + seen["aborted"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
