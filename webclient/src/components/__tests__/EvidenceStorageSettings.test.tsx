/**
 * EvidenceStorageSettings — Settings → Evidence storage (organisation admin).
 *
 * The screen is write-only: the API never returns a stored secret and the
 * component must never render one, even if the backend sends one by mistake.
 * These tests pin that, the four states the resolver can report, the provider
 * dropdown driving which fields render, the test-before-activate gate, the
 * structured 409s, and the two invariants that are security properties rather
 * than niceties — `is_bundled` is never a control, and a rotation is write-only.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import EvidenceStorageSettings from '../EvidenceStorageSettings'
import {
  activateEvidenceStorageConfig,
  createEvidenceStorageConfig,
  deleteEvidenceStorageConfig,
  getEffectiveEvidenceStorage,
  listEvidenceStorageConfigs,
  retireEvidenceStorageConfig,
  rotateEvidenceStorageSecret,
  testEvidenceStorageConfig,
  updateEvidenceStorageConfig,
} from '../../data/evidenceStorageApi'
import type {
  EvidenceStorageConfig,
  EvidenceStorageEffective,
} from '../../data/evidenceStorageApi'

vi.mock('../../data/evidenceStorageApi', async () => {
  const actual = await vi.importActual<typeof import('../../data/evidenceStorageApi')>(
    '../../data/evidenceStorageApi'
  )
  return {
    // errorDetailOf is a pure reader of a thrown error; the component's 409
    // rendering is only meaningful if it is the real one.
    errorDetailOf: actual.errorDetailOf,
    listEvidenceStorageConfigs: vi.fn(),
    getEvidenceStorageConfig: vi.fn(),
    createEvidenceStorageConfig: vi.fn(),
    updateEvidenceStorageConfig: vi.fn(),
    deleteEvidenceStorageConfig: vi.fn(),
    testEvidenceStorageConfig: vi.fn(),
    activateEvidenceStorageConfig: vi.fn(),
    rotateEvidenceStorageSecret: vi.fn(),
    retireEvidenceStorageConfig: vi.fn(),
    getEffectiveEvidenceStorage: vi.fn(),
  }
})

const mockEffective = vi.mocked(getEffectiveEvidenceStorage)
const mockList = vi.mocked(listEvidenceStorageConfigs)
const mockCreate = vi.mocked(createEvidenceStorageConfig)
const mockUpdate = vi.mocked(updateEvidenceStorageConfig)
const mockDelete = vi.mocked(deleteEvidenceStorageConfig)
const mockTest = vi.mocked(testEvidenceStorageConfig)
const mockActivate = vi.mocked(activateEvidenceStorageConfig)
const mockRotate = vi.mocked(rotateEvidenceStorageSecret)
const mockRetire = vi.mocked(retireEvidenceStorageConfig)

const ORG = 'org-1'

/** Nothing like this may ever reach the DOM, from any code path. */
const STORED_PLAINTEXT = 'wJalrXUtnFEMI-super-secret-do-not-render'

function effective(overrides: Partial<EvidenceStorageEffective> = {}): EvidenceStorageEffective {
  return {
    config_id: null,
    source: 'legacy_env',
    managed_by_operator: true,
    configured: true,
    is_bundled: false,
    provider: 'minio',
    provider_label: 'MinIO',
    bucket: 'evidence',
    region: 'eu-west-1',
    endpoint_url: 'http://minio:9000',
    public_endpoint: 'http://localhost:9000',
    path_style: true,
    sse_mode: 'none',
    key_version: null,
    ...overrides,
  }
}

function config(overrides: Partial<EvidenceStorageConfig> = {}): EvidenceStorageConfig {
  return {
    id: 'cfg-1',
    organization_id: ORG,
    provider: 's3_compatible',
    provider_label: 'Other S3-compatible',
    bucket: 'acme-evidence',
    region: 'eu-west-1',
    endpoint_url: 'https://objects.example.com',
    public_endpoint: null,
    path_style: true,
    sse_mode: 'none',
    access_key_id: 'AKIAEXAMPLE',
    secret_mask: '••••••••',
    key_version: 1,
    status: 'draft',
    is_bundled: false,
    source: 'org',
    managed_by_operator: false,
    created_at: '2026-09-12T09:00:00Z',
    updated_at: '2026-09-12T09:00:00Z',
    updated_by: 'admin@example.com',
    ...overrides,
  }
}

function conflict(detail: unknown, message: string): Error & { status: number; detail: unknown } {
  const err = new Error(message) as Error & { status: number; detail: unknown }
  err.status = 409
  err.detail = detail
  return err
}

