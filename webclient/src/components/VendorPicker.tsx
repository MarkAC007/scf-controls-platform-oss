import React, { useState, useEffect, useMemo, useRef } from 'react'
import toast from 'react-hot-toast'
import { getVendors, createVendor } from '../data/apiClient'
import type { Vendor, VendorSimple } from '../types'

interface VendorPickerProps {
  organizationId?: string
  value: VendorSimple | null
  onChange: (vendor: VendorSimple | null) => void
  /**
   * When set and nothing is selected, offer a one-click suggestion to link an
   * existing vendor (case-insensitive name match) or quick-create a new one.
   * Used by the template step in AddSystemModal — suggest-and-confirm, never
   * auto-create silently.
   */
  suggestedName?: string
}

function toSimple(vendor: Vendor): VendorSimple {
  return {
    id: vendor.id,
    name: vendor.name,
    website: vendor.website ?? null,
    category: vendor.category ?? null,
    status: vendor.status ?? null,
  }
}

/**
 * Searchable vendor selector for embedding inside forms.
 *
 * - Loads the org's vendors once and filters client-side (case-insensitive).
 * - Selecting a vendor emits a VendorSimple.
 * - "+ New vendor" expands an inline quick-create panel. On duplicate (409)
 *   the existing vendor is auto-selected instead of erroring; on tier-cap (403)
 *   a toast explains the limit and the form stays usable WITHOUT a vendor.
 * - The clear affordance resets to no-vendor (internal systems need none).
 */
