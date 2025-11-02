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