async function renderLoaded() {
  const view = render(<EvidenceStorageSettings organizationId={ORG} />)
  await waitFor(() => {
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
  })
  return view
}

describe('EvidenceStorageSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockEffective.mockResolvedValue(effective())
    mockList.mockResolvedValue({ items: [] })
  })

  // --- the four states the resolver can report -----------------------------

  it('shows a bundled platform store as installed, and still offers a switch', async () => {
    mockEffective.mockResolvedValue(
      effective({
        source: 'platform',
        managed_by_operator: true,
        is_bundled: true,
        config_id: 'platform-row',
      })
    )

    await renderLoaded()

    expect(screen.getByText('MinIO — bundled, installed by the installer')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-source-chip')).toHaveTextContent(
      'Managed by operator (platform default)'
    )
    expect(screen.getByTestId('evidence-storage-operator-note')).toBeInTheDocument()
    // Switchable: Mark's stated requirement is that bundled is not a dead end.
    expect(screen.getByTestId('evidence-storage-use-different')).toBeEnabled()
    // Never a link to a platform configuration id — an org admin gets 404 on it.
    expect(screen.queryByRole('link', { name: /platform-row/ })).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('platform-row')
  })

  it('labels an operator environment store as managed by the operator', async () => {
    mockEffective.mockResolvedValue(effective({ source: 'legacy_env', provider: 'aws_s3', provider_label: 'Amazon S3', endpoint_url: null }))

    await renderLoaded()

    expect(screen.getByTestId('evidence-storage-source-chip')).toHaveTextContent(
      'Managed by operator (environment)'
    )
    expect(screen.getByText('Amazon S3')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-operator-note')).toBeInTheDocument()
  })

  it("shows an organisation's own store as managed in app, with its row", async () => {
    mockEffective.mockResolvedValue(
      effective({
        source: 'org',
        managed_by_operator: false,
        config_id: 'cfg-1',
        provider: 's3_compatible',
        provider_label: 'Other S3-compatible',
        endpoint_url: 'https://objects.example.com',
        key_version: 2,
      })
    )
    mockList.mockResolvedValue({ items: [config({ status: 'active', key_version: 2 })] })

    await renderLoaded()

    expect(screen.getByTestId('evidence-storage-source-chip')).toHaveTextContent('Managed in app')
    expect(screen.queryByTestId('evidence-storage-operator-note')).not.toBeInTheDocument()

    const row = screen.getByTestId('evidence-storage-row-cfg-1')
    expect(within(row).getByText('Active')).toBeInTheDocument()
    expect(within(row).getByTestId('evidence-storage-rotate-cfg-1')).toBeEnabled()
    expect(within(row).getByTestId('evidence-storage-retire-cfg-1')).toBeEnabled()
    expect(within(row).getByTestId('evidence-storage-test-cfg-1')).toBeEnabled()
    // An active configuration is where evidence is being written right now.
    expect(within(row).getByTestId('evidence-storage-delete-cfg-1')).toBeDisabled()
  })

  it('shows blank editable fields when nothing is configured anywhere', async () => {
    // D42: the resolver still reports a provider and an SSE mode from preset
    // defaults, so the address block is gated on `configured`, not on provider.
    mockEffective.mockResolvedValue(
      effective({
        source: 'legacy_env',
        configured: false,
        bucket: null,
        endpoint_url: null,
        region: null,
        provider: 'aws_s3',
        provider_label: 'Amazon S3',
        sse_mode: 'AES256',
      })
    )

    await renderLoaded()

    expect(screen.getByTestId('evidence-storage-unconfigured')).toHaveTextContent(
      'No evidence store is configured'
    )
    expect(screen.queryByTestId('evidence-storage-address')).not.toBeInTheDocument()
    // AES256 belongs to the preset default, not to anything configured; it
    // must not be presented as this organisation's setting.
    expect(document.body.textContent).not.toContain('AES256')

    await userEvent.click(screen.getByTestId('evidence-storage-use-different'))
    expect(screen.getByTestId('evidence-storage-bucket')).toHaveValue('')
    expect(screen.getByTestId('evidence-storage-secret')).toHaveValue('')
  })

  // --- ISC 38, the dropdown ------------------------------------------------

  it('the provider dropdown drives which fields render', async () => {
    const user = userEvent.setup()
    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-use-different'))

    // Amazon S3: the endpoint is the provider's own, so there is no field.
    expect(screen.getByTestId('evidence-storage-provider')).toHaveValue('aws_s3')
    expect(screen.queryByTestId('evidence-storage-endpoint')).not.toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-endpoint-fixed')).toHaveTextContent(
      "Amazon's own endpoint"
    )

    // Google Cloud Storage: fixed to the XML API host, and the help says HMAC.
    await user.selectOptions(screen.getByTestId('evidence-storage-provider'), 'gcs')
    expect(screen.queryByTestId('evidence-storage-endpoint')).not.toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-endpoint-fixed')).toHaveTextContent(
      'https://storage.googleapis.com'
    )
    expect(screen.getByTestId('evidence-storage-provider-help')).toHaveTextContent('HMAC key pair')

    // A generic S3 store: the operator supplies the address, and is told the rules.
    await user.selectOptions(screen.getByTestId('evidence-storage-provider'), 's3_compatible')
    expect(screen.getByTestId('evidence-storage-endpoint')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-public-endpoint')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-provider-help')).toHaveTextContent('must be https')

    // MinIO: an operator's own MinIO, same address rules.
    await user.selectOptions(screen.getByTestId('evidence-storage-provider'), 'minio')
    expect(screen.getByTestId('evidence-storage-endpoint')).toBeInTheDocument()

    // All four presets, and only those four.
    const options = within(screen.getByTestId('evidence-storage-provider')).getAllByRole('option')
    expect(options.map((o) => (o as HTMLOptionElement).value)).toEqual([
      'minio',
      'aws_s3',
      'gcs',
      's3_compatible',
    ])
  })

  it('creates a draft from the form and never sends is_bundled', async () => {
    const user = userEvent.setup()
    mockCreate.mockResolvedValue(config())
    await renderLoaded()

    await user.click(screen.getByTestId('evidence-storage-use-different'))
    await user.selectOptions(screen.getByTestId('evidence-storage-provider'), 's3_compatible')
    await user.type(screen.getByTestId('evidence-storage-bucket'), 'acme-evidence')
    await user.type(screen.getByTestId('evidence-storage-endpoint'), 'https://objects.example.com')
    await user.type(screen.getByTestId('evidence-storage-access-key'), 'AKIAEXAMPLE')
    await user.type(screen.getByTestId('evidence-storage-secret'), STORED_PLAINTEXT)
    await user.click(screen.getByTestId('evidence-storage-save'))

    await waitFor(() => expect(mockCreate).toHaveBeenCalled())
    const [, payload] = mockCreate.mock.calls[0]
    expect(payload).toMatchObject({
      provider: 's3_compatible',
      bucket: 'acme-evidence',
      endpoint_url: 'https://objects.example.com',
      access_key_id: 'AKIAEXAMPLE',
      secret_access_key: STORED_PLAINTEXT,
    })
    expect(Object.keys(payload)).not.toContain('is_bundled')

    // The typed secret is dropped with the editor, before any re-render.
    await waitFor(() =>
      expect(screen.queryByTestId('evidence-storage-editor')).not.toBeInTheDocument()
    )
    expect(document.body.textContent).not.toContain(STORED_PLAINTEXT)
  })

  // --- ISC 42, the anti-criterion ------------------------------------------

  it('renders no stored secret, whatever the backend sends', async () => {
    // A backend that wrongly leaks a value on every shape it returns.
    const leaky = {
      ...config({ status: 'active' }),
      value: STORED_PLAINTEXT,
      secret: STORED_PLAINTEXT,
      secret_access_key: STORED_PLAINTEXT,
      secret_ciphertext: `enc:v1:${STORED_PLAINTEXT}`,
    } as unknown as EvidenceStorageConfig
    mockList.mockResolvedValue({ items: [leaky] })
    mockEffective.mockResolvedValue({
      ...effective({ source: 'org', managed_by_operator: false, config_id: 'cfg-1' }),
      secret_access_key: STORED_PLAINTEXT,
      value: STORED_PLAINTEXT,
    } as unknown as EvidenceStorageEffective)

    await renderLoaded()

    expect(document.body.textContent).not.toContain(STORED_PLAINTEXT)
    expect(document.body.textContent).not.toContain('enc:v1:')

    // What a configured row shows instead is the fixed mask, which is eight
    // identical characters and is not derived from anything.
    const row = screen.getByTestId('evidence-storage-row-cfg-1')
    const mask = within(row).getByLabelText('Secret hidden')
    expect(mask.textContent).toBe('••••••••')
    expect(mask.textContent).not.toBe(STORED_PLAINTEXT)
  })

  it('offers no control bound to is_bundled anywhere on the screen', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ is_bundled: false })] })
    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-use-different'))

    // Not as a checkbox, not as a field, not as a label. It is what exempts a
    // row from the address refusals; only the installer may set it.
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain('is_bundled')
    for (const field of document.querySelectorAll('input, select')) {
      expect(field.getAttribute('name')).not.toBe('is_bundled')
    }
  })

  // --- ISC 41, test before activate ----------------------------------------

  it('keeps Activate disabled until the connection test passes', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'draft' })] })
    mockTest.mockResolvedValue({
      success: false,
      config_id: 'cfg-1',
      steps: [
        { name: 'address', ok: true, status_code: null, error_class: null },
        { name: 'put', ok: false, status_code: null, error_class: 'EndpointConnectionError' },
      ],
    })

    await renderLoaded()
    expect(screen.getByTestId('evidence-storage-activate-cfg-1')).toBeDisabled()

    await user.click(screen.getByTestId('evidence-storage-test-cfg-1'))

    const report = await screen.findByTestId('evidence-storage-report-cfg-1')
    expect(within(report).getByText('Address check')).toBeInTheDocument()
    expect(within(report).getByText('Write a test object')).toBeInTheDocument()
    expect(within(report).getByText('EndpointConnectionError')).toBeInTheDocument()
    expect(within(report).getAllByText('Failed').length).toBeGreaterThan(0)
    // A failing probe leaves activation shut.
    expect(screen.getByTestId('evidence-storage-activate-cfg-1')).toBeDisabled()

    // A passing probe opens it.
    mockTest.mockResolvedValue({
      success: true,
      config_id: 'cfg-1',
      steps: [
        { name: 'address', ok: true, status_code: null, error_class: null },
        { name: 'put', ok: true, status_code: 200, error_class: null },
        { name: 'get', ok: true, status_code: 200, error_class: null },
        { name: 'delete', ok: true, status_code: 204, error_class: null },
      ],
    })
    await user.click(screen.getByTestId('evidence-storage-test-cfg-1'))
    await waitFor(() =>
      expect(screen.getByTestId('evidence-storage-activate-cfg-1')).toBeEnabled()
    )

    mockActivate.mockResolvedValue(config({ status: 'active' }))
    await user.click(screen.getByTestId('evidence-storage-activate-cfg-1'))
    await waitFor(() => expect(mockActivate).toHaveBeenCalledWith(ORG, 'cfg-1'))
  })

  it('shows the probe report the server sends back with a refused activation', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'draft' })] })
    mockTest.mockResolvedValue({
      success: true,
      config_id: 'cfg-1',
      steps: [{ name: 'address', ok: true, status_code: null, error_class: null }],
    })
    mockActivate.mockRejectedValue(
      conflict(
        {
          message: 'The connection test failed, so the configuration was not activated',
          report: {
            success: false,
            steps: [{ name: 'get', ok: false, status_code: 403, error_class: null }],
          },
        },
        'The connection test failed, so the configuration was not activated'
      )
    )

    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-test-cfg-1'))
    await waitFor(() =>
      expect(screen.getByTestId('evidence-storage-activate-cfg-1')).toBeEnabled()
    )
    await user.click(screen.getByTestId('evidence-storage-activate-cfg-1'))

    expect(
      await screen.findByText(
        'The connection test failed, so the configuration was not activated'
      )
    ).toBeInTheDocument()
    const report = screen.getByTestId('evidence-storage-report-cfg-1')
    expect(within(report).getByText('Read it back')).toBeInTheDocument()
    expect(within(report).getByText('HTTP 403')).toBeInTheDocument()
  })

  // --- ISC 41, rotate ------------------------------------------------------

  it('rotates write-only: the typed secret goes to the API and never to the DOM', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'active' })] })
    mockRotate.mockResolvedValue(config({ status: 'active', key_version: 2 }))

    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-rotate-cfg-1'))

    const input = screen.getByTestId('evidence-storage-rotate-secret-cfg-1')
    expect(input).toHaveAttribute('type', 'password')
    await user.type(input, STORED_PLAINTEXT)
    await user.click(screen.getByTestId('evidence-storage-rotate-save-cfg-1'))

    await waitFor(() =>
      expect(mockRotate).toHaveBeenCalledWith(ORG, 'cfg-1', {
        secret_access_key: STORED_PLAINTEXT,
        access_key_id: undefined,
      })
    )
    await waitFor(() =>
      expect(
        screen.queryByTestId('evidence-storage-rotate-secret-cfg-1')
      ).not.toBeInTheDocument()
    )
    expect(document.body.textContent).not.toContain(STORED_PLAINTEXT)
  })

  it('opens one panel at a time', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'draft' })] })
    await renderLoaded()

    await user.click(screen.getByTestId('evidence-storage-use-different'))
    expect(screen.getByTestId('evidence-storage-editor')).toBeInTheDocument()

    await user.click(screen.getByTestId('evidence-storage-rotate-cfg-1'))
    expect(screen.queryByTestId('evidence-storage-editor')).not.toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-rotate-secret-cfg-1')).toBeInTheDocument()

    await user.click(screen.getByTestId('evidence-storage-delete-cfg-1'))
    expect(
      screen.queryByTestId('evidence-storage-rotate-secret-cfg-1')
    ).not.toBeInTheDocument()
    expect(screen.getByTestId('evidence-storage-delete-confirm-cfg-1')).toBeInTheDocument()
  })

  // --- structured 409s -----------------------------------------------------

  it('renders the file count when a delete is refused because files reference it', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'retired' })] })
    mockDelete.mockRejectedValue(
      conflict(
        {
          message: 'Evidence files are still stored under this configuration',
          evidence_file_count: 12,
        },
        'Evidence files are still stored under this configuration'
      )
    )

    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-delete-cfg-1'))
    await user.click(screen.getByTestId('evidence-storage-delete-confirm-cfg-1'))

    const error = await screen.findByRole('alert')
    expect(error).toHaveTextContent('Evidence files are still stored under this configuration')
    expect(error).toHaveTextContent('12 evidence files are still stored under it')
  })

  it('raises the SCF_SECRET_KEY banner when the API says there is no key', async () => {
    const user = userEvent.setup()
    mockCreate.mockRejectedValue(
      conflict(
        { message: 'SCF_SECRET_KEY is not configured — see docs', encryption_key_configured: false },
        'SCF_SECRET_KEY is not configured — see docs'
      )
    )

    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-use-different'))
    await user.type(screen.getByTestId('evidence-storage-bucket'), 'acme-evidence')
    await user.click(screen.getByTestId('evidence-storage-save'))

    expect(
      await screen.findByTestId('evidence-storage-encryption-banner')
    ).toHaveTextContent('SCF_SECRET_KEY is not configured')
    // The editor stays open so the operator can retry once the host is fixed.
    expect(screen.getByTestId('evidence-storage-editor')).toBeInTheDocument()
  })

  // --- degraded reads ------------------------------------------------------

  it('renders a read-only explanation, not a blank card, on a 403', async () => {
    const forbidden = new Error('Requires admin role') as Error & { status: number }
    forbidden.status = 403
    mockEffective.mockRejectedValue(forbidden)
    mockList.mockRejectedValue(forbidden)

    await renderLoaded()

    expect(screen.getByTestId('evidence-storage-forbidden')).toHaveTextContent(
      'You cannot change this.'
    )
    expect(screen.queryByTestId('evidence-storage-use-different')).not.toBeInTheDocument()
  })

  it('reads bundled off the response rather than inferring it (D48)', async () => {
    // The inference this replaces was `source === 'platform' && provider ===
    // 'minio'`. An organisation's OWN MinIO under a platform-scope row - which
    // an operator can create and the installer never does - read as bundled
    // and was described to its administrator as installed by the installer.
    mockEffective.mockResolvedValue(
      effective({ source: 'platform', provider: 'minio', is_bundled: false })
    )

    await renderLoaded()

    expect(
      screen.queryByText('MinIO — bundled, installed by the installer')
    ).not.toBeInTheDocument()
    expect(screen.getByText('MinIO')).toBeInTheDocument()
  })

  it('trusts is_bundled even when the provider is not MinIO', async () => {
    // The other half of the mutation. The old inference could not describe a
    // bundled store on any other provider at all.
    mockEffective.mockResolvedValue(
      effective({
        source: 'platform',
        provider: 's3_compatible',
        provider_label: 'S3-compatible',
        is_bundled: true,
      })
    )

    await renderLoaded()

    expect(
      screen.getByText('MinIO — bundled, installed by the installer')
    ).toBeInTheDocument()
  })

  it('keeps the summary when the configuration list fails', async () => {
    mockEffective.mockResolvedValue(effective({ source: 'platform', is_bundled: true }))
    mockList.mockRejectedValue(new Error('boom'))

    await renderLoaded()

    expect(screen.getByText('MinIO — bundled, installed by the installer')).toBeInTheDocument()
    expect(screen.getByText(/could not be listed right now/)).toBeInTheDocument()
  })

  it('edits a draft and keeps the stored secret when the field is left blank', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'draft' })] })
    mockUpdate.mockResolvedValue(config({ status: 'draft', bucket: 'acme-evidence-2' }))

    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-edit-cfg-1'))

    const bucket = screen.getByTestId('evidence-storage-bucket')
    expect(bucket).toHaveValue('acme-evidence')
    // The secret is not read back, because there is nothing to read back.
    expect(screen.getByTestId('evidence-storage-secret')).toHaveValue('')

    await user.clear(bucket)
    await user.type(bucket, 'acme-evidence-2')
    await user.click(screen.getByTestId('evidence-storage-save'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled())
    const [, , patch] = mockUpdate.mock.calls[0]
    expect(patch.bucket).toBe('acme-evidence-2')
    expect(Object.keys(patch)).not.toContain('secret_access_key')
  })

  it('retires an active configuration through the API', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'active' })] })
    mockRetire.mockResolvedValue(config({ status: 'retired' }))

    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-retire-cfg-1'))

    await waitFor(() => expect(mockRetire).toHaveBeenCalledWith(ORG, 'cfg-1'))
  })

  // --- Phase 5 carry-forwards, fixed in Phase 6 ----------------------------

  it('re-gates Activate behind a fresh Test after a successful rotation (F1)', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'draft' })] })
    mockTest.mockResolvedValue({
      success: true,
      config_id: 'cfg-1',
      steps: [{ name: 'address', ok: true, status_code: null, error_class: null }],
    })
    mockRotate.mockResolvedValue(config({ status: 'draft', key_version: 2 }))

    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-test-cfg-1'))
    await waitFor(() =>
      expect(screen.getByTestId('evidence-storage-activate-cfg-1')).toBeEnabled()
    )

    await user.click(screen.getByTestId('evidence-storage-rotate-cfg-1'))
    await user.type(
      screen.getByTestId('evidence-storage-rotate-secret-cfg-1'),
      STORED_PLAINTEXT
    )
    await user.click(screen.getByTestId('evidence-storage-rotate-save-cfg-1'))
    await waitFor(() => expect(mockRotate).toHaveBeenCalled())

    // The probe proved the OLD credential. The row now holds a different one,
    // so the green tick has to go and Activate has to be re-gated.
    await waitFor(() =>
      expect(screen.getByTestId('evidence-storage-activate-cfg-1')).toBeDisabled()
    )
    expect(screen.queryByTestId('evidence-storage-report-cfg-1')).not.toBeInTheDocument()
  })

  it('re-gates Activate when an activation is refused without a probe report (F2)', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [config({ status: 'draft' })] })
    mockTest.mockResolvedValue({
      success: true,
      config_id: 'cfg-1',
      steps: [{ name: 'address', ok: true, status_code: null, error_class: null }],
    })
    // The encryption-key 409: no steps, because no probe ever ran.
    mockActivate.mockRejectedValue(
      conflict(
        {
          message: 'Credential encryption is not configured on this server',
          encryption_key_configured: false,
        },
        'Credential encryption is not configured on this server'
      )
    )

    await renderLoaded()
    await user.click(screen.getByTestId('evidence-storage-test-cfg-1'))
    await waitFor(() =>
      expect(screen.getByTestId('evidence-storage-activate-cfg-1')).toBeEnabled()
    )
    await user.click(screen.getByTestId('evidence-storage-activate-cfg-1'))

    expect(
      await screen.findByText('Credential encryption is not configured on this server')
    ).toBeInTheDocument()
    // The activation did not happen. Leaving the passing probe in place would
    // leave Activate enabled and invite the same refusal again.
    await waitFor(() =>
      expect(screen.getByTestId('evidence-storage-activate-cfg-1')).toBeDisabled()
    )
    expect(screen.queryByTestId('evidence-storage-report-cfg-1')).not.toBeInTheDocument()
  })

  it('renders the Phase 6 migration panel where one is given, and nothing otherwise', async () => {
    const { unmount } = await renderLoaded()
    expect(screen.queryByTestId('migration-panel')).not.toBeInTheDocument()
    unmount()

    render(
      <EvidenceStorageSettings
        organizationId={ORG}
        migrationPanel={<div data-testid="migration-panel">copy progress</div>}
      />
    )
    expect(await screen.findByTestId('migration-panel')).toBeInTheDocument()
  })
})
