// A DOM small enough to run the composer's real JavaScript, and no smaller.
//
// The composer's summary panel is assembled in the browser from three
// asynchronous responses. There is nothing rendered server-side to assert on,
// so a Python test can only ever check the shape of the source — which is how
// session 5m's defect survived: `paintAudienceSummary()` *is* wired to the
// select's change event, and the panel was still wrong. The only proof with
// teeth is running the file the browser runs.
//
// So this loads the four composer partials verbatim out of app/templates/,
// gives them the handful of DOM objects they touch, and lets a scenario drive
// them with response latencies measured against the production shape. Nothing
// here reimplements composer behaviour; if a scenario disagrees with the
// browser it is because this stub is wrong, and the stub is deliberately
// literal about the two things the defect turned on:
//
//   - assigning `select.value` fires no `change` event (only user input does),
//   - `select.innerHTML = …` resets the selection to the first option.

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

// COMPOSER_TEMPLATES points this at a scratch copy of the partials, which is
// how `test_the_harness_reproduces_the_defect_it_was_written_for` runs the
// pre-fix code without touching the repo. Unset, it is the real templates.
const TEMPLATES = process.env.COMPOSER_TEMPLATES
    || path.resolve(import.meta.dirname, '..', '..', 'app', 'templates');
const PARTIALS = ['_composer-summary.html', '_composer-script.html',
                  '_composer-upload.html', '_composer-link.html',
                  '_composer-lists.html'];

/** The <script> bodies of the composer partials, in include order. */
export function composerSource() {
    return PARTIALS.map(name => {
        const text = fs.readFileSync(path.join(TEMPLATES, name), 'utf8');
        const blocks = [...text.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
        if (!blocks.length) throw new Error(`no <script> in ${name}`);
        return `// ── ${name} ──\n${blocks.join('\n')}`;
    }).join('\n');
}

const decode = (value) => String(value)
    .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');

class ClassList {
    constructor() { this.set = new Set(); }
    add(...names) { names.forEach(n => this.set.add(n)); }
    remove(...names) { names.forEach(n => this.set.delete(n)); }
    contains(name) { return this.set.has(name); }
    toggle(name, force) {
        const on = force === undefined ? !this.set.has(name) : force;
        if (on) this.set.add(name); else this.set.delete(name);
        return on;
    }
}

class Element {
    constructor(id, kind = 'div') {
        this.id = id;
        this.kind = kind;
        this.value = '';
        this.textContent = '';
        this.innerHTML = '';
        this.className = '';
        this.title = '';
        this.disabled = false;
        this.dataset = {};
        this.files = [];
        this.classList = new ClassList();
        this.attributes = {};
        this.handlers = {};
        this.children = [];
    }
    addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
    querySelector() { return new Element(`${this.id}:child`, 'button'); }
    querySelectorAll() { return []; }
    /** Only user interaction fires an event; assigning `.value` never does. */
    dispatch(type, event = {}) {
        (this.handlers[type] || []).forEach(fn => fn.call(this, {
            preventDefault() {}, ...event,
        }));
    }
}

/** A <select> faithful in the two ways this defect depends on. */
class SelectElement extends Element {
    constructor(id) {
        super(id, 'select');
        this.options = [];
        this.index = -1;
        Object.defineProperty(this, 'innerHTML', {
            get: () => this._html,
            set: (html) => {
                this._html = html;
                this.options = [...String(html).matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)]
                    .map(([, attrs, text]) => {
                        const value = /value="([^"]*)"/.exec(attrs);
                        const label = /data-label="([^"]*)"/.exec(attrs);
                        return {
                            value: value ? decode(value[1]) : '',
                            text: decode(text),
                            dataset: { label: label ? decode(label[1]) : undefined },
                        };
                    });
                // A fresh option list selects the first option, exactly as a
                // browser does when no option carries `selected`.
                this.index = this.options.length ? 0 : -1;
            },
        });
        Object.defineProperty(this, 'value', {
            get: () => (this.index >= 0 ? this.options[this.index].value : ''),
            set: (wanted) => {
                // Per HTML, assigning a value that matches no option deselects
                // everything: Chrome and Firefox both leave selectedIndex at -1
                // and `value` at "". The reset that re-selects the first option
                // runs on option *insertion and removal*, which the innerHTML
                // setter above models — and that, not this, is what makes an
                // archived list fall back to the pinned entry.
                //
                // The stub said "fall back to the first option" here until the
                // 5m review, which is wrong in the permissive direction: it
                // would report a selector where a browser reports none, and the
                // composer's `if (!name || !audience || !message)` refusal is
                // exactly that distinction. Unreachable today — `loadAudiences`
                // only assigns a value it has just found in the payload — and
                // fixed anyway, because a stub that is wrong about the browser
                // is a test that is wrong about the product.
                //
                // Assigning `.value` fires no `change` either way, which is the
                // half the defect this file exists for turned on.
                this.index = this.options.findIndex(o => o.value === wanted);
            },
        });
        this._html = '';
    }
    get selectedOptions() { return this.index >= 0 ? [this.options[this.index]] : []; }
    /** What a human doing it looks like: the value moves AND `change` fires. */
    choose(wanted) { this.value = wanted; this.dispatch('change'); }
}

