import React, { useEffect, useState } from 'react'
import { uploadDocument, listDocuments, getDocument } from './services/api'
import UploadArea from './components/UploadArea'

type DocBrief = {
  id: string
  original_filename: string
  upload_date: string
  total_pages: number
  status: string
}

type DocDetail = {
  document_id: string
  status: string
  total_pages: number
  pages_done: number
  last_error?: string | null
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
          setSelectedDoc({
            document_id: d.document_id,
            status: d.status,
            total_pages: d.total_pages,
            pages_done: d.pages_done,
            last_error: d.last_error,
          })
          if (d.status === 'completed' || d.status === 'failed') break
          await new Promise((r) => setTimeout(r, 500))
        }
      } else {
        const d = await getDocument(out.document_id)
        setSelectedDoc({
          document_id: d.document_id,
          status: d.status,
          total_pages: d.total_pages,
          pages_done: d.pages_done,
          last_error: d.last_error,
        })
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
        </section>
      )}
    </div>
  )
}

