// DocFlow shared UI components and utilities

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

function renderNav() {
    const active = currentView();
    const navItems = VIEWS.map(v => {
        const isActive = v.id === active;
        const cls = isActive
            ? 'flex items-center gap-3 px-3 py-2.5 rounded-lg text-slate-900 dark:text-slate-100 font-bold border-r-4 border-slate-900 dark:border-slate-100 transition-colors'
            : 'flex items-center gap-3 px-3 py-2.5 rounded-lg text-slate-500 dark:text-slate-400 hover:bg-slate-200/50 dark:hover:bg-slate-700/50 transition-colors';
        const fillStyle = isActive ? "font-variation-settings: 'FILL' 1;" : '';
        const ariaCurrent = isActive ? ' aria-current="page"' : '';
        return `<a href="${v.path}" class="${cls}"${ariaCurrent}>
            <span class="material-symbols-outlined" style="${fillStyle}">${v.icon}</span>
            <span class="nav-label font-medium">${v.label}</span>
        </a>`;
    }).join('');

    return `
    <aside id="sidebar" class="h-screen w-64 lg:w-64 md:w-16 fixed left-0 top-0 flex flex-col py-8 px-4 md:px-2 lg:px-4 bg-slate-100 dark:bg-slate-800 z-50 transition-all duration-200" aria-label="Main navigation">
        <div class="mb-10 px-2 md:hidden lg:block">
            <h1 class="text-xl font-black text-slate-900 dark:text-slate-100 font-headline tracking-tight">DocFlow</h1>
            <p class="text-[10px] font-bold tracking-widest uppercase text-slate-500 mt-1">The Digital Archivist</p>
        </div>
        <div class="mb-10 px-2 hidden md:block lg:hidden text-center">
            <h1 class="text-lg font-black text-slate-900 dark:text-slate-100 font-headline">DF</h1>
        </div>
        <button onclick="globalUpload()" role="button" aria-label="Upload PDF" class="mb-8 flex items-center justify-center gap-2 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 py-3 rounded-lg font-bold text-sm hover:scale-[0.98] transition-transform active:scale-95">
            <span class="material-symbols-outlined text-sm">add_circle</span>
            <span class="nav-label">Scan Mail</span>
        </button>
        <nav role="navigation" class="space-y-1 flex-1">${navItems}</nav>
        <div class="mt-auto border-t border-slate-200/50 dark:border-slate-700/50 pt-4">
            <button onclick="toggleDarkMode()" class="flex items-center gap-3 px-3 py-2 rounded-lg text-slate-500 dark:text-slate-400 hover:bg-slate-200/50 dark:hover:bg-slate-700/50 transition-colors w-full">
                <span class="material-symbols-outlined" id="dark-mode-icon">dark_mode</span>
                <span class="nav-label text-sm font-medium" id="dark-mode-label">Dark Mode</span>
            </button>
            <a href="#" class="flex items-center gap-3 px-3 py-3 rounded-lg text-slate-500 dark:text-slate-400 hover:bg-slate-200/50 dark:hover:bg-slate-700/50 transition-colors">
                <span class="material-symbols-outlined">help_outline</span>
                <span class="nav-label text-sm font-medium">Support</span>
            </a>
        </div>
    </aside>`;
}

function renderHeader(title) {
    return `
    <header class="flex justify-between items-center w-full px-6 py-4 ml-16 lg:ml-64 max-w-[calc(100%-4rem)] lg:max-w-[calc(100%-16rem)] sticky top-0 bg-slate-50/80 dark:bg-slate-900/80 backdrop-blur-xl z-40 shadow-sm transition-all duration-200">
        <div class="flex items-center gap-8">
            <h2 class="text-lg font-bold tracking-tight text-slate-900 dark:text-slate-100 font-headline">${title}</h2>
        </div>
        <div class="flex items-center gap-4">
            <div class="relative" id="search-container">
                <input id="search-input" aria-label="Search archive" class="bg-surface-container-low dark:bg-slate-800 border-none rounded-full px-4 py-2 text-sm w-64 focus:ring-2 focus:ring-primary/15 outline-none transition-all dark:text-slate-200" placeholder="Search archive..." type="text" autocomplete="off"/>
                <span class="material-symbols-outlined absolute right-3 top-2 text-slate-400 text-sm">search</span>
                <div id="search-results" class="absolute top-full mt-2 right-0 w-96 bg-white rounded-xl shadow-2xl border border-slate-200 hidden max-h-80 overflow-y-auto z-50"></div>
            </div>
            <div class="flex gap-2 text-slate-600">
                <span class="material-symbols-outlined cursor-pointer hover:text-primary transition-colors">notifications</span>
            </div>
        </div>
    </header>`;
}

