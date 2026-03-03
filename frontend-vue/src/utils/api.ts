const LOCAL_API_ORIGIN = 'http://localhost:5050'
const CLOUD_API_ORIGIN = 'https://monitor-9dtg.onrender.com'

const LOCAL_HOSTNAMES = new Set(['localhost', '127.0.0.1', '0.0.0.0'])

const normalizeBase = (base: string) => base.replace(/\/+$/, '')
const normalizePath = (path: string) => `/${path.replace(/^\/+/, '')}`

const inferApiBaseUrl = () => {
  if (typeof window !== 'undefined' && LOCAL_HOSTNAMES.has(window.location.hostname)) {
    return LOCAL_API_ORIGIN
  }
  return CLOUD_API_ORIGIN
}

export const API_BASE_URL = normalizeBase(import.meta.env.VITE_API_BASE_URL || inferApiBaseUrl())

export const resolveApiUrl = (path: string, specificUrl?: string) => {
  if (specificUrl && specificUrl.trim()) {
    return specificUrl.trim()
  }
  return `${API_BASE_URL}${normalizePath(path)}`
}
