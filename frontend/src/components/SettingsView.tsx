import React, { useEffect, useState } from 'react'
import { getSettings, updateSettings } from '../services/api'

type LLMConfig = {
  llm?: { vision_provider?: string; text_provider?: string }
  providers?: {
    claude?: { enabled?: boolean }
    ollama?: { enabled?: boolean; base_url?: string }
  }
}

export default function SettingsView() {
  const [cfg, setCfg] = useState<LLMConfig | null>(null)
  const [vision, setVision] = useState('')
  const [text, setText] = useState('')
  const [ollamaEnabled, setOllamaEnabled] = useState<boolean>(true)
  const [ollamaBaseUrl, setOllamaBaseUrl] = useState<string>('http://localhost:11434')
  const [claudeEnabled, setClaudeEnabled] = useState<boolean>(false)
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('changeme')
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function refresh() {
    setMessage(null)
    const data = await getSettings()
    setCfg(data)
    setVision(data?.llm?.vision_provider || '')
    setText(data?.llm?.text_provider || '')
    setOllamaEnabled(Boolean(data?.providers?.ollama?.enabled))
    setOllamaBaseUrl(data?.providers?.ollama?.base_url || 'http://localhost:11434')
    setClaudeEnabled(Boolean(data?.providers?.claude?.enabled))
  }

  useEffect(() => {
    refresh().catch((e) => setMessage(e?.message ?? 'Failed to load settings'))
  }, [])

  async function handleSave() {
    setBusy(true)
    setMessage(null)
    try {
      const payload: any = {
        vision_provider: vision,
        text_provider: text,
        providers: {
          ollama_enabled: ollamaEnabled,
          ollama_base_url: ollamaBaseUrl,
          claude_enabled: claudeEnabled,
        },
      }
      const out = await updateSettings(payload, { username, password })
      setMessage('Settings updated')
      setCfg(out.settings)
    } catch (e: any) {
      setMessage(e?.message ?? 'Update failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h3>LLM Settings</h3>
      {!cfg ? (
        <div>Loading…</div>
      ) : (
        <>
          <div className="controls" style={{ flexWrap: 'wrap' }}>
            <label>
              Vision Provider:
              <select value={vision} onChange={(e) => setVision(e.target.value)}>
                <option value="claude">claude</option>
                <option value="ollama">ollama</option>
                <option value="openai">openai</option>
              </select>
            </label>
            <label>
              Text Provider:
              <select value={text} onChange={(e) => setText(e.target.value)}>
                <option value="claude">claude</option>
                <option value="ollama">ollama</option>
                <option value="openai">openai</option>
              </select>
            </label>
            <label>
              <input type="checkbox" checked={ollamaEnabled} onChange={(e) => setOllamaEnabled(e.target.checked)} />
              Ollama Enabled
            </label>
            <label>
              Ollama Base URL:
              <input type="text" value={ollamaBaseUrl} onChange={(e) => setOllamaBaseUrl(e.target.value)} />
            </label>
            <label>
              <input type="checkbox" checked={claudeEnabled} onChange={(e) => setClaudeEnabled(e.target.checked)} />
              Claude Enabled
            </label>
          </div>

          <h4>Auth (for saving)</h4>
          <div className="controls">
            <label>
              Username:
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label>
              Password:
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button onClick={handleSave} disabled={busy}>Save</button>
          </div>
          {message && <div className="message">{message}</div>}
        </>
      )}
    </div>
  )
}

