<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import MarketTable from '../components/MarketTable.vue'
import DashboardPanel from '../components/DashboardPanel.vue'
import { resolveApiUrl } from '../utils/api'

type MarketItem = {
  id: string | number
  name: string
  nearMonth: number
  farMonth: number
  combine: number
}

const highest20 = ref<MarketItem[]>([])
const lowest20 = ref<MarketItem[]>([])
const isDev = import.meta.env.VITE_ENV === 'DEV'
const marketAccessPassword = 'futures'
const isMarketLocked = ref(true)
const lockPassword = ref('')
const lockError = ref('')

const marketSentiment = ref({
  foreign: 0,
  retail: 0,
  guerilla: 0,
})
const marketSentimentDate = ref<string>('')
const MXF_API_URL = resolveApiUrl('/api/mxf', import.meta.env.VITE_MXF_API_URL)

const refreshMarketData = async () => {
  await fetchMarketSentiment()
}

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

const startMarketPolling = () => {
  if (refreshTimer) return
  void refreshMarketData()
  refreshTimer = setInterval(refreshMarketData, 60_000)
}

const stopMarketPolling = () => {
  if (!refreshTimer) return
  clearInterval(refreshTimer)
  refreshTimer = null
}

const unlockMarket = async () => {
  if (lockPassword.value !== marketAccessPassword) {
    lockError.value = '密碼錯誤'
    lockPassword.value = ''
    return
  }

  lockError.value = ''
  isMarketLocked.value = false
}

const tradeSuggestion = computed(() => {
  const { foreign, retail, guerilla } = marketSentiment.value
  if (foreign < 0 && guerilla < 0 && retail > 0) return '做空'
  if (foreign > 0 && guerilla > 0 && retail < 0) return '做多'
  return '混沌'
})

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  if (!isMarketLocked.value) startMarketPolling()
})

onBeforeUnmount(() => {
  stopMarketPolling()
})

watch(isMarketLocked, (locked) => {
  if (locked) {
    stopMarketPolling()
    return
  }

  startMarketPolling()
})
</script>

<template>
  <main class="home-shell h-screen w-screen overflow-hidden bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 p-4">
    <div class="home-layout grid grid-cols-2 gap-4 h-full w-full">
      <div class="home-panel home-panel--dashboard h-full min-h-0 rounded-xl overflow-hidden shadow-2xl border border-gray-800">
        <DashboardPanel
          :highest20="highest20"
          :lowest20="lowest20"
          :tradeSuggestion="tradeSuggestion"
        />
      </div>
      <div class="home-panel home-panel--market h-full rounded-xl overflow-hidden shadow-2xl border border-gray-800 flex flex-col relative">
        <template v-if="!isMarketLocked">
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

            <div class="home-sentiment-row flex gap-4">
              <div v-if="isDev" class="home-sentiment-stats flex-1 grid grid-cols-3 gap-2">
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

              <div class="home-trade-box flex flex-col items-center justify-center border rounded" :class="{
                'w-32': isDev,
                'w-full': !isDev,
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
        </template>

        <div
          v-else
          class="absolute inset-0 flex items-center justify-center bg-slate-950/70 backdrop-blur-2xl"
        >
          <div class="absolute inset-0 overflow-hidden">
            <div class="absolute -left-16 top-8 h-56 w-56 rounded-full bg-cyan-500/20 blur-3xl"></div>
            <div class="absolute right-0 bottom-10 h-64 w-64 rounded-full bg-blue-500/10 blur-3xl"></div>
            <div class="absolute left-1/2 top-1/3 h-40 w-40 -translate-x-1/2 rounded-full bg-white/5 blur-3xl"></div>
          </div>

          <div class="relative z-10 w-[min(88%,420px)] rounded-2xl border border-white/10 bg-slate-900/75 p-6 shadow-2xl">
            <div class="mb-5 flex items-center gap-3">
              <span class="h-10 w-2 rounded-full bg-cyan-400"></span>
              <div>
                <p class="text-sm uppercase tracking-[0.35em] text-cyan-200/80">Locked</p>
                <h2 class="text-2xl font-semibold text-white">大盤氣氛 & 建議</h2>
              </div>
            </div>

            <div class="mb-5 rounded-xl border border-white/10 bg-white/5 p-4 text-sm leading-6 text-slate-300">
              這一區已上鎖。輸入密碼後才會載入真實的左半部資料與表格。
            </div>

            <form class="space-y-3" @submit.prevent="unlockMarket">
              <label class="block">
                <span class="mb-2 block text-xs uppercase tracking-[0.25em] text-slate-400">Access Code</span>
                <input
                  v-model="lockPassword"
                  type="password"
                  autocomplete="off"
                  spellcheck="false"
                  class="w-full rounded-xl border border-white/10 bg-slate-950/80 px-4 py-3 text-white outline-none ring-0 placeholder:text-slate-600 focus:border-cyan-300/60"
                  placeholder="輸入密碼"
                  @input="lockError = ''"
                />
              </label>

              <div v-if="lockError" class="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                {{ lockError }}
              </div>

              <button
                type="submit"
                class="w-full rounded-xl bg-cyan-500 px-4 py-3 font-semibold text-slate-950 transition hover:bg-cyan-400"
              >
                解鎖
              </button>
            </form>

          </div>
        </div>
      </div>
    </div>
  </main>
</template>


<style scoped>
.home-shell {
  min-height: 100vh;
  width: 100vw;
}

.home-panel {
  min-width: 0;
}

@media (max-width: 1180px) {
  .home-shell {
    height: auto;
    min-height: 100dvh;
    width: 100%;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 12px;
  }

  .home-layout {
    grid-template-columns: 1fr;
    height: auto;
  }

  .home-panel {
    height: auto;
    min-height: 0;
  }

  .home-sentiment-row {
    flex-direction: column;
  }

  .home-sentiment-row > * {
    width: 100%;
  }

  .home-sentiment-stats {
    width: 100%;
  }

  .home-trade-box {
    width: 100% !important;
  }
}

@media (max-width: 768px) {
  .home-shell {
    padding: 8px;
  }

  .home-layout {
    gap: 12px;
  }
}
</style>
