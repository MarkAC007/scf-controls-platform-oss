export interface FrameworkGroup {
  id: string
  label: string
  emoji: string
  prefixes: string[]
}

export const FRAMEWORK_GROUPS: FrameworkGroup[] = [
  {
    id: 'international',
    label: 'International Standards',
    emoji: '\u{1F310}',
    prefixes: ['iso_', 'iec_', 'bsi_', 'cobit_', 'coso_', 'apec_', 'oecd_']
  },
  {
    id: 'us_federal',
    label: 'US Federal',
    emoji: '\u{1F1FA}\u{1F1F8}',
    prefixes: [
      'us_fedramp_', 'us_nist_', 'us_cmmc_', 'us_hipaa_', 'us_sox_', 'us_glba_',
      'us_ffiec_', 'us_ferpa_', 'us_cjis_', 'us_irs_', 'us_nerc_', 'nist_', 'pci_dss_'
    ]
  },
  {
    id: 'us_state',
    label: 'US State Laws',
    emoji: '\u{1F5FA}\u{FE0F}',
    prefixes: [
      'us_ak_', 'us_ca_', 'us_co_', 'us_ct_', 'us_de_', 'us_fl_', 'us_il_',
      'us_in_', 'us_ia_', 'us_ky_', 'us_me_', 'us_mn_', 'us_mt_', 'us_ne_',
      'us_nh_', 'us_nj_', 'us_ny_', 'us_nc_', 'us_or_', 'us_ri_', 'us_tx_',
      'us_ut_', 'us_va_', 'us_vt_', 'us_wa_'
    ]
  },
  {
    id: 'emea',
    label: 'EMEA',
    emoji: '\u{1F1EA}\u{1F1FA}',
    prefixes: ['emea_']
  },
  {
    id: 'apac',
    label: 'APAC',
    emoji: '\u{1F30F}',
    prefixes: ['apac_']
  },
  {
    id: 'americas',
    label: 'Americas (ex-US)',
    emoji: '\u{1F30E}',
    prefixes: ['americas_']
  },
  {
    id: 'industry',
    label: 'Industry Standards',
    emoji: '\u{1F3ED}',
    prefixes: ['aicpa_', 'swift_', 'tisax_', 'csa_', 'mitre_', 'govramp_', 'sparta', 'us_cert_rmm_']
  },
  {
    id: 'scf_risk',
    label: 'SCF Core & Risk/Threat',
    emoji: '\u{1F4CB}',
    prefixes: ['scf_core_', 'risk_r_', 'threat_']
  }
]

export const OTHER_GROUP: FrameworkGroup = {
  id: 'other',
  label: 'Other',
  emoji: '\u{1F4C1}',
  prefixes: []
}

export function getFrameworkGroup(frameworkKey: string): string {
  for (const group of FRAMEWORK_GROUPS) {
    if (group.prefixes.some(p => frameworkKey.startsWith(p))) {
      return group.id
    }
  }
  return 'other'
}
