import { useState, useEffect, useCallback } from 'react'

interface MemoryEntry {
  key: string
  summary: string | null
  tags: string[]
  size_bytes: number
  updated_at: string
}

interface MemoryEntryDetail extends MemoryEntry {
  value: string
}

export function MemoryTab() {
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [activeNs, setActiveNs] = useState<string | null>(null)
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [selected, setSelected] = useState<MemoryEntryDetail | null>(null)
  useEffect(() => {
    fetch('/api/memory/namespaces')
      .then(r => r.json())
      .then(setNamespaces)
      .catch(console.error)
  }, [])

  const loadNamespace = useCallback(async (ns: string) => {
    setActiveNs(ns)
    setSelected(null)
    const data = await fetch(`/api/memory/${encodeURIComponent(ns)}`).then(r => r.json())
    setEntries(data)
  }, [])

  const loadEntry = useCallback(async (ns: string, key: string) => {
    const data = await fetch(`/api/memory/${encodeURIComponent(ns)}/${encodeURIComponent(key)}`).then(r => r.json())
    setSelected(data)
  }, [])

  const deleteEntry = useCallback(async (ns: string, key: string) => {
    await fetch(`/api/memory/${encodeURIComponent(ns)}/${encodeURIComponent(key)}`, { method: 'DELETE' })
    setSelected(null)
    if (activeNs) await loadNamespace(activeNs)
  }, [activeNs, loadNamespace])

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* Namespace sidebar */}
      <div className="w-48 border-r border-gray-800 flex flex-col overflow-y-auto">
        <div className="p-2 text-xs text-gray-500 uppercase tracking-wide">Namespaces</div>
        {namespaces.map(ns => (
          <button
            key={ns}
            onClick={() => loadNamespace(ns)}
            className={`px-3 py-1.5 text-left text-sm truncate ${
              activeNs === ns ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {ns}
          </button>
        ))}
        {namespaces.length === 0 && (
          <div className="px-3 py-2 text-xs text-gray-600">No namespaces yet</div>
        )}
      </div>

      {/* Entry list */}
      <div className="w-64 border-r border-gray-800 flex flex-col overflow-y-auto">
        {activeNs && (
          <>
            <div className="p-2 text-xs text-gray-500 truncate">{activeNs}</div>
            {entries.map(entry => (
              <button
                key={entry.key}
                onClick={() => loadEntry(activeNs, entry.key)}
                className={`px-3 py-2 text-left border-b border-gray-800 ${
                  selected?.key === entry.key ? 'bg-gray-800' : 'hover:bg-gray-850'
                }`}
              >
                <div className="text-sm text-gray-200 truncate">{entry.key}</div>
                {entry.summary && (
                  <div className="text-xs text-gray-500 truncate">{entry.summary}</div>
                )}
                <div className="text-xs text-gray-600">{entry.size_bytes}B</div>
              </button>
            ))}
            {entries.length === 0 && (
              <div className="px-3 py-2 text-xs text-gray-600">Empty namespace</div>
            )}
          </>
        )}
      </div>

      {/* Entry detail */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="flex items-center justify-between p-3 border-b border-gray-800">
              <div>
                <div className="text-sm text-white font-mono">{selected.key}</div>
                {selected.summary && (
                  <div className="text-xs text-gray-400">{selected.summary}</div>
                )}
                {selected.tags.length > 0 && (
                  <div className="flex gap-1 mt-1">
                    {selected.tags.map(t => (
                      <span key={t} className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <button
                onClick={() => activeNs && deleteEntry(activeNs, selected.key)}
                className="text-xs text-red-400 hover:text-red-300 px-2 py-1 border border-red-800 rounded"
              >
                Delete
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              <pre className="text-sm text-gray-300 whitespace-pre-wrap font-mono">{selected.value}</pre>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
            {activeNs ? 'Select an entry' : 'Select a namespace'}
          </div>
        )}
      </div>
    </div>
  )
}
