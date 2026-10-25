import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Circle,
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'
import { divIcon, latLngBounds } from 'leaflet'
import type { LatLngTuple } from 'leaflet'
import 'leaflet/dist/leaflet.css'

// ---------------------------------------------------------
// Shared Constants (Single Source of Truth)
// ---------------------------------------------------------

export const ROAD_STATUS_CONFIG = {
  clear: {
    label: 'Clear',
    fullLabel: 'Clear (Safe)',
    color: '#16a34a',
    weight: 2.5,
    dashArray: undefined as string | undefined,
    style: 'Solid line',
    description: 'Safe corridor with normal traffic flow',
    badgeClass: 'badge-clear',
  },
  avoid: {
    label: 'Avoid',
    fullLabel: 'Avoid (Hazardous)',
    color: '#d97706',
    weight: 3.5,
    dashArray: '6, 8',
    style: 'Dashed line',
    description: 'Passable but not preferred due to pollution, smoke, or gas plume; use only if needed',
    badgeClass: 'badge-avoid',
  },
  closed: {
    label: 'Closed',
    fullLabel: 'Closed (Physically Blocked)',
    color: '#b91c1c',
    weight: 5.5,
    dashArray: undefined as string | undefined,
    style: 'Thick solid line',
    description: 'Physically blocked (debris collapse, flood, structural failure); strictly impassable',
    badgeClass: 'badge-closed',
  },
} as const

export const HAZARD_CONFIG: Record<
  string,
  { label: string; shortLabel: string; icon: string; color: string; badgeClass: string }
> = {
  pollution: {
    label: 'Air Pollution (NO₂ / PM2.5)',
    shortLabel: 'Air Pollution',
    icon: '💨',
    color: '#ea580c',
    badgeClass: 'badge-pollution',
  },
  gas: {
    label: 'Toxic Gas Plume',
    shortLabel: 'Gas Plume',
    icon: '☣️',
    color: '#9333ea',
    badgeClass: 'badge-gas',
  },
  fire: {
    label: 'Wildfire / Thermal Hotspot',
    shortLabel: 'Fire Hotspot',
    icon: '🔥',
    color: '#dc2626',
    badgeClass: 'badge-fire',
  },
  flood: {
    label: 'Flood / Water Inundation',
    shortLabel: 'Flood Inundation',
    icon: '🌊',
    color: '#0284c7',
    badgeClass: 'badge-flood',
  },
  earthquake: {
    label: 'Earthquake / Seismic Tremor',
    shortLabel: 'Earthquake',
    icon: '⚡',
    color: '#b45309',
    badgeClass: 'badge-quake',
  },
  storm: {
    label: 'Severe Storm / Cyclone',
    shortLabel: 'Severe Storm',
    icon: '🌀',
    color: '#0d9488',
    badgeClass: 'badge-storm',
  },
  clear: {
    label: 'Safe / Normal Flow',
    shortLabel: 'Safe / Clear',
    icon: '🛡️',
    color: '#16a34a',
    badgeClass: 'badge-clear',
  },
  unknown: {
    label: 'Environmental Hazard',
    shortLabel: 'Hazard',
    icon: '⚠️',
    color: '#f59e0b',
    badgeClass: 'badge-unknown',
  },
}

export const RESOURCE_CONFIG: Record<
  string,
  { label: string; icon: string; color: string }
> = {
  ambulance: { label: 'Ambulance', icon: '🚑', color: '#dc2626' },
  hospital: { label: 'Hospital', icon: '🏥', color: '#2563eb' },
  shelter: { label: 'Shelter', icon: '🏕️', color: '#16a34a' },
  fire_station: { label: 'Fire Station', icon: '🚒', color: '#ea580c' },
  police: { label: 'Police', icon: '🚓', color: '#4f46e5' },
}

export const SEVERITY_SCALE = [
  { label: 'Low', min: 0.0, max: 0.399, color: '#16a34a', range: '< 0.40', desc: 'Minimal risk footprint; standard monitoring' },
  { label: 'Moderate', min: 0.4, max: 0.699, color: '#d97706', range: '0.40 - 0.69', desc: 'Moderate hazard; heightened vigilance advised' },
  { label: 'High', min: 0.7, max: 0.899, color: '#ea580c', range: '0.70 - 0.89', desc: 'High risk hazard; advisory route diversions in effect' },
  { label: 'Critical', min: 0.9, max: 1.0, color: '#dc2626', range: '≥ 0.90', desc: 'Critical emergency; immediate dispatch and road closure' },
]

export function getSeverityTier(severity: number) {
  if (severity >= 0.9) return SEVERITY_SCALE[3]
  if (severity >= 0.7) return SEVERITY_SCALE[2]
  if (severity >= 0.4) return SEVERITY_SCALE[1]
  return SEVERITY_SCALE[0]
}

export function detectHazardType(eventType?: string | null, reason?: string | null): string {
  const text = `${eventType || ''} ${reason || ''}`.toLowerCase()
  if (text.includes('pollution') || text.includes('no2') || text.includes('pm2') || text.includes('smog')) {
    return 'pollution'
  }
  if (text.includes('gas') || text.includes('plume') || text.includes('chemical')) {
    return 'gas'
  }
  if (text.includes('fire') || text.includes('wildfire') || text.includes('thermal') || text.includes('firms')) {
    return 'fire'
  }
  if (text.includes('flood') || text.includes('water') || text.includes('inundat') || text.includes('submerg')) {
    return 'flood'
  }
  if (text.includes('quake') || text.includes('earthquake') || text.includes('tremor') || text.includes('seismic')) {
    return 'earthquake'
  }
  if (text.includes('storm') || text.includes('cyclone') || text.includes('wind')) {
    return 'storm'
  }
  return 'unknown'
}

export function humanizeWardName(zoneId: string, rawName?: string | null): string {
  if (rawName && !rawName.startsWith('zone_') && rawName.trim().length > 0) {
    return rawName.replace('Delhi-Ward-', 'Delhi Ward ')
  }
  const cleanId = zoneId.replace('zone_', '').replace(/^0+/, '')
  return `Delhi Ward ${cleanId}`
}