export function makeDocument() {
    const elements = new Map();
    const select = new SelectElement('audience');
    elements.set('audience', select);

    const modeTab = (mode) => {
        const tab = new Element(`mode:${mode}`, 'button');
        tab.dataset.mode = mode;
        return tab;
    };
    const tabs = [modeTab('upload'), modeTab('existing')];

    const document = {
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, new Element(id));
            return elements.get(id);
        },
        createElement() { return new Element('scratch'); },
        querySelectorAll(selector) {
            if (selector === '.audience-mode') return tabs;
            return [];
        },
        querySelector() { return null; },
        addEventListener() {},
    };
    return { document, elements, select, tabs };
}

/**
 * Run the composer against a scripted network.
 *
 * `plan(url, options)` returns `{ delay, body }`. Delays are real timers, so
 * responses land in the order the browser would land them in.
 */
export function runComposer({ plan, settleMs = 1200 }) {
    const { document, elements, select, tabs } = makeDocument();
    const toasts = [];
    const calls = [];

    const respond = (url, options) => {
        const answer = plan(url, options);
        calls.push({ url, body: options?.body ?? null, at: Date.now() });
        return new Promise(resolve =>
            setTimeout(() => resolve(answer.body), answer.delay || 0));
    };

    const sandbox = {
        document,
        console,
        setTimeout, clearTimeout, setInterval, clearInterval,
        FormData: class { constructor() { this.entries = []; } append(k, v) { this.entries.push([k, v]); } },
        confirm: () => true,
        esc: (value) => (value === null || value === undefined ? '' : String(value))
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
        showToast: (message, type) => toasts.push({ message, type }),
        // base.html's three, stubbed together. `paintSuppression()` calls
        // `clockTime` and `recipientLine()` calls `shortDate`, and both resolve
        // to these; without them a scenario that reached either would die on a
        // ReferenceError that reads like a defect in the composer.
        fmtDate: (iso) => String(iso),
        fmtDay: (iso) => String(iso),
        fmtClock: (iso) => String(iso),
        api: (url, options) => respond(url, options),
        fetch: async (url, options) => {
            const body = await respond(url, options);
            return { ok: body.__status ? body.__status < 400 : true,
                     status: body.__status || 200, json: async () => body };
        },
    };
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;

    const context = vm.createContext(sandbox);
    vm.runInContext(composerSource(), context, { filename: 'composer.js' });

    const panel = () => ({
        audience: elements.get('sumAudience')?.textContent ?? null,
        recipients: elements.get('sumRecipients')?.textContent ?? null,
        held_back: elements.get('sumSuppressed')?.textContent ?? null,
        opted_out: elements.get('sumOptedOut')?.textContent ?? null,
        segments: elements.get('sumSegments')?.textContent ?? null,
        cost: elements.get('sumCost')?.textContent ?? null,
        selector: select.value,
        selected_text: select.selectedOptions[0]?.text ?? null,
        // Not part of "This send", and that is the point: these rows are painted
        // by the preview handler and by nothing else, so they are how a scenario
        // sees whether the handler's own token gate held.
        sample: elements.get('previewWho')?.textContent ?? null,
        characters: elements.get('pvChars')?.textContent ?? null,
        checklist: elements.get('preflightList')?.innerHTML ?? null,
    });

    const settled = new Promise(resolve => setTimeout(resolve, settleMs));
    const el = (id) => document.getElementById(id);
    return { sandbox, context, document, el, elements, select, tabs, toasts,
             calls, panel, settled };
}
