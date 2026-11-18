import { useState, useEffect, useRef, useCallback } from 'react'

// R-206b (D-165, D-166): live aircraft on a map. Data comes only from our own
// server (/api/v2/flights), which proxies adsb.lol (primary) and adsb.fi
// (fallback) under one shared throttle. The map is a small built-in slippy map
// over OpenStreetMap tiles (no new npm dependency). Attribution for the tiles
// and for whichever data source answered is always shown; a failure is shown
// as a failure, never as "no aircraft".
const API_BASE = (import.meta.env && import.meta.env.VITE_API_BASE) || ''
const TILE = 256
const TILE_URL = (z, x, y) => `https://tile.openstreetmap.org/${z}/${x}/${y}.png`
const MIN_Z = 3
const MAX_Z = 12
const REFRESH_MS = 15000
const MAX_RADIUS = 250

export const MODES = [
  { id: 'point', label: 'المنطقة الظاهرة' },
  { id: 'callsign', label: 'رمز الرحلة', hint: 'مثل SVA123' },
  { id: 'reg', label: 'رقم التسجيل', hint: 'مثل HZ-AK11' },
  { id: 'hex', label: 'رمز ICAO', hint: '6 خانات ست عشرية' },
  { id: 'type', label: 'طراز الطائرة', hint: 'مثل B77W' },
  { id: 'squawk', label: 'رمز المرسل (squawk)', hint: '4 أرقام 0-7' },
  { id: 'mil', label: 'الطائرات العسكرية' },
  { id: 'ladd', label: 'قائمة LADD' },
  { id: 'pia', label: 'عناوين PIA الخاصة' },
]

// Web Mercator, world pixel coordinates at zoom z.
export function project(lat, lon, z) {
  const s = TILE * 2 ** z
  const φ = Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI / 180
  return { x: (lon + 180) / 360 * s, y: (1 - Math.log(Math.tan(φ) + 1 / Math.cos(φ)) / Math.PI) / 2 * s }
}

export function unproject(x, y, z) {
  const s = TILE * 2 ** z
  const lon = x / s * 360 - 180
  const n = Math.PI - 2 * Math.PI * y / s
  return { lat: 180 / Math.PI * Math.atan(Math.sinh(n)), lon: ((lon + 540) % 360) - 180 }
}

// Nautical-mile radius covering the visible map (half its diagonal), capped at the API's 250.
export function radiusForView(lat, z, w, h) {
  const mPerPx = 156543.03392 * Math.cos(lat * Math.PI / 180) / 2 ** z
  const nm = Math.hypot(w, h) / 2 * mPerPx / 1852
  return Math.max(1, Math.min(MAX_RADIUS, Math.round(nm)))
}

export async function fetchFlights(params, fetchImpl = globalThis.fetch) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== ''))
  const r = await fetchImpl(`${API_BASE}/api/v2/flights?${qs}`)
  let body = {}
  try { body = await r.json() } catch { /* non-JSON error page */ }
  if (!r.ok) {
    const detail = typeof body.detail === 'string' ? body.detail
      : (body.errors || []).map((e) => `${e.provider}: ${e.error}`).join(' · ')
    const e = new Error(r.status === 400 ? `مدخل غير صالح: ${detail}`
      : r.status === 429 ? 'طلبات كثيرة؛ انتظر قليلًا ثم أعد المحاولة'
        : `تعذر جلب بيانات الطائرات (${r.status})${detail ? ': ' + detail : ''}`)
    e.status = r.status
    throw e
  }
  return body
}

const ft = (v) => (typeof v === 'number' ? `${v.toLocaleString('ar')} قدم` : v === 'ground' ? 'على الأرض' : '—')
const kt = (v) => (typeof v === 'number' ? `${Math.round(v)} عقدة` : '—')