export function formatRelativeTime(dateStr?: string | number | null): string {
  if (!dateStr) return 'just now'
  let timeMs = 0
  if (typeof dateStr === 'number') {
    timeMs = dateStr > 1e11 ? dateStr : dateStr * 1000
  } else {
    timeMs = new Date(dateStr).getTime()
  }
  if (Number.isNaN(timeMs)) return String(dateStr)

  const diffSec = Math.floor((Date.now() - timeMs) / 1000)
  if (diffSec < 60) return `${Math.max(1, diffSec)}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  return `${Math.floor(diffHr / 24)}d ago`
}

// ---------------------------------------------------------
// Interfaces
// ---------------------------------------------------------

interface ZonePoint {
  id: string
  name: string
  lat: number
  lng: number
  severity: number
  is_affected: boolean
}

interface ActiveEvent {
  event_id: string
  event_type: string
  severity: number
  timestamp?: string | null
  satellite_source?: string | null
  source?: string | null
  affected_zone_ids: string[]
  lat?: number | null
  lon?: number | null
}

interface Assignment {
  assignment_id: string
  resource_id: string
  resource_name?: string
  resource_type?: string
  resource_lat?: number | null
  resource_lng?: number | null
  person_id: string
  person_name?: string
  person_zone?: string
  person_lat?: number | null
  person_lng?: number | null
  medical_needs?: string
  assigned_at?: string | number | null
  eta_min?: number | null
  fastest_eta_min?: number | null
  extra_minutes?: number | null
  distance_km?: number | null
  status?: string
  route_status?: 'clear' | 'uses_hazard_road' | 'no_safe_route'
  no_safe_route?: boolean
  uses_hazard_road?: boolean
  routing_source?: 'osmnx' | 'osrm' | 'approximate'
  encoded_geometry?: string
  route_coords?: [number, number][]
  fastest_coords?: [number, number][]
  hazard_zones_crossed?: string[]
  road_names?: string[]
  ai_reason?: string
  is_test_data?: boolean
}

interface RouteSegment {
  route_id: string
  from_zone_id: string
  to_zone_id: string
  road_name?: string
  from: {
    lat: number
    lng: number
  }
  to: {
    lat: number
    lng: number
  }
  path?: [number, number][]
  distance_km?: number | null
  estimated_time_min?: number | null
  is_blocked: boolean
  status?: 'clear' | 'avoid' | 'closed'
  status_label?: string
  blockage_reason?: string
  hazard_type?: string
}

interface ResourceItem {
  id: string
  name: string
  resource_type: string
  lat: number
  lng: number
  is_available: boolean
  capacity?: number
  current_load?: number
}

interface OverviewMeta {
  zone_count: number
  affected_zone_count: number
  active_event_count: number
  assignment_count: number
  resource_count: number
  available_resource_count: number
  resources_by_type: Record<string, number>
  resources_inventory?: Record<string, { total: number; available: number }>
  routes_total_count?: number
  routes_clear_count?: number
  routes_avoid_count?: number
  routes_closed_count?: number
  data_mode?: string
  last_updated_at?: string
}

interface OverviewPayload {
  zones: ZonePoint[]
  active_events: ActiveEvent[]
  resources?: ResourceItem[]
  assignments?: Assignment[]
  meta: OverviewMeta
}

interface HealthPayload {
  dashboard: {
    status: string
  }
  ingestion: {
    status?: string
    detail?: string
    scheduler_running?: boolean
    firms_job?: boolean
    sentinel_job?: boolean
    [key: string]: unknown
  }
}

interface ActionLogEntry {
  id: string
  kind: 'info' | 'success' | 'error'
  title: string
  detail: string
  time: string
}

interface ToastNotification {
  id: string
  kind: 'success' | 'error' | 'info'
  message: string
}

const DEFAULT_CENTER: LatLngTuple = [28.6139, 77.209]
const POLL_INTERVAL_MS = 60_000

// ---------------------------------------------------------
// Helper: Map Auto-Fit
// ---------------------------------------------------------
function MapController({
  focusCenter,
  focusZoom,
  zones,
  selectedRouteCoords,
}: {
  focusCenter: LatLngTuple | null
  focusZoom?: number
  zones: ZonePoint[]
  selectedRouteCoords?: [number, number][] | null
}) {
  const map = useMap()
  const initialFitDone = useRef(false)

  useEffect(() => {
    if (selectedRouteCoords && selectedRouteCoords.length > 1) {
      try {
        const bounds = latLngBounds(selectedRouteCoords as LatLngTuple[])
        map.fitBounds(bounds, { padding: [50, 50], maxZoom: 14 })
        return
      } catch (err) {
        console.warn('fitBounds on route failed:', err)
      }
    }
    if (focusCenter) {
      map.flyTo(focusCenter, focusZoom ?? 13, { duration: 1.2 })
    }
  }, [focusCenter, focusZoom, selectedRouteCoords, map])

  useEffect(() => {
    if (!initialFitDone.current && zones.length > 2) {
      const bounds = zones.map((z) => [z.lat, z.lng] as LatLngTuple)
      map.fitBounds(bounds, { padding: [30, 30] })
      initialFitDone.current = true
    }
  }, [map, zones])

  return null
}

// ---------------------------------------------------------
// Helper: Fetch JSON with timeout
// ---------------------------------------------------------
async function fetchJson<T>(url: string, init?: RequestInit, timeoutMs = 25_000): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    })
    const text = await res.text()
    let data: unknown
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: text }
    }
    if (!res.ok) {
      if (typeof data === 'object' && data !== null && 'detail' in data) {
        throw new Error(String((data as { detail: unknown }).detail))
      }
      throw new Error(`HTTP ${res.status} ${res.statusText}`)
    }
    return data as T
  } finally {
    window.clearTimeout(timer)
  }
}

// ---------------------------------------------------------
// Component: Legend Modal (Accessible & Scrollable)
// ---------------------------------------------------------
function LegendModal({
  isOpen,
  onClose,
  initialSection,
}: {
  isOpen: boolean
  onClose: () => void
  initialSection: string | null
}) {
  const modalRef = useRef<HTMLDivElement>(null)
  const prevFocusedRef = useRef<HTMLElement | null>(null)
  const [dontShowAgain, setDontShowAgain] = useState(false)

  useEffect(() => {
    if (isOpen) {
      prevFocusedRef.current = document.activeElement as HTMLElement | null
      const closeBtn = modalRef.current?.querySelector<HTMLButtonElement>('.modal-close-btn')
      closeBtn?.focus()

      if (initialSection) {
        window.setTimeout(() => {
          const el = modalRef.current?.querySelector(`#${initialSection}`)
          if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }
        }, 120)
      }
    } else {
      prevFocusedRef.current?.focus()
    }
  }, [isOpen, initialSection])

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        handleClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen])

  const handleClose = () => {
    if (dontShowAgain) {
      try {
        localStorage.setItem('disastergraph_legend_dismissed', 'true')
      } catch {
        // local storage may fail in private mode
      }
    }
    onClose()
  }

  if (!isOpen) return null

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose()
      }}
      role="presentation"
    >
      <div
        className="modal-container"
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="legend-title"
      >
        <header className="modal-header">
          <div>
            <h2 id="legend-title">Emergency Dispatcher Map Legend &amp; Guide</h2>
            <p className="modal-lead-note">
              Click any zone, road, or active event marker on the map for live drill-down details.
            </p>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={handleClose}
            aria-label="Close legend popup"
          >
            ✕
          </button>
        </header>

        <div className="modal-body">
          {/* Section A: Road Status */}
          <section id="legend-roads" className="legend-section">
            <div className="section-head">
              <h3>🛣️ Road Passability &amp; Risk Status</h3>
              <span className="section-tag">3-Tier Status System</span>
            </div>
            <p className="section-blurb">
              Roads are rarely physically closed. Many are passable corridors flagged with toxic smog or gas plumes where emergency dispatch should avoid unless no other route exists. Line styles and colors are accessible for color-blind dispatchers.
            </p>
            <div className="legend-table">
              <div className="legend-row">
                <div className="legend-sample">
                  <div className="road-line clear-sample" />
                </div>
                <div className="legend-info">
                  <strong>{ROAD_STATUS_CONFIG.clear.fullLabel}</strong>
                  <p>{ROAD_STATUS_CONFIG.clear.description}</p>
                  <span className="badge badge-clear">Solid Green Line</span>
                </div>
              </div>
              <div className="legend-row">
                <div className="legend-sample">
                  <div className="road-line avoid-sample" />
                </div>
                <div className="legend-info">
                  <strong>{ROAD_STATUS_CONFIG.avoid.fullLabel}</strong>
                  <p>{ROAD_STATUS_CONFIG.avoid.description}</p>
                  <span className="badge badge-avoid">Dashed Amber Line</span>
                </div>
              </div>
              <div className="legend-row">
                <div className="legend-sample">
                  <div className="road-line closed-sample" />
                </div>
                <div className="legend-info">
                  <strong>{ROAD_STATUS_CONFIG.closed.fullLabel}</strong>
                  <p>{ROAD_STATUS_CONFIG.closed.description}</p>
                  <span className="badge badge-closed">Thick Solid Red Line</span>
                </div>
              </div>
            </div>
          </section>

          {/* Section B: Hazard Zones & Severity */}
          <section id="legend-hazards" className="legend-section">
            <div className="section-head">
              <h3>⚠️ Hazard Zones &amp; Severity Scale</h3>
              <span className="section-tag">Continuous Sensor Scaling</span>
            </div>
            <p className="section-blurb">
              Circle radius indicates the hazard footprint area. Fill colors identify the hazard type, while the opacity and border weight scale with severity.
            </p>

            <h4 className="sub-heading">Hazard Types &amp; Colors</h4>
            <div className="hazard-chip-grid">
              {Object.entries(HAZARD_CONFIG).map(([key, item]) => (
                <div key={key} className="hazard-chip" style={{ borderLeftColor: item.color }}>
                  <span className="chip-icon">{item.icon}</span>
                  <div>
                    <strong>{item.shortLabel}</strong>
                    <span className="chip-color-dot" style={{ backgroundColor: item.color }} />
                  </div>
                </div>
              ))}
            </div>

            <h4 className="sub-heading" style={{ marginTop: '16px' }}>Severity Scale Breakdown</h4>
            <div className="severity-bar-grid">
              {SEVERITY_SCALE.map((scale) => (
                <div key={scale.label} className="severity-card" style={{ borderTopColor: scale.color }}>
                  <div className="sev-header">
                    <strong style={{ color: scale.color }}>{scale.label}</strong>
                    <span className="sev-range">{scale.range}</span>
                  </div>
                  <p>{scale.desc}</p>
                </div>
              ))}
            </div>
          </section>

          {/* Section C: Map Markers & Resources */}
          <section id="legend-markers" className="legend-section">
            <div className="section-head">
              <h3>📡 Disaster Pulses &amp; Resource Markers</h3>
              <span className="section-tag">Real-Time Ingestion</span>
            </div>
            <div className="resource-legend-grid">
              <div className="marker-item">
                <span className="pulse-demo" />
                <div>
                  <strong>Pulsing Red Radar Circle</strong>
                  <p>Active disaster event detected via NASA FIRMS, Sentinel-5P, or Open-Meteo AQ</p>
                </div>
              </div>
              {Object.entries(RESOURCE_CONFIG).map(([key, res]) => (
                <div key={key} className="marker-item">
                  <span className="resource-icon-demo">{res.icon}</span>
                  <div>
                    <strong>{res.label}</strong>
                    <p>Stationed asset. Green outline = Available; Grey outline = Dispatched/In Load</p>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Section D: AI Dispatch Assignments */}
          <section id="legend-assignments" className="legend-section">
            <div className="section-head">
              <h3>🤖 AI Dispatch &amp; Route Guidance</h3>
              <span className="section-tag">Human-In-The-Loop</span>
            </div>
            <p className="section-blurb">
              The AI dispatch agent computes graph routes avoiding toxic plumes and closed corridors. Dispatchers review recommendations in the sidebar and click cards to trace the chosen path on the map.
            </p>
            <div className="status-chip-row">
              <div className="chip-explain">
                <span className="badge badge-proposed">Proposed</span>
                <p>Generated by AI model; awaiting dispatcher approval or rejection.</p>
              </div>
              <div className="chip-explain">
                <span className="badge badge-dispatched">Dispatched</span>
                <p>Approved and dispatched to incident location.</p>
              </div>
              <div className="chip-explain">
                <span className="badge badge-completed">Completed</span>
                <p>Patient rescued or transferred to receiving hospital facility.</p>
              </div>
            </div>
          </section>

          {/* Section E: Data Labels & Badges */}
          <section id="legend-datalabels" className="legend-section">
            <div className="section-head">
              <h3>🏷️ Data Modes &amp; Badges</h3>
              <span className="section-tag">Data Integrity</span>
            </div>
            <div className="datalabel-grid">
              <div className="datalabel-card">
                <span className="badge badge-live">LIVE DATA</span>
                <p>Direct feed from NASA FIRMS, Open-Meteo Air Quality, USGS, and live Neo4j AuraDB.</p>
              </div>
              <div className="datalabel-card">
                <span className="badge badge-test">TEST DATA</span>
                <p>Generated by automated E2E tests (prefixed with e2e_). Toggle in assignments panel to hide.</p>
              </div>
              <div className="datalabel-card">
                <strong>Last Updated</strong>
                <p>Indicates seconds elapsed since the most recent live query refresh to Neo4j.</p>
              </div>
            </div>
          </section>
        </div>

        <footer className="modal-footer">
          <label className="dont-show-again-label">
            <input
              type="checkbox"
              checked={dontShowAgain}
              onChange={(e) => setDontShowAgain(e.target.checked)}
            />
            Don't show automatically on startup
          </label>
          <button type="button" className="btn btn-primary" onClick={handleClose}>
            Got It, Close Legend
          </button>
        </footer>
      </div>
    </div>
  )
}

