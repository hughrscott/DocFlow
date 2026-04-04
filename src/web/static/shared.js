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
            ? 'flex items-center gap-3 px-3 py-2.5 rounded-lg text-slate-900 font-bold border-r-4 border-slate-900 transition-colors'
            : 'flex items-center gap-3 px-3 py-2.5 rounded-lg text-slate-500 hover:bg-slate-200/50 transition-colors';
        const fillStyle = isActive ? "font-variation-settings: 'FILL' 1;" : '';
        return `<a href="${v.path}" class="${cls}">
            <span class="material-symbols-outlined" style="${fillStyle}">${v.icon}</span>
            <span class="font-medium">${v.label}</span>
        </a>`;
    }).join('');

    return `
    <aside class="h-screen w-64 fixed left-0 top-0 flex flex-col py-8 px-4 bg-slate-100 z-50">
        <div class="mb-10 px-2">
            <h1 class="text-xl font-black text-slate-900 font-headline tracking-tight">DocFlow</h1>
            <p class="text-[10px] font-bold tracking-widest uppercase text-slate-500 mt-1">The Digital Archivist</p>
        </div>
        <button onclick="document.getElementById('upload-input')?.click()" class="mb-8 flex items-center justify-center gap-2 bg-slate-900 text-white py-3 rounded-lg font-bold text-sm hover:scale-[0.98] transition-transform active:scale-95">
            <span class="material-symbols-outlined text-sm">add_circle</span>
            Scan Mail
        </button>
        <nav class="space-y-1 flex-1">${navItems}</nav>
        <div class="mt-auto border-t border-slate-200/50 pt-4">
            <a href="#" class="flex items-center gap-3 px-3 py-3 rounded-lg text-slate-500 hover:bg-slate-200/50 transition-colors">
                <span class="material-symbols-outlined">help_outline</span>
                <span class="text-sm font-medium">Support</span>
            </a>
        </div>
    </aside>`;
}

function renderHeader(title) {
    return `
    <header class="flex justify-between items-center w-full px-6 py-4 ml-64 max-w-[calc(100%-16rem)] sticky top-0 bg-slate-50/80 backdrop-blur-xl z-40 shadow-sm">
        <div class="flex items-center gap-8">
            <h2 class="text-lg font-bold tracking-tight text-slate-900 font-headline">${title}</h2>
        </div>
        <div class="flex items-center gap-4">
            <div class="relative">
                <input class="bg-surface-container-low border-none rounded-full px-4 py-2 text-sm w-64 focus:ring-2 focus:ring-primary/15 outline-none transition-all" placeholder="Search archive..." type="text"/>
                <span class="material-symbols-outlined absolute right-3 top-2 text-slate-400 text-sm">search</span>
            </div>
            <div class="flex gap-2 text-slate-600">
                <span class="material-symbols-outlined cursor-pointer hover:text-primary transition-colors">notifications</span>
            </div>
        </div>
    </header>`;
}

function renderFooter() {
    return `
    <footer id="status-bar" class="fixed bottom-0 right-0 w-[calc(100%-16rem)] h-8 bg-slate-100/50 backdrop-blur-md border-t border-slate-200/20 flex justify-between items-center px-6 z-50">
        <div class="flex items-center gap-4">
            <p class="text-xs font-medium tracking-wide uppercase text-slate-600">
                Watch Folder: <span class="text-emerald-600 font-bold" id="watch-status">Checking...</span>
                • LLM Status: <span class="text-emerald-600 font-bold" id="llm-status">Checking...</span>
            </p>
        </div>
        <div class="flex gap-4">
            <a href="#" class="text-xs font-medium tracking-wide uppercase text-slate-400 hover:text-slate-900 transition-colors">System Logs</a>
        </div>
    </footer>`;
}

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

function confidenceBadge(confidence) {
    if (confidence >= 0.9) {
        return `<span class="bg-secondary-container text-on-secondary-container text-[11px] font-bold px-2 py-0.5 rounded-full">${Math.round(confidence * 100)}% Match</span>`;
    } else if (confidence >= 0.75) {
        return `<span class="bg-primary-fixed text-primary text-[11px] font-bold px-2 py-0.5 rounded-full">${Math.round(confidence * 100)}% Match</span>`;
    } else {
        return `<span class="bg-tertiary-fixed text-on-tertiary-container text-[11px] font-bold px-2 py-0.5 rounded-full">${Math.round(confidence * 100)}% Low Conf</span>`;
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

// Initialize page shell
function initPage(title) {
    document.getElementById('nav').innerHTML = renderNav();
    document.getElementById('header').innerHTML = renderHeader(title);
    document.getElementById('footer').innerHTML = renderFooter();
    updateHealthStatus();
}
