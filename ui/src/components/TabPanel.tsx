import React from 'react'

export type TabId = 'work' | 'tasks' | 'agent' | 'tools' | 'memory'

interface Tab {
  id: TabId
  label: string
}

const TABS: Tab[] = [
  { id: 'work', label: 'Work' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'agent', label: 'Agent' },
  { id: 'tools', label: 'Tools' },
  { id: 'memory', label: 'Memory' },
]

interface Props {
  activeTab: TabId
  onTabChange: (tab: TabId) => void
  children: (activeTab: TabId) => React.ReactNode
}

export function TabPanel({ activeTab, onTabChange, children }: Props) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex border-b border-gray-800 bg-gray-900">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              tab.id === activeTab
                ? 'border-blue-500 text-white'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="flex flex-1 min-h-0 flex-col">
        {children(activeTab)}
      </div>
    </div>
  )
}