function renderFooter() {
    return `
    <footer id="status-bar" role="contentinfo" class="fixed bottom-0 right-0 w-[calc(100%-4rem)] lg:w-[calc(100%-16rem)] h-8 bg-slate-100/50 dark:bg-slate-800/50 backdrop-blur-md border-t border-slate-200/20 dark:border-slate-700 flex justify-between items-center px-6 z-50 transition-all duration-200">
        <div class="flex items-center gap-4">
            <p class="text-xs font-medium tracking-wide uppercase text-slate-600 dark:text-slate-400">
                Watch Folder: <span class="text-emerald-600 font-bold" id="watch-status">Checking...</span>
                • LLM Status: <span class="text-emerald-600 font-bold" id="llm-status">Checking...</span>
            </p>
        </div>
        <div class="flex gap-4">
            <a href="#" class="text-xs font-medium tracking-wide uppercase text-slate-400 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200 transition-colors">System Logs</a>
        </div>
    </footer>`;
}

// ---------------------------------------------------------------------------
// Responsive styles injected once
// ---------------------------------------------------------------------------
function injectResponsiveStyles() {
    if (document.getElementById('docflow-responsive-css')) return;
    const style = document.createElement('style');
    style.id = 'docflow-responsive-css';
    style.textContent = `
        /* Collapse nav labels on medium screens */
        @media (min-width: 768px) and (max-width: 1023px) {
            .nav-label { display: none; }
            #sidebar { width: 4rem; padding-left: 0.5rem; padding-right: 0.5rem; }
            #sidebar nav a { justify-content: center; padding-left: 0; padding-right: 0; }
            #sidebar button { padding-left: 0; padding-right: 0; }
        }
        /* Below 768px: hide sidebar entirely, show bottom tab bar */
        @media (max-width: 767px) {
            #sidebar { display: none; }
            #mobile-tabs { display: flex !important; }
            main { margin-left: 0 !important; padding-bottom: 4rem !important; }
            header { margin-left: 0 !important; max-width: 100% !important; }
            #status-bar { width: 100% !important; bottom: 3.5rem !important; }
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
        const cls = isActive ? 'text-slate-900' : 'text-slate-400';
        const fill = isActive ? "font-variation-settings: 'FILL' 1;" : '';
        return `<a href="${v.path}" class="flex flex-col items-center gap-0.5 ${cls}">
            <span class="material-symbols-outlined text-xl" style="${fill}">${v.icon}</span>
            <span class="text-[10px] font-bold">${v.label.split(' ')[0]}</span>
        </a>`;
    }).join('');

    return `<nav id="mobile-tabs" class="fixed bottom-0 left-0 right-0 h-14 bg-white border-t border-slate-200 justify-around items-center z-50 hidden">${tabs}</nav>`;
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

    // Close on click outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('#search-container')) results.classList.add('hidden');
    });

    // Keyboard shortcut: / to focus search
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
        if (!resp.ok) {
            results.classList.add('hidden');
            return;
        }
        const data = await resp.json();
        if (!data.results || data.results.length === 0) {
            results.innerHTML = '<div class="p-4 text-sm text-on-surface-variant text-center">No results found</div>';
            results.classList.remove('hidden');
            return;
        }
        results.innerHTML = data.results.map(r => `
            <a href="${r.url || '#'}" class="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors border-b border-slate-100 last:border-0">
                <span class="material-symbols-outlined text-slate-400 text-sm">${r.icon || 'description'}</span>
                <div class="min-w-0 flex-1">
                    <p class="text-sm font-medium text-slate-900 truncate">${r.filename || r.name}</p>
                    <p class="text-xs text-on-surface-variant truncate">${r.directory || r.path || ''}</p>
                </div>
                ${r.confidence != null ? confidenceBadge(r.confidence) : ''}
            </a>
        `).join('');
        results.classList.remove('hidden');
    } catch (e) {
        results.classList.add('hidden');
    }
}

// ---------------------------------------------------------------------------
// Health status
// ---------------------------------------------------------------------------
async function updateHealthStatus() {
    try {
        const resp = await fetch('/api/health');
        const data = await resp.json();
        const watchEl = document.getElementById('watch-status');
        const llmEl = document.getElementById('llm-status');
        if (watchEl) watchEl.textContent = data.watch_folder ? 'Active' : 'Not Set';
        if (llmEl) {
            llmEl.textContent = data.llm_ready ? 'Ready' : 'No API Key';
            llmEl.className = data.llm_ready ? 'text-emerald-600 font-bold' : 'text-red-500 font-bold';
        }
    } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Global upload (works from any page)
// ---------------------------------------------------------------------------
function globalUpload() {
    // If we're on the dashboard, use its file input directly
    const dashInput = document.getElementById('upload-input');
    if (dashInput) {
        dashInput.click();
        return;
    }
    // On other pages, use a global file input that uploads then redirects
    let input = document.getElementById('global-upload-input');
    if (!input) {
        input = document.createElement('input');
        input.type = 'file';
        input.id = 'global-upload-input';
        input.accept = '.pdf';
        input.multiple = true;
        input.className = 'hidden';
        input.addEventListener('change', async (e) => {
            const files = Array.from(e.target.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
            if (files.length === 0) return;
            for (const file of files) {
                try {
                    const formData = new FormData();
                    formData.append('file', file);
                    const uploadResp = await fetch('/api/upload', { method: 'POST', body: formData });
                    if (!uploadResp.ok) { showToast('Upload failed', 'error'); continue; }
                    const upload = await uploadResp.json();
                    const procResp = await fetch('/api/process', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: upload.path }),
                    });
                    if (!procResp.ok) { showToast('Failed to start processing', 'error'); continue; }
                    const proc = await procResp.json();
                    sessionStorage.setItem('docflow_active_job', proc.job_id);
                    showToast('Processing started — redirecting to dashboard', 'info', 2000);
                    setTimeout(() => { window.location = '/'; }, 500);
                    return;
                } catch (e) {
                    showToast('Network error', 'error');
                }
            }
            input.value = '';
        });
        document.body.appendChild(input);
    }
    input.click();
}

// ---------------------------------------------------------------------------
// Badges
// ---------------------------------------------------------------------------
function confidenceBadge(confidence) {
    const pct = Math.round(confidence * 100);
    if (confidence >= 0.9) {
        return `<span class="bg-secondary-container text-on-secondary-container text-[11px] font-bold px-2 py-0.5 rounded-full">${pct}% Match</span>`;
    } else if (confidence >= 0.75) {
        return `<span class="bg-primary-fixed text-primary text-[11px] font-bold px-2 py-0.5 rounded-full">${pct}% Match</span>`;
    } else if (confidence >= 0.6) {
        return `<span class="bg-tertiary-fixed text-on-tertiary-container text-[11px] font-bold px-2 py-0.5 rounded-full">${pct}% Uncertain</span>`;
    } else {
        return `<span class="bg-red-100 text-red-700 text-[11px] font-bold px-2 py-0.5 rounded-full">${pct}% Low</span>`;
    }
}

function ruleBadge(rule) {
    if (rule === 'llm_suggested') {
        return `<span class="flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-secondary"></span><span class="text-xs text-slate-600 font-medium">AI Suggested</span></span>`;
    } else if (rule === 'none') {
        return `<span class="flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-red-400"></span><span class="text-xs text-slate-600 font-medium">Unmatched</span></span>`;
    }
    return `<span class="flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-primary"></span><span class="text-xs text-slate-600 font-medium">Rule: ${rule}</span></span>`;
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

function showToast(message, type = 'error', duration = 5000) {
    initToastContainer();
    const container = document.getElementById('toast-container');

    const colors = {
        error: 'bg-red-50 border-red-200 text-red-800',
        success: 'bg-emerald-50 border-emerald-200 text-emerald-800',
        warning: 'bg-amber-50 border-amber-200 text-amber-800',
        info: 'bg-blue-50 border-blue-200 text-blue-800',
    };
    const icons = {
        error: 'error',
        success: 'check_circle',
        warning: 'warning',
        info: 'info',
    };

    const toast = document.createElement('div');
    toast.className = `pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-lg border shadow-lg ${colors[type] || colors.info} transform translate-x-full opacity-0 transition-all duration-300`;
    toast.innerHTML = `
        <span class="material-symbols-outlined text-sm">${icons[type] || icons.info}</span>
        <span class="text-sm font-medium flex-1">${message}</span>
        <button onclick="this.parentElement.remove()" class="opacity-50 hover:opacity-100 transition-opacity">
            <span class="material-symbols-outlined text-sm">close</span>
        </button>
    `;

    container.appendChild(toast);
    requestAnimationFrame(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    });

    if (duration > 0) {
        setTimeout(() => {
            toast.classList.add('translate-x-full', 'opacity-0');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }
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
// Initialize page shell
// ---------------------------------------------------------------------------
function initPage(title) {
    document.getElementById('nav').innerHTML = renderNav();
    document.getElementById('header').innerHTML = renderHeader(title);
    document.getElementById('footer').innerHTML = renderFooter();
    // Add mobile tab bar
    document.body.insertAdjacentHTML('beforeend', renderMobileTabs());
    injectResponsiveStyles();
    initDarkMode();
    updateHealthStatus();
    initToastContainer();
    initSearch();
}
