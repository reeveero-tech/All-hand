import { useState, useEffect, useRef, useMemo, useCallback } from 'react'

// R-206c (D-165): our own movie map. The data is built from Wikidata (CC0) by
// the weekly workflow movies-map.yml and served by our server at
// /api/v2/movies/map. Similar films sit close together; each film has its 20
// nearest films. Suggestions are computed here in the browser from the films
// you mark — your list stays in this browser (localStorage), the server keeps
// no viewing history.
const API_BASE = (import.meta.env && import.meta.env.VITE_API_BASE) || ''
const LIKED_KEY = 'agos.movies.liked'

export async function fetchMap(fetchImpl = globalThis.fetch) {
  const r = await fetchImpl(`${API_BASE}/api/v2/movies/map`)
  let body = null
  try { body = await r.json() } catch { /* non-JSON */ }
  if (!r.ok) {
    const e = new Error(r.status === 503
      ? `خريطة الأفلام غير متاحة بعد${body?.detail ? ': ' + body.detail : ''}`
      : r.status === 429 ? 'طلبات كثيرة؛ انتظر قليلًا ثم أعد المحاولة'
        : `تعذر تحميل خريطة الأفلام (${r.status})`)
    e.status = r.status
    throw e
  }
  return body
}

// Arrays from the server → objects the UI can use.
export function decode(data) {
  const f = data.fields
  const at = (name) => f.indexOf(name)
  const I = { qid: at('qid'), ar: at('title_ar'), en: at('title_en'), year: at('year'), imdb: at('imdb'),
    genres: at('genres'), x: at('x'), y: at('y'), island: at('island'), links: at('sitelinks') }
  const genres = (data.genres || []).map(([id, label]) => ({ id, label }))
  const films = data.films.map((row, i) => ({
    i, qid: row[I.qid], ar: row[I.ar], en: row[I.en], year: row[I.year], imdb: row[I.imdb],
    genres: (row[I.genres] || []).map((g) => genres[g]?.label).filter(Boolean),
    x: row[I.x], y: row[I.y], island: row[I.island], links: row[I.links],
    key: `${row[I.ar] || ''} ${row[I.en] || ''}`.toLowerCase(),
  }))
  let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity
  for (const m of films) {
    if (m.x < minX) minX = m.x; if (m.x > maxX) maxX = m.x
    if (m.y < minY) minY = m.y; if (m.y > maxY) maxY = m.y
  }
  return { films, neighbours: data.neighbours, islands: data.islands || [], genres,
    bounds: { minX, maxX, minY, maxY }, generated_at: data.generated_at, source: data.source }
}

export const title = (m) => m.ar || m.en || m.qid

export function searchFilms(map, text, limit = 20) {
  const t = text.trim().toLowerCase()
  if (t.length < 2) return []
  const hits = []
  for (const m of map.films) {
    const at = m.key.indexOf(t)
    if (at >= 0) hits.push([at === 0 ? 0 : 1, -m.links, m])
  }
  hits.sort((a, b) => a[0] - b[0] || a[1] - b[1])
  return hits.slice(0, limit).map((h) => h[2])
}

// Films most similar to everything in `liked`: sum of neighbour scores, liked
// films themselves excluded; ties broken by popularity.
export function suggest(map, liked, k = 20) {
  const likedSet = new Set(liked)
  const score = new Map()
  for (const i of liked) {
    for (const [j, s] of map.neighbours[i] || []) {
      if (!likedSet.has(j)) score.set(j, (score.get(j) || 0) + s)
    }
  }
  return [...score.entries()]
    .sort((a, b) => b[1] - a[1] || map.films[b[0]].links - map.films[a[0]].links)
    .slice(0, k).map(([j, s]) => ({ film: map.films[j], score: s }))
}

export function fitView(bounds, w, h) {
  const bw = Math.max(1, bounds.maxX - bounds.minX)
  const bh = Math.max(1, bounds.maxY - bounds.minY)
  const scale = Math.min(w / bw, h / bh) * 0.92
  return { cx: (bounds.minX + bounds.maxX) / 2, cy: (bounds.minY + bounds.maxY) / 2, scale }
}

// Greedy label placement: a label is drawn only if its box does not overlap one
// already drawn (films come most-popular first, islands largest first).
export function labelPlacer(w, h, pad = 3) {
  const boxes = []
  return (x, y, width, height = 14) => {
    const b = { l: x - width / 2 - pad, r: x + width / 2 + pad, t: y - height, btm: y + pad }
    if (b.r < 0 || b.l > w || b.btm < 0 || b.t > h) return false
    for (const o of boxes) if (b.l < o.r && b.r > o.l && b.t < o.btm && b.btm > o.t) return false
    boxes.push(b)
    return true
  }
}

const colour = (island) => `hsl(${(island * 137.508) % 360} 65% 55%)`

function loadLiked() {
  try { return JSON.parse(globalThis.localStorage?.getItem(LIKED_KEY) || '[]') } catch { return [] }
}

