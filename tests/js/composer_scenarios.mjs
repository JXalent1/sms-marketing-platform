// The scenarios the composer's summary panel has to survive, driven through the
// real partials by composer_harness.mjs.
//
//     node tests/js/composer_scenarios.mjs <scenario> --fixtures <path.json>
//
// **Every response body comes from the real endpoint.** The fixture file is
// written by tests/test_composer_panel.py, which seeds a database and calls
// /api/campaigns/{audiences,preview,preflight} for the selectors below. A
// hand-written body here would be a second, drifting copy of the API — and the
// property under test is precisely that the panel says what the API said about
// the audience the send will use.
//
// The latencies are the fixture's too. Measured at the production shape —
// 10,146 active contacts, 3,460 opted out, a 443-contact list:
//
//     POST /api/campaigns/preview   audience=all      237.9 ms
//     POST /api/campaigns/preview   audience=list:N    20.3 ms
//     POST /api/campaigns/preflight audience=list:N    34.9 ms
//
// A twelvefold gap, on a machine an order of magnitude faster than the client's
// one-vCPU box. The scenarios keep that ordering and exaggerate the gap so a
// race is a fact rather than a coin toss.

import fs from 'node:fs';
import { runComposer } from './composer_harness.mjs';

const args = process.argv.slice(2);
const name = args[0];
const fixturesAt = args[args.indexOf('--fixtures') + 1];
if (!name || args.indexOf('--fixtures') < 0) {
    console.error('usage: composer_scenarios.mjs <scenario> --fixtures <path.json>');
    process.exit(2);
}
const F = JSON.parse(fs.readFileSync(fixturesAt, 'utf8'));

const audienceOf = (options) => {
    try { return JSON.parse(options?.body || '{}').audience ?? null; }
    catch { return null; }
};

function plan(url, options) {
    if (url.startsWith('/api/campaigns/audiences')) {
        return { delay: F.latency.audiences, body: F.audiences };
    }
    if (url.startsWith('/api/campaigns/preview')) {
        const who = audienceOf(options);
        return { delay: F.latency.preview[who] ?? 10,
                 body: F.preview[who] ?? F.preview[''] };
    }
    if (url.startsWith('/api/campaigns/preflight')) {
        const who = audienceOf(options);
        return { delay: F.latency.preflight, body: F.preflight[who] };
    }
    if (url.startsWith('/api/campaigns/from-upload')) {
        return { delay: 20, body: F.from_upload };
    }
    if (url === '/api/campaigns') return { delay: 20, body: F.created };
    if (url.startsWith('/api/campaigns?')) return { delay: 10, body: { campaigns: [] } };
    if (url.startsWith('/api/lists/manage')) return { delay: 10, body: { lists: [] } };
    return { delay: 5, body: {} };
}

const wait = (ms) => new Promise(r => setTimeout(r, ms));
const LIST = F.list_selector;
const settle = () => wait(F.latency.preview[LIST] + F.latency.preview.all
                          + F.latency.preflight + 500);

// The keystroke path is debounced at 300 ms, so a request is only in flight
// once that has elapsed. Every scenario that needs two replies racing has to
// let the first one leave.
const DEBOUNCE = 300;

/** Page load, a message typed, and the pinned all-bidders entry in flight. */
async function openComposer() {
    const run = runComposer({ plan });
    await wait(F.latency.audiences + 20);          // the dropdown is populated
    run.el('message').value = F.message;
    run.sandbox.setMode('existing');               // arms the first refresh
    await wait(DEBOUNCE + 50);                     // …which is now on the wire
    return run;
}

/** The campaign-first flow: upload as step one. */
async function uploadFlow() {
    const run = runComposer({ plan });
    await wait(F.latency.audiences + F.latency.preview.all + 150);
    run.el('campaignName').value = F.list_label;
    run.el('message').value = F.message;
    run.el('uploadFile').files = [{ name: 'record-collection.csv' }];
    run.sandbox.setMode('upload');
    run.el('composer').dispatch('submit');
    await settle();
    return run;
}

const SCENARIOS = {
    // Page load asks about the pinned all-bidders entry, which at this shape is
    // the slowest answer there is. He picks the list a moment later. The reply
    // about the audience he left must never reach the panel, however late it is.
    stale_reply_loses: async () => {
        const run = await openComposer();
        run.select.choose(LIST);                   // while `all` is still in flight
        await settle();
        return run.panel();
    },

    // The campaign-first flow. The panel names the list the upload created, and
    // every figure in it is that list's.
    upload_names_its_list: async () => (await uploadFlow()).panel(),

    // Run checks re-resolves the audience. Between the preview and the checks
    // the list gained members (the fixture is built that way, from the real
    // endpoint), so the two responses genuinely disagree about how many people
    // this send reaches — and the panel has to move as one answer, not two rows.
    preflight_repaints_the_whole_panel: async () => {
        const run = await openComposer();
        run.select.choose(LIST);
        await settle();
        await settle();
        const before = run.panel();
        run.el('preflightBtn').dispatch('click');
        await wait(F.latency.preflight + 300);
        return { before, after: run.panel() };
    },

    // The question with ten thousand people on the other side of it: what
    // selector does POST /api/campaigns receive, and does the panel name it?
    create_posts_the_selector: async () => {
        const run = await uploadFlow();
        run.el('composer').dispatch('submit');
        await wait(300);
        const posted = run.calls.filter(c => c.url === '/api/campaigns').pop();
        return { panel: run.panel(), posted: JSON.parse(posted.body) };
    },

    // The rows the panel does not own — the phone preview and the character
    // count — are painted by the preview handler, and a superseded reply must
    // not repaint those either. A message preview showing a different audience's
    // contact is the same defect one box over.
    stale_reply_does_not_repaint_the_phone_preview: async () => {
        const run = await openComposer();
        run.select.choose(LIST);
        await settle();
        return run.panel();
    },

    // What the panel says between picking an audience and hearing about it.
    panel_while_it_waits: async () => {
        const run = await openComposer();
        run.select.choose(LIST);
        await wait(40);                            // inside the debounce
        const waiting = run.panel();
        await settle();
        return { waiting, settled: run.panel() };
    },

    // Press Run checks, then change your mind about the audience while they are
    // running — the change handler takes a ticket at once, so the report that
    // comes back is about a send nobody is making. It must not paint, and it
    // must not leave "Running checks…" up forever either.
    preflight_superseded_midway: async () => {
        const run = await openComposer();
        run.select.choose(LIST);
        await settle();
        run.el('preflightBtn').dispatch('click');
        await wait(5);                             // the report is on the wire
        run.select.choose('all');                  // …and now it is about nobody
        await settle();
        await settle();
        return { checklist: run.panel().checklist };
    },

    // Switching to the upload tab while a preview is in flight. The panel is
    // cleared, and the reply that lands afterwards must not refill it under a
    // composer whose audience is a file that has not been read yet.
    upload_mode_clears_and_stays_clear: async () => {
        const run = await openComposer();
        run.select.choose('all');
        await wait(DEBOUNCE + F.latency.preview.all / 4);   // in flight, not back
        run.sandbox.setMode('upload');
        await settle();
        return run.panel();
    },
};

const scenario = SCENARIOS[name];
if (!scenario) {
    console.error(`unknown scenario: ${name}. known: ${Object.keys(SCENARIOS).join(', ')}`);
    process.exit(2);
}
scenario().then(out => {
    console.log(JSON.stringify(out, null, 2));
    process.exit(0);
}).catch(err => { console.error(err); process.exit(1); });
