import React, { useEffect, useState } from 'react'
import { uploadDocument, listDocuments, getDocument } from './services/api'
import UploadArea from './components/UploadArea'
import SettingsView from './components/SettingsView'

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
  provider_used?: string | null
  model_used?: string | null
  success: boolean
  error?: string | null
}

type DocDetail = {
  document_id: string
  status: string
  total_pages: number
  pages_done: number
  last_error?: string | null
  pages: PageResult[]
}

export default function App() {
  const [docs, setDocs] = useState<DocBrief[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedDoc, setSelectedDoc] = useState<DocDetail | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  async function refresh() {
    setLoading(true)
    try {
      const res = await listDocuments(1, 10)
      setDocs(res.items)
    } catch (e: any) {
      setMessage(e?.message ?? 'Failed to load documents')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function handleUpload(file: File, opts: { analyze: boolean; background: boolean; dpi: number }) {
    setMessage(null)
    try {
      const out = await uploadDocument(file, opts)
      setMessage(`Uploaded: ${out.document_id}`)
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
      setMessage(e?.message ?? 'Upload failed')
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
        {message && <div className="message">{message}</div>}
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
                    <button onClick={async () => setSelectedDoc(await getDocument(d.id))}>Details</button>
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
                <th>Folder</th>
                <th>Filename</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {selectedDoc.pages?.map((p) => (
                <tr key={p.page_number}>
                  <td>{p.page_number}</td>
                  <td>{p.document_type || 'unknown'}</td>
                  <td>{p.institution || ''}</td>
                  <td>{p.date || ''}</td>
                  <td>{(p.confidence_score ?? 0).toFixed(2)}</td>
                  <td>{p.folder}</td>
                  <td>{p.filename}</td>
                  <td>{p.provider_used || ''}</td>
                  <td>{p.model_used || ''}</td>
                  <td>{p.success ? 'ok' : (p.error ? `err: ${p.error}` : 'pending')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