function Plane({ a, x, y, selected, onSelect }) {
  const rot = typeof a.track === 'number' ? a.track : 0
  return (
    <button type="button" className={`flights-marker ${a.military ? 'mil' : ''} ${selected ? 'sel' : ''}`}
      style={{ left: x, top: y }} title={a.flight || a.r || a.hex} aria-label={`طائرة ${a.flight || a.hex}`}
      data-hex={a.hex} onClick={(e) => { e.stopPropagation(); onSelect(a.hex) }}>
      <svg width="18" height="18" viewBox="-9 -9 18 18" style={{ transform: `rotate(${rot}deg)` }} aria-hidden="true">
        <path d="M0,-8 L5,6 L0,3 L-5,6 Z" />
      </svg>
    </button>
  )
}

export default function Flights({ load = fetchFlights, initial = { lat: 21.68, lon: 39.15, z: 8 }, auto = true }) {
  const box = useRef(null)
  const drag = useRef(null)
  const [size, setSize] = useState({ w: 800, h: 460 })
  const [view, setView] = useState(initial)
  const [mode, setMode] = useState('point')
  const [q, setQ] = useState('')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [live, setLive] = useState(auto)
  const [sel, setSel] = useState(null)
  const [asked, setAsked] = useState({ mode: 'point' })

  useEffect(() => {
    const el = box.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth || 800, h: el.clientHeight || 460 }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const run = useCallback(async (req) => {
    setBusy(true)
    try {
      const d = await load(req)
      setData(d); setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }, [load])

  const areaReq = useCallback(() => ({
    mode: 'point', lat: view.lat.toFixed(2), lon: view.lon.toFixed(2),
    radius: radiusForView(view.lat, view.z, size.w, size.h),
  }), [view, size])

  // first load, then live refresh of whatever was last asked
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; run(areaReq()) }
  }, [run, areaReq])
  useEffect(() => {
    if (!live) return undefined
    const t = setInterval(() => run(asked.mode === 'point' ? areaReq() : asked), REFRESH_MS)
    return () => clearInterval(t)
  }, [live, asked, run, areaReq])

  const search = (e) => {
    e?.preventDefault()
    const req = mode === 'point' ? areaReq() : { mode, ...(MODES.find((m) => m.id === mode).hint ? { q } : {}) }
    setAsked(req); setSel(null); run(req)
  }

  const c = project(view.lat, view.lon, view.z)
  const left = c.x - size.w / 2
  const top = c.y - size.h / 2
  const n = 2 ** view.z
  const tiles = []
  for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + size.w) / TILE); tx++) {
    for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + size.h) / TILE); ty++) {
      if (ty < 0 || ty >= n) continue
      tiles.push({ key: `${tx}/${ty}`, src: TILE_URL(view.z, ((tx % n) + n) % n, ty), x: tx * TILE - left, y: ty * TILE - top })
    }
  }

  const zoom = (d) => setView((v) => ({ ...v, z: Math.max(MIN_Z, Math.min(MAX_Z, v.z + d)) }))
  const onDown = (e) => { drag.current = { x: e.clientX, y: e.clientY, c } }
  const onMove = (e) => {
    if (!drag.current) return
    const d = drag.current
    const p = unproject(d.c.x - (e.clientX - d.x), d.c.y - (e.clientY - d.y), view.z)
    setView((v) => ({ ...v, lat: p.lat, lon: p.lon }))
  }
  const onUp = () => { drag.current = null }

  const aircraft = data?.aircraft || []
  const placed = aircraft.filter((a) => typeof a.lat === 'number' && typeof a.lon === 'number')
  const selA = aircraft.find((a) => a.hex === sel)
  const focus = (a) => { setSel(a.hex); if (typeof a.lat === 'number') setView((v) => ({ ...v, lat: a.lat, lon: a.lon })) }
  const modeInfo = MODES.find((m) => m.id === mode)

  return (
    <div className="flights-page">
      <form className="card flights-bar" onSubmit={search}>
        <select className="form-input account-select" value={mode} aria-label="نوع البحث"
          onChange={(e) => setMode(e.target.value)}>
          {MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
        {modeInfo.hint && (
          <input className="form-input" value={q} placeholder={modeInfo.hint} aria-label="قيمة البحث"
            onChange={(e) => setQ(e.target.value)} />
        )}
        <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? 'جارٍ…' : 'ابحث'}</button>
        <label className="account-hint flights-live">
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> تحديث كل 15 ثانية
        </label>
      </form>

      {error && <div className="badge badge-error" role="alert">{error}</div>}
      {data && (
        <div className="account-hint" data-testid="flights-summary">
          {data.count} طائرة · المصدر {data.provider}
          {data.fallback_used && <span className="badge badge-warning"> المصدر الأساسي لم يجب؛ استُخدم الاحتياطي</span>}
          {data.cached && ' · من الذاكرة المؤقتة'}
          {data.count > placed.length && ` · ${data.count - placed.length} بلا موقع`}
        </div>
      )}

      <div className="flights-map" ref={box} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
        onPointerLeave={onUp} onClick={() => setSel(null)} data-testid="flights-map">
        {tiles.map((t) => <img key={t.key} className="flights-tile" src={t.src} alt="" draggable="false"
          style={{ left: t.x, top: t.y }} />)}
        {placed.map((a) => {
          const p = project(a.lat, a.lon, view.z)
          let x = p.x - left
          const world = TILE * n
          if (x < -world / 2) x += world
          if (x > world / 2 + size.w) x -= world
          return <Plane key={a.hex} a={a} x={x} y={p.y - top} selected={a.hex === sel} onSelect={setSel} />
        })}
        <div className="flights-zoom">
          <button type="button" className="btn btn-ghost" onClick={(e) => { e.stopPropagation(); zoom(1) }} aria-label="تكبير">+</button>
          <button type="button" className="btn btn-ghost" onClick={(e) => { e.stopPropagation(); zoom(-1) }} aria-label="تصغير">−</button>
          <button type="button" className="btn btn-ghost" onClick={(e) => { e.stopPropagation(); const r = areaReq(); setMode('point'); setAsked(r); run(r) }}>
            ابحث هنا</button>
        </div>
        <div className="flights-attrib" data-testid="flights-attrib">
          خرائط © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">مساهمو OpenStreetMap</a>
          {data?.attribution && <> · بيانات <a href={data.attribution.home} target="_blank" rel="noreferrer">{data.provider}</a>
            {' '}(<a href={data.attribution.license_url} target="_blank" rel="noreferrer">{data.attribution.license}</a>)</>}
        </div>
      </div>

      {selA && (
        <div className="card account-card" data-testid="flight-detail">
          <div className="card-title">{selA.flight || selA.r || selA.hex}{selA.military && <span className="badge badge-warning"> عسكرية</span>}</div>
          <div className="account-hint">
            ICAO <code>{selA.hex}</code> · التسجيل {selA.r || '—'} · الطراز {selA.t || '—'} {selA.desc ? `(${selA.desc})` : ''}
          </div>
          <div className="account-hint">الارتفاع {ft(selA.alt_baro)} · السرعة {kt(selA.gs)} · الاتجاه {typeof selA.track === 'number' ? `${Math.round(selA.track)}°` : '—'} · squawk {selA.squawk || '—'}</div>
        </div>
      )}

      {data && asked.mode !== 'point' && (
        <div className="card account-card">
          <div className="card-title">النتائج</div>
          {aircraft.length === 0 ? <div className="account-hint">لا توجد طائرة مطابقة الآن.</div> : (
            <ul className="account-list">
              {aircraft.slice(0, 200).map((a) => (
                <li className="account-row" key={a.hex}>
                  <span>{a.flight || '—'} · {a.r || '—'} · {a.t || '—'} · {ft(a.alt_baro)}</span>
                  <button className="btn btn-ghost" type="button" disabled={typeof a.lat !== 'number'} onClick={() => focus(a)}>على الخريطة</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="account-hint">البيانات من شبكات متطوعين (ADS-B) وقد لا تغطي كل منطقة؛ للاستخدام الشخصي غير التجاري.</div>
    </div>
  )
}
