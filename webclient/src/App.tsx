import { useEffect, useMemo, useState, useCallback, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Toaster, toast } from 'react-hot-toast'
import { enrichControls, loadAllData } from './data/loaders'
import { loadScopedControls } from './data/scopingService'
import {
  getConsultantClients,
  getConsultantInvites,
  createConsultantInvite,
  cancelConsultantInvite,
  checkConsultantStatus,
  registerAsConsultant,
  createClientOrganisation,
  inviteOrgAdmin,
  transformClientSummary,
  transformConsultantInvite
} from './data/apiClient'
import type { EnrichedControl, ScopedControlsFile, CollectionInterfacesFile, ERLFile, FrameworkNameMap, EvidenceTemplatesFile } from './types'
import ControlList from './components/ControlList'
import ControlDetail from './components/ControlDetail'
import ControlScoping from './components/ControlScoping'
import EvidenceWorkspace from './components/EvidenceWorkspace'
import Dashboard from './components/Dashboard'
import MappingMatrix from './components/MappingMatrix'
import TasksPage from './components/TasksPage'
import SystemsRegistry from './components/SystemsRegistry'
import AddSystemModal from './components/AddSystemModal'
import UserManagement from './components/UserManagement'
import type { System } from './types'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import Footer from './components/Footer'
import { ConsultantDashboard } from './components/consultant'
import RiskDashboard from './components/RiskDashboard'
import VendorManagement from './components/VendorManagement'
import CapabilityPosture from './components/CapabilityPosture'
import RiskProfileSettings from './components/RiskProfileSettings'
import AppearanceSettings from './components/AppearanceSettings'
import ApiKeyManagement from './components/ApiKeyManagement'
import WebhookManagement from './components/WebhookManagement'
import BackupRestore from './components/BackupRestore'
import AuditLogPage from './components/AuditLogPage'
import EngagementsPage from './components/EngagementsPage'
import CDMWorkspace from './components/CDMWorkspace'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ThemeProvider } from './contexts/ThemeContext'
import { OrganizationProvider, useOrganization } from './contexts/OrganizationContext'
import { RiskProfileProvider } from './contexts/RiskProfileContext'
import GoogleSignIn from './components/GoogleSignIn'
import OidcSignIn from './components/OidcSignIn'
import { OIDC_ENABLED } from './data/authToken'
import CatalogOnboarding from './components/CatalogOnboarding'
import { getCatalogStatus } from './data/apiClient'
import InviteAcceptance from './components/InviteAcceptance'
import OrgSwitcher from './components/OrgSwitcher'
import type { ClientSummary, ConsultantInvite } from './types'

type Tab = 'dashboard' | 'capability-posture' | 'library' | 'scoping' | 'evidence' | 'mapping-matrix' | 'tasks' | 'systems' | 'users' | 'consultant-portal' | 'risk-register' | 'vendors' | 'settings' | 'webhooks' | 'audit-log' | 'engagements' | 'cdm'

