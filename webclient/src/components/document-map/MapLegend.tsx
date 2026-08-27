/**
 * How to read the map — compact inline form for the Explorer chrome.
 *
 * Phase-5 restyle: the four coverage states are folded into the section-head
 * row as compact swatch + label pairs. The expanded card form (three-column
 * legend with glyph explanations) is preserved in the full dm-legend section
 * that sits above the grid for accessible, printable reference.
 *
 * Every non-colour cue (glyph, word, strip pattern) remains on the tiles
 * themselves; the inline legend adds orientation without replacing the
 * redundant channels on each tile.
 */

/** Compact inline version — four swatch+label pairs for the section head. */
export function MapLegendInline() {
  return (
    <div className="dm-legend-inline" aria-label="Coverage legend">
      <span className="dm-legend-inline-item">
        <span className="dm-legend-inline-swatch dm-legend-inline-swatch-covered" aria-hidden="true" />
        Confirmed
      </span>
      <span className="dm-legend-inline-item">
        <span className="dm-legend-inline-swatch dm-legend-inline-swatch-claimed" aria-hidden="true" />
        Suggested
      </span>
      <span className="dm-legend-inline-item">
        <span className="dm-legend-inline-swatch dm-legend-inline-swatch-gap" aria-hidden="true" />
        Gap
      </span>
      <span className="dm-legend-inline-item">
        <span className="dm-legend-inline-swatch dm-legend-inline-swatch-oos" aria-hidden="true" />
        Not in scope
      </span>
    </div>
  )
}

import { AlertGlyph, CheckGlyph, MinusGlyph, RingGlyph } from './icons'

/**
 * Full expanded legend — three-column card above the grid.
 * Still rendered on the page for accessible, printable reference; the section-
 * head inline variant adds quick orientation when the card scrolls out of view.
 */
export default function MapLegend() {
  return (
    <section className="dm-legend" aria-label="How to read the map">
      <div className="dm-legend-group">
        <p className="dm-legend-title">Domain coverage</p>
        <p className="dm-legend-item">
          <span className="dm-swatch dm-swatch-covered" />
          <span>
            <b>Confirmed</b> <span className="dm-legend-desc">— a person has accepted at least one mapping here</span>
          </span>
        </p>
        <p className="dm-legend-item">
          <span className="dm-swatch dm-swatch-claimed" />
          <span>
            <b>Suggested</b> <span className="dm-legend-desc">— documents placed, none reviewed yet</span>
          </span>
        </p>
        <p className="dm-legend-item">
          <span className="dm-swatch dm-swatch-gap" />
          <span>
            <b>Gap</b> <span className="dm-legend-desc">— controls scoped, no document</span>
          </span>
        </p>
        <p className="dm-legend-item">
          <span className="dm-swatch dm-swatch-oos" />
          <span>
            <b>Not in scope</b> <span className="dm-legend-desc">— no controls scoped</span>
          </span>
        </p>
      </div>

      <div className="dm-legend-group">
        <p className="dm-legend-title">How a placement is known</p>
        <p className="dm-legend-item">
          <span className="dm-legend-glyph">
            <CheckGlyph />
          </span>
          <span>
            <b>Confirmed</b>{' '}
            <span className="dm-legend-desc">— solid edge, solid check, continuous strip</span>
          </span>
        </p>
        <p className="dm-legend-item">
          <span className="dm-legend-glyph">
            <RingGlyph />
          </span>
          <span>
            <b>Suggested</b>{' '}
            <span className="dm-legend-desc">— dashed edge, hollow ring, segmented strip</span>
          </span>
        </p>
        <p className="dm-legend-item">
          <span className="dm-legend-glyph">
            <AlertGlyph />
          </span>
          <span>
            <b>Unmapped</b>{' '}
            <span className="dm-legend-desc">— no in-scope domain reached; sits in the rail</span>
          </span>
        </p>
        <p className="dm-legend-item">
          <span className="dm-legend-glyph">
            <MinusGlyph />
          </span>
          <span>
            <b>Not in scope</b> <span className="dm-legend-desc">— a scoping decision, not a gap</span>
          </span>
        </p>
      </div>

      <div className="dm-legend-group">
        <p className="dm-legend-title">Depth of a covered domain</p>
        <p className="dm-legend-item">
          <span className="dm-pip dm-pip-docs dm-legend-pip">
            1<span className="dm-pip-key">DOCS</span>
          </span>
          <span className="dm-legend-desc">documents placed on this domain</span>
        </p>
        <p className="dm-legend-item">
          <span className="dm-pip dm-pip-conf dm-legend-pip">
            12<span className="dm-pip-key">CONF</span>
          </span>
          <span className="dm-legend-desc">control mappings confirmed by a person</span>
        </p>
        <p className="dm-legend-item dm-legend-note">
          A step appears only once it has something to show, so a tile never
          reports a stage it has not reached.
        </p>
      </div>
    </section>
  )
}
