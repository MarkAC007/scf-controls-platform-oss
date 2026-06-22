import { useState } from 'react'
import type { System } from '../../types'
import { createWebhookEndpoint, testWebhookEndpoint } from '../../data/apiClient'

// ---- Step 1: Select System ----

interface SystemSelectStepProps {
  systems: System[]
  selectedSystem: System | null
  onSelect: (system: System) => void
  onNext: () => void
}

export function SystemSelectStep({ systems, selectedSystem, onSelect, onNext }: SystemSelectStepProps) {
  const [query, setQuery] = useState('')

  const filtered = systems.filter(s =>
    s.name.toLowerCase().includes(query.toLowerCase()) ||
    (s.vendor || '').toLowerCase().includes(query.toLowerCase())
  )

  return (
    <div className="wizard-step-content">
      <h3>Which system will provide evidence?</h3>
      <p className="wizard-hint">
        Select the tool or platform that will send evidence to your collection point.
      </p>

      <input
        type="text"
        className="wizard-search"
        placeholder="Search systems..."
        value={query}
        onChange={e => setQuery(e.target.value)}
        autoFocus
      />

      <div className="wizard-system-list">
        {filtered.length === 0 && (
          <p className="wizard-empty">No systems found. Add one in the Systems tab first.</p>
        )}
        {filtered.map(system => (
          <button
            key={system.id}
            className={`wizard-system-card ${selectedSystem?.id === system.id ? 'selected' : ''}`}
            onClick={() => onSelect(system)}
          >
            <strong>{system.name}</strong>
            {system.vendor && <span className="wizard-system-vendor">{system.vendor}</span>}
            {system.system_type && <span className="wizard-system-type">{system.system_type}</span>}
          </button>
        ))}
      </div>

      <div className="wizard-actions">
        <button
          className="btn btn-primary"
          disabled={!selectedSystem}
          onClick={onNext}
        >
          Continue
        </button>
      </div>
    </div>
  )
}

// ---- Step 2: Configure Collection ----

interface ConfigureCollectionStepProps {
  collectionMethod: 'manual' | 'automated' | null
  frequency: string
  evidenceIds: string[]
  onUpdate: (updates: Record<string, any>) => void
  onBack: () => void
  onNext: () => void
}

const FREQUENCY_OPTIONS = [
  { value: 'real_time', label: 'Real-time' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'annually', label: 'Annually' },
]

