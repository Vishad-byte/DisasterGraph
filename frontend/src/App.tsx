import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Circle,
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  useMap,
} from 'react-leaflet'
import type { LatLngTuple } from 'leaflet'
import 'leaflet/dist/leaflet.css'

type ActionTone = 'critical' | 'normal' | 'secondary'
type LogKind = 'info' | 'success' | 'error'

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
  affected_zone_ids: string[]
}

interface Assignment {
  resource_id: string
  person_id: string
  assigned_at?: string | null
  eta_min?: number | null
}

interface RouteSegment {
  route_id: string
  from_zone_id: string
  to_zone_id: string
  from: {
    lat: number
    lng: number
  }
  to: {
    lat: number
    lng: number
  }
  distance_km?: number | null
  estimated_time_min?: number | null
  is_blocked: boolean
  blockage_reason?: string
}

interface OverviewMeta {
  zone_count: number
  affected_zone_count: number
  active_event_count: number
  assignment_count: number
  resource_count: number
  available_resource_count: number
  resources_by_type: Record<string, number>
}

interface OverviewPayload {
  zones: ZonePoint[]
  active_events: ActiveEvent[]
  meta: OverviewMeta
}

interface IngestionHealth {
  status?: string
  detail?: string
  scheduler_running?: boolean
  firms_job?: boolean
  sentinel_job?: boolean
  [key: string]: unknown
}

interface HealthPayload {
  dashboard: {
    status: string
  }
  ingestion: IngestionHealth
}

interface ActionLogEntry {
  id: string
  kind: LogKind
  title: string
  detail: string
  time: string
}

interface ActionDefinition {
  id: string
  label: string
  description: string
  endpoint: string
  tone: ActionTone
}

const DEFAULT_CENTER: LatLngTuple = [28.6139, 77.209]
const POLL_INTERVAL_MS = 60_000

const ACTIONS: ActionDefinition[] = [
  {
    id: 'run_all',
    label: 'Run All Ingestion',
    description: 'FIRMS + Sentinel + OSM roads in one action',
    endpoint: '/ui-api/ingest/run_all',
    tone: 'critical',
  },
  {
    id: 'agent_once',
    label: 'Run Agent Once',
    description: 'Generates assignment edges and triggers alerts',
    endpoint: '/ui-api/agent/run_once',
    tone: 'critical',
  },
  {
    id: 'firms',
    label: 'Ingest FIRMS',
    description: 'Pulls NASA hotspot points and marks affected zones',
    endpoint: '/ui-api/ingest/firms',
    tone: 'normal',
  },
  {
    id: 'sentinel',
    label: 'Ingest Sentinel',
    description: 'Loads cached NO2 points and updates pollution events',
    endpoint: '/ui-api/ingest/sentinel',
    tone: 'normal',
  },
  {
    id: 'osm',
    label: 'Ingest OSM Roads',
    description: 'Refreshes route graph and passability metadata',
    endpoint: '/ui-api/ingest/osm_roads',
    tone: 'secondary',
  },
  {
    id: 'scheduler',
    label: 'Trigger Scheduler Jobs',
    description: 'Forces firms_poll and sentinel_poll run immediately',
    endpoint: '/ui-api/ingest/scheduler/trigger',
    tone: 'secondary',
  },
]

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

async function fetchJson<T>(url: string, init?: RequestInit, timeoutMs = 25000): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)

  let response: Response
  try {
    response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init?.headers ?? {}),
      },
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error(`Request timed out: ${url}`)
    }
    throw err
  } finally {
    window.clearTimeout(timeout)
  }

  const contentType = response.headers.get('content-type') ?? ''
  let payload: unknown
  if (contentType.includes('application/json')) {
    payload = await response.json()
  } else {
    payload = { detail: await response.text() }
  }

  if (!response.ok) {
    if (isObject(payload) && typeof payload.detail === 'string') {
      throw new Error(payload.detail)
    }
    throw new Error(`${response.status} ${response.statusText}`)
  }

  if (!isObject(payload)) {
    throw new Error(`Unexpected response from ${url}`)
  }

  return payload as T
}

