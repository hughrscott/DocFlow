import React, { createContext, useCallback, useContext, useMemo, useState } from 'react'

type Toast = { id: string; type: 'success' | 'error' | 'info'; text: string }
type ToastContextType = {
  toasts: Toast[]
  show: (text: string, type?: Toast['type'], timeoutMs?: number) => void
  remove: (id: string) => void
}

const ToastContext = createContext<ToastContextType | null>(null)

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const remove = useCallback((id: string) => {
    setToasts((t) => t.filter((x) => x.id !== id))
  }, [])

  const show = useCallback((text: string, type: Toast['type'] = 'info', timeoutMs = 3000) => {
    const id = Math.random().toString(36).slice(2)
    setToasts((t) => [...t, { id, type, text }])
    if (timeoutMs > 0) {
      setTimeout(() => remove(id), timeoutMs)
    }
  }, [remove])

  const value = useMemo(() => ({ toasts, show, remove }), [toasts, show, remove])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type}`} onClick={() => remove(t.id)}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}