// ---------------------------------------------------------
// Component: Zone Detail Drawer
// ---------------------------------------------------------
function ZoneDetailDrawer({
  zone,
  selectedEvent,
  activeEvents,
  routes,
  resources,
  onClose,
  onFocusMap,
}: {
  zone: ZonePoint | null
  selectedEvent?: ActiveEvent | null
  activeEvents: ActiveEvent[]
  routes: RouteSegment[]
  resources: ResourceItem[]
  onClose: () => void
  onFocusMap: (lat: number, lng: number) => void
}) {
  if (!zone) return null

  const wardTitle = humanizeWardName(zone.id, zone.name)
  const zoneEvents = activeEvents.filter((ev) => (ev.affected_zone_ids || []).includes(zone.id))
  
  // Prioritize selectedEvent if it relates to this zone, otherwise sort by highest severity
  const sortedZoneEvents = [...zoneEvents].sort((a, b) => (b.severity || 0) - (a.severity || 0))
  const primaryEvent = (selectedEvent && (selectedEvent.affected_zone_ids || []).includes(zone.id))
    ? selectedEvent
    : (sortedZoneEvents[0] || selectedEvent)

  const hazardType = detectHazardType(primaryEvent?.event_type, primaryEvent?.source)
  const hazardConfig = HAZARD_CONFIG[hazardType] || HAZARD_CONFIG.unknown

  // Event incident severity vs zone baseline severity (ensures exact match with alert cards)
  const activeSeverity = primaryEvent ? Number(primaryEvent.severity || 0) : zone.severity
  const activeTier = getSeverityTier(activeSeverity)
  const wardTier = getSeverityTier(zone.severity)

  // Connected routes
  const connectedRoutes = routes.filter(
    (r) => r.from_zone_id === zone.id || r.to_zone_id === zone.id,
  )
  const avoidCount = connectedRoutes.filter((r) => r.status === 'avoid').length
  const closedCount = connectedRoutes.filter((r) => r.status === 'closed' || r.is_blocked).length
  const clearCount = connectedRoutes.filter((r) => r.status === 'clear' && !r.is_blocked).length

  // Nearby resources (within ~8km approximation: 0.08 deg lat/lng)
  const nearbyResources = resources.filter((res) => {
    const dLat = Math.abs(res.lat - zone.lat)
    const dLng = Math.abs(res.lng - zone.lng)
    return dLat < 0.08 && dLng < 0.08
  })

  return (
    <aside className="zone-drawer" aria-label="Zone Details">
      <header className="drawer-header">
        <div>
          <span className="drawer-pretitle">{zone.id.toUpperCase()}</span>
          <h2>{wardTitle}</h2>
        </div>
        <button type="button" className="drawer-close-btn" onClick={onClose} aria-label="Close panel">
          ✕
        </button>
      </header>

      <div className="drawer-content">
        {/* Hazard Header Banner */}
        <div
          className="hazard-banner"
          style={{ borderColor: zone.is_affected ? hazardConfig.color : '#16a34a' }}
        >
          <span className="hazard-banner-icon">{zone.is_affected ? hazardConfig.icon : '🛡️'}</span>
          <div className="hazard-banner-text">
            <strong>{zone.is_affected ? hazardConfig.label : 'Safe / Clear Ward'}</strong>
            <span>
              {zone.is_affected
                ? `Detected via ${primaryEvent?.satellite_source || primaryEvent?.source || 'Sensor Network'}`
                : 'No active hazards or blockages detected'}
            </span>
          </div>
        </div>

        {/* Severity Progress (Matches active incident alert) */}
        <div className="drawer-metric-box">
          <div className="metric-header-row">
            <span>
              {primaryEvent ? `${hazardConfig.shortLabel || 'Incident'} Severity` : 'Disaster Severity'}
            </span>
            <strong style={{ color: activeTier.color }}>
              {activeTier.label} ({activeSeverity.toFixed(2)})
            </strong>
          </div>
          <div className="severity-bar-track">
            <div
              className="severity-bar-fill"
              style={{
                width: `${Math.min(100, Math.max(8, activeSeverity * 100))}%`,
                backgroundColor: activeTier.color,
              }}
            />
          </div>
          <div className="severity-scale-ticks">
            <span>0.0 Low</span>
            <span>0.4 Moderate</span>
            <span>0.7 High</span>
            <span>1.0 Critical</span>
          </div>

          {/* Ward Baseline Context */}
          {primaryEvent && (
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                marginTop: '8px',
                paddingTop: '6px',
                borderTop: '1px dashed #e2e8f0',
                fontSize: '11px',
                color: '#64748b',
              }}
            >
              <span>Ward Baseline Impact:</span>
              <span style={{ fontWeight: 600, color: wardTier.color }}>
                {wardTier.label} ({zone.severity.toFixed(3)})
              </span>
            </div>
          )}
        </div>

        {/* Status Breakdown */}
        <div className="drawer-info-grid">
          <div className="drawer-info-card">
            <span>Primary Hazard</span>
            <strong>{zone.is_affected ? hazardConfig.shortLabel : 'Normal'}</strong>
          </div>
          <div className="drawer-info-card">
            <span>Last Updated</span>
            <strong>{formatRelativeTime(primaryEvent?.timestamp)}</strong>
          </div>
          <div className="drawer-info-card">
            <span>Est. Vulnerable Pop.</span>
            <strong>{zone.is_affected ? '2,400+ residents' : 'Standard'}</strong>
          </div>
          <div className="drawer-info-card">
            <span>Incident Status</span>
            <strong style={{ color: zone.is_affected ? '#dc2626' : '#16a34a' }}>
              {zone.is_affected ? 'Active Alert' : 'Clear'}
            </strong>
          </div>
        </div>

        {/* Connected Corridor Health */}
        <div className="drawer-section">
          <h3>Surrounding Road Corridors ({connectedRoutes.length})</h3>
          <div className="corridor-summary-pills">
            <span className="corridor-pill clear">
              <span className="dot" /> {clearCount} Clear
            </span>
            <span className="corridor-pill avoid">
              <span className="dot" /> {avoidCount} Avoid (Hazard)
            </span>
            <span className="corridor-pill closed">
              <span className="dot" /> {closedCount} Closed
            </span>
          </div>

          <div className="corridor-list">
            {connectedRoutes.slice(0, 5).map((route) => {
              const otherId = route.from_zone_id === zone.id ? route.to_zone_id : route.from_zone_id
              const otherName = humanizeWardName(otherId)
              const statusCfg =
                route.status === 'closed' || route.is_blocked
                  ? ROAD_STATUS_CONFIG.closed
                  : route.status === 'avoid'
                  ? ROAD_STATUS_CONFIG.avoid
                  : ROAD_STATUS_CONFIG.clear

              return (
                <div key={route.route_id} className="corridor-item">
                  <div className="corridor-info">
                    <strong>To {otherName}</strong>
                    <p>{route.blockage_reason || statusCfg.description}</p>
                  </div>
                  <span className={`badge ${statusCfg.badgeClass}`}>{statusCfg.label}</span>
                </div>
              )
            })}
          </div>
        </div>

        {/* Nearby Resources */}
        <div className="drawer-section">
          <h3>Nearby Emergency Resources ({nearbyResources.length})</h3>
          {nearbyResources.length === 0 ? (
            <p className="empty-subtext">No resource stations directly in this ward perimeter.</p>
          ) : (
            <div className="nearby-resource-list">
              {nearbyResources.slice(0, 6).map((res) => {
                const cfg = RESOURCE_CONFIG[res.resource_type] || RESOURCE_CONFIG.ambulance
                return (
                  <div key={res.id} className="nearby-res-item">
                    <span className="res-icon">{cfg.icon}</span>
                    <div className="res-details">
                      <strong>{res.name}</strong>
                      <span>
                        {cfg.label} • {res.is_available ? '🟢 Available' : '🟡 Assigned'}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      <footer className="drawer-footer">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => onFocusMap(zone.lat, zone.lng)}
        >
          🎯 Center on Map
        </button>
        <button type="button" className="btn btn-secondary" onClick={onClose}>
          Close
        </button>
      </footer>
    </aside>
  )
}

// ---------------------------------------------------------
// Main App Component
// ---------------------------------------------------------
export default function App() {
  const [overview, setOverview] = useState<OverviewPayload | null>(null)
  const [assignments, setAssignments] = useState<Assignment[]>([])
  const [routes, setRoutes] = useState<RouteSegment[]>([])
  const [health, setHealth] = useState<HealthPayload | null>(null)
  const [busyActionId, setBusyActionId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string>('never')
  const [logEntries, setLogEntries] = useState<ActionLogEntry[]>([])
  const [bootstrapping, setBootstrapping] = useState<boolean>(true)
  const [toasts, setToasts] = useState<ToastNotification[]>([])

  // UI Interactive States
  const [legendOpen, setLegendOpen] = useState(false)
  const [legendSection, setLegendSection] = useState<string | null>(null)
  const [selectedZone, setSelectedZone] = useState<ZonePoint | null>(null)
  const [selectedEvent, setSelectedEvent] = useState<ActiveEvent | null>(null)
  const [selectedAssignment, setSelectedAssignment] = useState<Assignment | null>(null)
  const [focusCenter, setFocusCenter] = useState<LatLngTuple | null>(null)
  const [filterHazardType, setFilterHazardType] = useState<string>('all')
  const [eventSortOrder, setEventSortOrder] = useState<'severity' | 'newest'>('severity')
  const [hideTestData, setHideTestData] = useState<boolean>(true)
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null)

  // Map Layer Toggles: Keep general road network behind toggle to eliminate spaghetti
  const [showRoads, setShowRoads] = useState(false)
  const [showZones, setShowZones] = useState(true)
  const [showResources, setShowResources] = useState(true)
  const [showEvents, setShowEvents] = useState(true)

  // Loading locks
  const overviewLoadInFlight = useRef(false)
  const assignmentsLoadInFlight = useRef(false)
  const routesLoadInFlight = useRef(false)

  // Toast Dispatcher
  const showToast = useCallback((kind: 'success' | 'error' | 'info', message: string) => {
    const id = `${Date.now()}_${Math.random().toString(36).substring(2, 7)}`
    setToasts((prev) => [...prev, { id, kind, message }])
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4000)
  }, [])

  const pushLog = useCallback((kind: 'info' | 'success' | 'error', title: string, detail: string) => {
    const now = new Date()
    const next: ActionLogEntry = {
      id: `${now.getTime()}_${Math.random().toString(16).slice(2, 8)}`,
      kind,
      title,
      detail,
      time: now.toLocaleTimeString(),
    }
    setLogEntries((prev) => [next, ...prev].slice(0, 14))
  }, [])

  // Data Loaders
  const loadOverview = useCallback(async (quiet: boolean) => {
    if (overviewLoadInFlight.current) return
    overviewLoadInFlight.current = true
    try {
      const payload = await fetchJson<OverviewPayload>('/ui-api/overview', undefined, 90_000)
      setOverview(payload)
      setLastUpdatedAt(new Date().toLocaleTimeString())
      if (!quiet) setError(null)
    } catch (err) {
      if (!quiet) setError(err instanceof Error ? err.message : String(err))
    } finally {
      overviewLoadInFlight.current = false
    }
  }, [])

  const loadAssignments = useCallback(async (quiet: boolean) => {
    if (assignmentsLoadInFlight.current) return
    assignmentsLoadInFlight.current = true
    try {
      const payload = await fetchJson<{ assignments: Assignment[] }>(
        '/ui-api/assignments',
        undefined,
        90_000,
      )
      setAssignments(Array.isArray(payload.assignments) ? payload.assignments : [])
      if (!quiet) setError(null)
    } catch (err) {
      if (!quiet) setError(err instanceof Error ? err.message : String(err))
    } finally {
      assignmentsLoadInFlight.current = false
    }
  }, [])

  const loadRoutes = useCallback(async (quiet: boolean) => {
    if (routesLoadInFlight.current) return
    routesLoadInFlight.current = true
    try {
      const payload = await fetchJson<{ routes: RouteSegment[] }>('/ui-api/routes', undefined, 90_000)
      setRoutes(Array.isArray(payload.routes) ? payload.routes : [])
      if (!quiet) setError(null)
    } catch (err) {
      if (!quiet) setError(err instanceof Error ? err.message : String(err))
    } finally {
      routesLoadInFlight.current = false
    }
  }, [])

  const loadHealth = useCallback(async () => {
    try {
      const payload = await fetchJson<HealthPayload>('/ui-api/health')
      setHealth(payload)
    } catch (err) {
      setHealth({
        dashboard: { status: 'unknown' },
        ingestion: {
          status: 'down',
          detail: err instanceof Error ? err.message : String(err),
        },
      })
    }
  }, [])

  // Auto-show legend on first load or respond to URL query params
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search)
      if (params.get('hidetest') === '1') {
        setHideTestData(true)
      }
      if (params.get('showroads') === '1') {
        setShowRoads(true)
      }
      if (params.get('nolegend') === '1') {
        setLegendOpen(false)
        return
      }
      if (params.get('legend') === '1') {
        setLegendOpen(true)
        const sec = params.get('section')
        if (sec) setLegendSection(sec)
        return
      }
      const dismissed = localStorage.getItem('disastergraph_legend_dismissed')
      if (!dismissed) {
        setLegendOpen(true)
      }
    } catch {
      // ignore
    }
  }, [])

  // Bootstrap & Polling
  useEffect(() => {
    let alive = true
    const bootstrap = async () => {
      await Promise.allSettled([
        loadOverview(false),
        loadAssignments(false),
        loadRoutes(false),
        loadHealth(),
      ])
      if (alive) setBootstrapping(false)
    }
    void bootstrap()

    const timer = window.setInterval(() => {
      void loadOverview(true)
      void loadAssignments(true)
      void loadRoutes(true)
      void loadHealth()
    }, POLL_INTERVAL_MS)

    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [loadAssignments, loadHealth, loadOverview, loadRoutes])

  // Ingestion Action Runner
  const runAction = useCallback(
    async (id: string, label: string, endpoint: string, description: string) => {
      setBusyActionId(id)
      setError(null)
      pushLog('info', `${label} initiated`, description)
      try {
        const payload = await fetchJson<Record<string, unknown>>(
          endpoint,
          { method: 'POST' },
          8 * 60_000,
        )
        pushLog('success', `${label} succeeded`, JSON.stringify(payload).slice(0, 160))
        showToast('success', `${label} completed successfully!`)
        void Promise.all([loadOverview(true), loadAssignments(true), loadRoutes(true), loadHealth()])
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        pushLog('error', `${label} failed`, msg)
        showToast('error', `${label} failed: ${msg}`)
      } finally {
        setBusyActionId(null)
      }
    },
    [loadAssignments, loadHealth, loadOverview, loadRoutes, pushLog, showToast],
  )

  // Assignment Decision Actions
  const handleAssignmentDecision = async (
    assignment: Assignment,
    decision: 'approve' | 'reject',
  ) => {
    const key = assignment.assignment_id || `${assignment.resource_id}::${assignment.person_id}`
    const endpoint = `/ui-api/assignments/${encodeURIComponent(key)}/${decision}`
    try {
      await fetchJson(endpoint, { method: 'POST' })
      const verb = decision === 'approve' ? 'Approved & Dispatched' : 'Rejected'
      showToast('success', `Assignment ${assignment.resource_name || assignment.resource_id} ${verb}`)
      pushLog('info', `Assignment ${verb}`, `Unit sent to ${assignment.person_name || assignment.person_id}`)
      void loadAssignments(true)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      showToast('error', `Failed to ${decision} assignment: ${msg}`)
    }
  }

  // Open Legend to Specific Anchor
  const openLegendAt = (sectionId: string) => {
    setLegendSection(sectionId)
    setLegendOpen(true)
  }

  // Filter & Sort Data
  const zones = overview?.zones ?? []
  const rawEvents = overview?.active_events ?? []
  const resourcesList = overview?.resources ?? []
  const meta = overview?.meta

  // Handle auto-selection query params for testing/demos
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const targetZoneId = params.get('zone')
    if (targetZoneId && zones.length > 0) {
      const found = zones.find((z) => z.id === targetZoneId || z.id.includes(targetZoneId))
      if (found) {
        setSelectedZone(found)
        setFocusCenter([found.lat, found.lng])
      }
    }
    const assignParam = params.get('assign')
    if (assignParam && assignments.length > 0) {
      if (assignParam === '1' || assignParam === 'true') {
        const nonTest = assignments.find((a) => !a.is_test_data)
        setSelectedAssignment(nonTest || assignments[0])
      } else {
        const found = assignments.find(
          (a) => a.assignment_id.includes(assignParam) || a.resource_id.includes(assignParam)
        )
        if (found) setSelectedAssignment(found)
      }
    }
  }, [zones, assignments])

  // Route breakdown
  const avoidRoutes = routes.filter((r) => r.status === 'avoid')
  const closedRoutes = routes.filter((r) => r.status === 'closed' || r.is_blocked)
  const clearRoutes = routes.filter((r) => r.status === 'clear' && !r.is_blocked)

  // Events filtered & sorted
  const filteredEvents = useMemo(() => {
    let list = [...rawEvents]
    if (filterHazardType !== 'all') {
      list = list.filter((ev) => detectHazardType(ev.event_type, ev.source) === filterHazardType)
    }
    if (eventSortOrder === 'severity') {
      list.sort((a, b) => (b.severity || 0) - (a.severity || 0))
    } else {
      list.sort((a, b) => String(b.timestamp || '').localeCompare(String(a.timestamp || '')))
    }
    return list
  }, [rawEvents, filterHazardType, eventSortOrder])

  // Assignments filtered
  const visibleAssignments = useMemo(() => {
    if (!hideTestData) return assignments
    return assignments.filter((a) => !a.is_test_data)
  }, [assignments, hideTestData])

  // Selected Assignment Road Route (following real road curvature)
  const selectedAssignmentRoute = useMemo(() => {
    if (!selectedAssignment) return null
    if (
      Array.isArray(selectedAssignment.route_coords) &&
      selectedAssignment.route_coords.length > 1
    ) {
      return selectedAssignment.route_coords as LatLngTuple[]
    }
    if (
      selectedAssignment.resource_lat != null &&
      selectedAssignment.resource_lng != null &&
      selectedAssignment.person_lat != null &&
      selectedAssignment.person_lng != null
    ) {
      return [
        [selectedAssignment.resource_lat, selectedAssignment.resource_lng] as LatLngTuple,
        [selectedAssignment.person_lat, selectedAssignment.person_lng] as LatLngTuple,
      ]
    }
    return null
  }, [selectedAssignment])

  // Fastest Comparison Route
  const selectedFastestRoute = useMemo(() => {
    if (!selectedAssignment) return null
    if (
      Array.isArray(selectedAssignment.fastest_coords) &&
      selectedAssignment.fastest_coords.length > 1
    ) {
      return selectedAssignment.fastest_coords as LatLngTuple[]
    }
    return null
  }, [selectedAssignment])

  // Explicit Origin and Destination coordinates matching the exact ends of the visible route
  const originCoords: LatLngTuple | null = useMemo(() => {
    if (!selectedAssignment) return null
    if (selectedAssignmentRoute && selectedAssignmentRoute.length > 0) {
      return selectedAssignmentRoute[0]
    }
    if (selectedAssignment.resource_lat != null && selectedAssignment.resource_lng != null) {
      return [selectedAssignment.resource_lat, selectedAssignment.resource_lng] as LatLngTuple
    }
    return null
  }, [selectedAssignment, selectedAssignmentRoute])

  const destCoords: LatLngTuple | null = useMemo(() => {
    if (!selectedAssignment) return null
    if (selectedAssignmentRoute && selectedAssignmentRoute.length > 0) {
      return selectedAssignmentRoute[selectedAssignmentRoute.length - 1]
    }
    if (selectedAssignment.person_lat != null && selectedAssignment.person_lng != null) {
      return [selectedAssignment.person_lat, selectedAssignment.person_lng] as LatLngTuple
    }
    return null
  }, [selectedAssignment, selectedAssignmentRoute])

  const originStationLabel = useMemo(() => {
    if (!selectedAssignment) return 'Emergency Station / Depot'
    if (selectedAssignment.road_names && selectedAssignment.road_names.length > 0) {
      return `Station near ${selectedAssignment.road_names[0]}`
    }
    return 'Emergency Response Depot'
  }, [selectedAssignment])

  const destResidentLabel = useMemo(() => {
    if (!selectedAssignment) return 'Target Resident'
    const ward = selectedAssignment.person_zone || 'Destination Ward'
    if (selectedAssignment.road_names && selectedAssignment.road_names.length > 1) {
      return `${selectedAssignment.person_name} (${ward}, near ${selectedAssignment.road_names[selectedAssignment.road_names.length - 1]})`
    }
    return `${selectedAssignment.person_name} (${ward})`
  }, [selectedAssignment])

  return (
    <div className="command-shell">
      {/* Toast Notification Stack */}
      <div className="toast-container" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast-card toast-${toast.kind}`}>
            <span className="toast-icon">
              {toast.kind === 'success' ? '✓' : toast.kind === 'error' ? '⚠' : 'ℹ'}
            </span>
            <span className="toast-msg">{toast.message}</span>
          </div>
        ))}
      </div>

      {/* Header Band */}
      <header className="header-band">
        <div className="header-title-group">
          <div className="eyebrow-row">
            <span className="eyebrow">DisasterGraph</span>
            <span className="badge badge-live">
              <span className="live-dot" /> LIVE DATA
            </span>
            <span className="header-source-note">NASA FIRMS • Open-Meteo AQ • Sentinel-5P</span>
          </div>
          <h1>Emergency Operations Command Center</h1>
          <p className="subtitle">
            Autonomous multi-hazard ingestion, real-time corridor risk assessment, and decision-support ambulance dispatch.
          </p>
        </div>

        <div className="header-actions-group">
          <button
            type="button"
            className="btn btn-legend-header"
            onClick={() => openLegendAt('legend-roads')}
            aria-label="Open Legend and Guide"
          >
            📖 Map Legend &amp; Guide
          </button>
          <div className="header-stats">
            <div className="header-chip good">
              <span>Dashboard API</span>
              <strong>{health?.dashboard.status ?? 'ok'}</strong>
            </div>
            <div className={`header-chip ${health?.ingestion?.status === 'ok' ? 'good' : 'warn'}`}>
              <span>Ingestion</span>
              <strong>{health?.ingestion?.status ?? 'active'}</strong>
            </div>
            <div className="header-chip">
              <span>Last Sync</span>
              <strong>{lastUpdatedAt}</strong>
            </div>
          </div>
        </div>
      </header>

      {error ? <div className="error-banner">⚠️ {error}</div> : null}

      {/* KPI Cards Strip */}
      <section className="metric-strip" aria-label="Key Performance Indicators">
        {/* Total Zones */}
        <div className="metric-card">
          <div className="card-top">
            <span className="metric-label">Total Zones</span>
            <button
              type="button"
              className="kpi-help-btn"
              onClick={() => openLegendAt('legend-hazards')}
              title="Click for Zone & Hazard Legend"
              aria-label="Zone info"
            >
              ?
            </button>
          </div>
          <strong className="metric-val">{meta?.zone_count ?? zones.length}</strong>
          <span className="metric-help">Monitored wards across Delhi NCR</span>
        </div>

        {/* Affected Zones */}
        <div
          className="metric-card danger clickable"
          onClick={() => {
            const affected = zones.find((z) => z.is_affected)
            if (affected) {
              setSelectedZone(affected)
              setFocusCenter([affected.lat, affected.lng])
            }
          }}
          title="Click to view first affected zone"
        >
          <div className="card-top">
            <span className="metric-label">Affected Zones</span>
            <button
              type="button"
              className="kpi-help-btn"
              onClick={(e) => {
                e.stopPropagation()
                openLegendAt('legend-hazards')
              }}
              aria-label="Affected zones info"
            >
              ?
            </button>
          </div>
          <strong className="metric-val">{meta?.affected_zone_count ?? zones.filter((z) => z.is_affected).length}</strong>
          <span className="metric-help">Active environmental hazard hotspots</span>
        </div>

        {/* Roads to Avoid */}
        <div
          className="metric-card warn clickable"
          onClick={() => {
            if (avoidRoutes.length > 0) {
              setFocusCenter([avoidRoutes[0].from.lat, avoidRoutes[0].from.lng])
              showToast('info', `Focusing on hazardous route: ${avoidRoutes[0].road_name}`)
            }
          }}
          title="Click to highlight hazardous roads"
        >
          <div className="card-top">
            <span className="metric-label">Roads to Avoid</span>
            <button
              type="button"
              className="kpi-help-btn"
              onClick={(e) => {
                e.stopPropagation()
                openLegendAt('legend-roads')
              }}
              aria-label="Road status info"
            >
              ?
            </button>
          </div>
          <strong className="metric-val" style={{ color: '#d97706' }}>
            {avoidRoutes.length}
          </strong>
          <span className="metric-help">High pollution / smog; usable only if needed</span>
        </div>

        {/* Roads Closed */}
        <div
          className="metric-card closed clickable"
          onClick={() => {
            if (closedRoutes.length > 0) {
              setFocusCenter([closedRoutes[0].from.lat, closedRoutes[0].from.lng])
              showToast('info', `Focusing on closed road: ${closedRoutes[0].road_name}`)
            } else {
              showToast('info', 'No physically blocked roads currently reported.')
            }
          }}
          title="Click to highlight closed roads"
        >
          <div className="card-top">
            <span className="metric-label">Roads Closed</span>
            <button
              type="button"
              className="kpi-help-btn"
              onClick={(e) => {
                e.stopPropagation()
                openLegendAt('legend-roads')
              }}
              aria-label="Closed roads info"
            >
              ?
            </button>
          </div>
          <strong className="metric-val" style={{ color: '#b91c1c' }}>
            {closedRoutes.length}
          </strong>
          <span className="metric-help">Physically blocked (collapse, flood, debris)</span>
        </div>

        {/* Active Events */}
        <div
          className="metric-card clickable"
          onClick={() => {
            const feedEl = document.getElementById('active-events-section')
            feedEl?.scrollIntoView({ behavior: 'smooth' })
          }}
          title="Click to view Active Events feed"
        >
          <div className="card-top">
            <span className="metric-label">Active Events</span>
            <button
              type="button"
              className="kpi-help-btn"
              onClick={(e) => {
                e.stopPropagation()
                openLegendAt('legend-markers')
              }}
              aria-label="Active events info"
            >
              ?
            </button>
          </div>
          <strong className="metric-val">{meta?.active_event_count ?? rawEvents.length}</strong>
          <span className="metric-help">Satellite &amp; sensor alert detections</span>
        </div>

        {/* Dispatch Assignments */}
        <div
          className="metric-card clickable"
          onClick={() => {
            const assignEl = document.getElementById('assignments-section')
            assignEl?.scrollIntoView({ behavior: 'smooth' })
          }}
          title="Click to view Assignments feed"
        >
          <div className="card-top">
            <span className="metric-label">Dispatch Assignments</span>
            <button
              type="button"
              className="kpi-help-btn"
              onClick={(e) => {
                e.stopPropagation()
                openLegendAt('legend-assignments')
              }}
              aria-label="Assignments info"
            >
              ?
            </button>
          </div>
          <strong className="metric-val">{assignments.length}</strong>
          <span className="metric-help">AI-calculated ambulance emergency routes</span>
        </div>
      </section>

      {/* Main Dashboard Layout */}
      <main className="dashboard-layout">
        {/* Left Column: Operations Console */}
        <section className="panel controls-panel">
          <h2>Operations Console</h2>
          <p className="panel-blurb">
            Trigger ingestion pipelines, run the AI dispatch cycle, and inspect live service health.
          </p>

          <div className="action-grid">
            <button
              type="button"
              className={`action-btn critical ${busyActionId === 'run_all' ? 'running' : ''}`}
              onClick={() =>
                runAction(
                  'run_all',
                  'Run All Ingestion',
                  '/ui-api/ingest/run_all',
                  'FIRMS + Sentinel + OSM roads in one action',
                )
              }
              disabled={busyActionId !== null}
            >
              <div className="btn-title-row">
                <span className="action-icon">⚡</span>
                <span className="action-label">Run All Ingestion</span>
              </div>
              <span className="action-detail">
                {busyActionId === 'run_all' ? 'Running full pipeline...' : 'Pulls FIRMS, Sentinel & OSM graph'}
              </span>
            </button>

            <button
              type="button"
              className={`action-btn critical ${busyActionId === 'agent_once' ? 'running' : ''}`}
              onClick={() =>
                runAction(
                  'agent_once',
                  'Run Agent Cycle',
                  '/ui-api/agent/run_once',
                  'Calculates emergency assignments and evaluates routes',
                )
              }
              disabled={busyActionId !== null}
            >
              <div className="btn-title-row">
                <span className="action-icon">🤖</span>
                <span className="action-label">Run Agent Once</span>
              </div>
              <span className="action-detail">
                {busyActionId === 'agent_once' ? 'Reasoning over graph...' : 'Generates AI dispatch recommendations'}
              </span>
            </button>

            <button
              type="button"
              className={`action-btn normal ${busyActionId === 'firms' ? 'running' : ''}`}
              onClick={() =>
                runAction(
                  'firms',
                  'Ingest FIRMS',
                  '/ui-api/ingest/firms',
                  'Queries NASA thermal hotspots',
                )
              }
              disabled={busyActionId !== null}
            >
              <div className="btn-title-row">
                <span className="action-icon">🔥</span>
                <span className="action-label">Ingest FIRMS</span>
              </div>
              <span className="action-detail">
                {busyActionId === 'firms' ? 'Contacting NASA...' : 'Thermal fire anomalies'}
              </span>
            </button>

            <button
              type="button"
              className={`action-btn normal ${busyActionId === 'air_quality' ? 'running' : ''}`}
              onClick={() =>
                runAction(
                  'air_quality',
                  'Ingest Air Quality',
                  '/ui-api/ingest/air_quality',
                  'Fetches European AQI, NO2 & PM2.5 from Open-Meteo',
                )
              }
              disabled={busyActionId !== null}
            >
              <div className="btn-title-row">
                <span className="action-icon">💨</span>
                <span className="action-label">Ingest Air Quality</span>
              </div>
              <span className="action-detail">
                {busyActionId === 'air_quality' ? 'Fetching Open-Meteo...' : 'NO2 & PM2.5 corridor delays'}
              </span>
            </button>

            <button
              type="button"
              className={`action-btn secondary ${busyActionId === 'osm' ? 'running' : ''}`}
              onClick={() =>
                runAction(
                  'osm',
                  'Ingest OSM Roads',
                  '/ui-api/ingest/osm_roads',
                  'Refreshes corridor graph & distances',
                )
              }
              disabled={busyActionId !== null}
            >
              <div className="btn-title-row">
                <span className="action-icon">🛣️</span>
                <span className="action-label">Ingest OSM Roads</span>
              </div>
              <span className="action-detail">
                {busyActionId === 'osm' ? 'Updating network...' : 'Road network topology'}
              </span>
            </button>

            <button
              type="button"
              className={`action-btn secondary ${busyActionId === 'demo_block' ? 'running' : ''}`}
              onClick={() =>
                runAction(
                  'demo_block',
                  'Block Demo Roads',
                  '/ui-api/routes/block_demo?count=3',
                  'Marks 3 sample corridors as physically closed',
                )
              }
              disabled={busyActionId !== null}
            >
              <div className="btn-title-row">
                <span className="action-icon">🚧</span>
                <span className="action-label">Simulate Blockages</span>
              </div>
              <span className="action-detail">
                {busyActionId === 'demo_block' ? 'Applying demo closures...' : 'Simulate structural road collapse'}
              </span>
            </button>
          </div>

          {/* Scheduler & Ingestion Status Strip */}
          <div className="scheduler-status-box">
            <h3>Scheduler &amp; Background Ingestion</h3>
            <div className="job-status-grid">
              <div className="job-status-item">
                <div className="job-head">
                  <span className="job-indicator active" />
                  <strong>Scheduler Daemon</strong>
                </div>
                <span className="job-detail">
                  {health?.ingestion?.scheduler_running ? 'Active (Polling every 15m)' : 'Standby / Trigger-ready'}
                </span>
              </div>

              <div className="job-status-item">
                <div className="job-head">
                  <span className="job-indicator active" />
                  <strong>NASA FIRMS Job</strong>
                </div>
                <span className="job-detail">Scheduled (15m interval)</span>
              </div>

              <div className="job-status-item">
                <div className="job-head">
                  <span className="job-indicator active" />
                  <strong>Open-Meteo AQ Job</strong>
                </div>
                <span className="job-detail">Scheduled (Continuous hourly)</span>
              </div>

              <div className="job-status-item">
                <div className="job-head">
                  <span className="job-indicator ondemand" />
                  <strong>Sentinel-5P NO₂</strong>
                  <span
                    className="job-tooltip-trigger"
                    title="In LIVE mode, continuous NO2 monitoring is handled in real time by Open-Meteo AQ. Sentinel-5P satellite cache runs on demand."
                  >
                    ℹ️
                  </span>
                </div>
                <span className="job-detail">On-Demand Cached Mode</span>
              </div>
            </div>
          </div>

          {/* Activity Log */}
          <div className="log-section">
            <h3>Operations Activity Log</h3>
            <div className="activity-log">
              {logEntries.length === 0 ? (
                <p className="empty-subtext">No actions performed in this session yet.</p>
              ) : (
                logEntries.map((entry) => (
                  <article key={entry.id} className={`log-entry ${entry.kind}`}>
                    <div className="log-text">
                      <strong>{entry.title}</strong>
                      <p>{entry.detail}</p>
                    </div>
                    <time>{entry.time}</time>
                  </article>
                ))
              )}
            </div>
          </div>
        </section>

        {/* Center Column: Live Risk Map */}
        <section className="panel map-panel">
          <div className="map-toolbar">
            <div className="toolbar-left">
              <h2>Live Risk Map</h2>
              <span className="map-subtitle">
                Interactive real-time spatial graph of wards, corridors &amp; disaster footprints
              </span>
            </div>

            <div className="toolbar-right">
              {/* Layer Toggles */}
              <div className="layer-toggles" role="toolbar" aria-label="Map Layers">
                <button
                  type="button"
                  className={`layer-btn ${showRoads ? 'active' : ''}`}
                  onClick={() => setShowRoads((v) => !v)}
                  title={`Toggle Road Corridors: ${clearRoutes.length} clear, ${avoidRoutes.length} avoid, ${closedRoutes.length} closed`}
                >
                  🛣️ Roads ({routes.length})
                </button>
                <button
                  type="button"
                  className={`layer-btn ${showZones ? 'active' : ''}`}
                  onClick={() => setShowZones((v) => !v)}
                  title="Toggle Hazard Zones"
                >
                  ⚠️ Zones ({zones.length})
                </button>
                <button
                  type="button"
                  className={`layer-btn ${showResources ? 'active' : ''}`}
                  onClick={() => setShowResources((v) => !v)}
                  title="Toggle Emergency Resources"
                >
                  🚑 Resources ({resourcesList.length})
                </button>
                <button
                  type="button"
                  className={`layer-btn ${showEvents ? 'active' : ''}`}
                  onClick={() => setShowEvents((v) => !v)}
                  title="Toggle Disaster Events"
                >
                  📡 Events ({rawEvents.length})
                </button>
              </div>

              {/* Legend Trigger Button */}
              <button
                type="button"
                className="btn btn-legend-map"
                onClick={() => openLegendAt('legend-roads')}
                aria-label="Open Map Legend"
              >
                ℹ️ Legend
              </button>
            </div>
          </div>

          <div className="map-shell">
            <MapContainer center={DEFAULT_CENTER} zoom={11} scrollWheelZoom className="map-canvas">
              <TileLayer
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution="&copy; OpenStreetMap contributors"
              />
              <MapController
                focusCenter={focusCenter}
                zones={zones}
                selectedRouteCoords={selectedAssignmentRoute as [number, number][] | null}
              />

              {/* 1. Road Corridors Layer */}
              {showRoads &&
                routes.map((route) => {
                  const statusCfg =
                    route.status === 'closed' || route.is_blocked
                      ? ROAD_STATUS_CONFIG.closed
                      : route.status === 'avoid'
                      ? ROAD_STATUS_CONFIG.avoid
                      : ROAD_STATUS_CONFIG.clear

                  return (
                    <Polyline
                      key={`${route.from_zone_id}_${route.to_zone_id}_${route.route_id}`}
                      positions={
                        Array.isArray(route.path) && route.path.length > 1
                          ? (route.path as [number, number][])
                          : [
                              [route.from.lat, route.from.lng],
                              [route.to.lat, route.to.lng],
                            ]
                      }
                      pathOptions={{
                        color: statusCfg.color,
                        weight: statusCfg.weight,
                        dashArray: statusCfg.dashArray,
                        opacity: route.status === 'clear' ? 0.75 : 0.95,
                      }}
                    >
                      <Popup>
                        <div className="popup-wrap">
                          <div className="popup-badge-row">
                            <span className={`badge ${statusCfg.badgeClass}`}>
                              {statusCfg.fullLabel}
                            </span>
                            <span className="popup-subtle">{statusCfg.style}</span>
                          </div>
                          <strong>{route.road_name || `Corridor ${route.route_id}`}</strong>
                          <p className="popup-reason">
                            <strong>Reason:</strong> {route.blockage_reason || statusCfg.description}
                          </p>
                          <div className="popup-meta-row">
                            <span>Distance: {Number(route.distance_km ?? 0).toFixed(1)} km</span>
                            <span>Est. Travel: {Number(route.estimated_time_min ?? 0).toFixed(1)} min</span>
                          </div>
                          <p className="popup-hint">
                            Connecting {humanizeWardName(route.from_zone_id)} ↔ {humanizeWardName(route.to_zone_id)}
                          </p>
                        </div>
                      </Popup>
                    </Polyline>
                  )
                })}

              {/* 2. Hazard Zones Layer */}
              {showZones &&
                zones.map((zone) => {
                  const zoneEvents = rawEvents.filter((ev) =>
                    (ev.affected_zone_ids || []).includes(zone.id),
                  )
                  const hasHazard = zoneEvents.length > 0 || zone.is_affected
                  const hazardType = detectHazardType(zoneEvents[0]?.event_type, zoneEvents[0]?.source)
                  const hazardCfg = HAZARD_CONFIG[hazardType] || HAZARD_CONFIG.unknown
                  const tier = getSeverityTier(zone.severity)
                  const radius = (zone.is_affected ? 9 : 5) + Math.round(zone.severity * 13)

                  const fillColor = hasHazard ? hazardCfg.color : '#64748b'

                  return (
                    <CircleMarker
                      key={zone.id}
                      center={[zone.lat, zone.lng]}
                      radius={radius}
                      eventHandlers={{
                        click: () => {
                          setSelectedZone(zone)
                          setSelectedEvent(null)
                        },
                      }}
                      pathOptions={{
                        color: fillColor,
                        fillColor: fillColor,
                        fillOpacity: zone.is_affected ? 0.42 : 0.22,
                        weight: zone.is_affected ? 2.5 : 1.5,
                      }}
                    >
                      <Popup>
                        <div className="popup-wrap">
                          <div className="popup-badge-row">
                            <span className={`badge ${hasHazard ? hazardCfg.badgeClass : 'badge-clear'}`}>
                              {hasHazard ? `${hazardCfg.icon} ${hazardCfg.shortLabel}` : 'Safe Ward'}
                            </span>
                            <span className="popup-subtle">{tier.label} ({zone.severity.toFixed(2)})</span>
                          </div>
                          <strong>{humanizeWardName(zone.id, zone.name)}</strong>
                          <p className="popup-subtle">Ward Code: {zone.id}</p>
                          <p className="popup-reason">
                            {hasHazard
                              ? `Affected by ${zoneEvents.map((e) => e.event_type.toUpperCase()).join(', ') || 'environmental hazard'}`
                              : 'No hazardous corridors or plumes currently reported.'}
                          </p>
                          <button
                            type="button"
                            className="btn btn-sm btn-primary"
                            style={{ marginTop: '8px', width: '100%' }}
                            onClick={() => setSelectedZone(zone)}
                          >
                            Open Detailed Ward Panel
                          </button>
                        </div>
                      </Popup>
                    </CircleMarker>
                  )
                })}

              {/* 3. Resources Layer */}
              {showResources &&
                resourcesList.map((res) => {
                  const cfg = RESOURCE_CONFIG[res.resource_type] || RESOURCE_CONFIG.ambulance
                  const iconHtml = `<div class="resource-marker-pin ${res.is_available ? 'avail' : 'busy'}">${cfg.icon}</div>`
                  const customIcon = divIcon({
                    className: 'custom-res-icon',
                    html: iconHtml,
                    iconSize: [28, 28],
                    iconAnchor: [14, 14],
                  })

                  return (
                    <Marker
                      key={res.id}
                      position={[res.lat, res.lng]}
                      icon={customIcon}
                    >
                      <Popup>
                        <div className="popup-wrap">
                          <strong>{cfg.icon} {res.name}</strong>
                          <p>Type: {cfg.label}</p>
                          <p>Status: {res.is_available ? '🟢 Available' : '🟡 Assigned on dispatch'}</p>
                          {res.capacity != null && (
                            <p>Capacity: {res.current_load ?? 0} / {res.capacity} patients</p>
                          )}
                        </div>
                      </Popup>
                    </Marker>
                  )
                })}

              {/* 4. Active Events Pulsing Layer */}
              {showEvents &&
                rawEvents.slice(0, 10).map((event) => {
                  const anchorZoneId = event.affected_zone_ids[0]
                  const anchorZone = zones.find((z) => z.id === anchorZoneId)
                  if (!anchorZone) return null

                  const hazardType = detectHazardType(event.event_type, event.source)
                  const cfg = HAZARD_CONFIG[hazardType] || HAZARD_CONFIG.unknown
                  const pulseRadius = 1400 + (event.severity || 0.6) * 4500

                  return (
                    <Circle
                      key={event.event_id}
                      center={[anchorZone.lat, anchorZone.lng]}
                      radius={pulseRadius}
                      pathOptions={{
                        color: cfg.color,
                        fillColor: cfg.color,
                        fillOpacity: 0.1,
                        weight: 1.5,
                        dashArray: '4, 4',
                      }}
                    >
                      <Popup>
                        <div className="popup-wrap">
                          <span className={`badge ${cfg.badgeClass}`}>{cfg.icon} {cfg.label}</span>
                          <strong>{humanizeWardName(anchorZone.id, anchorZone.name)}</strong>
                          <p className="popup-subtle">ID: {event.event_id}</p>
                          <p>Source: {event.satellite_source || event.source || 'Live Satellite'}</p>
                          <p>Severity Score: {(event.severity || 0).toFixed(3)}</p>
                          <p>Reported: {formatRelativeTime(event.timestamp)}</p>
                        </div>
                      </Popup>
                    </Circle>
                  )
                })}

              {/* 5. Selected Assignment Road Routes */}
              {selectedAssignment && (
                <>
                  {/* Fastest Comparison Route (Thin dashed gray) */}
                  {selectedFastestRoute && selectedFastestRoute.length > 1 && (
                    <Polyline
                      positions={selectedFastestRoute}
                      pathOptions={{
                        color: '#64748b',
                        weight: 2.5,
                        dashArray: '5, 8',
                        opacity: 0.75,
                      }}
                    >
                      <Popup>
                        <div className="popup-wrap">
                          <span className="badge badge-subtle">Comparison Route</span>
                          <strong>Fastest Route (Direct)</strong>
                          <p>
                            ETA: {Number(selectedAssignment.fastest_eta_min ?? selectedAssignment.eta_min).toFixed(1)} min
                            (ignoring hazards)
                          </p>
                        </div>
                      </Popup>
                    </Polyline>
                  )}

                  {/* Recommended Safe Route (Following Real Roads) */}
                  {selectedAssignmentRoute && selectedAssignmentRoute.length > 1 && (
                    <Polyline
                      positions={selectedAssignmentRoute}
                      pathOptions={{
                        color: selectedAssignment.no_safe_route
                          ? '#dc2626'
                          : selectedAssignment.uses_hazard_road
                          ? '#d97706'
                          : '#16a34a',
                        weight: 5.5,
                        dashArray: selectedAssignment.uses_hazard_road ? '8, 8' : undefined,
                        opacity: 0.95,
                      }}
                    >
                      <Popup>
                        <div className="popup-wrap">
                          <div className="popup-badge-row">
                            <span
                              className={`badge ${
                                selectedAssignment.no_safe_route
                                  ? 'badge-closed'
                                  : selectedAssignment.uses_hazard_road
                                  ? 'badge-avoid'
                                  : 'badge-clear'
                              }`}
                            >
                              {selectedAssignment.no_safe_route
                                ? 'No Safe Route Available'
                                : selectedAssignment.uses_hazard_road
                                ? 'Hazard-Avoidance Route'
                                : 'Clear Road Corridor'}
                            </span>
                          </div>
                          <strong>{selectedAssignment.resource_name || 'Emergency Dispatch'}</strong>
                          <div className="popup-route-endpoints" style={{ margin: '8px 0', fontSize: '11.5px', lineHeight: '1.45', background: '#f8fafc', padding: '6px 8px', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
                            <div style={{ color: '#15803d', fontWeight: 600 }}>🟢 <strong>From (Source):</strong> {selectedAssignment.resource_name || 'Ambulance'} • {originStationLabel}</div>
                            <div style={{ color: '#b91c1c', fontWeight: 600, marginTop: '3px' }}>🔴 <strong>To (Destination):</strong> {destResidentLabel}</div>
                            <div style={{ color: '#64748b', fontSize: '10.5px', marginTop: '2px' }}>Medical Need: {selectedAssignment.medical_needs || 'Urgent care'}</div>
                          </div>
                          <p className="popup-reason">
                            <strong>Reason:</strong>{' '}
                            {selectedAssignment.ai_reason || 'Verified safe route computed by deterministic road graph.'}
                          </p>
                          <div className="popup-meta-row">
                            <span>Distance: {Number(selectedAssignment.distance_km ?? 0).toFixed(1)} km</span>
                            <span>ETA: {Number(selectedAssignment.eta_min ?? 0).toFixed(1)} min</span>
                          </div>
                          <p className="popup-hint">estimated, no live traffic</p>
                        </div>
                      </Popup>
                    </Polyline>
                  )}

                  {/* Dispatch Origin Marker (Start / Ambulance) */}
                  {originCoords && (
                    <>
                      {/* Outer pulsing ring for visibility */}
                      <CircleMarker
                        center={originCoords}
                        radius={20}
                        pathOptions={{
                          color: '#16a34a',
                          fillColor: '#16a34a',
                          fillOpacity: 0.25,
                          weight: 2,
                        }}
                      />
                      {/* Inner solid anchor */}
                      <CircleMarker
                        center={originCoords}
                        radius={12}
                        pathOptions={{
                          color: '#ffffff',
                          fillColor: '#15803d',
                          fillOpacity: 1,
                          weight: 3,
                        }}
                      >
                        <Tooltip
                          permanent
                          direction="top"
                          className="route-endpoint-tooltip origin"
                          offset={[0, -10]}
                        >
                          🟢 START: {selectedAssignment.resource_name || 'Ambulance'}
                        </Tooltip>
                        <Popup>
                          <div className="popup-wrap">
                            <span className="badge badge-clear">Dispatch Source / Start Point</span>
                            <strong>{selectedAssignment.resource_name || 'Emergency Ambulance'}</strong>
                            <p><strong>Location:</strong> {originStationLabel}</p>
                            <p className="popup-hint">Vehicle starting depot / current staging location</p>
                          </div>
                        </Popup>
                      </CircleMarker>
                      {/* Ambulance Icon Marker directly on point */}
                      <Marker
                        position={originCoords}
                        icon={divIcon({
                          className: 'route-endpoint-pin-container',
                          html: '<div class="endpoint-map-icon">🚑</div>',
                          iconSize: [36, 36],
                          iconAnchor: [18, 18],
                        })}
                      />
                    </>
                  )}

                  {/* Dispatch Destination Marker (End / Resident) */}
                  {destCoords && (
                    <>
                      {/* Outer pulsing ring for visibility */}
                      <CircleMarker
                        center={destCoords}
                        radius={20}
                        pathOptions={{
                          color: '#dc2626',
                          fillColor: '#dc2626',
                          fillOpacity: 0.25,
                          weight: 2,
                        }}
                      />
                      {/* Inner solid anchor */}
                      <CircleMarker
                        center={destCoords}
                        radius={12}
                        pathOptions={{
                          color: '#ffffff',
                          fillColor: '#b91c1c',
                          fillOpacity: 1,
                          weight: 3,
                        }}
                      >
                        <Tooltip
                          permanent
                          direction="top"
                          className="route-endpoint-tooltip dest"
                          offset={[0, -10]}
                        >
                          🔴 DESTINATION: {selectedAssignment.person_name}
                        </Tooltip>
                        <Popup>
                          <div className="popup-wrap">
                            <span className="badge badge-avoid">Evacuation Target / End Point</span>
                            <strong>{selectedAssignment.person_name}</strong>
                            <p><strong>Destination:</strong> {destResidentLabel}</p>
                            <p><strong>Medical Need:</strong> <strong>{selectedAssignment.medical_needs || 'Urgent medical care'}</strong></p>
                            <p className="popup-hint">Resident waiting for medical dispatch</p>
                          </div>
                        </Popup>
                      </CircleMarker>
                      {/* Resident Target Pin Icon directly on point */}
                      <Marker
                        position={destCoords}
                        icon={divIcon({
                          className: 'route-endpoint-pin-container',
                          html: '<div class="endpoint-map-icon">📍</div>',
                          iconSize: [36, 36],
                          iconAnchor: [18, 18],
                        })}
                      />
                    </>
                  )}
                </>
              )}
            </MapContainer>

            {/* Floating Route Info Box */}
            {selectedAssignment && (
              <div className="route-info-box" role="region" aria-label="Selected Route Guidance">
                <div className="route-info-header">
                  <div className="route-info-title">
                    <span className="route-icon">🚑</span>
                    <div>
                      <strong>{selectedAssignment.resource_name || selectedAssignment.resource_id}</strong>
                      <span className="route-dest-sub">
                        → {selectedAssignment.person_name} ({selectedAssignment.person_zone || 'Ward'})
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="route-info-close"
                    onClick={() => setSelectedAssignment(null)}
                    title="Close route overview"
                  >
                    ✕
                  </button>
                </div>

                {/* Explicit Start & Destination Card */}
                <div className="route-endpoints-card">
                  <div className="endpoint-row start-endpoint">
                    <span className="endpoint-chip chip-start">START / SOURCE</span>
                    <div className="endpoint-details">
                      <strong>🚑 {selectedAssignment.resource_name || 'Ambulance'}</strong>
                      <span>{originStationLabel}</span>
                    </div>
                  </div>
                  <div className="endpoint-track-line">
                    <div className="track-pipe" />
                    <span className="track-distance">
                      {Number(selectedAssignment.distance_km ?? 0).toFixed(1)} km corridor ({Number(selectedAssignment.eta_min ?? 0).toFixed(1)} min)
                    </span>
                  </div>
                  <div className="endpoint-row dest-endpoint">
                    <span className="endpoint-chip chip-dest">DESTINATION / END</span>
                    <div className="endpoint-details">
                      <strong>📍 {selectedAssignment.person_name}</strong>
                      <span>{destResidentLabel} • Need: {selectedAssignment.medical_needs || 'Urgent care'}</span>
                    </div>
                  </div>
                </div>

                <div className="route-kpi-row">
                  <div className="route-kpi">
                    <span className="kpi-label">DISTANCE</span>
                    <span className="kpi-val">
                      {Number(selectedAssignment.distance_km ?? 0).toFixed(1)} <small>km</small>
                    </span>
                  </div>
                  <div className="route-kpi">
                    <span className="kpi-label">EST. TRAVEL</span>
                    <span className="kpi-val">
                      {Number(selectedAssignment.eta_min ?? 0).toFixed(1)} <small>min</small>
                    </span>
                  </div>
                  {Number(selectedAssignment.extra_minutes || 0) > 0 && (
                    <div className="route-kpi highlight">
                      <span className="kpi-label">HAZARD DETOUR</span>
                      <span className="kpi-val">
                        +{Number(selectedAssignment.extra_minutes).toFixed(1)} <small>min</small>
                      </span>
                    </div>
                  )}
                </div>

                <div className="route-status-line">
                  <span
                    className={`badge ${
                      selectedAssignment.no_safe_route
                        ? 'badge-closed'
                        : selectedAssignment.uses_hazard_road
                        ? 'badge-avoid'
                        : 'badge-clear'
                    }`}
                  >
                    {selectedAssignment.no_safe_route
                      ? '🚫 No safe route available'
                      : selectedAssignment.uses_hazard_road
                      ? '⚠️ Avoids Hazard Corridor'
                      : selectedAssignment.routing_source === 'approximate'
                      ? '📏 Approximate route'
                      : '✅ Clear Road Network'}
                  </span>
                  <span className="route-traffic-disclaimer">estimated, no live traffic</span>
                </div>

                {selectedAssignment.road_names && selectedAssignment.road_names.length > 0 && (
                  <div className="route-roads-section">
                    <span className="roads-label">Roads along route:</span>
                    <div className="roads-chip-wrap">
                      {selectedAssignment.road_names.slice(0, 5).map((name) => (
                        <span key={name} className="road-chip">
                          {name}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {selectedFastestRoute && selectedFastestRoute.length > 1 && (
                  <div className="route-comparison-legend">
                    <span className="cmp-item">
                      <span className="line-sample solid-safe" /> Recommended Safe Path
                    </span>
                    <span className="cmp-item">
                      <span className="line-sample dashed-fast" /> Fastest Direct (
                      {Number(selectedAssignment.fastest_eta_min ?? selectedAssignment.eta_min).toFixed(1)}m)
                    </span>
                  </div>
                )}
              </div>
            )}

            {/* Quick Status Legend Watermark on Map */}
            <div className="map-legend-pill">
              <span className="dot clear-dot" /> Clear
              <span className="dot avoid-dot" /> Avoid (Hazard)
              <span className="dot closed-dot" /> Closed
            </div>

            {/* Zone Detail Drawer */}
            <ZoneDetailDrawer
              zone={selectedZone}
              selectedEvent={selectedEvent}
              activeEvents={rawEvents}
              routes={routes}
              resources={resourcesList}
              onClose={() => {
                setSelectedZone(null)
                setSelectedEvent(null)
              }}
              onFocusMap={(lat, lng) => {
                setFocusCenter([lat, lng])
              }}
            />
          </div>
        </section>

        {/* Right Column: Live Intelligence Feeds */}
        <section className="panel feed-panel">
          {/* Resource Inventory Block */}
          <div className="resource-inventory-card">
            <h3>Resource Inventory Breakdown</h3>
            <div className="inventory-chips">
              {Object.entries(RESOURCE_CONFIG).map(([key, cfg]) => {
                const inv = meta?.resources_inventory?.[key]
                const total = inv?.total ?? meta?.resources_by_type?.[key] ?? 0
                const avail = inv?.available ?? (total > 0 ? total - 1 : 0)
                return (
                  <div key={key} className="inv-chip">
                    <span className="inv-icon">{cfg.icon}</span>
                    <span className="inv-label">{cfg.label}</span>
                    <strong className="inv-count">
                      {avail}/{total}
                    </strong>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Active Events Section */}
          <div id="active-events-section" className="feed-section">
            <div className="feed-header-row">
              <h3>Active Disaster Events ({filteredEvents.length})</h3>
              <div className="feed-controls">
                <select
                  value={eventSortOrder}
                  onChange={(e) => setEventSortOrder(e.target.value as 'severity' | 'newest')}
                  className="control-select"
                  aria-label="Sort events"
                >
                  <option value="severity">Sort: Highest Severity</option>
                  <option value="newest">Sort: Newest Time</option>
                </select>
              </div>
            </div>

            {/* Filter pills */}
            <div className="hazard-filter-bar">
              {['all', 'pollution', 'fire', 'flood', 'gas'].map((f) => (
                <button
                  key={f}
                  type="button"
                  className={`filter-pill ${filterHazardType === f ? 'active' : ''}`}
                  onClick={() => setFilterHazardType(f)}
                >
                  {f === 'all' ? 'All Hazards' : HAZARD_CONFIG[f]?.shortLabel || f}
                </button>
              ))}
            </div>

            <div className="feed-scroll-box">
              {filteredEvents.length === 0 ? (
                <p className="empty-subtext">No active events matching current filters.</p>
              ) : (
                filteredEvents.slice(0, 10).map((event) => {
                  const hazardType = detectHazardType(event.event_type, event.source)
                  const hazardCfg = HAZARD_CONFIG[hazardType] || HAZARD_CONFIG.unknown
                  const tier = getSeverityTier(event.severity)
                  const isExpanded = expandedEventId === event.event_id
                  const primaryZoneId = event.affected_zone_ids[0]
                  const primaryZone = zones.find((z) => z.id === primaryZoneId)

                  return (
                    <article
                      key={event.event_id}
                      className="event-card clickable"
                      onClick={() => {
                        if (primaryZone) {
                          setSelectedZone(primaryZone)
                          setSelectedEvent(event)
                          setFocusCenter([primaryZone.lat, primaryZone.lng])
                        }
                      }}
                    >
                      <header className="event-head">
                        <div className="event-title-line">
                          <span className="hazard-icon">{hazardCfg.icon}</span>
                          <strong>
                            {hazardCfg.shortLabel} — {humanizeWardName(primaryZoneId || '', primaryZone?.name)}
                          </strong>
                        </div>
                        <span
                          className="badge"
                          style={{ backgroundColor: tier.color, color: '#fff' }}
                        >
                          {tier.label} {event.severity.toFixed(2)}
                        </span>
                      </header>

                      <div className="event-meta-row">
                        <span>Source: {event.satellite_source || event.source || 'Satellite API'}</span>
                        <span
                          className="time-tag"
                          title={event.timestamp ? new Date(event.timestamp).toLocaleString() : 'Recent'}
                        >
                          🕒 {formatRelativeTime(event.timestamp)}
                        </span>
                      </div>

                      <div className="event-id-expand-row">
                        <button
                          type="button"
                          className="expand-id-btn"
                          onClick={(e) => {
                            e.stopPropagation()
                            setExpandedEventId(isExpanded ? null : event.event_id)
                          }}
                        >
                          {isExpanded ? 'Hide Raw ID ▲' : 'Show Raw Event ID ▼'}
                        </button>
                        {isExpanded && <code className="raw-id-text">{event.event_id}</code>}
                      </div>
                    </article>
                  )
                })
              )}
            </div>
          </div>

          {/* AI Dispatch Assignments Section */}
          <div id="assignments-section" className="feed-section">
            <div className="feed-header-row">
              <h3>Dispatch Recommendations ({visibleAssignments.length})</h3>
              <label className="test-data-toggle">
                <input
                  type="checkbox"
                  checked={hideTestData}
                  onChange={(e) => setHideTestData(e.target.checked)}
                />
                Hide Test Data
              </label>
            </div>

            <div className="feed-scroll-box">
              {visibleAssignments.length === 0 ? (
                <p className="empty-subtext">
                  No pending assignments. Click <strong>Run Agent Once</strong> to calculate dispatch routes.
                </p>
              ) : (
                visibleAssignments.slice(0, 10).map((assignment) => {
                  const isSelected = selectedAssignment?.assignment_id === assignment.assignment_id
                  const status = assignment.status || 'proposed'

                  return (
                    <article
                      key={assignment.assignment_id || `${assignment.resource_id}_${assignment.person_id}`}
                      className={`assignment-card ${isSelected ? 'selected' : ''}`}
                      onClick={() => {
                        setSelectedAssignment(assignment)
                        if (assignment.resource_lat && assignment.resource_lng) {
                          setFocusCenter([assignment.resource_lat, assignment.resource_lng])
                        }
                      }}
                    >
                      <header className="assign-head">
                        <div className="assign-res-title">
                          <span className="res-icon">🚑</span>
                          <strong>{assignment.resource_name || assignment.resource_id}</strong>
                          {assignment.is_test_data && (
                            <span className="badge badge-test">TEST DATA</span>
                          )}
                        </div>
                        <span className={`badge badge-${status}`}>
                          {status.toUpperCase()}
                        </span>
                      </header>

                      <div className="assign-destination">
                        <strong>Going to:</strong>{' '}
                        <span>
                          {assignment.person_name || assignment.person_id} • {assignment.person_zone || 'Central Ward'}
                        </span>
                        {assignment.medical_needs && (
                          <span className="medical-tag">Medical: {assignment.medical_needs}</span>
                        )}
                      </div>

                      <div className="assign-pills-row">
                        <span
                          className="pill eta-pill"
                          title="Estimated travel time, no live traffic"
                        >
                          ⏱️ ETA {Number(assignment.eta_min ?? 15).toFixed(1)} min
                        </span>
                        <span className="pill dist-pill">
                          📍 {Number(assignment.distance_km ?? 0).toFixed(1)} km
                        </span>
                        <span
                          className={`pill route-pill ${
                            assignment.no_safe_route
                              ? 'danger'
                              : assignment.route_status === 'uses_hazard_road'
                              ? 'warn'
                              : assignment.routing_source === 'approximate'
                              ? 'approx'
                              : 'good'
                          }`}
                        >
                          {assignment.no_safe_route
                            ? '🚫 No safe route'
                            : assignment.route_status === 'uses_hazard_road'
                            ? `⚠️ Detour (+${Number(assignment.extra_minutes || 0).toFixed(1)}m)`
                            : assignment.routing_source === 'approximate'
                            ? '📏 Approximate Route'
                            : '✅ Safe Road Route'}
                        </span>
                      </div>

                      {assignment.ai_reason && (
                        <blockquote className="ai-reason-quote">
                          🤖 <em>"{assignment.ai_reason}"</em>
                        </blockquote>
                      )}

                      <div className="assign-actions-row">
                        <span className="time-subtle">
                          {formatRelativeTime(assignment.assigned_at)}
                        </span>
                        {status === 'proposed' && (
                          <div className="btn-decision-group">
                            <button
                              type="button"
                              className="btn btn-sm btn-approve"
                              onClick={(e) => {
                                e.stopPropagation()
                                void handleAssignmentDecision(assignment, 'approve')
                              }}
                            >
                              ✓ Approve
                            </button>
                            <button
                              type="button"
                              className="btn btn-sm btn-reject"
                              onClick={(e) => {
                                e.stopPropagation()
                                void handleAssignmentDecision(assignment, 'reject')
                              }}
                            >
                              ✕ Reject
                            </button>
                          </div>
                        )}
                      </div>
                    </article>
                  )
                })
              )}
            </div>
          </div>
        </section>
      </main>

      {/* Legend Modal Popup */}
      <LegendModal
        isOpen={legendOpen}
        onClose={() => setLegendOpen(false)}
        initialSection={legendSection}
      />

      {/* Bootstrapping Overlay */}
      {bootstrapping ? (
        <div className="loading-overlay" role="alert" aria-live="assertive">
          <div className="spinner" />
          <p>Connecting to DisasterGraph Command Engine...</p>
        </div>
      ) : null}
    </div>
  )
}
