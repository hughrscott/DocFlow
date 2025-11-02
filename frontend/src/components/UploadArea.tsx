import React, { useRef, useState } from 'react'

type Props = {
  onUpload: (file: File, opts: { analyze: boolean; background: boolean; dpi: number }) => Promise<void>
}

export default function UploadArea({ onUpload }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [analyze, setAnalyze] = useState(true)
  const [background, setBackground] = useState(true)
  const [dpi, setDpi] = useState(120)
  const [busy, setBusy] = useState(false)

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    const file = files[0]
    setBusy(true)
    try {
      await onUpload(file, { analyze, background, dpi })
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div className="upload">
      <div className="controls">
        <label>
          <input type="checkbox" checked={analyze} onChange={(e) => setAnalyze(e.target.checked)} /> Analyze (AI)
        </label>
        <label>
          <input type="checkbox" checked={background} onChange={(e) => setBackground(e.target.checked)} /> Background
        </label>
        <label>
          DPI:
          <input
            type="number"
            min={72}
            max={300}
            value={dpi}
            onChange={(e) => setDpi(parseInt(e.target.value || '120', 10))}
          />
        </label>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        onChange={(e) => handleFiles(e.target.files)}
        disabled={busy}
      />
      {busy && <div className="spinner">Uploading…</div>}
    </div>
  )}