export const VendorPicker: React.FC<VendorPickerProps> = ({
  organizationId,
  value,
  onChange,
  suggestedName,
}) => {
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState(false)

  // Inline quick-create panel state
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newWebsite, setNewWebsite] = useState('')
  const [newCategory, setNewCategory] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    getVendors(undefined, organizationId)
      .then(result => {
        if (!cancelled) {
          setVendors(result)
          setLoading(false)
        }
      })
      .catch(err => {
        console.error('Failed to load vendors:', err)
        if (!cancelled) {
          setLoadError('Could not load vendors.')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [organizationId])

  // Close the dropdown when clicking outside
  useEffect(() => {
    if (!open) return
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return vendors
    return vendors.filter(v =>
      v.name.toLowerCase().includes(needle) ||
      (v.category || '').toLowerCase().includes(needle)
    )
  }, [vendors, search])

  const findByName = (name: string): Vendor | undefined => {
    const needle = name.trim().toLowerCase()
    return vendors.find(v => v.name.trim().toLowerCase() === needle)
  }

  const suggestionMatch = useMemo(() => {
    if (!suggestedName) return null
    return findByName(suggestedName) ?? null
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestedName, vendors])

  const selectVendor = (vendor: Vendor | VendorSimple) => {
    const simple: VendorSimple = 'organization_id' in vendor
      ? toSimple(vendor as Vendor)
      : (vendor as VendorSimple)
    onChange(simple)
    setOpen(false)
    setCreating(false)
    setSearch('')
  }

  const resetCreateForm = () => {
    setNewName('')
    setNewWebsite('')
    setNewCategory('')
  }

  const openCreatePanel = (prefillName?: string) => {
    setCreating(true)
    setNewName(prefillName ?? search.trim())
    setNewWebsite('')
    setNewCategory('')
  }

  /**
   * Create a vendor and select it. Resolves duplicate/tier-cap gracefully so
   * the surrounding system-creation flow is never dead-ended.
   */
  const createAndSelect = async (
    name: string,
    website?: string,
    category?: string,
  ) => {
    const trimmed = name.trim()
    if (!trimmed) return

    // Proactive dedupe: if it already exists locally, just link it.
    const existing = findByName(trimmed)
    if (existing) {
      selectVendor(existing)
      resetCreateForm()
      return
    }

    setSubmitting(true)
    try {
      const created = await createVendor(
        {
          name: trimmed,
          website: website?.trim() || null,
          category: category?.trim() || null,
        },
        organizationId,
      )
      setVendors(prev => [created, ...prev])
      selectVendor(created)
      resetCreateForm()
    } catch (err) {
      const status = (err as { status?: number } | null)?.status
      const message = err instanceof Error ? err.message : 'Failed to create vendor'

      if (status === 409) {
        // Duplicate — link the existing record instead of erroring.
        let match = findByName(trimmed)
        if (!match) {
          // Not in our loaded list; refetch and try once more.
          try {
            const refreshed = await getVendors(undefined, organizationId)
            setVendors(refreshed)
            match = refreshed.find(v => v.name.trim().toLowerCase() === trimmed.toLowerCase())
          } catch {
            // fall through to toast below
          }
        }
        if (match) {
          selectVendor(match)
          resetCreateForm()
          return
        }
        toast.error(`A vendor named "${trimmed}" already exists.`)
        return
      }

      if (status === 403) {
        // Vendor tier cap reached — explain, but keep the flow usable.
        toast.error(
          message ||
            "You've reached your plan's vendor limit. You can still add this system without linking a vendor.",
        )
        setCreating(false)
        return
      }

      toast.error(message)
    } finally {
      setSubmitting(false)
    }
  }

  const submitCreate = () => {
    if (submitting || !newName.trim()) return
    void createAndSelect(newName, newWebsite, newCategory)
  }

  // The picker renders inside AddSystemModal's <form>; a nested <form> is
  // invalid HTML and its submit bubbles to the outer form, saving the system
  // prematurely. Plain div + explicit button/Enter handling instead.
  const handleCreateKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      submitCreate()
    }
  }

  // ── Selected state ──────────────────────────────────────────────────
  if (value) {
    return (
      <div className="vendor-picker" ref={containerRef}>
        <div className="vp-selected">
          <span className="vp-selected-icon" aria-hidden>🔗</span>
          <span className="vp-selected-name">{value.name}</span>
          {value.category && <span className="vp-selected-meta">{value.category}</span>}
          <button
            type="button"
            className="vp-clear"
            aria-label="Remove linked vendor"
            title="Remove linked vendor"
            onClick={() => onChange(null)}
          >
            &times;
          </button>
        </div>
        <style>{vendorPickerStyles}</style>
      </div>
    )
  }

  // ── Unselected state ────────────────────────────────────────────────
  return (
    <div className="vendor-picker" ref={containerRef}>
      {/* Suggestion banner (template step) */}
      {!loading && suggestedName && (
        <div className="vp-suggestion">
          {suggestionMatch ? (
            <>
              <span>
                Link vendor <strong>{suggestionMatch.name}</strong>?
              </span>
              <button
                type="button"
                className="vp-suggestion-btn"
                onClick={() => selectVendor(suggestionMatch)}
              >
                Link vendor
              </button>
            </>
          ) : (
            <>
              <span>
                Create vendor <strong>{suggestedName}</strong>?
              </span>
              <button
                type="button"
                className="vp-suggestion-btn"
                disabled={submitting}
                onClick={() => void createAndSelect(suggestedName)}
              >
                {submitting ? 'Creating…' : 'Create vendor'}
              </button>
            </>
          )}
        </div>
      )}

      <input
        type="text"
        className="vp-search"
        placeholder={loading ? 'Loading vendors…' : 'Search vendors, or leave blank for none'}
        value={search}
        onChange={e => {
          setSearch(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        disabled={loading}
      />

      {loadError && <div className="vp-status vp-error">{loadError}</div>}

      {open && !loading && !creating && (
        <div className="vp-dropdown">
          {filtered.length > 0 ? (
            filtered.map(vendor => (
              <button
                key={vendor.id}
                type="button"
                className="vp-option"
                onClick={() => selectVendor(vendor)}
              >
                <span className="vp-option-name">{vendor.name}</span>
                {vendor.category && <span className="vp-option-meta">{vendor.category}</span>}
              </button>
            ))
          ) : (
            <div className="vp-status">
              {vendors.length === 0 ? 'No vendors yet.' : 'No matching vendors.'}
            </div>
          )}
          <button
            type="button"
            className="vp-option vp-new"
            onClick={() => openCreatePanel()}
          >
            + New vendor{search.trim() ? ` "${search.trim()}"` : ''}
          </button>
        </div>
      )}

      {/* Inline quick-create panel */}
      {creating && (
        <div className="vp-create" onKeyDown={handleCreateKeyDown}>
          <div className="vp-create-title">New vendor</div>
          <input
            type="text"
            className="vp-create-input"
            placeholder="Vendor name *"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            disabled={submitting}
            autoFocus
          />
          <input
            type="text"
            className="vp-create-input"
            placeholder="Website (optional)"
            value={newWebsite}
            onChange={e => setNewWebsite(e.target.value)}
            disabled={submitting}
          />
          <input
            type="text"
            className="vp-create-input"
            placeholder="Category (optional)"
            value={newCategory}
            onChange={e => setNewCategory(e.target.value)}
            disabled={submitting}
          />
          <div className="vp-create-actions">
            <button
              type="button"
              className="vp-btn-secondary"
              onClick={() => {
                setCreating(false)
                resetCreateForm()
              }}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="button"
              className="vp-btn-primary"
              disabled={submitting || !newName.trim()}
              onClick={submitCreate}
            >
              {submitting ? 'Creating…' : 'Create & link'}
            </button>
          </div>
        </div>
      )}

      <style>{vendorPickerStyles}</style>
    </div>
  )
}

const vendorPickerStyles = `
  .vendor-picker {
    position: relative;
  }
  .vp-search {
    width: 100%;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 14px;
    box-sizing: border-box;
    font-family: inherit;
    background: var(--panel);
    color: var(--text);
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .vp-search:focus {
    outline: none;
    border-color: #3b82f6;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
  }
  .vp-search::placeholder {
    color: var(--muted);
  }
  .vp-selected {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--panel);
  }
  .vp-selected-icon {
    font-size: 13px;
  }
  .vp-selected-name {
    font-weight: 600;
    font-size: 14px;
    color: var(--text);
  }
  .vp-selected-meta {
    font-size: 12px;
    color: var(--muted);
  }
  .vp-clear {
    margin-left: auto;
    background: transparent;
    border: none;
    color: var(--muted);
    font-size: 20px;
    line-height: 1;
    cursor: pointer;
    padding: 0 4px;
    border-radius: 4px;
  }
  .vp-clear:hover {
    color: var(--text);
    background: var(--secondary);
  }
  .vp-dropdown {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    right: 0;
    z-index: 20;
    max-height: 240px;
    overflow-y: auto;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  }
  .vp-option {
    display: flex;
    align-items: baseline;
    gap: 8px;
    width: 100%;
    text-align: left;
    padding: 10px 12px;
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    color: var(--text);
    font-size: 14px;
  }
  .vp-option:last-child {
    border-bottom: none;
  }
  .vp-option:hover {
    background: var(--secondary);
  }
  .vp-option-name {
    font-weight: 500;
  }
  .vp-option-meta {
    font-size: 12px;
    color: var(--muted);
  }
  .vp-new {
    color: #3b82f6;
    font-weight: 600;
  }
  .vp-status {
    padding: 12px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
  }
  .vp-error {
    color: #f87171;
  }
  .vp-suggestion {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 12px;
    margin-bottom: 8px;
    border: 1px solid rgba(59, 130, 246, 0.35);
    background: rgba(59, 130, 246, 0.08);
    border-radius: 8px;
    font-size: 13px;
    color: var(--text);
  }
  .vp-suggestion-btn {
    flex-shrink: 0;
    background: #1976d2;
    border: none;
    color: #fff;
    font-size: 13px;
    font-weight: 500;
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
  }
  .vp-suggestion-btn:hover:not(:disabled) {
    background: #1565c0;
  }
  .vp-suggestion-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .vp-create {
    margin-top: 8px;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--panel);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .vp-create-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
  }
  .vp-create-input {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 14px;
    box-sizing: border-box;
    font-family: inherit;
    background: var(--card);
    color: var(--text);
  }
  .vp-create-input:focus {
    outline: none;
    border-color: #3b82f6;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
  }
  .vp-create-input::placeholder {
    color: var(--muted);
  }
  .vp-create-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .vp-btn-secondary,
  .vp-btn-primary {
    padding: 8px 14px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
  }
  .vp-btn-secondary {
    background: var(--secondary);
    border: 1px solid var(--border);
    color: var(--text);
  }
  .vp-btn-secondary:hover:not(:disabled) {
    background: var(--panel);
    border-color: var(--muted);
  }
  .vp-btn-primary {
    background: #1976d2;
    border: none;
    color: #fff;
  }
  .vp-btn-primary:hover:not(:disabled) {
    background: #1565c0;
  }
  .vp-btn-primary:disabled,
  .vp-btn-secondary:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
`

export default VendorPicker
