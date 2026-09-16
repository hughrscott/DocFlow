// DocFlow shared UI components and utilities — Paper Archive redesign

const VIEWS = [
    { id: 'dashboard', icon: 'dashboard', label: 'Dashboard', path: '/' },
    { id: 'review', icon: 'mark_email_unread', label: 'Review Queue', path: '/review' },
    { id: 'archive', icon: 'inventory_2', label: 'Archive Browser', path: '/archive' },
    { id: 'settings', icon: 'settings', label: 'Settings', path: '/settings' },
];

function currentView() {
    const path = window.location.pathname;
    if (path === '/' || path === '') return 'dashboard';
    return path.replace('/', '');
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------
function renderNav() {
    const active = currentView();

    const navItems = VIEWS.map(v => {
        const isActive = v.id === active;
        const baseCls = 'flex items-center gap-3 w-full border-none rounded-btn py-[11px] px-[14px] cursor-pointer font-semibold text-[13.5px] transition-all duration-150';
        const activeCls = isActive
            ? `${baseCls} bg-ink text-[#F4F1EA]`
            : `${baseCls} bg-transparent text-text-secondary hover:bg-[#E3DCCD]`;
        const fillCls = isActive ? 'fill' : '';
        const ariaCurrent = isActive ? ' aria-current="page"' : '';

        // Badge for Review Queue (pending count, populated later by JS)
        let badge = '';
        if (v.id === 'review') {
            badge = '<span id="nav-review-badge" class="bg-gold text-white text-[10.5px] font-bold rounded-pill px-[7px] py-[1px] hidden"></span>';
        }

        return `<a href="${v.path}" class="${activeCls}"${ariaCurrent}>
            <span class="ms ${fillCls}" style="font-size:20px;">${v.icon}</span>
            <span class="nav-label flex-1 text-left">${v.label}</span>
            ${badge}
        </a>`;
    }).join('');

    return `
    <aside id="sidebar" class="w-[248px] flex-shrink-0 bg-sidebar fixed left-0 top-0 h-screen flex flex-col py-[26px] px-[18px] z-50 border-r border-border-sidebar" aria-label="Main navigation">
        <div class="px-2 pb-1">
            <div class="flex items-center gap-[9px]">
                <div class="w-7 h-7 rounded-tile bg-ink flex items-center justify-center">
                    <span class="ms fill" style="font-size:18px;color:#F4F1EA;">inventory_2</span>
                </div>
                <h1 class="m-0 font-headline font-extrabold text-[21px] tracking-[-0.02em] text-ink">DocFlow</h1>
            </div>
            <p class="mt-[9px] text-[9.5px] font-bold tracking-[.18em] uppercase text-text-faint">The Digital Archivist</p>
        </div>

        <button onclick="globalUpload()" class="mt-6 mb-[22px] flex items-center justify-center gap-2 bg-ink text-[#F4F1EA] border-none rounded-btn py-[13px] font-bold text-[13.5px] cursor-pointer shadow-btn hover:-translate-y-px transition-transform duration-150">
            <span class="ms" style="font-size:18px;">add</span>
            <span class="nav-label">Scan Mail</span>
        </button>

        <nav class="flex flex-col gap-1 flex-1">${navItems}</nav>

        <div class="border-t border-border-sidebar pt-3 flex flex-col gap-[2px]">
            <button onclick="toggleDarkMode()" class="flex items-center gap-3 px-[14px] py-[10px] rounded-btn border-none bg-transparent text-text-muted font-semibold text-[13.5px] cursor-pointer hover:bg-[#E3DCCD] transition-colors w-full">
                <span class="ms" style="font-size:20px;" id="dark-mode-icon">dark_mode</span>
                <span class="nav-label" id="dark-mode-label">Dark Mode</span>
            </button>
            <a href="#" class="flex items-center gap-3 px-[14px] py-[10px] rounded-btn text-text-muted font-semibold text-[13.5px] hover:bg-[#E3DCCD] transition-colors no-underline">
                <span class="ms" style="font-size:20px;">help</span>
                <span class="nav-label">Support</span>
            </a>
        </div>
    </aside>`;
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------
function renderHeader(title) {
    return `
    <header id="main-header" class="flex items-center justify-between py-5 px-[34px] border-b border-border-primary bg-[rgba(244,241,234,0.86)] backdrop-blur-[10px] sticky top-0 z-40 flex-shrink-0">
        <h2 class="m-0 font-headline font-bold text-[20px] tracking-[-0.01em] text-ink">${title}</h2>
        <div class="flex items-center gap-4">
            <div class="relative" id="search-container">
                <input id="search-input" aria-label="Search archive"
                    class="bg-white border border-border-primary rounded-pill py-[9px] pl-[38px] pr-4 text-[13px] font-medium w-[240px] outline-none text-ink"
                    placeholder="Search archive…" type="text" autocomplete="off" />
                <span class="ms absolute left-[13px] top-[9px] text-text-dim" style="font-size:18px;">search</span>
                <div id="search-results" class="absolute top-full mt-2 right-0 w-96 bg-white rounded-card shadow-modal border border-border-primary hidden max-h-80 overflow-y-auto z-50"></div>
            </div>
            <button class="relative w-10 h-10 rounded-btn border border-border-primary bg-white flex items-center justify-center cursor-pointer">
                <span class="ms" style="font-size:20px;color:#4A4D57;">notifications</span>
                <span class="absolute top-[9px] right-[10px] w-[7px] h-[7px] rounded-pill bg-danger border-[1.5px] border-white"></span>
            </button>
        </div>
    </header>`;
}

// ---------------------------------------------------------------------------
// Footer status bar
// ---------------------------------------------------------------------------
function renderFooter() {
    return `
    <footer id="status-bar" role="contentinfo" class="flex items-center justify-between py-2 px-[34px] border-t border-border-primary bg-panel flex-shrink-0">
        <p class="m-0 text-[10.5px] font-semibold tracking-[.06em] uppercase text-text-olive">
            Watch Folder: <span class="text-success font-bold" id="watch-status">Checking...</span>
            &nbsp;&bull;&nbsp; LLM Status: <span class="text-success font-bold" id="llm-status">Checking...</span>
        </p>
        <span class="text-[10.5px] font-semibold tracking-[.06em] uppercase text-text-dim cursor-pointer">System Logs</span>
    </footer>`;
}

// ---------------------------------------------------------------------------
// Responsive styles
// ---------------------------------------------------------------------------
function injectResponsiveStyles() {
    if (document.getElementById('docflow-responsive-css')) return;
    const style = document.createElement('style');
    style.id = 'docflow-responsive-css';
    style.textContent = `
        /* Material Symbols shorthand */
        .ms {
            font-family: 'Material Symbols Outlined';
            font-weight: normal;
            font-style: normal;
            line-height: 1;
            letter-spacing: normal;
            text-transform: none;
            display: inline-block;
            white-space: nowrap;
            word-wrap: normal;
            direction: ltr;
            font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
            -webkit-font-smoothing: antialiased;
            user-select: none;
        }
        .ms.fill {
            font-variation-settings: 'FILL' 1, 'wght' 500;
        }

        /* Custom scrollbar */
        .df-scroll::-webkit-scrollbar { width: 10px; height: 10px; }
        .df-scroll::-webkit-scrollbar-thumb {
            background: rgba(20,23,28,.14);
            border-radius: 8px;
            border: 3px solid transparent;
            background-clip: content-box;
        }
        .df-scroll::-webkit-scrollbar-thumb:hover {
            background: rgba(20,23,28,.26);
            background-clip: content-box;
        }

        /* Animations */
        @keyframes dfup { from { transform: translateY(14px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        @keyframes dffade { from { opacity: 0; } to { opacity: 1; } }

        /* Input placeholder */
        input::placeholder { color: #A9A595; }

        /* Full-height layout with sidebar */
        .df-shell {
            display: flex;
            min-height: 100vh;
            width: 100%;
            background: #F4F1EA;
            color: #15171C;
            font-family: 'Inter', sans-serif;
            -webkit-font-smoothing: antialiased;
        }
        .df-main {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
            height: 100vh;
            margin-left: 248px;
        }
        .df-body {
            flex: 1;
            overflow-y: auto;
            position: relative;
        }

        /* Below 768px: hide sidebar entirely, show bottom tab bar */
        @media (max-width: 767px) {
            #sidebar { display: none; }
            #mobile-tabs { display: flex !important; }
            .df-main { margin-left: 0 !important; }
            #status-bar { bottom: 3.5rem; }
            #search-container input { width: 8rem; }
            #search-results { width: calc(100vw - 2rem); right: -1rem; }
        }
    `;
    document.head.appendChild(style);
}

function renderMobileTabs() {
    const active = currentView();
    const tabs = VIEWS.map(v => {
        const isActive = v.id === active;
        const cls = isActive ? 'text-ink' : 'text-text-dim';
        const fill = isActive ? 'fill' : '';
        return `<a href="${v.path}" class="flex flex-col items-center gap-0.5 ${cls} no-underline">
            <span class="ms ${fill}" style="font-size:20px;">${v.icon}</span>
            <span class="text-[10px] font-bold">${v.label.split(' ')[0]}</span>
        </a>`;
    }).join('');

    return `<nav id="mobile-tabs" class="fixed bottom-0 left-0 right-0 h-14 bg-canvas border-t border-border-primary justify-around items-center z-50 hidden">${tabs}</nav>`;
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------
let _searchTimeout = null;

function initSearch() {
    const input = document.getElementById('search-input');
    const results = document.getElementById('search-results');
    if (!input || !results) return;

    input.addEventListener('input', () => {
        clearTimeout(_searchTimeout);
        const q = input.value.trim();
        if (q.length < 2) { results.classList.add('hidden'); return; }
        _searchTimeout = setTimeout(() => runSearch(q), 300);
    });

    input.addEventListener('focus', () => {
        if (input.value.trim().length >= 2) runSearch(input.value.trim());
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('#search-container')) results.classList.add('hidden');
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === '/' && !e.target.closest('input, textarea, select')) {
            e.preventDefault();
            input.focus();
        }
        if (e.key === 'Escape') {
            input.blur();
            results.classList.add('hidden');
        }
    });
}

async function runSearch(query) {
    const results = document.getElementById('search-results');
    try {
        const resp = await fetch('/api/search?q=' + encodeURIComponent(query));
        if (!resp.ok) { results.classList.add('hidden'); return; }
        const data = await resp.json();
        if (!data.results || data.results.length === 0) {
            results.replaceChildren(textElement('div', 'p-4 text-sm text-text-muted text-center', 'No results found'));
            results.classList.remove('hidden');
            return;
        }
        results.replaceChildren(...data.results.map(r => {
            const link = textElement('a', 'flex items-center gap-3 px-4 py-3 hover:bg-soft-hover transition-colors border-b border-border-card last:border-0 no-underline');
            const url = typeof r.url === 'string' ? r.url : '';
            link.href = url.startsWith('/') && !url.startsWith('//') ? url : '#';
            const text = textElement('div', 'min-w-0 flex-1');
            text.append(
                textElement('p', 'text-[12.5px] font-mono font-medium text-ink truncate m-0', r.filename || r.name || ''),
                textElement('p', 'text-[11.5px] text-text-faint truncate m-0 mt-0.5', r.directory || r.path || ''));
            const icon = /^[a-z_]+$/.test(r.icon || '') ? r.icon : 'description';
            link.append(iconElement(icon, 18, null, 'text-text-dim'), text);
            if (typeof r.confidence === 'number') {
                const pct = Math.round(r.confidence * 100);
                const info = confidenceInfo(r.confidence);
                const badge = textElement('span', 'text-[11px] font-bold px-[9px] py-1 rounded-pill whitespace-nowrap', `${pct}% Match`);
                badge.style.background = info.bg;
                badge.style.color = info.color;
                link.appendChild(badge);
            }
            return link;
        }));
        results.classList.remove('hidden');
    } catch (e) {
        results.classList.add('hidden');
    }
}

// ---------------------------------------------------------------------------
// Health status
// ---------------------------------------------------------------------------
// Severity classes for a server-reported status level. An unknown or absent level is
// neutral, never danger: the UI must not invent a failure it was not told about.
const STATUS_LEVEL_CLASS = {
    ok: 'text-success font-bold',
    neutral: 'text-text-olive font-bold',
    warning: 'text-danger font-bold',
};

async function updateHealthStatus() {
    try {
        const resp = await fetch('/api/health');
        const data = await resp.json();
        const watchEl = document.getElementById('watch-status');
        const llmEl = document.getElementById('llm-status');
        if (watchEl) watchEl.textContent = data.watch_folder ? 'Active' : 'Not Set';
        if (llmEl) {
            llmEl.textContent = data.llm_status_label || 'Unavailable';
            llmEl.className = STATUS_LEVEL_CLASS[data.llm_status_level]
                || STATUS_LEVEL_CLASS.neutral;
            llmEl.setAttribute('title', data.llm_status_detail || '');
        }
    } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Review queue badge in sidebar
// ---------------------------------------------------------------------------
async function updateReviewBadge() {
    try {
        const scopeId = await activeArchiveScopeId();
        const badge = document.getElementById('nav-review-badge');
        if (!badge || !scopeId) return;
        const { ok, data } = await docflowApi('GET',
            `/api/v1/review-items?archive_scope_id=${encodeURIComponent(scopeId)}&status=pending`);
        const count = ok && data && Array.isArray(data.items) ? data.items.length : 0;
        if (count > 0) {
            badge.textContent = count;
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Scan Mail: there is exactly one Add-scanned-mail flow, on the dashboard.
// This button never uploads or starts processing itself; from another page it
// routes to the dashboard, where the same file input handles the submission.
// ---------------------------------------------------------------------------
function globalUpload() {
    const dashInput = document.getElementById('upload-input');
    if (dashInput) {
        if (!dashInput.disabled) dashInput.click();
        return;
    }
    window.location = '/';
}

// ---------------------------------------------------------------------------
// Badges
// ---------------------------------------------------------------------------
// A run that never classified the document reports no confidence. That is neutral
// information, not a zero score: it is never shown as a low-confidence percentage.
function confidenceBadge(confidence) {
    const info = confidenceInfo(confidence);
    return `<span style="background:${info.bg};color:${info.color};" class="text-[11px] font-bold px-[9px] py-1 rounded-pill whitespace-nowrap">${confidenceLabel(confidence)}</span>`;
}

// The badge text for one confidence value: a percentage, or the neutral wording.
function confidenceLabel(confidence) {
    return typeof confidence === 'number'
        ? `${Math.round(confidence * 100)}% Match` : confidenceInfo(null).label;
}

function confidenceInfo(confidence) {
    if (typeof confidence !== 'number') {
        return { color: '#8A8B72', bg: '#ECE7DC', label: 'Not classified' };
    }
    if (confidence >= 0.75) return { color: '#0E8A5E', bg: '#E2F1E9', label: 'Confident' };
    if (confidence >= 0.60) return { color: '#B5751F', bg: '#F6E9D3', label: 'Needs a look' };
    return { color: '#BE4029', bg: '#F7E3DD', label: 'Low confidence' };
}

function ruleBadge(rule) {
    if (rule === 'llm_suggested') {
        return `<span class="flex items-center gap-[6px]"><span class="w-[6px] h-[6px] rounded-pill bg-ai flex-shrink-0"></span><span class="text-[11.5px] text-text-muted truncate">AI Suggested</span></span>`;
    } else if (rule === 'none') {
        return `<span class="flex items-center gap-[6px]"><span class="w-[6px] h-[6px] rounded-pill bg-danger flex-shrink-0"></span><span class="text-[11.5px] text-text-muted truncate">Unmatched</span></span>`;
    }
    return `<span class="flex items-center gap-[6px]"><span class="w-[6px] h-[6px] rounded-pill bg-success flex-shrink-0"></span><span class="text-[11.5px] text-text-muted truncate">Rule: ${escapeHtml(rule)}</span></span>`;
}

// ---------------------------------------------------------------------------
// Toast notification system
// ---------------------------------------------------------------------------
function initToastContainer() {
    if (document.getElementById('toast-container')) return;
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'fixed top-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none';
    document.body.appendChild(container);
}

// Build an element whose text is set with textContent: document/model strings are never markup.
function textElement(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
}

function iconElement(name, size, color, extraClass = '') {
    const icon = textElement('span', `ms ${extraClass}`.trim(), name);
    icon.style.fontSize = `${size}px`;
    if (color) icon.style.color = color;
    return icon;
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

// ---------------------------------------------------------------------------
// Local /api/v1 helpers
// ---------------------------------------------------------------------------
async function docflowApi(method, url, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }
    try {
        const resp = await fetch(url, options);
        const data = await resp.json().catch(() => null);
        return { ok: resp.ok, status: resp.status, data };
    } catch (e) {
        return { ok: false, status: 0, data: null };
    }
}

function apiErrorMessage(data, fallback) {
    const message = data && data.error && data.error.message;
    return typeof message === 'string' ? message : fallback;
}

// One new key per user action; the server stores the result under it.
function newIdempotencyKey(prefix) {
    const random = (window.crypto && typeof window.crypto.randomUUID === 'function')
        ? window.crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `${prefix}-${random}`;
}

let _scopePromise = null;
function activeArchiveScopeId() {
    if (!_scopePromise) {
        _scopePromise = docflowApi('GET', '/api/v1/archive-scopes/active').then(({ ok, data }) => {
            const id = ok && data && data.archive_scope && data.archive_scope.id;
            if (typeof id !== 'string') { _scopePromise = null; return null; }
            return id;
        });
    }
    return _scopePromise;
}

function showToast(message, type = 'error', duration = 5000) {
    initToastContainer();
    const container = document.getElementById('toast-container');

    const colors = {
        error:   'bg-danger-bg border border-danger/20 text-danger',
        success: 'bg-success-bg border border-success/20 text-success',
        warning: 'bg-amber-bg border border-amber/20 text-amber',
        info:    'bg-ai-bg border border-ai-border text-ai',
    };
    const icons = { error: 'error', success: 'check_circle', warning: 'warning', info: 'info' };

    const toast = document.createElement('div');
    toast.className = `pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-btn shadow-btn ${colors[type] || colors.info} transform translate-x-full opacity-0 transition-all duration-300`;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    const close = textElement('button', 'opacity-50 hover:opacity-100 transition-opacity border-none bg-transparent cursor-pointer');
    close.setAttribute('aria-label', 'Dismiss');
    close.appendChild(iconElement('close', 16));
    close.addEventListener('click', () => toast.remove());
    toast.append(iconElement(icons[type] || icons.info, 18),
        textElement('span', 'text-sm font-semibold flex-1', message), close);

    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.remove('translate-x-full', 'opacity-0'));

    if (duration > 0) {
        setTimeout(() => {
            toast.classList.add('translate-x-full', 'opacity-0');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }
}

// ---------------------------------------------------------------------------
// Undo snackbar
// ---------------------------------------------------------------------------
let _undoTimer = null;
let _undoCallback = null;

// onUndo must perform a durable undo (POST /api/v1/operations/{id}/undo).
// options.persistent keeps a restored snackbar visible; options.onDismiss forgets it.
function showUndoSnackbar(label, onUndo, options = {}) {
    if (typeof onUndo !== 'function') return;
    hideUndoSnackbar();
    _undoCallback = onUndo;

    const snack = document.createElement('div');
    snack.id = 'undo-snackbar';
    snack.className = 'fixed bottom-[26px] left-1/2 -translate-x-1/2 z-[120] flex items-center gap-[14px] bg-ink text-[#F4F1EA] rounded-[14px] py-3 pl-[18px] pr-[14px] shadow-snackbar';
    snack.style.animation = 'dfup .2s ease-out';
    snack.setAttribute('role', 'status');
    const undo = textElement('button', 'flex items-center gap-[6px] bg-white/10 text-[#F4F1EA] border-none rounded-[9px] py-2 px-[13px] font-bold text-[12.5px] cursor-pointer hover:bg-white/20 transition-colors');
    undo.append(iconElement('undo', 16), ' Undo ',
        textElement('span', 'opacity-50 text-[10.5px] border border-white/30 rounded px-1', 'U'));
    undo.addEventListener('click', triggerUndo);
    snack.append(iconElement('check_circle', 20, '#7FD7AB', 'fill'),
        textElement('span', 'text-[13.5px] font-semibold', label), undo);
    if (typeof options.onDismiss === 'function') {
        const dismiss = textElement('button', 'bg-transparent text-[#F4F1EA] border-none cursor-pointer opacity-60 hover:opacity-100');
        dismiss.setAttribute('aria-label', 'Dismiss');
        dismiss.appendChild(iconElement('close', 16));
        dismiss.addEventListener('click', () => { hideUndoSnackbar(); options.onDismiss(); });
        snack.appendChild(dismiss);
    }
    document.body.appendChild(snack);

    if (!options.persistent) _undoTimer = setTimeout(() => hideUndoSnackbar(), 6000);
}

// A visible, persistent report when undo could not compensate every step.
function showUndoFailure(message, details, onRetry, onDismiss) {
    hideUndoSnackbar();
    const previous = document.getElementById('undo-failure');
    if (previous) previous.remove();
    const panel = textElement('div', 'fixed bottom-[26px] left-1/2 -translate-x-1/2 z-[120] max-w-[560px] flex flex-col gap-2 bg-danger-bg border border-danger/20 text-danger rounded-[14px] py-3 px-[18px] shadow-snackbar');
    panel.id = 'undo-failure';
    panel.setAttribute('role', 'alert');
    const list = textElement('ul', 'm-0 pl-5 text-[12.5px]');
    details.forEach(detail => list.appendChild(textElement('li', 'font-mono', detail)));
    const actions = textElement('div', 'flex gap-2');
    const retry = textElement('button', 'bg-white border border-danger/20 rounded-[9px] py-1 px-3 font-bold text-[12.5px] cursor-pointer', 'Try undo again');
    retry.addEventListener('click', () => { panel.remove(); onRetry(); });
    const dismiss = textElement('button', 'bg-transparent border-none font-bold text-[12.5px] cursor-pointer', 'Dismiss');
    dismiss.addEventListener('click', () => { panel.remove(); if (onDismiss) onDismiss(); });
    actions.append(retry, dismiss);
    panel.append(textElement('p', 'm-0 text-[13.5px] font-semibold', message), list, actions);
    document.body.appendChild(panel);
}

function hideUndoSnackbar() {
    clearTimeout(_undoTimer);
    const el = document.getElementById('undo-snackbar');
    if (el) el.remove();
    _undoCallback = null;
}

function triggerUndo() {
    if (_undoCallback) _undoCallback();
    hideUndoSnackbar();
}

// ---------------------------------------------------------------------------
// Dark mode
// ---------------------------------------------------------------------------
function toggleDarkMode() {
    const isDark = document.documentElement.classList.toggle('dark');
    localStorage.setItem('docflow_dark_mode', isDark ? 'dark' : 'light');
    updateDarkModeUI(isDark);
}

function initDarkMode() {
    const saved = localStorage.getItem('docflow_dark_mode');
    let isDark;
    if (saved) {
        isDark = saved === 'dark';
    } else {
        isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    if (isDark) {
        document.documentElement.classList.add('dark');
    } else {
        document.documentElement.classList.remove('dark');
    }
    updateDarkModeUI(isDark);
}

function updateDarkModeUI(isDark) {
    const icon = document.getElementById('dark-mode-icon');
    const label = document.getElementById('dark-mode-label');
    if (icon) icon.textContent = isDark ? 'light_mode' : 'dark_mode';
    if (label) label.textContent = isDark ? 'Light Mode' : 'Dark Mode';
}

// ---------------------------------------------------------------------------
// Directory Picker (redesigned modal)
// ---------------------------------------------------------------------------
let _dirTreeCache = null;

async function openDirectoryPicker(callback) {
    const existing = document.getElementById('dir-picker-modal');
    if (existing) existing.remove();

    if (!_dirTreeCache) {
        try {
            const resp = await fetch('/api/archive/tree');
            const data = await resp.json();
            _dirTreeCache = data.tree || [];
        } catch (e) {
            showToast('Failed to load directory tree', 'error');
            return;
        }
    }

    const modal = document.createElement('div');
    modal.id = 'dir-picker-modal';
    modal.className = 'fixed inset-0 z-[200] flex items-center justify-center';
    modal.innerHTML = `
        <div class="absolute inset-0 bg-[rgba(20,23,28,0.42)] backdrop-blur-[3px]" onclick="closeDirPicker()"></div>
        <div class="relative w-[440px] max-h-[74vh] flex flex-col bg-soft-hover rounded-modal shadow-modal overflow-hidden">
            <div class="py-[18px] px-5 pb-[14px] border-b border-border-card">
                <div class="flex items-center justify-between mb-[13px]">
                    <h3 class="m-0 font-headline font-extrabold text-[16px] text-ink">Choose Filing Location</h3>
                    <button onclick="closeDirPicker()" class="w-[30px] h-[30px] border-none bg-tile rounded-[9px] flex items-center justify-center cursor-pointer">
                        <span class="ms" style="font-size:18px;color:#8A8B72;">close</span>
                    </button>
                </div>
                <div class="relative">
                    <input id="dir-picker-search" type="text" placeholder="Filter folders…" autocomplete="off"
                        class="w-full bg-white border border-border-primary rounded-input py-[10px] pl-9 pr-3 text-[13px] font-medium text-ink outline-none" />
                    <span class="ms absolute left-[11px] top-[10px] text-text-dim" style="font-size:17px;">search</span>
                </div>
            </div>
            <div id="dir-picker-tree" class="df-scroll flex-1 overflow-y-auto p-2"></div>
            <div class="px-5 py-3 border-t border-border-card flex items-center gap-2">
                <span class="ms text-text-dim" style="font-size:16px;">subdirectory_arrow_right</span>
                <input id="dir-picker-custom" type="text" placeholder="Or type a new path…"
                    class="flex-1 bg-transparent border-none text-sm text-ink outline-none font-mono" />
                <button onclick="dirPickerConfirmCustom()" class="px-3 py-1.5 bg-ink text-[#F4F1EA] rounded-input text-xs font-bold cursor-pointer hover:opacity-90 transition-opacity border-none">Use</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    window._dirPickerCallback = callback;
    _renderDirPickerTree(_dirTreeCache, '');

    const searchInput = document.getElementById('dir-picker-search');
    searchInput.focus();
    searchInput.addEventListener('input', () => {
        _renderDirPickerTree(_dirTreeCache, searchInput.value.trim().toLowerCase());
    });

    document.getElementById('dir-picker-custom').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') dirPickerConfirmCustom();
    });

    modal.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeDirPicker();
    });
}

function _renderDirPickerTree(nodes, filter, depth = 0) {
    const container = document.getElementById('dir-picker-tree');
    if (depth === 0) container.replaceChildren();

    for (const node of nodes) {
        const matchesFilter = !filter || node.path.toLowerCase().includes(filter) || node.name.toLowerCase().includes(filter);
        const childrenMatch = node.children && _treeHasMatch(node.children, filter);

        if (!matchesFilter && !childrenMatch) continue;

        const indent = 10 + depth * 16;
        const item = document.createElement('div');
        item.className = 'flex items-center gap-[9px] py-[9px] px-[10px] rounded-[9px] cursor-pointer hover:bg-sidebar transition-colors';
        item.style.paddingLeft = `${indent}px`;
        item.append(iconElement('folder', 18, '#C0A86E'),
            textElement('span', 'flex-1 text-[13px] text-ink truncate', node.name),
            textElement('span', 'text-[10.5px] text-text-dim font-semibold', node.pdf_count || ''));
        item.addEventListener('click', () => {
            if (window._dirPickerCallback) window._dirPickerCallback(node.path);
            closeDirPicker();
        });
        container.appendChild(item);

        if (node.children) {
            _renderDirPickerTree(node.children, filter, depth + 1);
        }
    }
}

function _treeHasMatch(nodes, filter) {
    if (!filter) return true;
    for (const n of nodes) {
        if (n.path.toLowerCase().includes(filter) || n.name.toLowerCase().includes(filter)) return true;
        if (n.children && _treeHasMatch(n.children, filter)) return true;
    }
    return false;
}

function closeDirPicker() {
    const modal = document.getElementById('dir-picker-modal');
    if (modal) modal.remove();
    window._dirPickerCallback = null;
}

function dirPickerConfirmCustom() {
    const input = document.getElementById('dir-picker-custom');
    const val = input ? input.value.trim() : '';
    if (!val) return;
    if (window._dirPickerCallback) window._dirPickerCallback(val);
    closeDirPicker();
}

// ---------------------------------------------------------------------------
// Initialize page shell
// ---------------------------------------------------------------------------
function initPage(title) {
    // Inject sidebar
    const navEl = document.getElementById('nav');
    if (navEl) navEl.innerHTML = renderNav();

    // The header and footer are now part of df-main flow, not absolute-positioned
    const headerEl = document.getElementById('header');
    if (headerEl) headerEl.innerHTML = renderHeader(title);

    const footerEl = document.getElementById('footer');
    if (footerEl) footerEl.innerHTML = renderFooter();

    // Mobile tab bar
    document.body.insertAdjacentHTML('beforeend', renderMobileTabs());

    injectResponsiveStyles();
    initDarkMode();
    updateHealthStatus();
    initToastContainer();
    initSearch();
    updateReviewBadge();
}
