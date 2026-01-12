<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type TurnoverItem = {
    id: number | string
    name: string
    price: string | number
    volume: string | number
}

// 1. Turnover Ranking (大盤成交值排行)
const turnoverRanking = ref<TurnoverItem[]>([])
const TURNOVER_API_URL =
    import.meta.env.VITE_TURNOVER_API_URL || 'http://localhost:5050/api/turnover'

const fetchTurnoverRanking = async () => {
    try {
        const response = await fetch(TURNOVER_API_URL)
        const payload = await response.json()
        const data = Array.isArray(payload?.data) ? payload.data : []
        turnoverRanking.value = data.map((item: { no?: number; name?: string; close?: string; turnover?: string }) => ({
            id: item.no ?? item.name ?? Math.random(),
            name: item.name ?? '',
            price: item.close ?? '-',
            volume: item.turnover ?? '-',
        }))
    } catch (error) {
        console.error('Failed to load turnover ranking:', error)
    }
}

// 2. Market Sentiment (大盤氣氛 - 3個數字)
const marketSentiment = ref({
    foreign: 0,
    retail: 0,
    guerilla: 0,
})
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
    } catch (error) {
        console.error('Failed to load market sentiment:', error)
    }
}

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
    fetchTurnoverRanking()
    fetchMarketSentiment()
    refreshTimer = setInterval(() => {
        fetchTurnoverRanking()
        fetchMarketSentiment()
    }, 60_000)
})

onBeforeUnmount(() => {
    if (refreshTimer) {
        clearInterval(refreshTimer)
        refreshTimer = null
    }
})

// 3. Recommendation (建議做多or空)
const tradeSuggestion = computed(() => {
    const { foreign, retail, guerilla } = marketSentiment.value
    if (foreign < 0 && guerilla < 0 && retail > 0) return '做空'
    if (foreign > 0 && guerilla > 0 && retail < 0) return '做多'
    return '混沌'
})

// 4. Mock Data for Cross Analysis (交叉建議股票)
const crossSuggestions = ref([
    { id: 1, name: '台積電', signal: '多方共振', score: 95 },
    { id: 2, name: '聯發科', signal: '量價突破', score: 88 },
    { id: 3, name: '緯創', signal: '底部反轉', score: 82 },
    { id: 4, name: '廣達', signal: '高檔鈍化', score: 70 },
])
</script>

<template>
    <div class="flex flex-col h-full bg-[#1a1a1a] text-gray-300 font-sans overflow-hidden">
        
        <!-- Section 1: Top Dashboard (Sentiment & Suggestion) -->
        <div class="p-4 bg-[#242424] border-b border-gray-700 shrink-0">
            <h2 class="text-white font-bold mb-4 flex items-center gap-2">
                <span class="w-2 h-6 bg-blue-500 rounded"></span>
                大盤氣氛 & 建議
            </h2>
            
            <div class="flex gap-4">
                <!-- 3 Numbers -->
                <div class="flex-1 grid grid-cols-3 gap-2">
                    <div class="flex flex-col items-center justify-center bg-[#1a1a1a] p-2 rounded border border-gray-700">
                        <span class="text-xs text-gray-500">外資</span>
                        <span class="text-xl font-bold text-red-400">{{ marketSentiment.foreign }}</span>
                    </div>
                    <div class="flex flex-col items-center justify-center bg-[#1a1a1a] p-2 rounded border border-gray-700">
                        <span class="text-xs text-gray-500">散戶</span>
                        <span class="text-xl font-bold text-yellow-400">{{ marketSentiment.retail }}</span>
                    </div>
                    <div class="flex flex-col items-center justify-center bg-[#1a1a1a] p-2 rounded border border-gray-700">
                        <span class="text-xs text-gray-500">游擊隊</span>
                        <span class="text-xl font-bold text-green-400">{{ marketSentiment.guerilla }}</span>
                    </div>
                </div>

                <!-- Suggestion Box -->
                <div
                    class="w-32 flex flex-col items-center justify-center border rounded"
                    :class="{
                        'bg-gradient-to-br from-red-900/50 to-red-600/20 border-red-500/30': tradeSuggestion === '做多',
                        'bg-gradient-to-br from-green-900/50 to-green-600/20 border-green-500/30': tradeSuggestion === '做空',
                        'bg-gradient-to-br from-gray-800/60 to-gray-700/30 border-gray-500/30': tradeSuggestion === '混沌',
                    }"
                >
                    <span class="text-xs text-red-200 mb-1">操作建議</span>
                    <span class="text-2xl font-black text-white tracking-widest">{{ tradeSuggestion }}</span>
                </div>
            </div>
        </div>

        <!-- Section 2: Turnover Ranking (Table) -->
        <div class="flex-1 flex flex-col min-h-0 border-b border-gray-700">
            <div class="p-2 bg-[#1f1f1f] flex items-center justify-between shrink-0">
                <h3 class="font-bold text-sm text-white flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-purple-400" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M12 7a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0V8.414l-4.293 4.293a1 1 0 01-1.414 0L8 10.414l-4.293 4.293a1 1 0 01-1.414-1.414l5-5a1 1 0 011.414 0L11 10.586 14.586 7H12z" clip-rule="evenodd" />
                    </svg>
                    大盤成交值排行
                </h3>
            </div>
            
            <div class="grid grid-cols-3 text-center py-2 bg-[#2d2d2d] text-xs font-medium text-gray-400 shrink-0">
                <div>股名</div>
                <div>現價</div>
                <div>成交值</div>
            </div>

            <div class="overflow-y-auto flex-1 bg-black">
                <div v-for="stock in turnoverRanking" :key="stock.id" class="grid grid-cols-3 text-center py-3 border-b border-gray-900 hover:bg-gray-900 transition-colors text-sm">
                    <div class="font-medium text-white">{{ stock.name }}</div>
                    <div class="text-yellow-400">{{ stock.price }}</div>
                    <div class="text-gray-400">{{ stock.volume }}</div>
                </div>
            </div>
        </div>

        <!-- Section 3: Cross Suggestions (Table) -->
        <div class="flex-1 flex flex-col min-h-0">
            <div class="p-2 bg-[#1f1f1f] flex items-center justify-between shrink-0">
                <h3 class="font-bold text-sm text-white flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-green-400" viewBox="0 0 20 20" fill="currentColor">
                        <path d="M5 3a2 2 0 00-2 2v2a2 2 0 002 2h2a2 2 0 002-2V5a2 2 0 00-2-2H5zM5 11a2 2 0 00-2 2v2a2 2 0 002 2h2a2 2 0 002-2v-2a2 2 0 00-2-2H5zM11 5a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V5zM11 13a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                    </svg>
                    交叉建議 (多/空/量)
                </h3>
            </div>
            
            <div class="grid grid-cols-3 text-center py-2 bg-[#2d2d2d] text-xs font-medium text-gray-400 shrink-0">
                <div>標的</div>
                <div>訊號</div>
                <div>綜合分</div>
            </div>

            <div class="overflow-y-auto flex-1 bg-black">
                <div v-for="item in crossSuggestions" :key="item.id" class="grid grid-cols-3 text-center py-3 border-b border-gray-900 hover:bg-gray-900 transition-colors text-sm">
                    <div class="font-bold text-blue-300">{{ item.name }}</div>
                    <div class="text-red-400 font-medium">{{ item.signal }}</div>
                    <div class="flex justify-center">
                        <span class="bg-gray-800 px-2 rounded text-gray-300 text-xs py-0.5">{{ item.score }}</span>
                    </div>
                </div>
            </div>
        </div>

    </div>
</template>
