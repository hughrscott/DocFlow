import React, { useEffect, useMemo, useState, useRef } from 'react'
import { uploadDocument, listDocuments, getDocument } from './services/api'
import UploadArea from './components/UploadArea'
import SettingsView from './components/SettingsView'
import CarouselReview from './components/CarouselReview'
import { useToast } from './components/Toast'

type DocBrief = {
  id: string
  original_filename: string
  upload_date: string
  total_pages: number
  status: string
  pages_done: number
  pages_failed: number
  last_error?: string | null
  last_error_at?: string | null
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
  sequence_id?: string | null
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
  last_error_at?: string | null
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

import React, { useEffect, useMemo, useState, useRef } from 'react'
import { uploadDocument, listDocuments, getDocument } from './services/api'
import UploadArea from './components/UploadArea'
import SettingsView from './components/SettingsView'
import CarouselReview from './components/CarouselReview'
import { useToast } from './components/Toast'

type DocBrief = {
  id: string
  original_filename: string
  upload_date: string
  total_pages: number
  status: string
  pages_done: number
  pages_failed: number
  last_error?: string | null
  last_error_at?: string | null
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
  sequence_id?: string | null
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
  last_error_at?: string | null
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
  const [sequenceBusy, setSequenceBusy] = useState<string | null>(null)
  const [readiness, setReadiness] = useState<any | null>(null)
  const [folderSuggestions, setFolderSuggestions] = useState<any[]>([])
  const [folderSuggestionsUpdatedAt, setFolderSuggestionsUpdatedAt] = useState<string | null>(null)
  const [suggestionsBusy, setSuggestionsBusy] = useState(false)
  const [showCarousel, setShowCarousel] = useState(false)
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  const { show } = useToast()

  // Poll for document updates when there are processing documents
  useEffect(() => {
    // Check if there are any documents with status 'processing'
    const hasProcessingDocs = docs.some(doc => doc.status === 'processing');

    if (hasProcessingDocs && !intervalRef.current) {
      // Start polling every 2 seconds
      intervalRef.current = setInterval(() => {
        refresh(); // This will update the docs state
      }, 2000);
    } else if (!hasProcessingDocs && intervalRef.current) {
      // Stop polling if no processing docs
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    // Cleanup interval on unmount
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [docs]); // Re-run when docs change

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

  async function loadReadiness() {
    try {
      const { getProviderReadiness } = await import('./services/api')
      const data = await getProviderReadiness()
      setReadiness(data)
    } catch (e: any) {
      show(e?.message ?? 'Failed to load provider readiness', 'error')
    }
  }

  async function loadFolderSuggestions(forceRefresh = false) {
    try {
      if (forceRefresh) setSuggestionsBusy(true)
      const {
        getFolderSuggestions,
        refreshFolderSuggestions,
      } = await import('./services/api')
      const data = forceRefresh ? await refreshFolderSuggestions() : await getFolderSuggestions()
      setFolderSuggestions(data?.suggestions || [])
      setFolderSuggestionsUpdatedAt(data?.generated_at || null)
    } catch (e: any) {
      show(e?.message ?? 'Failed to load folder suggestions', 'error')
    } finally {
      if (forceRefresh) setSuggestionsBusy(false)
    }
  }

  useEffect(() => {
    refresh()
    loadReadiness()
    loadFolderSuggestions(false)
  }, [])

  useEffect(() => {
    setSequenceBusy(null)
  }, [selectedDoc?.document_id])

  // Poll for selected document updates when it's processing
  useEffect(() => {
    let interval: NodeJS.Timeout | null = null;

    if (selectedDoc && selectedDoc.status === 'processing' && selectedDoc.document_id) {
      // Start polling every 2 seconds for document details
      interval = setInterval(async () => {
        try {
          const detail = await getDocument(selectedDoc.document_id)
          if (!detail.pages) detail.pages = []
          setSelectedDoc(detail)
        } catch (e: any) {
          console.error('Failed to refresh document details:', e)
        }
      }, 2000);
    }

    // Cleanup interval on unmount or when document stops processing
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [selectedDoc?.document_id, selectedDoc?.status]) // Re-run when selected doc ID or status changes

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

  const sequenceEntries = useMemo(() => {
    if (!selectedDoc?.pages) return []
    const grouped = new Map<string, PageResult[]>()
    selectedDoc.pages.forEach((p) => {
      if (!p.sequence_id) return
      if (!grouped.has(p.sequence_id)) grouped.set(p.sequence_id, [])
      grouped.get(p.sequence_id)!.push(p)
    })
    return Array.from(grouped.entries()).map(([sequenceId, pages]) => ({
      sequenceId,
      pages: pages.slice().sort((a, b) => a.page_number - b.page_number),
    }))
  }, [selectedDoc])

  async function applySequence(seqId: string) {
    if (!selectedDoc) return
    setSequenceBusy(seqId)
    try {
      const { applySequenceProposed } = await import('./services/api')
      await applySequenceProposed(selectedDoc.document_id, seqId)
      show(`Applied proposals for ${seqId}`, 'success')
      const detail = await getDocument(selectedDoc.document_id)
      if (!detail.pages) detail.pages = []
      setSelectedDoc(detail)
      await refresh()
    } catch (e: any) {
      show(e?.message ?? 'Failed to apply sequence', 'error')
    } finally {
      setSequenceBusy(null)
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
                  <td>
                    {d.pages_done}/{d.total_pages}
                    {d.pages_failed > 0 ? (
                      <span className="error" style={{ display: 'block', fontSize: 12 }}>
                        {d.pages_failed} failed
                      </span>
                    ) : null}
                  </td>
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

      <section>
        <h2>Provider Status</h2>
        {readiness ? (
          <div className="card">
            <div>Vision Provider: <b>{readiness.vision_provider}</b></div>
            <div>Text Provider: <b>{readiness.text_provider}</b></div>
            <table className="table" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Enabled</th>
                  <th>Configured</th>
                  <th>Reachable</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {Object.values(readiness.providers || {}).map((p: any) => (
                  <tr key={p.name}>
                    <td>{p.name}</td>
                    <td>{p.enabled ? 'yes' : 'no'}</td>
                    <td>{p.configured ? 'yes' : 'no'}</td>
                    <td style={{ color: p.reachable ? '#2ecc71' : '#e67e22' }}>
                      {p.reachable ? 'online' : 'unreachable'}
                    </td>
                    <td>
                      {Object.entries(p.details || {}).map(([k, v]) => (
                        <div key={k}>{k}: {String(v)}</div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="card">Loading provider readiness…</div>
        )}
      </section>

      <section>
        <h2>Folder Suggestions</h2>
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>Last analyzed: {folderSuggestionsUpdatedAt ? new Date(folderSuggestionsUpdatedAt).toLocaleString() : 'n/a'}</div>
            <button onClick={() => loadFolderSuggestions(true)} disabled={suggestionsBusy}>
              {suggestionsBusy ? 'Refreshing…' : 'Refresh Analysis'}
            </button>
          </div>
          {folderSuggestions.length === 0 ? (
            <div style={{ marginTop: 8 }}>No suggestions yet.</div>
          ) : (
            <table className="table" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th>Path</th>
                  <th>Depth</th>
                  <th>Files</th>
                  <th>Subfolders</th>
                </tr>
              </thead>
              <tbody>
                {folderSuggestions.slice(0, 8).map((s) => (
                  <tr key={s.path}>
                    <td>{s.path}</td>
                    <td>{s.depth}</td>
                    <td>{s.file_count}</td>
                    <td>{(s.subfolders || []).join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
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
          {sequenceEntries.length > 0 && (
            <div className="card">
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Sequences</div>
              <table className="table">
                <thead>
                  <tr>
                    <th>Sequence</th>
                    <th>Pages</th>
                    <th>Sample Proposed Folder</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {sequenceEntries.map(({ sequenceId, pages }) => {
                    const displayProposal = pages.find((p) => p.proposed_folder || p.proposed_filename)
                    const canApply = pages.every((p) => p.proposed_folder && p.proposed_filename)
                    return (
                      <tr key={sequenceId}>
                        <td>{sequenceId}</td>
                        <td>{pages.map((p) => p.page_number).join(', ')}</td>
                        <td>
                          {displayProposal ? (
                            <>
                              <div>{displayProposal.proposed_folder}</div>
                              <div>{displayProposal.proposed_filename}</div>
                            </>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td>
                          <button
                            onClick={() => applySequence(sequenceId)}
                            disabled={!canApply || sequenceBusy === sequenceId}
                          >
                            Apply Proposed ({pages.length})
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="card">
            <div><b>ID:</b> {selectedDoc.document_id}</div>
            <div><b>Status:</b> {selectedDoc.status}</div>
            <div>
              <b>Progress:</b> {selectedDoc.pages_done}/{selectedDoc.total_pages}
            </div>
            {selectedDoc.last_error && (
              <div className="error">
                Last error: {selectedDoc.last_error}
                {selectedDoc.last_error_at ? ` @ ${new Date(selectedDoc.last_error_at).toLocaleString()}` : ''}
              </div>
            )}
            <button
              onClick={() => setShowCarousel(true)}
              style={{ marginTop: '10px', backgroundColor: '#9ece6a', color: '#0b1020' }}
            >
              Open Carousel Review
            </button>
          </div>

          <h3>Pages</h3>
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>Seq</th>
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
                  <td>{p.sequence_id || ''}</td>
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

      {showCarousel && selectedDoc && (
        <CarouselReview
          documentId={selectedDoc.document_id}
          onClose={() => setShowCarousel(false)}
          onComplete={() => {
            setShowCarousel(false);
            // Refresh the document details after carousel review
            getDocument(selectedDoc.document_id).then(setSelectedDoc).catch(console.error);
          }}
          showToast={show}
        />
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

  async function revertMove() {
    if (!page.page_id) return
    setBusy(true)
    try {
      const { revertPageMove } = await import('./services/api')
      await revertPageMove(page.page_id)
      show(`Reverted move for page ${page.page_number}`, 'success')
      await onUpdated()
    } catch (e: any) {
      show(e?.message ?? 'Revert failed', 'error')
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
      <button onClick={revertMove} disabled={busy || !page.page_id}>Revert Move</button>
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