export function ConfigureCollectionStep({
  collectionMethod,
  frequency,
  evidenceIds,
  onUpdate,
  onBack,
  onNext,
}: ConfigureCollectionStepProps) {
  const [evidenceInput, setEvidenceInput] = useState(evidenceIds.join(', '))

  return (
    <div className="wizard-step-content">
      <h3>How should evidence be collected?</h3>

      <div className="wizard-method-cards">
        <button
          className={`wizard-method-card ${collectionMethod === 'manual' ? 'selected' : ''}`}
          onClick={() => onUpdate({ collectionMethod: 'manual' })}
        >
          <div className="wizard-method-icon">&#128203;</div>
          <strong>Manual Upload</strong>
          <p>Upload evidence files directly through the platform.</p>
        </button>

        <button
          className={`wizard-method-card ${collectionMethod === 'automated' ? 'selected' : ''}`}
          onClick={() => onUpdate({ collectionMethod: 'automated' })}
        >
          <div className="wizard-method-icon">&#9889;</div>
          <strong>Automated Collection</strong>
          <p>Set up a collection point for your system to send evidence automatically.</p>
        </button>
      </div>

      <div className="wizard-form-group">
        <label>Collection Frequency</label>
        <select
          value={frequency}
          onChange={e => onUpdate({ frequency: e.target.value })}
          className="wizard-select"
        >
          {FREQUENCY_OPTIONS.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      <div className="wizard-form-group">
        <label>Evidence Types (comma-separated IDs)</label>
        <input
          type="text"
          className="wizard-input"
          placeholder="e.g. IRO-04, IRO-06"
          value={evidenceInput}
          onChange={e => {
            setEvidenceInput(e.target.value)
            const ids = e.target.value.split(',').map(s => s.trim()).filter(Boolean)
            onUpdate({ evidenceIds: ids })
          }}
        />
        <span className="wizard-hint">Leave empty to accept all evidence types.</span>
      </div>

      <div className="wizard-actions">
        <button className="btn btn-secondary" onClick={onBack}>Back</button>
        <button
          className="btn btn-primary"
          disabled={!collectionMethod}
          onClick={onNext}
        >
          Continue
        </button>
      </div>
    </div>
  )
}

// ---- Step 3: Generate Endpoint ----

interface GenerateEndpointStepProps {
  orgId: string
  systemName: string
  evidenceIds: string[]
  state: {
    endpointId: string | null
    endpointUrl: string | null
    secretKey: string | null
    secretPrefix: string | null
    testResult: 'idle' | 'testing' | 'success' | 'error'
  }
  onUpdate: (updates: Record<string, any>) => void
  onBack: () => void
  onNext: () => void
}

export function GenerateEndpointStep({
  orgId,
  systemName,
  evidenceIds,
  state,
  onUpdate,
  onBack,
  onNext,
}: GenerateEndpointStepProps) {
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showSecret, setShowSecret] = useState(true)
  const [copied, setCopied] = useState<string | null>(null)

  const handleGenerate = async () => {
    setGenerating(true)
    setError(null)
    try {
      const result = await createWebhookEndpoint(orgId, {
        name: `${systemName} Evidence Collection`,
        description: `Automated evidence collection from ${systemName}`,
        allowed_evidence_ids: evidenceIds.length > 0 ? evidenceIds : undefined,
      })
      const baseUrl = window.location.origin
      const evidencePlaceholder = evidenceIds.length > 0 ? evidenceIds[0] : '<EVIDENCE_ID>'
      onUpdate({
        endpointId: result.id,
        endpointUrl: `${baseUrl}/api/organizations/${orgId}/evidence/${evidencePlaceholder}/inbox`,
        secretKey: result.plaintext_secret,
        secretPrefix: result.secret_prefix,
      })
    } catch (err: any) {
      setError(err.message || 'Failed to create collection point')
    } finally {
      setGenerating(false)
    }
  }

  const handleTest = async () => {
    if (!state.endpointId) return
    onUpdate({ testResult: 'testing' })
    try {
      await testWebhookEndpoint(orgId, state.endpointId)
      onUpdate({ testResult: 'success' })
    } catch {
      onUpdate({ testResult: 'error' })
    }
  }

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text)
    setCopied(label)
    setTimeout(() => setCopied(null), 2000)
  }

  return (
    <div className="wizard-step-content">
      <h3>Generate your collection point</h3>

      {!state.endpointId ? (
        <>
          <p className="wizard-hint">
            Click below to create a secure collection point. Your system will use
            this to send evidence automatically.
          </p>
          {error && <div className="wizard-error">{error}</div>}
          <div className="wizard-actions wizard-actions-center">
            <button
              className="btn btn-primary btn-lg"
              onClick={handleGenerate}
              disabled={generating}
            >
              {generating ? 'Creating...' : 'Create Collection Point'}
            </button>
          </div>
        </>
      ) : (
        <div className="wizard-credentials">
          <div className="wizard-credential-row">
            <label>Collection Point URL</label>
            <div className="wizard-credential-value">
              <code>{state.endpointUrl}</code>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => copyToClipboard(state.endpointUrl!, 'url')}
              >
                {copied === 'url' ? 'Copied!' : 'Copy'}
              </button>
            </div>
            {evidenceIds.length === 0 && (
              <p className="wizard-hint">
                Replace <code>&lt;EVIDENCE_ID&gt;</code> with the evidence identifier for each submission.
              </p>
            )}
            {evidenceIds.length > 1 && (
              <p className="wizard-hint">
                Change the evidence ID in the URL for different types. Allowed: {evidenceIds.join(', ')}
              </p>
            )}
          </div>

          <div className="wizard-credential-row">
            <label>Collection Point ID</label>
            <div className="wizard-credential-value">
              <code>{state.endpointId}</code>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => copyToClipboard(state.endpointId!, 'id')}
              >
                {copied === 'id' ? 'Copied!' : 'Copy'}
              </button>
            </div>
          </div>

          <div className="wizard-credential-row">
            <label>Secret Key</label>
            <div className="wizard-credential-value">
              <code>{showSecret ? state.secretKey : state.secretPrefix + '...'}</code>
              <button className="btn btn-sm btn-ghost" onClick={() => setShowSecret(!showSecret)}>
                {showSecret ? 'Hide' : 'Show'}
              </button>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => copyToClipboard(state.secretKey!, 'secret')}
              >
                {copied === 'secret' ? 'Copied!' : 'Copy'}
              </button>
            </div>
            <p className="wizard-warning">
              Save this secret key now. It will not be shown again.
            </p>
          </div>

          <details className="wizard-advanced">
            <summary>Technical Details (for developers)</summary>
            <div className="wizard-advanced-content">
              <p>Send a POST request to the collection URL with these headers:</p>
              <pre className="wizard-code">{`X-SCF-Webhook-Id: ${state.endpointId}
X-SCF-Signature: sha256=<HMAC-SHA256 of request body>
X-SCF-Timestamp: <unix epoch seconds>
Content-Type: application/json`}</pre>
              <p>Sign the request body with your secret key using HMAC-SHA256.</p>
            </div>
          </details>

          <div className="wizard-test-section">
            <button
              className="btn btn-secondary"
              onClick={handleTest}
              disabled={state.testResult === 'testing'}
            >
              {state.testResult === 'testing' ? 'Testing...' : 'Test Connection'}
            </button>
            {state.testResult === 'success' && (
              <span className="wizard-test-success">Connection successful!</span>
            )}
            {state.testResult === 'error' && (
              <span className="wizard-test-error">Test failed. Check configuration.</span>
            )}
          </div>
        </div>
      )}

      <div className="wizard-actions">
        <button className="btn btn-secondary" onClick={onBack}>Back</button>
        <button
          className="btn btn-primary"
          disabled={!state.endpointId}
          onClick={onNext}
        >
          Continue
        </button>
      </div>
    </div>
  )
}

