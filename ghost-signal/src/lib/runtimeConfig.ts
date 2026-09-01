/**
 * Runtime URLs for arena-core (HTTP + WS).
 * Docker image bakes VITE_* at build time; from another host, `localhost` is wrong.
 * Before connect, you may set in the browser console:
 *   window.__ARENA_API_URL__ = 'http://192.168.x.x:8800'
 *   window.__ARENA_WS_URL__  = 'ws://192.168.x.x:8800/ws'
 */
export function getArenaApiUrl(): string {
  if (typeof window !== 'undefined') {
    const w = window as Window & { __ARENA_API_URL__?: string }
    if (w.__ARENA_API_URL__) return w.__ARENA_API_URL__.replace(/\/$/, '')
  }
  return (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? 'http://localhost:8800'
}

export function getArenaWsBase(): string {
  if (typeof window !== 'undefined') {
    const w = window as Window & { __ARENA_WS_URL__?: string }
    if (w.__ARENA_WS_URL__) return w.__ARENA_WS_URL__.replace(/\/$/, '')
  }
  return (import.meta.env.VITE_WS_URL as string | undefined)?.replace(/\/$/, '') ?? 'ws://localhost:8800/ws'
}
