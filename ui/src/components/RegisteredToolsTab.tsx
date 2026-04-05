import { useEffect, useState } from 'react';
import { ToolDef } from '../types';

export function RegisteredToolsTab() {
  const [tools, setTools] = useState<ToolDef[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/tools')
      .then(r => r.json())
      .then(data => { setTools(data); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, []);

  if (loading) return <div className="p-4 text-gray-400">Loading tools...</div>;
  if (error) return <div className="p-4 text-red-400">Error: {error}</div>;

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Registered Tools</h2>
        <span className="text-sm text-gray-400">{tools.length} tool{tools.length !== 1 ? 's' : ''}</span>
      </div>

      {tools.length === 0 && (
        <div className="text-gray-500 text-sm">No tools registered. Use POST /api/tools or the tool.register MCP tool to add one.</div>
      )}

      {tools.map(tool => (
        <div key={tool.id} className="bg-gray-800 rounded-lg p-4 space-y-2">
          <div className="flex items-start justify-between">
            <div>
              <span className="font-mono text-sm text-blue-400">{tool.id}</span>
              <span className="ml-2 text-white text-sm">{tool.name}</span>
            </div>
            <span className="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300">{tool.adapter}</span>
          </div>
          <p className="text-sm text-gray-300">{tool.description}</p>
          <div className="flex flex-wrap gap-2 text-xs">
            {tool.cap_allowlist.length > 0 && (
              <span className="text-gray-400">caps: {tool.cap_allowlist.join(', ')}</span>
            )}
            {tool.allowed_roles.length > 0 && (
              <span className="text-gray-400">roles: {tool.allowed_roles.join(', ')}</span>
            )}
            {tool.required_scopes.length > 0 && (
              <span className="text-gray-400">scopes: {tool.required_scopes.join(', ')}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
