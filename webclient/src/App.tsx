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
import LibraryPage from './components/library/LibraryPage'
import ScopingPage from './components/scoping/ScopingPage'
import EvidenceWorkspace from './components/EvidenceWorkspace'
import Dashboard from './components/Dashboard'
import MappingMatrix from './components/MappingMatrix'
import TasksPage from './components/TasksPage'
import SystemsManagement from './components/SystemsManagement'
import UserManagement from './components/UserManagement'
import TeamManagement from './components/TeamManagement'
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
import DocumentMap from './components/DocumentMap'
import DocumentsPage from './components/documents/DocumentsPage'
import DocGenSettingsCard from './components/documents/DocGenSettingsCard'
import CatalogUpgradePage from './components/platform/CatalogUpgradePage'
import TenantReconciliationBoard from './components/platform/TenantReconciliationBoard'
import CatalogChangelogPage from './components/CatalogChangelogPage'
import CatalogVersionCard from './components/CatalogVersionCard'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ThemeProvider } from './contexts/ThemeContext'
import { OrganizationProvider, useOrganization } from './contexts/OrganizationContext'
import { RiskProfileProvider } from './contexts/RiskProfileContext'
import GoogleSignIn from './components/GoogleSignIn'
import OidcSignIn from './components/OidcSignIn'
import { OIDC_ENABLED } from './data/authToken'
import CatalogOnboarding from './components/CatalogOnboarding'
import { getCatalogStatus } from './data/apiClient'
import {
  SYNCED_TABS,
  evidenceItemSearch,
  pushSearch,
  readAppLocation,
  readTabFromUrl,
  replaceSearch,
  searchForTab,
  withLibraryItem,
  withRiskItem,
  withVendorItem,
  withSystemItem,
  withTaskItem,
} from './data/appUrl'
import InviteAcceptance from './components/InviteAcceptance'
import OrgSwitcher from './components/OrgSwitcher'
import type { ClientSummary, ConsultantInvite } from './types'

type Tab = 'dashboard' | 'capability-posture' | 'library' | 'scoping' | 'evidence' | 'mapping-matrix' | 'tasks' | 'systems' | 'users' | 'consultant-portal' | 'risk-register' | 'vendors' | 'settings' | 'webhooks' | 'audit-log' | 'engagements' | 'cdm' | 'document-map' | 'documents' | 'platform-catalog' | 'platform-tenants' | 'catalog-changelog'

/**
 * Screen selection lives in `activeTab`; `data/appUrl.ts` owns the vocabulary
 * that mirrors it into the address bar, and this file decides when to write.
 *
 * `react-router-dom` is a dependency that drives nothing, and giving it the
 * wheel would touch all twenty-two screens below. So `?tab=` names the screen
 * instead — every destination the sidebar offers, so that each one survives a
 * reload, can be bookmarked and can be sent to a colleague (#810). The bare
 * path stays the dashboard's address.
 *
 * A `tab` naming anything `SYNCED_TABS` does not list is ignored rather than
 * honoured, and normalised off the URL on arrival.
 */

