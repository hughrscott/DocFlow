const API_BASE = import.meta.env.VITE_API_BASE || '' // default proxy through Vite

export async function uploadDocument(file: File, opts: { analyze: boolean; background: boolean; dpi: number }) {
  const form = new FormData()
  form.append('file', file)
  const params = new URLSearchParams({
    analyze: String(opts.analyze),
    background: String(opts.background),
    dpi: String(opts.dpi),
  })
  const res = await fetch(`${API_BASE}/api/v1/documents/upload?${params.toString()}`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
  return res.json()
}

export async function listDocuments(page = 1, page_size = 10) {
  const res = await fetch(`${API_BASE}/api/v1/documents?page=${page}&page_size=${page_size}`)
  if (!res.ok) throw new Error('List failed')
  return res.json()
}

export async function getDocument(id: string) {
  const res = await fetch(`${API_BASE}/api/v1/documents/${id}`)
  if (!res.ok) throw new Error('Fetch failed')
  return res.json()
}

export async function getSettings() {
  const res = await fetch(`${API_BASE}/api/v1/settings`)
  if (!res.ok) throw new Error('Settings fetch failed')
  return res.json()
}

export async function updateSettings(payload: any, basicAuth?: { username: string; password: string }) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (basicAuth) {
    const token = btoa(`${basicAuth.username}:${basicAuth.password}`)
    headers['Authorization'] = `Basic ${token}`
  }
  const res = await fetch(`${API_BASE}/api/v1/settings`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`Settings update failed: ${res.status}`)
  return res.json()
}

export async function reanalyzePage(pageId: string, dpi = 150) {
  const res = await fetch(`${API_BASE}/api/v1/documents/pages/${pageId}/reanalyze?dpi=${dpi}`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error('Reanalyze failed')
  return res.json()
}

export async function correctPage(pageId: string, folder: string, filename: string) {
  const res = await fetch(`${API_BASE}/api/v1/documents/pages/${pageId}/correct`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder, filename }),
  })
  if (!res.ok) throw new Error('Correction failed')
  return res.json()
}

export async function reanalyzeDocument(documentId: string, dpi = 150, background = true) {
  const res = await fetch(`${API_BASE}/api/v1/documents/${documentId}/reanalyze?dpi=${dpi}&background=${background}`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error('Document reanalyze failed')
  return res.json()
}

export async function confirmDocumentClass(documentId: string, document_class: string, rationale?: string) {
  const res = await fetch(`${API_BASE}/api/v1/documents/${documentId}/confirm_class`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_class, rationale }),
  })
  if (!res.ok) throw new Error('Confirm class failed')
  return res.json()
}

export async function applySequenceProposed(documentId: string, sequenceId: string) {
  const res = await fetch(`${API_BASE}/api/v1/documents/${documentId}/sequences/${sequenceId}/apply_proposed`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error('Sequence apply failed')
  return res.json()
}

export async function revertPageMove(pageId: string, auditId?: string) {
  const payload = auditId ? { audit_id: auditId } : {}
  const res = await fetch(`${API_BASE}/api/v1/documents/pages/${pageId}/moves/revert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error('Revert move failed')
  return res.json()
}

export async function getProviderReadiness() {
  const res = await fetch(`${API_BASE}/api/v1/providers/readiness`)
  if (!res.ok) throw new Error('Provider readiness failed')
  return res.json()
}

export async function getFolderSuggestions() {
  const res = await fetch(`${API_BASE}/api/v1/documents/folder_suggestions`)
  if (!res.ok) throw new Error('Folder suggestions failed')
  return res.json()
}

export async function refreshFolderSuggestions() {
  const res = await fetch(`${API_BASE}/api/v1/documents/folder_suggestions/refresh`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error('Folder suggestions refresh failed')
  return res.json()
}