function actionTimeoutMs(actionId: string): number {
  if (actionId === 'run_all') {
    return 8 * 60_000
  }
  if (actionId === 'agent_once') {
    return 3 * 60_000
  }
  if (actionId === 'firms' || actionId === 'sentinel' || actionId === 'osm') {
    return 3 * 60_000
  }
  if (actionId === 'scheduler') {
    return 60_000
  }
  return 25_000
}

function severityColor(zone: ZonePoint): string {
  if (!zone.is_affected) return '#88919d'
  if (zone.severity >= 0.7) return '#c03b1c'
  if (zone.severity >= 0.4) return '#cd6a1f'
  return '#e9a034'
}

function severityRadius(zone: ZonePoint): number {
  const baseline = zone.is_affected ? 8 : 5
  return baseline + Math.round((zone.severity || 0) * 11)
}

function compactJson(value: unknown, limit = 220): string {
  const raw = JSON.stringify(value)
  if (raw.length <= limit) return raw
  return `${raw.slice(0, limit)}...`
}

function renderTimestamp(value: string | null | undefined): string {
  if (!value) return 'n/a'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function MapAutoFit({ zones }: { zones: ZonePoint[] }): null {
  const map = useMap()
  const hasFitRef = useRef(false)

  useEffect(() => {
    if (hasFitRef.current || zones.length < 2) {
      return
    }
    const bounds = zones.map((zone) => [zone.lat, zone.lng] as LatLngTuple)
    map.fitBounds(bounds, { padding: [26, 26] })
    hasFitRef.current = true
  }, [map, zones])

  return null
}

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
  const overviewLoadInFlight = useRef(false)
  const assignmentsLoadInFlight = useRef(false)
  const routesLoadInFlight = useRef(false)

  const pushLog = useCallback((kind: LogKind, title: string, detail: string) => {
    const now = new Date()
    const next: ActionLogEntry = {
      id: `${now.getTime()}_${Math.random().toString(16).slice(2, 8)}`,
      kind,
      title,
      detail,
      time: now.toLocaleTimeString(),
    }
    setLogEntries((previous) => [next, ...previous].slice(0, 14))
  }, [])

  const loadOverview = useCallback(async (quiet: boolean) => {
    if (overviewLoadInFlight.current) {
      return
    }
    overviewLoadInFlight.current = true
    try {
      const payload = await fetchJson<OverviewPayload>('/ui-api/overview', undefined, 90_000)
      setOverview(payload)
      setLastUpdatedAt(new Date().toLocaleTimeString())
      if (!quiet) {
        setError(null)
      }
    } catch (err) {
      if (!quiet) {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      overviewLoadInFlight.current = false
    }
  }, [])

  const loadAssignments = useCallback(async (quiet: boolean) => {
    if (assignmentsLoadInFlight.current) {
      return
    }
    assignmentsLoadInFlight.current = true
    try {
      const payload = await fetchJson<{ assignments: Assignment[] }>(
        '/ui-api/assignments',
        undefined,
        90_000,
      )
      setAssignments(Array.isArray(payload.assignments) ? payload.assignments : [])
      if (!quiet) {
        setError(null)
      }
    } catch (err) {
      if (!quiet) {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      assignmentsLoadInFlight.current = false
    }
  }, [])

  const loadRoutes = useCallback(async (quiet: boolean) => {
    if (routesLoadInFlight.current) {
      return
    }
    routesLoadInFlight.current = true
    try {
      const payload = await fetchJson<{ routes: RouteSegment[] }>('/ui-api/routes', undefined, 90_000)
      setRoutes(Array.isArray(payload.routes) ? payload.routes : [])
      if (!quiet) {
        setError(null)
      }
    } catch (err) {
      if (!quiet) {
        setError(err instanceof Error ? err.message : String(err))
      }
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

  useEffect(() => {
    let alive = true
    const bootstrapWatchdog = window.setTimeout(() => {
      if (alive) {
        setBootstrapping(false)
      }
    }, 4_000)

    const bootstrap = async () => {
      await Promise.allSettled([loadOverview(false), loadAssignments(false), loadRoutes(false), loadHealth()])
      if (alive) {
        setBootstrapping(false)
      }
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
      window.clearTimeout(bootstrapWatchdog)
      window.clearInterval(timer)
    }
  }, [loadAssignments, loadHealth, loadOverview, loadRoutes])

  const runAction = useCallback(
    async (action: ActionDefinition) => {
      setBusyActionId(action.id)
      setError(null)
      pushLog('info', `${action.label} started`, action.description)

      try {
        const payload = await fetchJson<Record<string, unknown>>(action.endpoint, {
          method: 'POST',
        }, actionTimeoutMs(action.id))
        pushLog('success', `${action.label} completed`, compactJson(payload))
        void Promise.all([loadOverview(true), loadAssignments(true), loadRoutes(true), loadHealth()])
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        setError(message)
        pushLog('error', `${action.label} failed`, message)
      } finally {
        setBusyActionId(null)
      }
    },
    [loadAssignments, loadHealth, loadOverview, loadRoutes, pushLog],
  )

  const zones = overview?.zones ?? []
  const activeEvents = overview?.active_events ?? []
  const meta = overview?.meta
  const assignmentCount = assignments.length
  const routeCount = routes.length
  const blockedRouteCount = routes.filter((route) => route.is_blocked).length

  const mapCenter = useMemo<LatLngTuple>(() => {
    const affected = zones.find((zone) => zone.is_affected)
    if (affected) return [affected.lat, affected.lng]
    if (zones.length > 0) return [zones[0].lat, zones[0].lng]
    return DEFAULT_CENTER
  }, [zones])

  const zoneIndex = useMemo(() => {
    const index = new Map<string, ZonePoint>()
    zones.forEach((zone) => index.set(zone.id, zone))
    return index
  }, [zones])

  const eventAnchors = useMemo(() => {
    return activeEvents
      .slice(0, 10)
      .map((event) => {
        const anchorZoneId = event.affected_zone_ids.find((zoneId) => zoneIndex.has(zoneId))
        if (!anchorZoneId) {
          return null
        }
        const zone = zoneIndex.get(anchorZoneId)
        if (!zone) {
          return null
        }
        return { event, zone }
      })
      .filter((row): row is { event: ActiveEvent; zone: ZonePoint } => row !== null)
  }, [activeEvents, zoneIndex])

  const ingestionStatus = health?.ingestion?.status ?? 'unknown'
  const ingestionHealthy = ingestionStatus === 'ok'

  const stageStates = [
    {
      label: 'Ingestion',
      done:
        logEntries.some((entry) => entry.kind === 'success' && entry.title.includes('Ingest')) ||
        (meta?.active_event_count ?? 0) > 0,
    },
    {
      label: 'Event Detection',
      done: (meta?.active_event_count ?? 0) > 0,
    },
    {
      label: 'Resource Assignment',
      done: assignmentCount > 0,
    },
    {
      label: 'Alerting',
      done: assignmentCount > 0,
    },
  ]

  return (
    <div className="command-shell">
      <header className="header-band">
        <div>
          <p className="eyebrow">DisasterGraph</p>
          <h1>Emergency Operations Command Center</h1>
          <p className="subtitle">
            Unified live frontend for ingestion, risk monitoring, and AI response dispatch.
          </p>
        </div>
        <div className="header-stats">
          <div className="header-chip">
            <span>Dashboard</span>
            <strong>{health?.dashboard.status ?? 'unknown'}</strong>
          </div>
          <div className={`header-chip ${ingestionHealthy ? 'good' : 'warn'}`}>
            <span>Ingestion</span>
            <strong>{ingestionStatus}</strong>
          </div>
          <div className="header-chip">
            <span>Last refresh</span>
            <strong>{lastUpdatedAt}</strong>
          </div>
        </div>
      </header>

      <section className="stage-rail" aria-label="Demo progression">
        {stageStates.map((stage) => (
          <div key={stage.label} className={`stage-pill ${stage.done ? 'done' : 'pending'}`}>
            <span className="stage-dot" />
            {stage.label}
          </div>
        ))}
      </section>

      {error ? <div className="error-banner">{error}</div> : null}

      <main className="dashboard-layout">
        <section className="panel controls-panel">
          <h2>Operations Console</h2>
          <p className="panel-blurb">
            Trigger flows from the pitch stage and show state changes in real time.
          </p>
          <div className="action-grid">
            {ACTIONS.map((action) => {
              const busy = busyActionId === action.id
              return (
                <button
                  key={action.id}
                  type="button"
                  data-tone={action.tone}
                  className={`action-button ${busy ? 'running' : ''}`}
                  onClick={() => void runAction(action)}
                  disabled={busyActionId !== null}
                >
                  <span className="action-label">{action.label}</span>
                  <span className="action-detail">{busy ? 'Running...' : action.description}</span>
                </button>
              )
            })}
          </div>

          <div className="health-strip">
            <div className="mini-card">
              <span>Scheduler</span>
              <strong>{health?.ingestion?.scheduler_running ? 'running' : 'not running'}</strong>
            </div>
            <div className="mini-card">
              <span>Firms job</span>
              <strong>{health?.ingestion?.firms_job ? 'present' : 'missing'}</strong>
            </div>
            <div className="mini-card">
              <span>Sentinel job</span>
              <strong>{health?.ingestion?.sentinel_job ? 'present' : 'missing'}</strong>
            </div>
          </div>

          <h3>Activity Log</h3>
          <div className="activity-log">
            {logEntries.length === 0 ? (
              <p className="empty-log">No actions yet. Start with Run All Ingestion.</p>
            ) : (
              logEntries.map((entry) => (
                <article key={entry.id} className={`log-line ${entry.kind}`}>
                  <div>
                    <strong>{entry.title}</strong>
                    <p>{entry.detail}</p>
                  </div>
                  <time>{entry.time}</time>
                </article>
              ))
            )}
          </div>
        </section>

        <section className="panel map-panel">
          <div className="panel-row">
            <h2>Live Risk Map</h2>
            <p className="map-note">Zones = risk footprint; pulses = active disaster events</p>
          </div>
          <div className="metric-strip">
            <div className="metric-card">
              <span>Total zones</span>
              <strong>{meta?.zone_count ?? 0}</strong>
            </div>
            <div className="metric-card danger">
              <span>Affected zones</span>
              <strong>{meta?.affected_zone_count ?? 0}</strong>
            </div>
            <div className="metric-card">
              <span>Active events</span>
              <strong>{meta?.active_event_count ?? 0}</strong>
            </div>
            <div className="metric-card">
              <span>Assignments</span>
              <strong>{assignmentCount}</strong>
            </div>
            <div className="metric-card">
              <span>Road routes</span>
              <strong>{routeCount}</strong>
            </div>
            <div className="metric-card danger">
              <span>Blocked roads</span>
              <strong>{blockedRouteCount}</strong>
            </div>
          </div>

          <div className="map-shell">
            <MapContainer center={mapCenter} zoom={10} scrollWheelZoom className="map-canvas">
              <TileLayer
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                attribution="&copy; OpenStreetMap contributors"
              />
              <MapAutoFit zones={zones} />

              {zones.map((zone) => (
                <CircleMarker
                  key={zone.id}
                  center={[zone.lat, zone.lng]}
                  radius={severityRadius(zone)}
                  pathOptions={{
                    color: severityColor(zone),
                    fillColor: severityColor(zone),
                    fillOpacity: zone.is_affected ? 0.34 : 0.18,
                    weight: 2,
                  }}
                >
                  <Popup>
                    <div className="popup-wrap">
                      <strong>{zone.name}</strong>
                      <p>ID: {zone.id}</p>
                      <p>Affected: {String(zone.is_affected)}</p>
                      <p>Severity: {zone.severity.toFixed(3)}</p>
                    </div>
                  </Popup>
                </CircleMarker>
              ))}

              {routes.map((route) => (
                <Polyline
                  key={`${route.from_zone_id}_${route.to_zone_id}_${route.route_id}`}
                  positions={[
                    [route.from.lat, route.from.lng],
                    [route.to.lat, route.to.lng],
                  ]}
                  pathOptions={{
                    color: route.is_blocked ? '#b3261e' : '#2a8f6a',
                    weight: route.is_blocked ? 3 : 2,
                    opacity: route.is_blocked ? 0.95 : 0.75,
                  }}
                >
                  <Popup>
                    <div className="popup-wrap">
                      <strong>{route.route_id}</strong>
                      <p>
                        {route.from_zone_id} → {route.to_zone_id}
                      </p>
                      <p>Blocked: {String(route.is_blocked)}</p>
                      <p>Distance: {Number(route.distance_km ?? 0).toFixed(2)} km</p>
                      <p>ETA: {Number(route.estimated_time_min ?? 0).toFixed(1)} min</p>
                      {route.blockage_reason ? <p>Reason: {route.blockage_reason}</p> : null}
                    </div>
                  </Popup>
                </Polyline>
              ))}

              {eventAnchors.map(({ event, zone }) => (
                <Circle
                  key={event.event_id}
                  center={[zone.lat, zone.lng]}
                  radius={1300 + event.severity * 4700}
                  pathOptions={{
                    color: '#c03b1c',
                    fillColor: '#c03b1c',
                    fillOpacity: 0.08,
                    weight: 1,
                  }}
                >
                  <Popup>
                    <div className="popup-wrap">
                      <strong>{event.event_type}</strong>
                      <p>{event.event_id}</p>
                      <p>Severity: {event.severity.toFixed(3)}</p>
                      <p>Zones linked: {event.affected_zone_ids.length}</p>
                    </div>
                  </Popup>
                </Circle>
              ))}
            </MapContainer>

            <div className="map-legend">
              <div>
                <span className="dot high" /> High severity (&gt;= 0.7)
              </div>
              <div>
                <span className="dot medium" /> Medium severity (&gt;= 0.4)
              </div>
              <div>
                <span className="dot low" /> Low severity (&lt; 0.4)
              </div>
              <div>
                <span className="dot idle" /> Not affected
              </div>
              <div>
                <span className="dot" style={{ backgroundColor: '#2a8f6a' }} /> Passable roads
              </div>
              <div>
                <span className="dot" style={{ backgroundColor: '#b3261e' }} /> Blocked roads
              </div>
            </div>
          </div>
        </section>

        <section className="panel feed-panel">
          <h2>Live Intelligence Feed</h2>
          <div className="resource-block">
            <h3>Resource Inventory</h3>
            <p>
              Total resources: <strong>{meta?.resource_count ?? 0}</strong> | Available:{' '}
              <strong>{meta?.available_resource_count ?? 0}</strong>
            </p>
            <div className="resource-tags">
              {Object.entries(meta?.resources_by_type ?? {}).map(([type, count]) => (
                <span key={type} className="resource-tag">
                  {type}: {count}
                </span>
              ))}
            </div>
          </div>

          <div className="feed-section">
            <h3>Top Active Events</h3>
            <div className="feed-list">
              {activeEvents.length === 0 ? (
                <p className="empty-state">No active events right now.</p>
              ) : (
                activeEvents.slice(0, 8).map((event) => (
                  <article key={event.event_id} className="feed-card">
                    <header>
                      <strong>{event.event_type}</strong>
                      <span>sev {event.severity.toFixed(3)}</span>
                    </header>
                    <p className="mono">{event.event_id}</p>
                    <p>
                      Source: {event.satellite_source ?? 'unknown'} | affected zones:{' '}
                      {event.affected_zone_ids.length}
                    </p>
                    <time>{renderTimestamp(event.timestamp)}</time>
                  </article>
                ))
              )}
            </div>
          </div>

          <div className="feed-section">
            <h3>Latest Assignments</h3>
            <div className="feed-list">
              {assignments.length === 0 ? (
                <p className="empty-state">
                  No assignments yet. Run agent once after ingestion to generate dispatch records.
                </p>
              ) : (
                assignments.slice(0, 8).map((assignment) => (
                  <article key={`${assignment.resource_id}_${assignment.person_id}`} className="feed-card">
                    <header>
                      <strong>{assignment.resource_id}</strong>
                      <span>ETA {Number(assignment.eta_min ?? 0).toFixed(1)} min</span>
                    </header>
                    <p>Assigned to {assignment.person_id}</p>
                    <time>{renderTimestamp(assignment.assigned_at)}</time>
                  </article>
                ))
              )}
            </div>
          </div>
        </section>
      </main>

      {bootstrapping ? <div className="loading-overlay">Loading live graph state...</div> : null}
    </div>
  )
}