// ---- Step 4: Review & Export ----

interface ReviewExportStepProps {
  state: {
    selectedSystem: { name: string } | null
    collectionMethod: 'manual' | 'automated' | null
    frequency: string
    evidenceIds: string[]
    endpointId: string | null
    endpointUrl: string | null
    secretPrefix: string | null
  }
  onBack: () => void
  onDone: () => void
}

export function ReviewExportStep({ state, onBack, onDone }: ReviewExportStepProps) {
  const [copied, setCopied] = useState(false)

  const generateYaml = () => {
    return `# Evidence Collection Configuration
# Generated by SCF Controls Platform
system: "${state.selectedSystem?.name || 'Unknown'}"
method: "${state.collectionMethod}"
frequency: "${state.frequency}"
evidence_ids: [${state.evidenceIds.map(id => `"${id}"`).join(', ')}]
endpoint:
  url: "${state.endpointUrl}"
  id: "${state.endpointId}"
  secret_prefix: "${state.secretPrefix}"
`
  }

  const generateCurl = () => {
    return `curl -X POST "${state.endpointUrl}" \\
  -H "Content-Type: application/json" \\
  -H "X-SCF-Webhook-Id: ${state.endpointId}" \\
  -H "X-SCF-Signature: sha256=<computed_hmac>" \\
  -H "X-SCF-Timestamp: $(date +%s)" \\
  -d '{"source": "${state.selectedSystem?.name}", "data": {"description": "Test evidence"}}'`
  }

  const handleExportYaml = () => {
    const blob = new Blob([generateYaml()], { type: 'text/yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `evidence-collection-${state.selectedSystem?.name?.toLowerCase().replace(/\s+/g, '-') || 'config'}.yaml`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleCopyCurl = () => {
    navigator.clipboard.writeText(generateCurl())
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="wizard-step-content">
      <h3>Collection point ready!</h3>

      <div className="wizard-summary">
        <div className="wizard-summary-row">
          <span className="wizard-summary-label">System</span>
          <span>{state.selectedSystem?.name || 'Unknown'}</span>
        </div>
        <div className="wizard-summary-row">
          <span className="wizard-summary-label">Method</span>
          <span>{state.collectionMethod === 'automated' ? 'Automated' : 'Manual Upload'}</span>
        </div>
        <div className="wizard-summary-row">
          <span className="wizard-summary-label">Frequency</span>
          <span>{state.frequency}</span>
        </div>
        {state.evidenceIds.length > 0 && (
          <div className="wizard-summary-row">
            <span className="wizard-summary-label">Evidence Types</span>
            <span>{state.evidenceIds.join(', ')}</span>
          </div>
        )}
      </div>

      {state.collectionMethod === 'automated' && (
        <div className="wizard-export-actions">
          <button className="btn btn-secondary" onClick={handleExportYaml}>
            Export Configuration (YAML)
          </button>
          <button className="btn btn-secondary" onClick={handleCopyCurl}>
            {copied ? 'Copied!' : 'Copy Example Request'}
          </button>
        </div>
      )}

      <div className="wizard-actions">
        <button className="btn btn-secondary" onClick={onBack}>Back</button>
        <button className="btn btn-primary" onClick={onDone}>Done</button>
      </div>
    </div>
  )
}
