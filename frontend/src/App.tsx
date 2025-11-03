import React, { useEffect, useState } from 'react'
import { uploadDocument, listDocuments, getDocument } from './services/api'
import UploadArea from './components/UploadArea'
import SettingsView from './components/SettingsView'
import { useToast } from './components/Toast'

type DocBrief = {
  id: string
  original_filename: string
  upload_date: string
  total_pages: number
  status: string
}

type PageResult = {
  page_number: number
  document_type: string
  institution?: string | null
  date?: string | null
  confidence_score: number
  folder: string
  filename: string
  proposed_folder?: string | null
  proposed_filename?: string | null
  provider_used?: string | null
  model_used?: string | null
  success: boolean
  error?: string | null
  page_id?: string | null
}

type DocDetail = {
  document_id: string
  status: string
  total_pages: number
  pages_done: number
  last_error?: string | null
  pages: PageResult[]
  doc_level?: {
    document_class?: string
    confidence?: number
    issuer?: string | null
    recipient?: string | null
    period?: string | null
    identifiers?: Record<string, any>
    salient_facts?: { label: string; value: any }[]
    proposed_filename?: string | null
    rationale?: string | null
  } | null
}

export default function App() {
  const [docs, setDocs] = useState<DocBrief[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedDoc, setSelectedDoc] = useState<DocDetail | null>(null)
  const { show } = useToast()

  async function refresh() {
    setLoading(true)
    try {
      const res = await listDocuments(1, 10)
      setDocs(res.items)
    } catch (e: any) {
      show(e?.message ?? 'Failed to load documents', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function handleUpload(file: File, opts: { analyze: boolean; background: boolean; dpi: number }) {
    try {
      const out = await uploadDocument(file, opts)
      show(`Uploaded: ${out.document_id}`, 'success')
      // If background, poll a couple of times
      if (opts.background) {
        for (let i = 0; i < 10; i++) {
          const d = await getDocument(out.document_id)
          setSelectedDoc(d)
          if (d.status === 'completed' || d.status === 'failed') break
          await new Promise((r) => setTimeout(r, 500))
        }
      } else {
        const d = await getDocument(out.document_id)
        setSelectedDoc(d)
      }
      await refresh()
    } catch (e: any) {
      show(e?.message ?? 'Upload failed', 'error')
    }
  }

  return (
    <div className="container">
      <header>
        <h1>DocFlow</h1>
        <p>AI‑assisted document uploads and organization</p>
      </header>

      <section>
        <h2>Upload</h2>
        <UploadArea onUpload={handleUpload} />
        
      </section>

      <section>
        <h2>Recent Documents</h2>
        {loading ? (
          <div>Loading…</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Filename</th>
                <th>Uploaded</th>
                <th>Status</th>
                <th>Pages</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>{d.original_filename}</td>
                  <td>{new Date(d.upload_date).toLocaleString()}</td>
                  <td>{d.status}</td>
                  <td>{d.total_pages}</td>
                  <td>
                    <button onClick={async () => {
                      try {
                        const detail = await getDocument(d.id)
                        if (!detail.pages) detail.pages = []
                        setSelectedDoc(detail)
                      } catch (e: any) {
                        show(e?.message ?? 'Failed to load document', 'error')
                      }
                    }}>Details</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Settings</h2>
        <SettingsView />
      </section>

      {selectedDoc && (
        <section>
          <h2>Document Details</h2>
          <DocActions docId={selectedDoc.document_id} onUpdated={async ()=> setSelectedDoc(await getDocument(selectedDoc.document_id))} />
          {selectedDoc.doc_level && (
            <div className="card" style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Essentials</div>
              <div><b>Issuer:</b> {selectedDoc.doc_level.issuer || '—'}</div>
              <div><b>Class:</b> {selectedDoc.doc_level.document_class || '—'} {selectedDoc.doc_level.confidence != null ? `(${Math.round((selectedDoc.doc_level.confidence||0)*100)}% conf)` : ''}</div>
              <div><b>Period:</b> {selectedDoc.doc_level.period || '—'}</div>
              {selectedDoc.doc_level.proposed_filename && (
                <div><b>Suggested Filename:</b> {selectedDoc.doc_level.proposed_filename}</div>
              )}
              {selectedDoc.doc_level?.salient_facts && selectedDoc.doc_level.salient_facts.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <b>Key Facts:</b>
                  <ul>
                    {selectedDoc.doc_level.salient_facts.slice(0,5).map((sf, i)=> (
                      <li key={i}>{sf.label}: {String(sf.value)}</li>
                    ))}
                  </ul>
                </div>
              )}
              <ConfirmClassControls docId={selectedDoc.document_id} currentClass={selectedDoc.doc_level.document_class || ''} onUpdated={async ()=> setSelectedDoc(await getDocument(selectedDoc.document_id))} />
            </div>
          )}
          <div className="card">
            <div><b>ID:</b> {selectedDoc.document_id}</div>
            <div><b>Status:</b> {selectedDoc.status}</div>
            <div>
              <b>Progress:</b> {selectedDoc.pages_done}/{selectedDoc.total_pages}
            </div>
            {selectedDoc.last_error && <div className="error">Last error: {selectedDoc.last_error}</div>}
          </div>

          <h3>Pages</h3>
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>Type</th>
                <th>Institution</th>
                <th>Date</th>
                <th>Conf.</th>
                <th>Current Folder</th>
                <th>Current Filename</th>
                <th>Proposed Folder</th>
                <th>Proposed Filename</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {selectedDoc.pages?.map((p) => (
                <tr key={p.page_id || p.page_number}>
                  <td>{p.page_number}</td>
                  <td>{p.document_type || 'unknown'}</td>
                  <td>{p.institution || ''}</td>
                  <td>{p.date || ''}</td>
                  <td>{(p.confidence_score ?? 0).toFixed(2)}</td>
                  <td>{p.folder}</td>
                  <td>{p.filename}</td>
                  <td>{p.proposed_folder || ''}</td>
                  <td>{p.proposed_filename || ''}</td>
                  <td>{p.provider_used || ''}</td>
                  <td>{p.model_used || ''}</td>
                  <td>{p.success ? 'ok' : (p.error ? `err: ${p.error}` : 'pending')}</td>
                  <td>
                    <PageActions page={p} onUpdated={async ()=> setSelectedDoc(await getDocument(selectedDoc.document_id))} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}

function PageActions({ page, onUpdated }: { page: any, onUpdated: () => Promise<void> }) {
  const [dpi, setDpi] = useState(120)
  const [folder, setFolder] = useState(page.folder)
  const [filename, setFilename] = useState(page.filename)
  const [busy, setBusy] = useState(false)
  const { show } = useToast()

  async function doReanalyze() {
    if (!page.page_id) return
    setBusy(true)
    try {
      const { reanalyzePage } = await import('./services/api')
      await reanalyzePage(page.page_id, dpi)
      show(`Re-analyzed page ${page.page_number}`, 'success')
      await onUpdated()
    } catch (e: any) {
      show(e?.message ?? 'Re-analyze failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function doCorrect() {
    if (!page.page_id) return
    setBusy(true)
    try {
      const { correctPage } = await import('./services/api')
      await correctPage(page.page_id, folder, filename)
      show(`Moved page ${page.page_number}`, 'success')
      await onUpdated()
    } catch (e: any) {
      show(e?.message ?? 'Correction failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function moveToProposed() {
    if (!page.page_id) return
    if (!page.proposed_folder && !page.proposed_filename) return
    setBusy(true)
    try {
      const { correctPage } = await import('./services/api')
      const nextFolder = page.proposed_folder || page.folder
      const nextFilename = page.proposed_filename || page.filename
      await correctPage(page.page_id, nextFolder, nextFilename)
      show(`Applied proposal for page ${page.page_number}`,'success')
      // sync local inputs as well
      setFolder(nextFolder)
      setFilename(nextFilename)
      await onUpdated()
    } catch (e: any) {
      show(e?.message ?? 'Apply proposal failed','error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
      <button onClick={doReanalyze} disabled={busy || !page.page_id}>Re‑analyze</button>
      <label style={{ color: '#a8b2d1' }}>DPI <input type="number" min={72} max={300} value={dpi} onChange={(e)=> setDpi(parseInt(e.target.value||'120',10))} style={{ width: 70 }} /></label>
      <button onClick={()=> { if (page.proposed_folder) setFolder(page.proposed_folder); if (page.proposed_filename) setFilename(page.proposed_filename); }} disabled={busy || (!page.proposed_folder && !page.proposed_filename)}>Use Proposed</button>
      <button onClick={moveToProposed} disabled={busy || !page.page_id || (!page.proposed_folder && !page.proposed_filename)}>Move to Proposed</button>
      <label style={{ color: '#a8b2d1' }}>Folder <input type="text" value={folder} onChange={(e)=> setFolder(e.target.value)} style={{ width: 200 }} /></label>
      <label style={{ color: '#a8b2d1' }}>Filename <input type="text" value={filename} onChange={(e)=> setFilename(e.target.value)} style={{ width: 220 }} /></label>
      <button onClick={doCorrect} disabled={busy || !page.page_id}>Correct</button>
    </div>
  )
}

function ConfirmClassControls({ docId, currentClass, onUpdated }: { docId: string; currentClass: string; onUpdated: () => Promise<void> }) {
  const [value, setValue] = useState(currentClass || '')
  const [busy, setBusy] = useState(false)
  const { show } = useToast()

  async function onConfirm() {
    if (!value) return
    setBusy(true)
    try {
      const { confirmDocumentClass } = await import('./services/api')
      await confirmDocumentClass(docId, value)
      show('Document class confirmed', 'success')
      await onUpdated()
    } catch (e: any) {
      show(e?.message ?? 'Confirm class failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="controls" style={{ marginTop: 8, gap: 8 }}>
      <label>Confirm Class: <input type="text" value={value} onChange={(e)=> setValue(e.target.value)} style={{ width: 240 }} placeholder="e.g., legal, utility_bill, invoice" /></label>
      <button onClick={onConfirm} disabled={busy || !value}>Confirm</button>
    </div>
  )
}
function DocActions({ docId, onUpdated }: { docId: string; onUpdated: () => Promise<void> }) {
  const [dpi, setDpi] = useState(120)
  const [busy, setBusy] = useState(false)
  const { show } = useToast()

  async function doReanalyzeAll() {
    setBusy(true)
    try {
      const { reanalyzeDocument } = await import('./services/api')
      await reanalyzeDocument(docId, dpi, true)
      show('Re-analysis scheduled', 'info')
      // background; poll a few times
      for (let i = 0; i < 12; i++) {
        await onUpdated()
        await new Promise((r)=> setTimeout(r, 500))
      }
    } catch (e: any) {
      show(e?.message ?? 'Document re-analyze failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="controls">
      <button onClick={doReanalyzeAll} disabled={busy}>Re‑analyze All (background)</button>
      <label style={{ color: '#a8b2d1' }}>DPI <input type="number" min={72} max={300} value={dpi} onChange={(e)=> setDpi(parseInt(e.target.value||'120',10))} style={{ width: 70 }} /></label>
    </div>
  )
}
