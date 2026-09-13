// Runs review.html's inline script and shared.js against a minimal recording DOM.
//
// Usage: node ui_harness.js <scenario.json>
// Scenario: {"storage": {...}, "responses": [{method, url (regex), status, body, once}],
//            "steps": ["<js evaluated in the page context>", ...]}
// Output (stdout JSON): fetches, html_writes (every innerHTML/insertAdjacentHTML value),
// texts (every text node), snapshots (storage after each step), errors.
// Markup strings are never parsed, so the html_writes log is exactly what a browser
// would have interpreted as HTML.
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const scenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const staticDir = path.join(__dirname, '..', '..', 'docflow', 'web', 'static');
const page = fs.readFileSync(path.join(staticDir, 'review.html'), 'utf8');
const shared = fs.readFileSync(path.join(staticDir, 'shared.js'), 'utf8');
const inline = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n');

const htmlWrites = [];
const errors = [];
const fetches = [];
const registry = new Map();

class ClassList {
    constructor() { this.values = new Set(); }
    add(...names) { names.forEach((n) => this.values.add(n)); }
    remove(...names) { names.forEach((n) => this.values.delete(n)); }
    contains(name) { return this.values.has(name); }
    toggle(name, force) {
        const on = force === undefined ? !this.values.has(name) : Boolean(force);
        if (on) this.values.add(name); else this.values.delete(name);
        return on;
    }
}

class Element {
    constructor(tag) {
        this.tagName = String(tag).toUpperCase();
        this.childNodes = [];
        this.parentNode = null;
        this._text = '';
        this._html = '';
        this.id = '';
        this.className = '';
        this.classList = new ClassList();
        this.style = {};
        this.dataset = {};
        this.attributes = {};
        this.listeners = {};
        this.value = '';
        this.disabled = false;
        this.src = '';
    }
    get children() { return this.childNodes; }
    get parentElement() { return this.parentNode; }
    get textContent() { return this._text + this.childNodes.map((c) => c.textContent).join(''); }
    set textContent(value) {
        this.childNodes.forEach((c) => { c.parentNode = null; });
        this.childNodes = [];
        this._html = '';
        this._text = value == null ? '' : String(value);
    }
    get innerHTML() { return this._html; }
    set innerHTML(value) {
        htmlWrites.push({ id: this.id, html: String(value) });
        this.childNodes = [];
        this._text = '';
        this._html = String(value);
    }
    insertAdjacentHTML(position, value) { htmlWrites.push({ id: this.id, html: String(value) }); }
    appendChild(child) {
        if (child.parentNode) child.remove();
        child.parentNode = this;
        this.childNodes.push(child);
        return child;
    }
    append(...children) {
        children.forEach((c) => this.appendChild(typeof c === 'string' ? textNode(c) : c));
    }
    replaceChildren(...children) {
        this.textContent = '';
        this.append(...children);
    }
    remove() {
        if (this.parentNode) {
            this.parentNode.childNodes = this.parentNode.childNodes.filter((c) => c !== this);
            this.parentNode = null;
        }
        if (registry.get(this.id) === this) registry.delete(this.id);
    }
    setAttribute(name, value) {
        this.attributes[name] = String(value);
        if (name === 'id') this.id = String(value);
        if (name === 'class') this.className = String(value);
    }
    getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
    addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
    removeEventListener() {}
    dispatch(type) {
        const event = { type, target: this, preventDefault() {}, stopPropagation() {} };
        (this.listeners[type] || []).forEach((h) => h.call(this, event));
        if (typeof this['on' + type] === 'function') return this['on' + type].call(this, event);
        return undefined;
    }
    click() { return this.dispatch('click'); }
    focus() {}
    blur() {}
    closest() { return null; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
}

function textNode(value) {
    const node = new Element('#text');
    node._text = String(value);
    return node;
}

// Elements with a literal id in page markup or shared.js constant templates (header, picker).
for (const match of `${page}\n${shared}`.matchAll(/<([a-zA-Z0-9]+)\b([^>]*?)\sid="([^"$]+)"([^>]*)>/g)) {
    const element = new Element(match[1]);
    element.id = match[3];
    const classes = /\sclass="([^"]*)"/.exec(`${match[2]} ${match[4]}`);
    if (classes) {
        element.className = classes[1];
        element.classList.add(...classes[1].split(/\s+/).filter(Boolean));
    }
    registry.set(match[3], element);
}
const body = new Element('body');
const head = new Element('head');

