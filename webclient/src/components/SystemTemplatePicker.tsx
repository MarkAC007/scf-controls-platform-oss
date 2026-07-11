import React, { useState, useEffect, useMemo } from 'react'
import { getSystemCatalogTemplates } from '../data/apiClient'
import type { SystemCatalogTemplate, SystemType } from '../types'

interface SystemTemplatePickerProps {
  onSelect: (template: SystemCatalogTemplate) => void
  onCustom: () => void
}

const typeLabels: Record<string, string> = {
  cloud_provider: 'Cloud Provider',
  identity_provider: 'Identity Provider',
  ticketing: 'Ticketing',
  logging: 'Logging',
  security_tool: 'Security Tool',
  code_repository: 'Code Repository',
  document_management: 'Document Mgmt',
  endpoint_management: 'Endpoint Mgmt',
  vulnerability_management: 'Vulnerability Mgmt',
  email_security: 'Email Security',
  security_awareness: 'Security Awareness',
  password_manager: 'Password Manager',
  communication: 'Communication',
  hr_system: 'HR System',
  custom: 'Custom',
}

export const SystemTemplatePicker: React.FC<SystemTemplatePickerProps> = ({
  onSelect,
  onCustom,
}) => {
  const [templates, setTemplates] = useState<SystemCatalogTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<SystemType | 'all'>('all')

  useEffect(() => {
    let cancelled = false
    getSystemCatalogTemplates()
      .then(result => {
        if (!cancelled) {
          setTemplates(result)
          setLoading(false)
        }
      })
      .catch(err => {
        console.error('Failed to load system catalog:', err)
        if (!cancelled) {
          setError('Could not load the system catalogue.')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const availableTypes = useMemo(() => {
    const types = new Set(templates.map(t => t.system_type))
    return Array.from(types).sort()
  }, [templates])

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return templates.filter(t => {
      if (typeFilter !== 'all' && t.system_type !== typeFilter) return false
      if (!needle) return true
      return (
        t.name.toLowerCase().includes(needle) ||
        t.vendor.toLowerCase().includes(needle) ||
        t.slug.includes(needle) ||
        (t.category || '').toLowerCase().includes(needle)
      )
    })
  }, [templates, search, typeFilter])

  return (
    <div className="template-picker">
      <div className="template-picker-controls">
        <input
          type="text"
          className="template-picker-search"
          placeholder="Search systems (e.g. Cloudflare, GitHub, Okta)..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          autoFocus
        />
        <select
          className="template-picker-type"
          value={typeFilter}
          onChange={e => setTypeFilter(e.target.value as SystemType | 'all')}
        >
          <option value="all">All types</option>
          {availableTypes.map(t => (
            <option key={t} value={t}>{typeLabels[t] || t}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="template-picker-status">Loading system catalogue...</div>
      ) : error ? (
        <div className="template-picker-status template-picker-error">
          {error} You can still add a custom system below.
        </div>
      ) : null}

      <div className="template-picker-grid">
        {filtered.map(template => (
          <button
            key={template.id}
            type="button"
            className="template-card"
            onClick={() => onSelect(template)}
            title={template.description || template.name}
          >
            <div className="template-card-header">
              <span className="template-card-name">{template.name}</span>
              <span className="template-card-type">
                {typeLabels[template.system_type] || template.system_type}
              </span>
            </div>
            <div className="template-card-vendor">{template.vendor}</div>
            {template.description && (
              <div className="template-card-description">{template.description}</div>
            )}
            {template.recipe_levels.length > 0 && (
              <div className="template-card-levels">
                {template.recipe_levels[0]}–{template.recipe_levels[template.recipe_levels.length - 1]} collection guidance included
              </div>
            )}
          </button>
        ))}

        <button type="button" className="template-card template-card-custom" onClick={onCustom}>
          <div className="template-card-header">
            <span className="template-card-name">Custom system</span>
          </div>
          <div className="template-card-description">
            Not in the catalogue? Add it manually — you can generate collection
            guidance for it afterwards.
          </div>
        </button>
      </div>

      {!loading && !error && filtered.length === 0 && (
        <div className="template-picker-status">
          No matching systems — add it as a custom system.
        </div>
      )}

      <style>{`
        .template-picker-controls {
          display: flex;
          gap: 12px;
          margin-bottom: 16px;
        }
        .template-picker-search {
          flex: 1;
          padding: 10px 12px;
          border: 1px solid var(--border);
          border-radius: 8px;
          font-size: 14px;
          background: var(--panel);
          color: var(--text);
        }
        .template-picker-search:focus {
          outline: none;
          border-color: #3b82f6;
          box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
        }
        .template-picker-type {
          padding: 10px 12px;
          border: 1px solid var(--border);
          border-radius: 8px;
          font-size: 14px;
          background: var(--panel);
          color: var(--text);
          max-width: 200px;
        }
        .template-picker-status {
          padding: 20px;
          text-align: center;
          color: var(--muted);
          font-size: 14px;
        }
        .template-picker-error {
          color: #f87171;
        }
        .template-picker-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
          gap: 12px;
          max-height: 420px;
          overflow-y: auto;
          padding: 2px;
        }
        .template-card {
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding: 14px;
          border: 1px solid var(--border);
          border-radius: 10px;
          background: var(--panel);
          color: var(--text);
          text-align: left;
          cursor: pointer;
          transition: border-color 0.15s, box-shadow 0.15s, transform 0.1s;
        }
        .template-card:hover {
          border-color: #3b82f6;
          box-shadow: 0 2px 8px rgba(59, 130, 246, 0.15);
          transform: translateY(-1px);
        }
        .template-card-custom {
          border-style: dashed;
        }
        .template-card-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .template-card-name {
          font-weight: 600;
          font-size: 14px;
        }
        .template-card-type {
          font-size: 11px;
          color: var(--muted);
          white-space: nowrap;
        }
        .template-card-vendor {
          font-size: 12px;
          color: var(--muted);
        }
        .template-card-description {
          font-size: 12px;
          color: var(--muted);
          line-height: 1.4;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }
        .template-card-levels {
          margin-top: auto;
          font-size: 11px;
          color: #059669;
          font-weight: 500;
        }
      `}</style>
    </div>
  )
}

export default SystemTemplatePicker
