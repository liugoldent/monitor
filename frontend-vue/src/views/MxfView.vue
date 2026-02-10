<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const MXF_API_URL = import.meta.env.VITE_MXF_API_URL || 'http://localhost:5050/api/mxf'

type MxfPoint = {
  time: string
  tx_bvav?: number
  mtx_bvav?: number
  mtx_tbta?: number
  signal: 'bull' | 'bear' | 'none'
}

const points = ref<MxfPoint[]>([])
const chartScrollRef = ref<HTMLDivElement | null>(null)
const loading = ref(true)
const lastDate = ref('')
const lastUpdate = ref('')
const errorMessage = ref('')

const signalLabel = (signal: string) => {
  if (signal === 'bull') return '做多'
  if (signal === 'bear') return '做空'
  return '混沌'
}

const fetchSeries = async () => {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(`${MXF_API_URL}?all=1`)
    const payload = await response.json()
    const data = Array.isArray(payload?.data) ? payload.data : []
    points.value = data.map((item: any) => ({
      time: String(item.time || ''),
      tx_bvav: Number(item.tx_bvav ?? 0),
      mtx_bvav: Number(item.mtx_bvav ?? 0),
      mtx_tbta: Number(item.mtx_tbta ?? 0),
      signal: (item.signal || 'none') as 'bull' | 'bear' | 'none',
    })).reverse()
    if (points.value.length > 0) {
      lastUpdate.value = points.value[points.value.length - 1].time
      lastDate.value = lastUpdate.value.slice(0, 10)
    } else {
      lastUpdate.value = ''
      lastDate.value = ''
    }
    await nextTick()
    if (chartScrollRef.value) {
      chartScrollRef.value.scrollLeft = chartScrollRef.value.scrollWidth
    }
  } catch (error) {
    errorMessage.value = '讀取 MXF 資料失敗'
    console.error(error)
  } finally {
    loading.value = false
  }
}

const counts = computed(() => {
  return points.value.reduce(
    (acc, item) => {
      acc[item.signal] += 1
      return acc
    },
    { bull: 0, bear: 0, none: 0 } as Record<'bull' | 'bear' | 'none', number>
  )
})

const chartWidth = computed(() => {
  const base = 600
  const perPoint = 10
  return Math.max(base, points.value.length * perPoint)
})

const chartHeight = 140

const yForSignal = (signal: 'bull' | 'bear' | 'none') => {
  if (signal === 'bull') return 20
  if (signal === 'bear') return 120
  return 70
}

const refreshMs = 60_000
let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  fetchSeries()
  refreshTimer = setInterval(fetchSeries, refreshMs)
})

onBeforeUnmount(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>

<template>
  <main class="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 p-6 text-slate-100">
    <div class="max-w-6xl mx-auto">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-2xl font-black tracking-wide">MXF 今日動向</h1>
          <p class="text-sm text-slate-400">每分鐘策略方向：做多 / 做空 / 混沌</p>
        </div>
        <RouterLink
          to="/"
          class="text-xs text-slate-300 border border-slate-600 px-3 py-1 rounded-full hover:border-slate-300 hover:text-white transition"
        >
          返回首頁
        </RouterLink>
      </div>

      <div class="mt-6 grid grid-cols-3 gap-4">
        <div class="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <div class="text-xs text-slate-400">今日日期</div>
          <div class="text-lg font-semibold">{{ lastDate || '-' }}</div>
        </div>
        <div class="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <div class="text-xs text-slate-400">最後更新</div>
          <div class="text-lg font-semibold">{{ lastUpdate || '-' }}</div>
        </div>
        <div class="rounded-xl border border-slate-700 bg-slate-900/60 p-4">
          <div class="text-xs text-slate-400">資料筆數</div>
          <div class="text-lg font-semibold">{{ points.length }}</div>
        </div>
      </div>

      <div class="mt-6 rounded-2xl border border-slate-700 bg-slate-900/70 p-5">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-sm font-semibold text-slate-200">方向分布</h2>
          <div class="text-xs text-slate-400">bull: {{ counts.bull }} / bear: {{ counts.bear }} / none: {{ counts.none }}</div>
        </div>
        <div class="flex gap-3 text-xs">
          <span class="flex items-center gap-2"><span class="h-2 w-2 rounded-full bg-emerald-400"></span>做多</span>
          <span class="flex items-center gap-2"><span class="h-2 w-2 rounded-full bg-rose-400"></span>做空</span>
          <span class="flex items-center gap-2"><span class="h-2 w-2 rounded-full bg-slate-500"></span>混沌</span>
        </div>

        <div ref="chartScrollRef" class="mt-4 overflow-x-auto">
          <svg
            v-if="points.length > 0"
            :viewBox="`0 0 ${chartWidth} ${chartHeight}`"
            :width="chartWidth"
            :height="chartHeight"
            class="min-w-full"
          >
            <defs>
              <linearGradient id="grid" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stop-color="#1f2937" />
                <stop offset="1" stop-color="#0f172a" />
              </linearGradient>
            </defs>
            <rect x="0" y="0" :width="chartWidth" :height="chartHeight" fill="url(#grid)" />
            <line x1="0" y1="20" :x2="chartWidth" y2="20" stroke="#1f2937" stroke-dasharray="4 6" />
            <line x1="0" y1="70" :x2="chartWidth" y2="70" stroke="#1f2937" stroke-dasharray="4 6" />
            <line x1="0" y1="120" :x2="chartWidth" y2="120" stroke="#1f2937" stroke-dasharray="4 6" />
            <template v-for="(point, index) in points" :key="point.time + index">
              <rect
                :x="index * 10"
                :y="yForSignal(point.signal) - 8"
                width="8"
                height="16"
                :fill="point.signal === 'bull' ? '#34d399' : point.signal === 'bear' ? '#fb7185' : '#64748b'"
                rx="2"
              >
                <title>{{ point.time }} - {{ signalLabel(point.signal) }}</title>
              </rect>
            </template>
          </svg>
          <div v-else class="text-sm text-slate-400 py-12 text-center">
            {{ loading ? '載入中...' : (errorMessage || '沒有資料') }}
          </div>
        </div>
      </div>

      <div class="mt-6 rounded-2xl border border-slate-700 bg-slate-900/60 p-5">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold text-slate-200">時間序列</h2>
          <button
            type="button"
            class="text-xs text-slate-300 border border-slate-600 px-3 py-1 rounded-full hover:border-slate-300 hover:text-white transition"
            @click="fetchSeries"
          >
            重新整理
          </button>
        </div>
        <div class="mt-4 grid grid-cols-3 text-xs text-slate-400 border-b border-slate-700 pb-2">
          <div>時間</div>
          <div>方向</div>
          <div class="text-right">指標</div>
        </div>
        <div class="max-h-80 overflow-y-auto">
          <div
            v-for="point in points"
            :key="point.time"
            class="grid grid-cols-3 text-xs py-2 border-b border-slate-800"
          >
            <div class="text-slate-300">{{ point.time }}</div>
            <div :class="point.signal === 'bull' ? 'text-emerald-400' : point.signal === 'bear' ? 'text-rose-400' : 'text-slate-400'">
              {{ signalLabel(point.signal) }}
            </div>
            <div class="text-right text-slate-400">
              {{ point.tx_bvav ?? 0 }} / {{ point.mtx_bvav ?? 0 }} / {{ point.mtx_tbta ?? 0 }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </main>
</template>
