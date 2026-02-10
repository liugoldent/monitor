<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import MarketTable from '../components/MarketTable.vue'
import DashboardPanel from '../components/DashboardPanel.vue'

type MarketItem = {
  id: string | number
  name: string
  nearMonth: number
  farMonth: number
  combine: number
}

type CrossSuggestion = {
  id: string | number
  name: string
  price: string | number
  code?: string
}

const highest20 = ref<MarketItem[]>([])
const lowest20 = ref<MarketItem[]>([])
const crossSuggestions = ref<CrossSuggestion[]>([])
const turnoverTodayDate = ref<string>('')

const marketSentiment = ref({
  foreign: 0,
  retail: 0,
  guerilla: 0,
})
const marketSentimentDate = ref<string>('')
const MXF_API_URL = import.meta.env.VITE_MXF_API_URL || 'http://localhost:5050/api/mxf'

const fetchMarketSentiment = async () => {
  try {
    const response = await fetch(MXF_API_URL)
    const payload = await response.json()
    marketSentiment.value = {
      foreign: Number(payload?.tx_bvav ?? 0),
      retail: Number(payload?.mtx_tbta ?? 0),
      guerilla: Number(payload?.mtx_bvav ?? 0),
    }
    marketSentimentDate.value = payload?.time ? String(payload.time).slice(0, 10) : ''
  } catch (error) {
    console.error('Failed to load market sentiment:', error)
  }
}

const tradeSuggestion = computed(() => {
  const { foreign, retail, guerilla } = marketSentiment.value
  if (foreign < 0 && guerilla < 0 && retail > 0) return '做空'
  if (foreign > 0 && guerilla > 0 && retail < 0) return '做多'
  return '混沌'
})

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  fetchMarketSentiment()
  refreshTimer = setInterval(fetchMarketSentiment, 60_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>

<template>
  <main class="h-screen w-screen overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 p-4">
    <div class="grid grid-cols-2 gap-4 h-full w-full">
      <div class="h-full rounded-xl overflow-hidden shadow-2xl border border-gray-800 flex flex-col">
        <div class="p-4 bg-[#242424] border-b border-gray-700 shrink-0">
          <div class="flex items-start justify-between">
            <h2 class="text-white font-bold mb-4 flex items-center gap-2">
              <span class="w-2 h-6 bg-blue-500 rounded"></span>
              大盤氣氛 & 建議
              <span class="text-[10px] text-gray-400 ml-2">{{ marketSentimentDate || '-' }}</span>
            </h2>
            <RouterLink
              to="/mxf"
              class="text-xs text-slate-300 border border-slate-600 px-3 py-1 rounded-full hover:border-slate-300 hover:text-white transition"
            >
              MXF 分析
            </RouterLink>
          </div>

          <div class="flex gap-4">
            <div class="flex-1 grid grid-cols-3 gap-2">
              <div
                class="flex flex-col items-center justify-center bg-[#1a1a1a] p-2 rounded border border-gray-700">
                <span class="text-xs text-gray-500">外資</span>
                <span class="text-xl font-bold text-red-400">{{ marketSentiment.foreign }}</span>
              </div>
              <div
                class="flex flex-col items-center justify-center bg-[#1a1a1a] p-2 rounded border border-gray-700">
                <span class="text-xs text-gray-500">散戶</span>
                <span class="text-xl font-bold text-yellow-400">{{ marketSentiment.retail }}</span>
              </div>
              <div
                class="flex flex-col items-center justify-center bg-[#1a1a1a] p-2 rounded border border-gray-700">
                <span class="text-xs text-gray-500">游擊隊</span>
                <span class="text-xl font-bold text-green-400">{{ marketSentiment.guerilla }}</span>
              </div>
            </div>

            <div class="w-32 flex flex-col items-center justify-center border rounded" :class="{
              'bg-gradient-to-br from-red-900/50 to-red-600/20 border-red-500/30': tradeSuggestion === '做多',
              'bg-gradient-to-br from-green-900/50 to-green-600/20 border-green-500/30': tradeSuggestion === '做空',
              'bg-gradient-to-br from-gray-800/60 to-gray-700/30 border-gray-500/30': tradeSuggestion === '混沌',
            }">
              <span class="text-xs text-red-200 mb-1">操作建議</span>
              <span class="text-2xl font-black text-white tracking-widest">{{ tradeSuggestion }}</span>
            </div>
          </div>
        </div>

        <div class="flex-1 min-h-0">
          <MarketTable
            @update:highest20="highest20 = $event"
            @update:lowest20="lowest20 = $event"
          />
        </div>

        <div class="h-64 bg-[#1f1f1f] border-t border-gray-700 flex flex-col shrink-0">
          <div class="p-2 bg-[#1f1f1f] flex items-center justify-between shrink-0">
            <h3 class="font-bold text-sm text-white flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-green-400" viewBox="0 0 20 20"
                fill="currentColor">
                <path
                  d="M5 3a2 2 0 00-2 2v2a2 2 0 002 2h2a2 2 0 002-2V5a2 2 0 00-2-2H5zM5 11a2 2 0 00-2 2v2a2 2 0 002 2h2a2 2 0 002-2v-2a2 2 0 00-2-2H5zM11 5a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V5zM11 13a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
              </svg>
              交叉建議
              <span class="text-[10px] text-gray-400 ml-2">{{ turnoverTodayDate || '-' }}</span>
            </h3>
          </div>

          <div class="grid grid-cols-2 text-center py-2 bg-[#2d2d2d] text-xs font-medium text-gray-400 shrink-0">
            <div>標的</div>
            <div>現價</div>
          </div>

          <div class="overflow-y-auto flex-1 bg-black">
            <div v-for="item in crossSuggestions" :key="item.id"
              class="grid grid-cols-2 text-center py-3 border-b border-gray-900 transition-colors text-sm">
              <div class="font-bold text-blue-300">{{ item.name }}</div>
              <div class="text-yellow-400">{{ item.price }}</div>
            </div>
          </div>
        </div>
      </div>
      <div class="h-full rounded-xl overflow-hidden shadow-2xl border border-gray-800">
        <DashboardPanel
          :highest20="highest20"
          :lowest20="lowest20"
          :tradeSuggestion="tradeSuggestion"
          @update:crossSuggestions="crossSuggestions = $event"
          @update:turnoverDate="turnoverTodayDate = $event"
        />
      </div>
    </div>
  </main>
</template>
