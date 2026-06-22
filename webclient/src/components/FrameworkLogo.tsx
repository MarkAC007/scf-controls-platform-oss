interface FrameworkLogoProps {
  frameworkName: string
  size?: number
}

interface LogoConfig {
  abbr: string
  bg: string
  color: string
  fontSize?: number
}

/** Generate initials from a framework display name */
function getInitials(name: string): string {
  // Strip version numbers and punctuation, take leading caps
  const words = name
    .replace(/[^a-zA-Z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length > 0)

  // Prefer words that start with a capital letter or are all-caps
  const sigWords = words.filter(w => /^[A-Z]/.test(w)).slice(0, 3)
  if (sigWords.length >= 2) {
    return sigWords.map(w => w[0]).join('').toUpperCase()
  }
  return words
    .slice(0, 2)
    .map(w => w[0])
    .join('')
    .toUpperCase() || 'F'
}

/** Deterministic color palette from a string hash */
function hashColor(str: string): { bg: string; color: string } {
  const palette = [
    { bg: '#4A90D9', color: '#fff' },
    { bg: '#7B68EE', color: '#fff' },
    { bg: '#20B2AA', color: '#fff' },
    { bg: '#FF7F50', color: '#fff' },
    { bg: '#708090', color: '#fff' },
    { bg: '#2E8B57', color: '#fff' },
    { bg: '#B8860B', color: '#fff' },
    { bg: '#CD853F', color: '#fff' },
    { bg: '#6495ED', color: '#fff' },
    { bg: '#9370DB', color: '#fff' },
    { bg: '#3CB371', color: '#fff' },
    { bg: '#DC143C', color: '#fff' },
  ]
  let hash = 0
  for (const ch of str) hash = (hash * 31 + ch.charCodeAt(0)) | 0
  return palette[Math.abs(hash) % palette.length]
}

function getLogoConfig(frameworkName: string): LogoConfig {
  const n = frameworkName.toLowerCase()

  // ── ISO family ──────────────────────────────────────────────────────
  if (n.includes('iso') && n.includes('27001'))
    return { abbr: 'ISO\n27001', bg: '#1A237E', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('27002'))
    return { abbr: 'ISO\n27002', bg: '#1A237E', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('27017'))
    return { abbr: 'ISO\n27017', bg: '#283593', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('27018'))
    return { abbr: 'ISO\n27018', bg: '#283593', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('27701'))
    return { abbr: 'ISO\n27701', bg: '#3949AB', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('29100'))
    return { abbr: 'ISO\n29100', bg: '#3949AB', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('22301'))
    return { abbr: 'ISO\n22301', bg: '#1565C0', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('42001'))
    return { abbr: 'ISO\n42001', bg: '#1976D2', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && (n.includes('31000') || n.includes('31010')))
    return { abbr: 'ISO\n31000', bg: '#0288D1', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('sae'))
    return { abbr: 'ISO\nSAE', bg: '#1565C0', color: '#fff', fontSize: 10 }
  if (n.includes('iso') && n.includes('21434'))
    return { abbr: 'ISO\n21434', bg: '#1976D2', color: '#fff', fontSize: 10 }
  if (n.includes('iso'))
    return { abbr: 'ISO', bg: '#1A237E', color: '#fff' }

  // ── NIST family ─────────────────────────────────────────────────────
  if (n.includes('nist') && (n.includes('800-53') || n.includes('800_53') || n.includes('sp 800-53')))
    return { abbr: 'NIST\n800-53', bg: '#002868', color: '#fff', fontSize: 9 }
  if (n.includes('nist') && (n.includes('800-171') || n.includes('800_171')))
    return { abbr: 'NIST\n800-171', bg: '#003A70', color: '#fff', fontSize: 8 }
  if (n.includes('nist') && (n.includes('800-82') || n.includes('800_82')))
    return { abbr: 'NIST\n800-82', bg: '#004080', color: '#fff', fontSize: 9 }
  if (n.includes('nist') && (n.includes('800-161') || n.includes('800_161')))
    return { abbr: 'NIST\n800-161', bg: '#005B96', color: '#fff', fontSize: 8 }
  if (n.includes('nist') && (n.includes('800-160') || n.includes('800_160')))
    return { abbr: 'NIST\n800-160', bg: '#005B96', color: '#fff', fontSize: 8 }
  if (n.includes('nist') && (n.includes('800-172') || n.includes('800_172')))
    return { abbr: 'NIST\n800-172', bg: '#006BA6', color: '#fff', fontSize: 8 }
  if (n.includes('nist') && (n.includes('800-37') || n.includes('800_37')))
    return { abbr: 'NIST\nRMF', bg: '#004A7C', color: '#fff', fontSize: 9 }
  if (n.includes('nist') && (n.includes('800-207') || n.includes('800_207')))
    return { abbr: 'NIST\nZTA', bg: '#0057A0', color: '#fff', fontSize: 9 }
  if (n.includes('nist') && (n.includes('csf') || n.includes('cybersecurity framework')))
    return { abbr: 'NIST\nCSF', bg: '#006BA6', color: '#fff', fontSize: 10 }
  if (n.includes('nist') && n.includes('privacy'))
    return { abbr: 'NIST\nPRIV', bg: '#0072CE', color: '#fff', fontSize: 9 }
  if (n.includes('nist') && n.includes('ai'))
    return { abbr: 'NIST\nAI', bg: '#2196F3', color: '#fff', fontSize: 10 }
  if (n.includes('nist'))
    return { abbr: 'NIST', bg: '#002868', color: '#fff' }

  // ── GDPR / EU Data Protection ────────────────────────────────────────
  if (n.includes('gdpr') || n.includes('general data protection regulation'))
    return { abbr: 'GDPR', bg: '#003087', color: '#FFD700', fontSize: 10 }

  // ── PCI DSS ──────────────────────────────────────────────────────────
  if (n.includes('pci') && n.includes('dss'))
    return { abbr: 'PCI\nDSS', bg: '#6A0DAD', color: '#fff', fontSize: 10 }
  if (n.includes('pci'))
    return { abbr: 'PCI', bg: '#6A0DAD', color: '#fff' }

  // ── HIPAA ────────────────────────────────────────────────────────────
  if (n.includes('hipaa'))
    return { abbr: 'HIPAA', bg: '#2E7D32', color: '#fff', fontSize: 9 }

  // ── SOC 2 / AICPA ────────────────────────────────────────────────────
  if (n.includes('soc 2') || n.includes('aicpa') || n.includes('trust services criteria'))
    return { abbr: 'SOC\n2', bg: '#00796B', color: '#fff', fontSize: 10 }

  // ── FedRAMP ──────────────────────────────────────────────────────────
  if (n.includes('fedramp'))
    return { abbr: 'Fed\nRAMP', bg: '#BF0A30', color: '#fff', fontSize: 10 }

  // ── CMMC ─────────────────────────────────────────────────────────────
  if (n.includes('cmmc') || n.includes('cybersecurity maturity model certification'))
    return { abbr: 'CMMC', bg: '#003A70', color: '#fff', fontSize: 9 }

  // ── CIS Controls ─────────────────────────────────────────────────────
  if (n.includes('critical security controls') || (n.includes('cis') && n.includes('csc')))
    return { abbr: 'CIS', bg: '#1B5E20', color: '#fff' }

  // ── CSA ──────────────────────────────────────────────────────────────
  if (n.includes('cloud controls matrix') || (n.includes('csa') && n.includes('ccm')))
    return { abbr: 'CSA\nCCM', bg: '#01579B', color: '#fff', fontSize: 10 }
  if (n.includes('csa'))
    return { abbr: 'CSA', bg: '#0277BD', color: '#fff' }

  // ── COBIT ────────────────────────────────────────────────────────────
  if (n.includes('cobit'))
    return { abbr: 'COBIT', bg: '#1A237E', color: '#fff', fontSize: 9 }

  // ── COSO ─────────────────────────────────────────────────────────────
  if (n.includes('coso'))
    return { abbr: 'COSO', bg: '#827717', color: '#fff' }

  // ── OWASP ────────────────────────────────────────────────────────────
  if (n.includes('owasp'))
    return { abbr: 'OWASP', bg: '#E65100', color: '#fff', fontSize: 8 }

  // ── NIS2 ─────────────────────────────────────────────────────────────
  if (n.includes('nis2') || (n.includes('nis') && n.includes('2022/2555')))
    return { abbr: 'NIS2', bg: '#003087', color: '#fff', fontSize: 10 }

  // ── DORA ─────────────────────────────────────────────────────────────
  if (n.includes('dora') || n.includes('digital operational resilience'))
    return { abbr: 'DORA', bg: '#1565C0', color: '#fff' }

  // ── EU AI Act ────────────────────────────────────────────────────────
  if (n.includes('ai act') || (n.includes('eu') && n.includes('artificial intelligence') && n.includes('act')))
    return { abbr: 'EU\nAI', bg: '#003087', color: '#FFD700', fontSize: 10 }

  // ── EU Cyber Resilience Act ──────────────────────────────────────────
  if (n.includes('cyber resilience act'))
    return { abbr: 'CRA', bg: '#1565C0', color: '#fff' }

  // ── ENISA ────────────────────────────────────────────────────────────
  if (n.includes('enisa'))
    return { abbr: 'ENISA', bg: '#0D47A1', color: '#FFD700', fontSize: 8 }

  // ── SWIFT ────────────────────────────────────────────────────────────
  if (n.includes('swift'))
    return { abbr: 'SWIFT', bg: '#003865', color: '#fff', fontSize: 8 }

  // ── TISAX ────────────────────────────────────────────────────────────
  if (n.includes('tisax'))
    return { abbr: 'TISAX', bg: '#37474F', color: '#fff', fontSize: 8 }

  // ── IEC 62443 (Industrial) ───────────────────────────────────────────
  if (n.includes('iec') && n.includes('62443'))
    return { abbr: 'IEC\n62443', bg: '#E65100', color: '#fff', fontSize: 9 }

  // ── IEC 60601 (Medical) ──────────────────────────────────────────────
  if (n.includes('iec') && n.includes('60601'))
    return { abbr: 'IEC\n60601', bg: '#B71C1C', color: '#fff', fontSize: 9 }

  // ── GovRAMP ──────────────────────────────────────────────────────────
  if (n.includes('govramp'))
    return { abbr: 'Gov\nRAMP', bg: '#4A148C', color: '#fff', fontSize: 9 }

  // ── C2M2 ─────────────────────────────────────────────────────────────
  if (n.includes('c2m2') || n.includes('cybersecurity capability maturity model'))
    return { abbr: 'C2M2', bg: '#004D40', color: '#fff', fontSize: 9 }

  // ── DFARS ────────────────────────────────────────────────────────────
  if (n.includes('dfars') || n.includes('defense federal acquisition regulation supplement'))
    return { abbr: 'DFARS', bg: '#37474F', color: '#fff', fontSize: 8 }

  // ── SOX ──────────────────────────────────────────────────────────────
  if (n.includes('sarbanes') || (n.includes('sox') && !n.includes('posix')))
    return { abbr: 'SOX', bg: '#880E4F', color: '#fff' }

  // ── GLBA ─────────────────────────────────────────────────────────────
  if (n.includes('gramm') || n.includes('glba'))
    return { abbr: 'GLBA', bg: '#4A148C', color: '#fff' }

  // ── FFIEC ────────────────────────────────────────────────────────────
  if (n.includes('ffiec'))
    return { abbr: 'FFIEC', bg: '#1A237E', color: '#fff', fontSize: 9 }

  // ── NERC CIP ─────────────────────────────────────────────────────────
  if (n.includes('nerc') && n.includes('cip'))
    return { abbr: 'NERC\nCIP', bg: '#F57F17', color: '#1A237E', fontSize: 9 }

  // ── MITRE ATT&CK ─────────────────────────────────────────────────────
  if (n.includes('mitre') || n.includes('att&ck'))
    return { abbr: 'MITRE', bg: '#B71C1C', color: '#fff', fontSize: 8 }

  // ── DHS / CISA ───────────────────────────────────────────────────────
  if (n.includes('cisa'))
    return { abbr: 'CISA', bg: '#00205B', color: '#fff' }
  if (n.includes('homeland security') || n.includes('dhs'))
    return { abbr: 'DHS', bg: '#003A70', color: '#fff' }

  // ── DoD ──────────────────────────────────────────────────────────────
  if (n.includes('department of defense') || (n.includes('dod') && n.includes('zero trust')))
    return { abbr: 'DoD', bg: '#003A70', color: '#fff' }

  // ── FAR ──────────────────────────────────────────────────────────────
  if (n.includes('federal acquisition regulation') || n.includes('far 52'))
    return { abbr: 'FAR', bg: '#455A64', color: '#fff' }

  // ── CCPA / CPRA (California) ─────────────────────────────────────────
  if (n.includes('ccpa') || n.includes('california consumer privacy') || n.includes('cpra'))
    return { abbr: 'CCPA', bg: '#BF360C', color: '#fff', fontSize: 9 }

  // ── NY DFS ───────────────────────────────────────────────────────────
  if (n.includes('nycrr') || n.includes('ny dfs') || (n.includes('new york') && n.includes('financial')))
    return { abbr: 'NYDFS', bg: '#1A237E', color: '#fff', fontSize: 8 }

  // ── FERPA ────────────────────────────────────────────────────────────
  if (n.includes('ferpa'))
    return { abbr: 'FERPA', bg: '#0D47A1', color: '#fff', fontSize: 9 }

  // ── COPPA ────────────────────────────────────────────────────────────
  if (n.includes('coppa'))
    return { abbr: 'COPPA', bg: '#006064', color: '#fff', fontSize: 8 }

  // ── CJIS ─────────────────────────────────────────────────────────────
  if (n.includes('cjis'))
    return { abbr: 'CJIS', bg: '#37474F', color: '#fff' }

  // ── NAIC ─────────────────────────────────────────────────────────────
  if (n.includes('naic') || n.includes('insurance data security model'))
    return { abbr: 'NAIC', bg: '#1B5E20', color: '#fff' }

  // ── MPA ──────────────────────────────────────────────────────────────
  if (n.includes('mpa') || n.includes('content security best practices'))
    return { abbr: 'MPA', bg: '#424242', color: '#fff' }

  // ── IMO (Maritime) ───────────────────────────────────────────────────
  if (n.includes('maritime') || n.includes('imo'))
    return { abbr: 'IMO', bg: '#01579B', color: '#fff' }

  // ── APEC ─────────────────────────────────────────────────────────────
  if (n.includes('apec'))
    return { abbr: 'APEC', bg: '#004D40', color: '#fff' }

  // ── BSI ──────────────────────────────────────────────────────────────
  if (n.includes('bsi') || n.includes('standard 200'))
    return { abbr: 'BSI', bg: '#1B5E20', color: '#fff' }

  // ── UN / UNECE ───────────────────────────────────────────────────────
  if (n.includes('unece') || n.includes('un regulation') || n.includes('un r1'))
    return { abbr: 'UN', bg: '#006EB5', color: '#fff' }

  // ── SPARTA ───────────────────────────────────────────────────────────
  if (n.includes('sparta'))
    return { abbr: 'SPARTA', bg: '#B71C1C', color: '#fff', fontSize: 7 }

  // ── OECD ─────────────────────────────────────────────────────────────
  if (n.includes('oecd'))
    return { abbr: 'OECD', bg: '#00689D', color: '#fff' }

  // ── GAPP ─────────────────────────────────────────────────────────────
  if (n.includes('generally accepted privacy'))
    return { abbr: 'GAPP', bg: '#00796B', color: '#fff' }

  // ── Data Privacy Framework ───────────────────────────────────────────
  if (n.includes('data privacy framework') || n.includes('dpf'))
    return { abbr: 'DPF', bg: '#004A7C', color: '#fff' }

  // ── CERT RMM ─────────────────────────────────────────────────────────
  if (n.includes('cert') && n.includes('resilience management'))
    return { abbr: 'CERT\nRMM', bg: '#B71C1C', color: '#fff', fontSize: 9 }

  // ── SSDF ─────────────────────────────────────────────────────────────
  if (n.includes('ssdf') || n.includes('secure software development framework'))
    return { abbr: 'SSDF', bg: '#004A7C', color: '#fff' }

  // ── IRS ──────────────────────────────────────────────────────────────
  if (n.includes('internal revenue service') || n.includes('irs 1075'))
    return { abbr: 'IRS', bg: '#1A237E', color: '#fff' }

  // ── EO 14028 ─────────────────────────────────────────────────────────
  if (n.includes('executive order') || n.includes('eo 14028'))
    return { abbr: 'EO\n14028', bg: '#002868', color: '#BF0A30', fontSize: 9 }

  // ── Shared Assessments SIG ───────────────────────────────────────────
  if (n.includes('shared assessments') || n.includes('sig '))
    return { abbr: 'SIG', bg: '#37474F', color: '#fff' }

  // ── EU PSD2 ──────────────────────────────────────────────────────────
  if (n.includes('psd2') || n.includes('payment services directive'))
    return { abbr: 'PSD2', bg: '#003087', color: '#fff' }

  // ── EBA ──────────────────────────────────────────────────────────────
  if (n.includes('european banking authority') || n.includes('eba'))
    return { abbr: 'EBA', bg: '#1565C0', color: '#fff' }

  // ── Germany C5 ───────────────────────────────────────────────────────
  if (n.includes('cloud computing compliance') || (n.includes('c5') && n.includes('2020')))
    return { abbr: 'C5', bg: '#1B5E20', color: '#fff' }

  // ── BAIT ─────────────────────────────────────────────────────────────
  if (n.includes('bait') || n.includes('banking supervisory requirements for it'))
    return { abbr: 'BAIT', bg: '#37474F', color: '#fff' }

  // ── Fallback: generate initials with deterministic color ─────────────
  const { bg, color } = hashColor(frameworkName)
  const initials = getInitials(frameworkName)
  return {
    abbr: initials,
    bg,
    color,
    fontSize: initials.length > 4 ? 8 : undefined,
  }
}

/**
 * Renders a distinctive SVG badge icon for a GRC framework.
 * Uses framework family detection to assign consistent colors and abbreviations.
 * Falls back to initials-based icon with deterministic color for unknown frameworks.
 */
export function FrameworkLogo({ frameworkName, size = 64 }: FrameworkLogoProps) {
  const config = getLogoConfig(frameworkName)
  const lines = config.abbr.split('\n')
  const fontSize = config.fontSize ?? (lines.length > 1 ? 11 : 14)
  const lineHeight = fontSize + 3
  const totalTextHeight = lines.length * lineHeight
  const startY = size / 2 - totalTextHeight / 2 + fontSize * 0.85

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      xmlns="http://www.w3.org/2000/svg"
      aria-label={frameworkName}
      role="img"
      style={{ display: 'block', flexShrink: 0 }}
    >
      {/* Background */}
      <rect
        width={size}
        height={size}
        rx="12"
        ry="12"
        fill={config.bg}
      />
      {/* Subtle shine overlay */}
      <rect
        width={size}
        height={size / 2}
        rx="12"
        ry="12"
        fill="rgba(255,255,255,0.08)"
      />
      {/* Text lines */}
      {lines.map((line, i) => (
        <text
          key={i}
          x={size / 2}
          y={startY + i * lineHeight}
          textAnchor="middle"
          fill={config.color}
          fontSize={fontSize}
          fontFamily="system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"
          fontWeight="700"
          letterSpacing="0.3"
        >
          {line}
        </text>
      ))}
    </svg>
  )
}