export default function Movies({ load = fetchMap }) {
  const canvas = useRef(null)
  const wrap = useRef(null)
  const drag = useRef(null)
  const [map, setMap] = useState(null)
  const [error, setError] = useState(null)
  const [size, setSize] = useState({ w: 900, h: 520 })
  const [view, setView] = useState(null)
  const [sel, setSel] = useState(null)
  const [text, setText] = useState('')
  const [likedQ, setLikedQ] = useState(loadLiked)

  useEffect(() => {
    let alive = true
    load().then((d) => { if (alive) { setMap(decode(d)); setError(null) } })
      .catch((e) => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [load])

  useEffect(() => {
    const el = wrap.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth || 900, h: el.clientHeight || 520 }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => { if (map && !view) setView(fitView(map.bounds, size.w, size.h)) }, [map, view, size])
  useEffect(() => { try { globalThis.localStorage?.setItem(LIKED_KEY, JSON.stringify(likedQ)) } catch { /* private mode */ } }, [likedQ])

  const byQid = useMemo(() => new Map((map?.films || []).map((m) => [m.qid, m.i])), [map])
  const liked = useMemo(() => likedQ.map((q) => byQid.get(q)).filter((i) => i !== undefined), [likedQ, byQid])
  const suggestions = useMemo(() => (map && liked.length ? suggest(map, liked) : []), [map, liked])
  const hits = useMemo(() => (map ? searchFilms(map, text) : []), [map, text])

  const toScreen = useCallback((m) => ({
    x: (m.x - view.cx) * view.scale + size.w / 2, y: (m.y - view.cy) * view.scale + size.h / 2,
  }), [view, size])

  // draw
  useEffect(() => {
    const cv = canvas.current
    const ctx = cv && cv.getContext ? cv.getContext('2d') : null
    if (!ctx || !map || !view) return
    const dpr = globalThis.devicePixelRatio || 1
    cv.width = size.w * dpr; cv.height = size.h * dpr
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, size.w, size.h)
    const r = Math.max(1.5, Math.min(5, view.scale * 0.8))
    const nb = sel !== null ? new Set((map.neighbours[sel] || []).map(([j]) => j)) : null
    const likedSet = new Set(liked)
    for (const m of map.films) {
      const p = toScreen(m)
      if (p.x < -5 || p.y < -5 || p.x > size.w + 5 || p.y > size.h + 5) continue
      ctx.globalAlpha = nb && !nb.has(m.i) && m.i !== sel ? 0.25 : 0.9
      ctx.fillStyle = colour(m.island)
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); ctx.fill()
    }
    ctx.globalAlpha = 1
    // films in my list: a white ring, so the mark never looks like an island colour
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5
    for (const i of likedSet) {
      const p = toScreen(map.films[i])
      ctx.beginPath(); ctx.arc(p.x, p.y, r + 2.5, 0, Math.PI * 2); ctx.stroke()
    }
    ctx.font = '12px system-ui, sans-serif'; ctx.textAlign = 'center'
    const placed = labelPlacer(size.w, size.h)
    if (view.scale < 6) {
      ctx.fillStyle = 'rgba(230,230,240,0.9)'
      for (const isl of map.islands) {
        const p = toScreen(isl)
        if (placed(p.x, p.y, ctx.measureText(isl.label).width)) ctx.fillText(isl.label, p.x, p.y)
      }
    } else {
      ctx.fillStyle = 'rgba(235,235,245,0.9)'
      for (const m of map.films) {
        const p = toScreen(m)
        if (p.x < 0 || p.y < 0 || p.x > size.w || p.y > size.h) continue
        const t = title(m)
        if (placed(p.x, p.y - r - 3, ctx.measureText(t).width)) ctx.fillText(t, p.x, p.y - r - 3)
      }
    }
    if (sel !== null) {
      const p = toScreen(map.films[sel])
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2
      ctx.beginPath(); ctx.arc(p.x, p.y, r + 4, 0, Math.PI * 2); ctx.stroke()
    }
  }, [map, view, size, sel, liked, toScreen])

  const pick = (e) => {
    if (!map || !view) return
    const rect = canvas.current.getBoundingClientRect()
    const x = e.clientX - rect.left; const y = e.clientY - rect.top
    let best = null; let bd = 100
    for (const m of map.films) {
      const p = toScreen(m)
      const d = (p.x - x) ** 2 + (p.y - y) ** 2
      if (d < bd) { bd = d; best = m.i }
    }
    if (best !== null) setSel(best)
  }
  const focus = (i) => {
    setSel(i)
    const m = map.films[i]
    setView((v) => ({ ...v, cx: m.x, cy: m.y, scale: Math.max(v.scale, 8) }))
  }
  const zoom = (f) => setView((v) => ({ ...v, scale: Math.max(0.2, Math.min(80, v.scale * f)) }))
  const onDown = (e) => { drag.current = { x: e.clientX, y: e.clientY, v: view, moved: false } }
  const onMove = (e) => {
    const d = drag.current
    if (!d) return
    const dx = e.clientX - d.x; const dy = e.clientY - d.y
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
    setView({ ...d.v, cx: d.v.cx - dx / d.v.scale, cy: d.v.cy - dy / d.v.scale })
  }
  const onUp = (e) => { const d = drag.current; drag.current = null; if (d && !d.moved) pick(e) }
  const toggleLike = (m) => setLikedQ((l) => (l.includes(m.qid) ? l.filter((q) => q !== m.qid) : [...l, m.qid]))

  if (error) return <div className="badge badge-error" role="alert">{error}</div>
  if (!map) return <div className="account-hint">جارٍ تحميل خريطة الأفلام…</div>
  const s = sel !== null ? map.films[sel] : null

  return (
    <div className="movies-page">
      <div className="card movies-bar">
        <input className="form-input" value={text} placeholder="ابحث عن فيلم (عربي أو إنجليزي)" aria-label="بحث عن فيلم"
          onChange={(e) => setText(e.target.value)} />
        <span className="account-hint" data-testid="movies-summary">
          {map.films.length.toLocaleString('ar')} فيلمًا · {map.islands.length} جزيرة · حُدّثت {String(map.generated_at || '').slice(0, 10)}
        </span>
      </div>
      {hits.length > 0 && (
        <ul className="account-list movies-hits" data-testid="movies-hits">
          {hits.map((m) => (
            <li className="account-row" key={m.qid}>
              <button className="btn btn-ghost" type="button" onClick={() => { focus(m.i); setText('') }}>
                {title(m)}{m.en && m.ar ? ` — ${m.en}` : ''} {m.year ? `(${m.year})` : ''}</button>
            </li>
          ))}
        </ul>
      )}
      <div className="movies-layout">
        <div className="movies-map" ref={wrap} data-testid="movies-map">
          <canvas ref={canvas} style={{ width: size.w, height: size.h }}
            onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={() => { drag.current = null }}
            onWheel={(e) => zoom(e.deltaY < 0 ? 1.2 : 1 / 1.2)} />
          <div className="flights-zoom">
            <button type="button" className="btn btn-ghost" onClick={() => zoom(1.5)} aria-label="تكبير">+</button>
            <button type="button" className="btn btn-ghost" onClick={() => zoom(1 / 1.5)} aria-label="تصغير">−</button>
            <button type="button" className="btn btn-ghost" onClick={() => setView(fitView(map.bounds, size.w, size.h))}>الكل</button>
          </div>
        </div>
        <div className="movies-side">
          {s && (
            <div className="card account-card" data-testid="movie-detail">
              <div className="card-title">{title(s)} {s.year ? `(${s.year})` : ''}</div>
              {s.en && s.ar && <div className="account-hint">{s.en}</div>}
              <div className="account-hint">{s.genres.slice(0, 6).join('، ') || '—'}</div>
              <div className="movies-links">
                <button className="btn btn-primary" type="button" onClick={() => toggleLike(s)}>
                  {likedQ.includes(s.qid) ? 'إزالة من قائمتي' : 'أعجبني'}</button>
                <a className="btn btn-ghost" href={`https://www.wikidata.org/wiki/${s.qid}`} target="_blank" rel="noreferrer">Wikidata</a>
                {s.imdb && <a className="btn btn-ghost" href={`https://www.imdb.com/title/${s.imdb}/`} target="_blank" rel="noreferrer">IMDb</a>}
              </div>
              <div className="card-title movies-sub">أفلام قريبة</div>
              <ul className="account-list" data-testid="movie-neighbours">
                {(map.neighbours[s.i] || []).map(([j]) => (
                  <li className="account-row" key={j}>
                    <button className="btn btn-ghost" type="button" onClick={() => focus(j)}>
                      {title(map.films[j])} {map.films[j].year ? `(${map.films[j].year})` : ''}</button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="card account-card" data-testid="movie-suggestions">
            <div className="card-title">اقتراحات لك</div>
            {liked.length === 0
              ? <div className="account-hint">اختر أفلامًا أعجبتك («أعجبني») وستظهر هنا أفلام مشابهة لها.</div>
              : (
                <ul className="account-list">
                  {suggestions.map(({ film }) => (
                    <li className="account-row" key={film.qid}>
                      <button className="btn btn-ghost" type="button" onClick={() => focus(film.i)}>
                        {title(film)} {film.year ? `(${film.year})` : ''}</button>
                    </li>
                  ))}
                </ul>
              )}
            {liked.length > 0 && <div className="account-hint">قائمتي: {liked.length} · محفوظة في هذا المتصفح فقط</div>}
          </div>
        </div>
      </div>
      <div className="account-hint" data-testid="movies-attrib">
        البيانات من <a href="https://www.wikidata.org" target="_blank" rel="noreferrer">Wikidata</a> (CC0)؛ الخريطة والاقتراحات من بنائنا.
      </div>
    </div>
  )
}