function AppContent() {
  const { isAuthenticated, authReady, user } = useAuth()
  const { currentOrg, isLoading: orgLoading, switchOrganization } = useOrganization()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [controls, setControls] = useState<EnrichedControl[]>([])
  const [collectionInterfaces, setCollectionInterfaces] = useState<CollectionInterfacesFile>({})
  const [erlData, setErlData] = useState<ERLFile>({})
  const [evidenceTemplates, setEvidenceTemplates] = useState<EvidenceTemplatesFile>({})
  const [frameworkNames, setFrameworkNames] = useState<FrameworkNameMap>({})
  const queryClient = useQueryClient()

  // Scoping data — single source of truth for the whole app.
  // React Query owns it, so any writer that invalidates ['scoping-data']
  // (ControlScoping) or updates the cache (EvidenceReview / FrameworkGapDetail
  // via onScopingDataChange) propagates to every consumer by construction —
  // no full page reload, no per-tab refetch hack.
  const { data: scopingDataRaw } = useQuery({
    queryKey: ['scoping-data', currentOrg?.id],
    queryFn: async (): Promise<ScopedControlsFile> => {
      const scoping = await loadScopedControls()
      if (scoping) return scoping
      // No scoping data yet — initialise an empty structure from org context.
      return {
        organizationId: currentOrg!.id,
        organization: {
          name: currentOrg!.name,
          id: currentOrg!.id,
          created_at: currentOrg!.created_at,
          updated_at: currentOrg!.updated_at
        },
        scoped_controls: [],
        evidence_tracking: {},
        metadata: { version: '1.0', total_selected: 0, total_implemented: 0 }
      }
    },
    enabled: authReady && isAuthenticated && !!currentOrg && !orgLoading,
    staleTime: 0
  })
  const scopingData = scopingDataRaw ?? null

  // Optimistic writer used by evidence/dashboard flows (onScopingDataChange).
  // Keeps the same call shape as the old setScopingData(value) so prop sites
  // are unchanged; writes straight into the shared query cache.
  const setScopingData = useCallback((data: ScopedControlsFile) => {
    queryClient.setQueryData(['scoping-data', currentOrg?.id], data)
  }, [queryClient, currentOrg?.id])

  const [selectedId, setSelectedId] = useState<string | undefined>(undefined)
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  // OSS onboarding: null = not yet checked, false = empty (show upload gate), true = seeded
  const [catalogSeeded, setCatalogSeeded] = useState<boolean | null>(null)
  // NOTE: isRefreshing state removed in #273 — React Query handles data freshness
  // Systems Registry state
  const [showSystemModal, setShowSystemModal] = useState(false)
  const [editingSystem, setEditingSystem] = useState<System | null>(null)
  const [systemsKey, setSystemsKey] = useState(0) // Key to force refresh

  // Consultant Portal state
  const [isConsultant, setIsConsultant] = useState<boolean | null>(null) // null = loading
  const [consultantClients, setConsultantClients] = useState<ClientSummary[]>([])
  const [consultantInvites, setConsultantInvites] = useState<ConsultantInvite[]>([])
  const [consultantLoading, setConsultantLoading] = useState(false)
  const [consultantError, setConsultantError] = useState<string | null>(null)
  const [isRegistering, setIsRegistering] = useState(false)

  // Invite acceptance state - check URL for invite token and type
  const [inviteToken, setInviteToken] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('invite')
  })
  const [inviteType, setInviteType] = useState<'consultant' | 'org'>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('invite_type') === 'org' ? 'org' : 'consultant'
  })

  // Handler for navigating to specific evidence from task dashboard
  const handleNavigateToEvidence = (evidenceId: string) => {
    // Store evidence ID in sessionStorage for EvidenceReview to pick up
    sessionStorage.setItem('navigate_to_evidence', evidenceId)
    setActiveTab('evidence')
  }

  // Handler for invite acceptance completion
  const handleInviteComplete = () => {
    // Clear invite token/type and URL parameters
    setInviteToken(null)
    setInviteType('consultant')
    const url = new URL(window.location.href)
    url.searchParams.delete('invite')
    url.searchParams.delete('invite_type')
    window.history.replaceState({}, '', url.toString())
    // Reload data to get new org
    window.location.reload()
  }

  // Handler for invite cancellation/decline
  const handleInviteCancel = () => {
    setInviteToken(null)
    setInviteType('consultant')
    const url = new URL(window.location.href)
    url.searchParams.delete('invite')
    url.searchParams.delete('invite_type')
    window.history.replaceState({}, '', url.toString())
  }

  // Check if user is a consultant (called once on auth ready)
  const checkConsultantAccess = useCallback(async () => {
    try {
      const status = await checkConsultantStatus()
      setIsConsultant(
        (status.is_consultant && status.is_active) || status.has_consultant_subscription
      )
    } catch (err: any) {
      console.error('Failed to check consultant status:', err)
      setIsConsultant(false)
    }
  }, [])

  // Register as consultant
  const handleRegisterAsConsultant = useCallback(async (companyName?: string) => {
    setIsRegistering(true)
    try {
      await registerAsConsultant(companyName)
      setIsConsultant(true)
      toast.success('Successfully registered as a consultant!')
      // Now load consultant data
      await loadConsultantDataInternal()
    } catch (err: any) {
      console.error('Failed to register as consultant:', err)
      toast.error(err.message || 'Failed to register as consultant')
    } finally {
      setIsRegistering(false)
    }
  }, [])

  // Load consultant portal data (internal - only for confirmed consultants)
  const loadConsultantDataInternal = useCallback(async () => {
    setConsultantLoading(true)
    setConsultantError(null)
    try {
      // Fetch clients and invites in parallel
      const [clientsResponse, invitesResponse] = await Promise.all([
        getConsultantClients(0, 100, true),
        getConsultantInvites('pending')
      ])

      // Transform backend responses to frontend format
      const clients = clientsResponse.map(transformClientSummary)
      const invites = invitesResponse.map(transformConsultantInvite)

      setConsultantClients(clients)
      setConsultantInvites(invites)
    } catch (err: any) {
      console.error('Failed to load consultant data:', err)
      setConsultantError(err.message || 'Failed to load consultant data')
    } finally {
      setConsultantLoading(false)
    }
  }, [])

  // Load consultant portal data (public - checks consultant status first)
  const loadConsultantData = useCallback(async () => {
    // First check if user is a consultant
    if (isConsultant === null) {
      await checkConsultantAccess()
      return // Will be called again when isConsultant is set
    }

    if (!isConsultant) {
      // Not a consultant - don't try to load data
      setConsultantClients([])
      setConsultantInvites([])
      return
    }

    await loadConsultantDataInternal()
  }, [isConsultant, checkConsultantAccess, loadConsultantDataInternal])

  // Handle consultant invite
  const handleInviteClient = useCallback(async (email: string, orgName: string) => {
    try {
      await createConsultantInvite(email, orgName)
      toast.success(`Invitation sent to ${email}`)
      // Refresh the invites list
      const invitesResponse = await getConsultantInvites('pending')
      setConsultantInvites(invitesResponse.map(transformConsultantInvite))
    } catch (err: any) {
      console.error('Failed to create invite:', err)
      toast.error(err.message || 'Failed to send invitation')
      throw err // Re-throw so the modal knows it failed
    }
  }, [])

  // Handle cancel invite
  const handleCancelInvite = useCallback(async (inviteId: string) => {
    try {
      await cancelConsultantInvite(inviteId)
      toast.success('Invitation cancelled')
      // Remove from local state
      setConsultantInvites(prev => prev.filter(inv => inv.id !== inviteId))
    } catch (err: any) {
      console.error('Failed to cancel invite:', err)
      toast.error(err.message || 'Failed to cancel invitation')
    }
  }, [])

  // Handle creating a client organisation (two-step flow: step 1)
  const handleCreateOrg = useCallback(async (orgName: string) => {
    const result = await createClientOrganisation(orgName)
    toast.success(`Organisation "${result.name}" created`)
    // Refresh consultant data to show the new org
    await loadConsultantDataInternal()
    return { id: result.id, name: result.name }
  }, [loadConsultantDataInternal])

  // Handle inviting an admin to a pre-created org (two-step flow: step 2)
  const handleInviteAdmin = useCallback(async (orgId: string, email: string) => {
    await inviteOrgAdmin(orgId, email)
    toast.success(`Invitation sent to ${email}`)
    // Refresh invites list
    const invitesResponse = await getConsultantInvites('pending')
    setConsultantInvites(invitesResponse.map(transformConsultantInvite))
  }, [])

  // Derive client org IDs for the header org switcher
  const clientOrgIds = useMemo(
    () => consultantClients.map(c => c.organization_id),
    [consultantClients]
  )

  // Load consultant data when tab becomes active (only if confirmed consultant)
  useEffect(() => {
    if (activeTab === 'consultant-portal' && isAuthenticated && isConsultant) {
      loadConsultantDataInternal()
    }
  }, [activeTab, isAuthenticated, isConsultant, loadConsultantDataInternal])

  // Load initial data
  const loadData = async (showLoadingIndicator = true) => {
    // Wait for org context to be ready
    if (!currentOrg) {
      console.log('⏳ Waiting for organization context...')
      return
    }

    // showLoadingIndicator param retained for initial load spinner
    try {
      const { controls, mappings, erl, frameworkNames, collectionInterfaces, evidenceTemplates } = await loadAllData()

      const enriched = enrichControls(controls, mappings, erl, frameworkNames)
      setControls(enriched)
      setCollectionInterfaces(collectionInterfaces)
      setEvidenceTemplates(evidenceTemplates)
      setErlData(erl)
      setFrameworkNames(frameworkNames)

      // Scoping data is loaded by the ['scoping-data'] React Query above,
      // which is the single source of truth. loadData() no longer owns it.

      if (!selectedId && enriched.length > 0) {
        setSelectedId(enriched[0]?.scf_id)
      }

    } catch (e: any) {
      console.error('Failed to load data:', e)
      if (showLoadingIndicator) {
        setError(e?.message || 'Failed to load data')
      }
    } finally {
      setLoading(false)
    }
  }

  // Initial load - only after authentication is ready and org context is set
  useEffect(() => {
    if (authReady && isAuthenticated && currentOrg && !orgLoading) {
      console.log(`🔐 Auth + org ready (${currentOrg.name}), loading data...`)
      loadData(true)
    }
  }, [authReady, isAuthenticated, currentOrg, orgLoading])

  // OSS onboarding: once authenticated, check whether the SCF catalogue is
  // seeded. If empty (fresh self-hosted install), the upload gate is shown
  // before any org/data loading. SaaS deploys are always seeded → no-op.
  useEffect(() => {
    if (authReady && isAuthenticated && catalogSeeded === null) {
      getCatalogStatus()
        .then((s) => setCatalogSeeded(s.seeded))
        .catch(() => setCatalogSeeded(true)) // fail open: don't block on a status hiccup
    }
  }, [authReady, isAuthenticated, catalogSeeded])

  // Check consultant status when authenticated
  useEffect(() => {
    if (authReady && isAuthenticated && isConsultant === null) {
      console.log('🔍 Checking consultant status...')
      checkConsultantAccess()
    }
  }, [authReady, isAuthenticated, isConsultant, checkConsultantAccess])

  // Load consultant client list once for header org switcher (regardless of active tab)
  const consultantDataLoadedRef = useRef(false)
  useEffect(() => {
    if (isConsultant === true && !consultantDataLoadedRef.current) {
      consultantDataLoadedRef.current = true
      loadConsultantDataInternal()
    }
  }, [isConsultant, loadConsultantDataInternal])

  // NOTE: Legacy 30s polling and input-focus tracking removed in #273.
  // Saves are now immediate (debounced 300ms) via React Query.

  const selected = useMemo(
    () => controls.find(c => c.scf_id === selectedId),
    [controls, selectedId]
  )

  // Show invite acceptance flow if there's an invite token in URL
  // This takes priority over normal auth flow
  if (inviteToken) {
    return (
      <InviteAcceptance
        token={inviteToken}
        inviteType={inviteType}
        onComplete={handleInviteComplete}
        onCancel={handleInviteCancel}
      />
    )
  }

  // Check authentication state
  if (!authReady) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <div className="loading-spinner" />
          <div className="loading-text">Checking authentication</div>
          <div className="loading-subtext">Verifying your credentials...</div>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return OIDC_ENABLED ? <OidcSignIn /> : <GoogleSignIn />
  }

  // OSS onboarding gate: a fresh self-hosted install has no SCF catalogue
  // (it's licensed and not bundled). Prompt for the SCF Excel before loading.
  if (catalogSeeded === null) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <div className="loading-spinner" />
          <div className="loading-text">Checking catalogue</div>
          <div className="loading-subtext">Verifying your SCF catalogue is loaded...</div>
        </div>
      </div>
    )
  }
  if (catalogSeeded === false) {
    return <CatalogOnboarding onSeeded={() => setCatalogSeeded(true)} />
  }

  // Loading org context
  if (orgLoading) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <div className="loading-spinner" />
          <div className="loading-text">Loading organisations</div>
          <div className="loading-subtext">Fetching your accessible organisations...</div>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <div className="loading-spinner" />
          <div className="loading-text">Loading data</div>
          <div className="loading-subtext">Preparing your compliance workspace...</div>
        </div>
      </div>
    )
  }
  if (error) {
    return <div className="error">Error: {error}</div>
  }

  return (
    <div className="app-layout">
      <Sidebar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        showConsultantPortal={isConsultant === true}
      />
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onNavigateToEvidence={handleNavigateToEvidence}
        isConsultant={isConsultant === true}
        clientOrgIds={clientOrgIds}
        onOrgSwitch={(org) => {
          toast.success(`Switched to ${org.name}`)
        }}
      />
      <main className="app-main">
        <div className="app-content">
          {activeTab === 'dashboard' && scopingData && (
            <Dashboard
              controls={controls}
              scopingData={scopingData}
              onScopingDataChange={setScopingData}
            />
          )}
          {activeTab === 'capability-posture' && scopingData && (
            <CapabilityPosture organizationId={scopingData.organizationId!} />
          )}
          {activeTab === 'library' && (
            <div className="layout">
              <ControlList
                selectedId={selectedId}
                onSelect={setSelectedId}
                collectionInterfaces={collectionInterfaces}
                erlData={erlData}
                frameworkNames={frameworkNames}
              />
              <ControlDetail
                control={selected}
                scopingData={scopingData}
                organizationId={scopingData?.organizationId ?? undefined}
                onNavigateToEvidence={handleNavigateToEvidence}
              />
            </div>
          )}
          {activeTab === 'scoping' && scopingData && (
            <ControlScoping
              organizationId={scopingData.organizationId!}
              erlData={erlData}
              frameworkNames={frameworkNames}
              initialSelectedId={selectedId}
            />
          )}
          {activeTab === 'evidence' && scopingData && (
            <EvidenceWorkspace
              controls={controls}
              scopingData={scopingData}
              onScopingDataChange={setScopingData}
              collectionInterfaces={collectionInterfaces}
              erlData={erlData}
              evidenceTemplates={evidenceTemplates}
              organizationId={scopingData.organizationId!}
            />
          )}
          {activeTab === 'mapping-matrix' && (
            <MappingMatrix
              controls={controls}
              scopingData={scopingData}
            />
          )}
          {activeTab === 'tasks' && scopingData && (
            <TasksPage
              onNavigateToEvidence={handleNavigateToEvidence}
              organizationId={scopingData.organizationId!}
            />
          )}
          {activeTab === 'risk-register' && scopingData && (
            <RiskDashboard
              organizationId={scopingData.organizationId!}
              onNavigateToControl={(scfId) => {
                setSelectedId(scfId)
                setActiveTab('scoping')
              }}
            />
          )}
          {activeTab === 'vendors' && scopingData && (
            <VendorManagement
              organizationId={scopingData.organizationId!}
            />
          )}
          {activeTab === 'systems' && scopingData && (
            <>
              <SystemsRegistry
                key={systemsKey}
                organizationId={scopingData.organizationId!}
                collectionInterfaces={collectionInterfaces}
                onAddSystem={() => {
                  setEditingSystem(null)
                  setShowSystemModal(true)
                }}
                onEditSystem={(system) => {
                  setEditingSystem(system)
                  setShowSystemModal(true)
                }}
                onViewSystem={(system) => {
                  setEditingSystem(system)
                  setShowSystemModal(true)
                }}
              />
              {showSystemModal && (
                <AddSystemModal
                  organizationId={scopingData.organizationId!}
                  editSystem={editingSystem}
                  onClose={() => {
                    setShowSystemModal(false)
                    setEditingSystem(null)
                  }}
                  onSuccess={() => {
                    setShowSystemModal(false)
                    setEditingSystem(null)
                    setSystemsKey(prev => prev + 1) // Force refresh
                  }}
                />
              )}
            </>
          )}
          {activeTab === 'users' && scopingData && (
            <>
              <UserManagement
                organizationId={scopingData.organizationId!}
              />
              <ApiKeyManagement
                organizationId={scopingData.organizationId!}
              />
            </>
          )}
          {activeTab === 'webhooks' && scopingData && (
            <WebhookManagement
              organizationId={scopingData.organizationId!}
            />
          )}
          {activeTab === 'engagements' && scopingData && (
            <EngagementsPage organizationId={scopingData.organizationId!} />
          )}
          {activeTab === 'cdm' && scopingData && (
            <CDMWorkspace organizationId={scopingData.organizationId!} />
          )}
          {activeTab === 'audit-log' && currentOrg && (
            <AuditLogPage organizationId={currentOrg.id} />
          )}
          {activeTab === 'settings' && scopingData && (
            <>
              <AppearanceSettings
                organizationId={scopingData.organizationId!}
              />
              <RiskProfileSettings
                organizationId={scopingData.organizationId!}
              />
              <BackupRestore
                organizationId={scopingData.organizationId!}
              />
            </>
          )}
          {activeTab === 'consultant-portal' && scopingData && (
            // Check consultant status first
            isConsultant === null ? (
              <div className="consultant-loading">
                <div className="loading-spinner" />
                <p>Checking consultant access...</p>
              </div>
            ) : !isConsultant ? (
              // Not a consultant - show registration prompt
              <div className="consultant-registration">
                <div className="consultant-registration-content">
                  <h2>Consultant Portal</h2>
                  <p>
                    The Consultant Portal allows GRC consultants to manage multiple
                    client organisations from a single dashboard.
                  </p>
                  <p>
                    To access this feature, you need to register as a consultant.
                    This is a one-time registration that enables multi-client
                    management capabilities.
                  </p>
                  <div className="registration-form">
                    <input
                      type="text"
                      id="company-name"
                      placeholder="Company/Consultancy Name (optional)"
                      className="registration-input"
                    />
                    <button
                      className="btn-primary"
                      disabled={isRegistering}
                      onClick={() => {
                        const input = document.getElementById('company-name') as HTMLInputElement
                        handleRegisterAsConsultant(input?.value || undefined)
                      }}
                    >
                      {isRegistering ? 'Registering...' : 'Register as Consultant'}
                    </button>
                  </div>
                </div>
              </div>
            ) : consultantLoading ? (
              <div className="consultant-loading">
                <div className="loading-spinner" />
                <p>Loading consultant portal...</p>
              </div>
            ) : consultantError ? (
              <div className="consultant-error">
                <p>Error: {consultantError}</p>
                <button onClick={loadConsultantDataInternal} className="btn-primary">
                  Retry
                </button>
              </div>
            ) : (
              <ConsultantDashboard
                clients={consultantClients}
                currentOrgId={scopingData.organizationId}
                pendingInvites={consultantInvites}
                onCancelInvite={handleCancelInvite}
                onInviteClient={handleInviteClient}
                onCreateOrg={handleCreateOrg}
                onInviteAdmin={handleInviteAdmin}
              />
            )
          )}
        </div>
      </main>
      <Footer />
    </div>
  )
}

// Export wrapped version with AuthProvider, OrganizationProvider, ThemeProvider, and RiskProfileProvider
export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <OrganizationProvider>
          <RiskProfileProvider>
            <Toaster
              position="top-right"
              toastOptions={{
                duration: 4000,
                style: {
                  background: '#363636',
                  color: '#fff',
                },
                success: {
                  duration: 3000,
                  iconTheme: {
                    primary: '#4ade80',
                    secondary: '#fff',
                  },
                },
                error: {
                  duration: 5000,
                  iconTheme: {
                    primary: '#ef4444',
                    secondary: '#fff',
                  },
                },
              }}
            />
            <AppContent />
          </RiskProfileProvider>
        </OrganizationProvider>
      </AuthProvider>
    </ThemeProvider>
  )
}