function walk(root, visit) {
    visit(root);
    root.childNodes.forEach((c) => walk(c, visit));
}

function roots() { return [body, head, ...registry.values()]; }

function getElementById(id) {
    if (registry.has(id)) return registry.get(id);
    let found = null;
    for (const root of [body, head]) walk(root, (el) => { if (!found && el.id === id) found = el; });
    return found;
}

const docListeners = {};
const document = {
    body, head, documentElement: new Element('html'),
    createElement: (tag) => new Element(tag),
    createTextNode: textNode,
    getElementById,
    addEventListener(type, handler) { (docListeners[type] = docListeners[type] || []).push(handler); },
    querySelector: () => null,
};

function storage(initial) {
    const data = { ...(initial || {}) };
    return {
        getItem: (k) => (k in data ? data[k] : null),
        setItem: (k, v) => { data[k] = String(v); },
        removeItem: (k) => { delete data[k]; },
        _data: data,
    };
}
const localStorage = storage(scenario.storage);
const sessionStorage = storage();

const rules = (scenario.responses || []).slice();
async function fetch(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    let parsed = null;
    if (typeof options.body === 'string') {
        try { parsed = JSON.parse(options.body); } catch (e) { parsed = options.body; }
    }
    fetches.push({ method, url: String(url), body: parsed });
    const index = rules.findIndex((r) => r.method === method && new RegExp(r.url).test(String(url)));
    if (index < 0) {
        return reply(404, { error: { code: 'not_found', message: 'Not Found' } });
    }
    const rule = rules[index];
    if (rule.once) rules.splice(index, 1);
    return reply(rule.status || 200, rule.body);
}
function reply(status, payload) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => JSON.parse(JSON.stringify(payload === undefined ? {} : payload)),
    };
}

function findByText(text) {
    let found = null;
    for (const root of roots()) {
        walk(root, (el) => {
            if (!found && el.tagName === 'BUTTON' && el.textContent.includes(text)) found = el;
        });
    }
    if (!found) throw new Error(`no button with text ${text}`);
    return found;
}

const sandbox = {
    document, localStorage, sessionStorage, fetch, console,
    location: { pathname: '/review', href: '/review' },
    matchMedia: () => ({ matches: false }),
    setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
    requestAnimationFrame: (cb) => cb(),
    crypto: globalThis.crypto, URLSearchParams, URL,
    FormData: class FormData { append() {} },
    __clickText: (text) => findByText(text).click(),
    __click: (id) => getElementById(id).click(),
    __key: (key) => (docListeners.keydown || []).forEach((h) => h({
        key, target: { tagName: 'BODY', closest: () => null }, preventDefault() {},
    })),
};
sandbox.window = sandbox;
vm.createContext(sandbox);

process.on('unhandledRejection', (e) => errors.push(String(e && e.stack || e)));

function collectTexts() {
    const texts = [];
    for (const root of roots()) walk(root, (el) => { if (el._text) texts.push(el._text); });
    return texts;
}

async function settle() {
    for (let i = 0; i < 50; i += 1) await new Promise((r) => setImmediate(r));
}

(async () => {
    const snapshots = [];
    try {
        vm.runInContext(shared, sandbox, { filename: 'shared.js' });
        vm.runInContext(inline, sandbox, { filename: 'review.html' });
        await settle();
        for (const step of scenario.steps || []) {
            await vm.runInContext(step, sandbox);
            await settle();
            snapshots.push({ step, storage: { ...localStorage._data }, fetches: fetches.length,
                             texts: collectTexts() });
        }
    } catch (e) {
        errors.push(String(e && e.stack || e));
    }
    process.stdout.write(JSON.stringify({
        fetches, html_writes: htmlWrites, texts: collectTexts(), snapshots, errors,
        storage: localStorage._data,
    }));
})();