function AppContent() {
  const { isAuthenticated, authReady, user, isPlatformAdmin } = useAuth()
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
  // (ScopingPage) or updates the cache (EvidenceReview / FrameworkGapDetail
  // via onScopingDataChange) propagates to every consumer by construction —
  // no full page reload, no per-tab refetch hack.
  const { data: scopingDataRaw, isError: scopingFailed } = useQuery({
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
  // Library tab: the ?item= param from the URL (scf_id of the control to show, or null).
  // Seeded from the URL on mount so deep links land on the right control.
  const [libraryItem, setLibraryItem] = useState<string | null>(
    () => readAppLocation(window.location.search).libraryItem,
  )
  // Risk register tab: the ?risk= param from the URL (risk code to show in detail, or null).
  // Seeded from the URL on mount so deep links land on the right risk.
  const [riskItem, setRiskItem] = useState<string | null>(
    () => readAppLocation(window.location.search).riskItem,
  )
  // Vendors tab: the ?vendor= param from the URL (vendor id to show in detail, or null).
  // Seeded from the URL on mount so deep links land on the right vendor.
  const [vendorItem, setVendorItem] = useState<string | null>(
    () => readAppLocation(window.location.search).vendorItem,
  )
  // Systems tab: the ?system= param from the URL (system id to show in detail, or null).
  // Seeded from the URL on mount so deep links land on the right system.
  const [systemItem, setSystemItem] = useState<string | null>(
    () => readAppLocation(window.location.search).systemItem,
  )
  // Tasks tab: the ?task= param from the URL (task id to show in detail, or null).
  // Seeded from the URL on mount so deep links land on the right task.
  const [taskItem, setTaskItem] = useState<string | null>(
    () => readAppLocation(window.location.search).taskItem,
  )
  // One-shot "navigate me to this control" signal for ScopingPage.
  const [controlNavTarget, setControlNavTarget] = useState<string | undefined>(undefined)
  // Seeded from the URL: every sidebar destination is addressable, so a reload
  // or a pasted link lands on the screen it names rather than the dashboard
  // (#810). An unrecognised `?tab=` still resolves to the dashboard.
  const [activeTab, setActiveTab] = useState<Tab>(readTabFromUrl)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  // OSS onboarding: null = not yet checked, false = empty (show upload gate), true = seeded
  const [catalogSeeded, setCatalogSeeded] = useState<boolean | null>(null)
  // NOTE: isRefreshing state removed in #273 — React Query handles data freshness
  // Systems Registry state now owned by SystemsManagement (Phase 4 Task 6)

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

  // Handler for navigating to specific evidence from the dashboard, the header
  // search, a notification or the task list.
  //
  // The URL is the carrier (#785). It replaced a one-shot sessionStorage key
  // that only the sender and one reader knew about; the URL survives a reload,
  // can be sent to a colleague, and — being the single carrier — cannot
  // disagree with a deep link about which item is selected.
  // `pushState`, because arriving at an item is somewhere the user can go Back
  // from, exactly as selecting one inside the workspace is.
  const handleNavigateToEvidence = (evidenceId: string) => {
    pushSearch(evidenceItemSearch(window.location.search, evidenceId))
    setActiveTab('evidence')
  }

  // Handler for library tab ?item= changes.
  //
  // Navigation decisions (mirrors evidence's push/replace idiom):
  //   list → detail  : pushSearch  (libraryItem was null → new id)
  //   prev / next    : replaceSearch (libraryItem was non-null → new id; one entry)
  //   back / Esc     : pushSearch  (clearing item; user can press Back to return)
  //
  // App owns the push/replace decision; LibraryPage calls this for all cases.
  const handleLibraryItemChange = useCallback((id: string | null) => {
    const current = readAppLocation(window.location.search).libraryItem
    const next = withLibraryItem(window.location.search, id)
    if (id !== null && current !== null) {
      // prev/next: same "detail view" history entry
      replaceSearch(next)
    } else {
      // list→detail or back: new history entry
      pushSearch(next)
    }
    setLibraryItem(id)
  }, [])

  // Handler for risk register tab ?risk= changes.
  //
  // Navigation decisions (mirrors library's push/replace idiom):
  //   list → detail  : pushSearch  (riskItem was null → new code)
  //   prev / next    : replaceSearch (riskItem was non-null → new code; one entry)
  //   back / Esc     : pushSearch  (clearing risk; user can press Back to return)
  //
  // App owns the push/replace decision; RiskDashboard calls this for all cases.
  const handleRiskItemChange = useCallback((code: string | null) => {
    const current = readAppLocation(window.location.search).riskItem
    const next = withRiskItem(window.location.search, code)
    if (code !== null && current !== null) {
      // prev/next: same "detail view" history entry
      replaceSearch(next)
    } else {
      // list→detail or back: new history entry
      pushSearch(next)
    }
    setRiskItem(code)
  }, [])

  // Handler for vendor tab ?vendor= changes.
  //
  // Navigation decisions (mirrors risk's push/replace idiom):
  //   list → detail  : pushSearch  (vendorItem was null → new id)
  //   prev / next    : replaceSearch (vendorItem was non-null → new id; one entry)
  //   back / Esc     : pushSearch  (clearing vendor; user can press Back to return)
  //
  // App owns the push/replace decision; VendorManagement calls this for all cases.
  const handleVendorItemChange = useCallback((id: string | null) => {
    const current = readAppLocation(window.location.search).vendorItem
    const next = withVendorItem(window.location.search, id)
    if (id !== null && current !== null) {
      // prev/next: same "detail view" history entry
      replaceSearch(next)
    } else {
      // list→detail or back: new history entry
      pushSearch(next)
    }
    setVendorItem(id)
  }, [])

  // Handler for system tab ?system= changes.
  //
  // Navigation decisions (mirrors vendor's push/replace idiom):
  //   list → detail  : pushSearch  (systemItem was null → new id)
  //   prev / next    : replaceSearch (systemItem was non-null → new id; one entry)
  //   back / Esc     : pushSearch  (clearing system; user can press Back to return)
  //
  // App owns the push/replace decision; SystemsManagement calls this for all cases.
  const handleSystemItemChange = useCallback((id: string | null) => {
    const current = readAppLocation(window.location.search).systemItem
    const next = withSystemItem(window.location.search, id)
    if (id !== null && current !== null) {
      // prev/next: same "detail view" history entry
      replaceSearch(next)
    } else {
      // list→detail or back: new history entry
      pushSearch(next)
    }
    setSystemItem(id)
  }, [])

  // Handler for task tab ?task= changes.
  //
  // Navigation decisions (mirrors vendor's push/replace idiom):
  //   list → detail  : pushSearch  (taskItem was null → new id)
  //   prev / next    : replaceSearch (taskItem was non-null → new id; one entry)
  //   back / Esc     : pushSearch  (clearing task; user can press Back to return)
  //
  // App owns the push/replace decision; TasksPage calls this for all cases.
  const handleTaskItemChange = useCallback((id: string | null) => {
    const current = readAppLocation(window.location.search).taskItem
    const next = withTaskItem(window.location.search, id)
    if (id !== null && current !== null) {
      // prev/next: same "detail view" history entry
      replaceSearch(next)
    } else {
      // list→detail or back: new history entry
      pushSearch(next)
    }
    setTaskItem(id)
  }, [])

  // Handler for navigating to a specific control from the dashboard work
  // queue, a risk assessment, or a notification. `controlNavTarget` is the
  // "take me there" signal — distinct from `selectedId`, which only remembers
  // the last selection. ScopingPage clears it once acted on (onNavigationConsumed),
  // so arriving at Scoping later by other means does not re-trigger the search.
  const handleNavigateToControl = (scfId: string) => {
    setSelectedId(scfId)
    setControlNavTarget(scfId)
    setActiveTab('scoping')
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

  // Keep `?tab=` in step with the active screen. `searchForTab` returns null
  // whenever the URL already agrees, which is what keeps this from racing the
  // three components that own other parameters on the same URL — DocumentsPage
  // has `doc`/`mode`, EvidenceWorkspace has `view`, EvidenceReview has `item`.
  //
  // `pushState`, not `replaceState` (the question #785 left open, #810 closed).
  // Choosing a destination in the sidebar is a place the user went, and the
  // browser's Back button is how anyone expects to leave a place they went;
  // with `replaceState` the app wrote a history it then refused to let anyone
  // walk. It is only *within* a screen that replacing still applies — the
  // evidence auto-select correcting its own `item`, say — and those writers
  // are elsewhere and unchanged.
  //
  // The exception is the first pass. Whatever the address bar arrived with is
  // normalised then — an unknown `?tab=`, a stale `item` — and a correction to
  // a URL the user never chose is not somewhere to go Back to. Replacing it
  // also keeps the entry the user actually arrived on at the top of the stack,
  // so Back still leaves the app rather than bouncing off a rewritten entry.
  const tabUrlNormalised = useRef(false)
  useEffect(() => {
    const next = searchForTab(window.location.search, activeTab)
    if (next !== null) {
      if (tabUrlNormalised.current) pushSearch(next)
      else replaceSearch(next)
    }
    tabUrlNormalised.current = true
  }, [activeTab])

  // Browser Back and Forward. This acts when the URL names a synced tab, or
  // when we are leaving one for the dashboard, and otherwise leaves `activeTab`
  // alone. Also syncs libraryItem and riskItem from the URL (same event — the
  // tabs own their params while active; reading them here avoids separate
  // listeners that would contend with EvidenceWorkspace/EvidenceReview).
  useEffect(() => {
    const onPopState = () => {
      const fromUrl = readTabFromUrl()
      setActiveTab((current) =>
        SYNCED_TABS.some((tab) => tab === fromUrl || tab === current) ? fromUrl : current
      )
      const loc = readAppLocation(window.location.search)
      // Restore libraryItem from the history entry we're navigating to
      setLibraryItem(loc.libraryItem)
      // Restore riskItem from the history entry we're navigating to
      setRiskItem(loc.riskItem)
      // Restore vendorItem from the history entry we're navigating to
      setVendorItem(loc.vendorItem)
      // Restore systemItem from the history entry we're navigating to
      setSystemItem(loc.systemItem)
      // Restore taskItem from the history entry we're navigating to
      setTaskItem(loc.taskItem)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

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
        isPlatformAdmin={isPlatformAdmin}
        mobileOpen={mobileNavOpen}
        onMobileClose={() => setMobileNavOpen(false)}
      />
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onNavigateToEvidence={handleNavigateToEvidence}
        onNavigateToControl={handleNavigateToControl}
        isConsultant={isConsultant === true}
        clientOrgIds={clientOrgIds}
        onOrgSwitch={(org) => {
          toast.success(`Switched to ${org.name}`)
        }}
        onMobileNavToggle={() => setMobileNavOpen(open => !open)}
        mobileNavOpen={mobileNavOpen}
      />
      <main className="app-main">
        <div className="app-content">
          {activeTab === 'dashboard' && scopingData && (
            <Dashboard
              controls={controls}
              scopingData={scopingData}
              onScopingDataChange={setScopingData}
              onNavigateToEvidence={handleNavigateToEvidence}
              onNavigateToControl={handleNavigateToControl}
              onNavigateToScoping={() => setActiveTab('scoping')}
            />
          )}
          {activeTab === 'capability-posture' && scopingData && (
            <CapabilityPosture organizationId={scopingData.organizationId!} />
          )}
          {activeTab === 'library' && (
            <LibraryPage
              item={libraryItem}
              onItemChange={handleLibraryItemChange}
              scopingData={scopingData}
              erlData={erlData}
              frameworkNames={frameworkNames}
              onNavigateToEvidence={handleNavigateToEvidence}
              organizationId={scopingData?.organizationId ?? undefined}
              controls={controls}
            />
          )}
          {activeTab === 'scoping' && scopingData && (
            <ScopingPage
              organizationId={scopingData.organizationId!}
              erlData={erlData}
              frameworkNames={frameworkNames}
              initialSelectedId={selectedId}
              navigateToId={controlNavTarget}
              onNavigationConsumed={() => setControlNavTarget(undefined)}
              scopingData={scopingData}
              onScopingDataChange={setScopingData}
            />
          )}
          {activeTab === 'evidence' && scopingData && (
            <EvidenceWorkspace
              controls={controls}
              scopingData={scopingData}
              onScopingDataChange={setScopingData}
              erlData={erlData}
              evidenceTemplates={evidenceTemplates}
              organizationId={scopingData.organizationId!}
              onNavigateToSystems={() => setActiveTab('systems')}
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
              taskItem={taskItem}
              onTaskItemChange={handleTaskItemChange}
            />
          )}
          {activeTab === 'risk-register' && scopingData && (
            <RiskDashboard
              organizationId={scopingData.organizationId!}
              onNavigateToControl={handleNavigateToControl}
              riskItem={riskItem}
              onRiskItemChange={handleRiskItemChange}
            />
          )}
          {activeTab === 'vendors' && scopingData && (
            <VendorManagement
              organizationId={scopingData.organizationId!}
              vendorItem={vendorItem}
              onVendorItemChange={handleVendorItemChange}
            />
          )}
          {activeTab === 'systems' && scopingData && (
            <SystemsManagement
              organizationId={scopingData.organizationId!}
              collectionInterfaces={collectionInterfaces}
              systemItem={systemItem}
              onSystemItemChange={handleSystemItemChange}
            />
          )}
          {activeTab === 'users' && scopingData && (
            <>
              <UserManagement
                organizationId={scopingData.organizationId!}
              />
              {/* Teams sit with user management, not in settings: they are a
                  statement about people. They grant no permissions — org role
                  above is still the only thing access is decided on. */}
              <TeamManagement
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
          {activeTab === 'document-map' && scopingData && (
            <DocumentMap
              organizationId={scopingData.organizationId!}
              onOpenDocuments={() => setActiveTab('cdm')}
            />
          )}
          {activeTab === 'documents' && scopingData && (
            <DocumentsPage
              organizationId={scopingData.organizationId!}
              onOpenSettings={() => setActiveTab('settings')}
            />
          )}
          {/* Every screen above is gated on `scopingData` the same way, and a
              null renders nothing at all. That is survivable where the user
              clicked a tab and can click another, but this is the one tab a
              link can land on cold: `?tab=documents` reopens here after a
              reload, and `loading` clears when `loadAllData` finishes, which
              does not own this query. If it is still in flight the pane is
              briefly empty; if it failed it is empty for good, with nothing on
              screen to say so. Say which, rather than showing a blank page. */}
          {activeTab === 'documents' && !scopingData && (
            <div className="doc-tab-placeholder">
              {scopingFailed ? (
                <>
                  <p>Your scoping data could not be loaded, so documents cannot open.</p>
                  <button type="button" className="btn-secondary" onClick={() => window.location.reload()}>
                    Retry
                  </button>
                </>
              ) : (
                <p>Loading your scoped controls…</p>
              )}
            </div>
          )}
          {activeTab === 'audit-log' && currentOrg && (
            <AuditLogPage organizationId={currentOrg.id} />
          )}
          {activeTab === 'platform-catalog' && (
            <CatalogUpgradePage />
          )}
          {activeTab === 'platform-tenants' && (
            <TenantReconciliationBoard />
          )}
          {activeTab === 'catalog-changelog' && scopingData && (
            <CatalogChangelogPage organizationId={scopingData.organizationId!} />
          )}
          {activeTab === 'settings' && scopingData && (
            <div className="settings-page-layout">
              <nav className="settings-section-nav" aria-label="Settings sections">
                <a className="settings-section-nav-item" href="#settings-catalog-version">CATALOG VERSION</a>
                <a className="settings-section-nav-item" href="#settings-branding">ORGANISATION BRANDING</a>
                <a className="settings-section-nav-item" href="#settings-risk">RISK &amp; GOVERNANCE</a>
                <a className="settings-section-nav-item" href="#settings-docgen">DOCUMENT GENERATION</a>
                <a className="settings-section-nav-item" href="#settings-backups">BACKUPS</a>
                <p className="settings-section-nav-note">
                  Settings apply to this organisation only. Platform-wide catalog administration lives under Platform → Catalog.
                </p>
              </nav>
              <div className="settings-page-content">
                <div id="settings-catalog-version">
                  <CatalogVersionCard
                    organizationId={scopingData.organizationId!}
                  />
                </div>
                <div id="settings-branding">
                  <AppearanceSettings
                    organizationId={scopingData.organizationId!}
                  />
                </div>
                <div id="settings-risk">
                  <RiskProfileSettings
                    organizationId={scopingData.organizationId!}
                  />
                </div>
                <div id="settings-docgen">
                  <DocGenSettingsCard
                    organizationId={scopingData.organizationId!}
                  />
                </div>
                <div id="settings-backups">
                  <BackupRestore
                    organizationId={scopingData.organizationId!}
                  />
                </div>
              </div>
            </div>
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
