<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type TurnoverItem = {
    id: number | string
    name: string
    price: string | number
    volume: string | number
    code?: string
}

type TurnoverTechItem = {
    code: string
    heikin_Ashi?: string | number
    ma_UpperAll?: string | number
    sqzmom_stronger_2d?: string | number
}

type MarketItem = {
    id: number | string
    name: string
    nearMonth: number
    farMonth: number
    combine: number
}

type CrossSuggestion = {
    id: number | string
    name: string
    price: string | number
    code?: string
}

const props = defineProps<{
    highest20: MarketItem[]
    lowest20: MarketItem[]
}>()

// 1. Turnover Ranking (大盤成交值排行)
const turnoverToday = ref<TurnoverItem[]>([])
const turnoverYesterday = ref<TurnoverItem[]>([])
const turnoverTodayDate = ref<string>('')
const turnoverYesterdayDate = ref<string>('')
const turnoverTechMap = ref<Map<string, TurnoverTechItem>>(new Map())
const turnoverTechDate = ref<string>('')
const turnoverTechLastSlot = ref<string>('')
const TURNOVER_API_URL =
    import.meta.env.VITE_TURNOVER_API_URL || 'http://localhost:5050/api/turnover'
const TURNOVER_TECH_API_URL =
    import.meta.env.VITE_TURNOVER_TECH_API_URL || 'http://localhost:5050/api/turnover_tech'

const formatDateString = (date: Date) => {
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    return `${year}-${month}-${day}`
}

const getDateStringByOffset = (base: Date, offsetDays: number) => {
    const date = new Date(base)
    date.setDate(date.getDate() + offsetDays)
    return formatDateString(date)
}

const parseDateString = (dateString: string) => {
    const [year, month, day] = dateString.split('-').map(Number)
    return new Date(year, month - 1, day)
}

const normalizeCode = (code?: string) => String(code ?? '').trim()

const getCurrentTechSlot = (now: Date) => {
    const slots = [
        { label: '10:30', hour: 10, minute: 30 },
        { label: '12:00', hour: 12, minute: 0 },
        { label: '13:30', hour: 13, minute: 30 },
    ]
    let latestLabel = ''
    slots.forEach((slot) => {
        const slotTime = new Date(now)
        slotTime.setHours(slot.hour, slot.minute, 0, 0)
        if (now >= slotTime) {
            latestLabel = slot.label
        }
    })
    return latestLabel
}

const fetchTurnoverRanking = async (date?: string) => {
    try {
        const url = date ? `${TURNOVER_API_URL}?date=${date}` : TURNOVER_API_URL
        const response = await fetch(url)
        const payload = await response.json()
        const data = Array.isArray(payload?.data) ? payload.data : []
        return data.map((item: { no?: number; name?: string; close?: string; turnover?: string; code?: string }) => ({
            id: item.no ?? item.name ?? Math.random(),
            name: item.name ?? '',
            price: item.close ?? '-',
            volume: item.turnover ?? '-',
            code: item.code ?? '- '
        }))
    } catch (error) {
        console.error('Failed to load turnover ranking:', error)
        return []
    }
}

const fetchTurnoverTech = async (date?: string) => {
    console.log("🚀 ~ fetchTurnoverTech ~ date:", date)
    debugger
    try {
        const url = date ? `${TURNOVER_TECH_API_URL}?date=${date}` : TURNOVER_TECH_API_URL
        const response = await fetch(url)
        console.log("🚀 ~ fetchTurnoverTech ~ response:", response)
        const payload = await response.json()
        console.log("🚀 ~ fetchTurnoverTech ~ payload:", payload)
        const data = Array.isArray(payload?.data) ? payload.data : Array.isArray(payload) ? payload : []
        console.log("🚀 ~ fetchTurnoverTech ~ data:", data)
        const nextMap = new Map<string, TurnoverTechItem>()

        data.forEach((item: TurnoverTechItem) => {
            const code = normalizeCode(item.code)
            if (!code) return
            nextMap.set(code, {
                code,
                heikin_Ashi: item.heikin_Ashi,
                ma_UpperAll: item.ma_UpperAll,
                sqzmom_stronger_2d: item.sqzmom_stronger_2d,
            })
        })

        turnoverTechMap.value = nextMap
        if (date) {
            turnoverTechDate.value = date
        }
    } catch (error) {
        console.error('Failed to load turnover tech data:', error)
    }
}

const refreshTurnoverTech = async (date: string) => {
    const now = new Date()
    const today = formatDateString(now)
    if (!date || date !== today) return

    const slot = getCurrentTechSlot(now)
    if (!slot) return

    const slotKey = `${today} ${slot}`
    if (turnoverTechLastSlot.value === slotKey) return

    turnoverTechLastSlot.value = slotKey
    await fetchTurnoverTech(date)
}

const findLatestTurnoverData = async (maxLookbackDays = 7) => {
    const today = new Date()
    let latestDate: string | null = null
    let latestList: TurnoverItem[] = []
    let previousDate: string | null = null

    for (let offset = 0; offset <= maxLookbackDays; offset += 1) {
        const dateString = getDateStringByOffset(today, -offset)
        const list = await fetchTurnoverRanking(dateString)
        if (list.length) {
            latestDate = dateString
            latestList = list
            break
        }
    }

    if (!latestDate) {
        return { latestList: [], previousList: [], latestDate: '', previousDate: '' }
    }

    const baseDate = parseDateString(latestDate)
    let previousList: TurnoverItem[] = []
    for (let offset = 1; offset <= maxLookbackDays; offset += 1) {
        const dateString = getDateStringByOffset(baseDate, -offset)
        const list = await fetchTurnoverRanking(dateString)
        if (list.length) {
            previousList = list
            previousDate = dateString
            break
        }
    }

    return {
        latestList,
        previousList,
        latestDate,
        previousDate: previousDate ?? '',
    }
}

// 2. Market Sentiment (大盤氣氛 - 3個數字)
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

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
    const refreshTurnover = async () => {
        const { latestList, previousList, latestDate, previousDate } = await findLatestTurnoverData()
        turnoverToday.value = latestList
        turnoverYesterday.value = previousList
        turnoverTodayDate.value = latestDate
        turnoverYesterdayDate.value = previousDate
        await refreshTurnoverTech(latestDate)
    }
    refreshTurnover()
    fetchMarketSentiment()
    refreshTimer = setInterval(() => {
        refreshTurnover()
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

const normalizeName = (name: string) => {
    return name?.split(' ')[0].replace(/小型|期/g, '').trim()
}

// 4. Cross Analysis (交叉建議股票)
const crossSuggestions = computed<CrossSuggestion[]>(() => {
    const targetList =
        tradeSuggestion.value === '做空'
            ? props.lowest20
            : tradeSuggestion.value === '做多'
              ? props.highest20
              : []
    if (!targetList.length || !turnoverToday.value.length) return []

    const targetMap = new Map(targetList.map((item) => [normalizeName(item.name), item]))
    const seen = new Set<string>()

    return turnoverToday.value
        .filter((stock) => {
            const key = normalizeName(stock.name)
            if (!targetMap.has(key) || seen.has(key)) return false
            seen.add(key)
            return true
        })
        .map((stock) => {
            const key = normalizeName(stock.name)
            const target = targetMap.get(key)
            return {
                id: stock.id,
                name: target?.name ?? stock.name,
                price: stock.price,
                code: stock.code,
            }
        })
})

const yesterdayRankMap = computed(() => {
    return new Map(
        turnoverYesterday.value.map((item, index) => [normalizeName(item.name), index + 1])
    )
})

const getRankDeltaLabel = (name: string, currentRank: number) => {
    const previousRank = yesterdayRankMap.value.get(normalizeName(name))
    if (!previousRank) return 'NEW'
    const diff = previousRank - currentRank
    if (diff > 0) return `▲${diff}`
    if (diff < 0) return `▼${Math.abs(diff)}`
    return '0'
}

const getRankDeltaClass = (name: string, currentRank: number) => {
    const previousRank = yesterdayRankMap.value.get(normalizeName(name))
    if (!previousRank) return 'text-blue-300'
    const diff = previousRank - currentRank
    if (diff > 0) return 'text-red-400'
    if (diff < 0) return 'text-green-400'
    return 'text-gray-400'
}

const isTechSignal = (code?: string) => {
    const key = normalizeCode(code)
    const item = turnoverTechMap.value.get(key)
    if (!item) return false
    const isOn = (value?: string | number) => Number(value) === 1
    return (
        isOn(item.heikin_Ashi) &&
        isOn(item.ma_UpperAll) &&
        isOn(item.sqzmom_stronger_2d)
    )
}
</script>

<template>
    <div class="flex flex-col h-full bg-[#1a1a1a] text-gray-300 font-sans overflow-hidden">
        
        <!-- Section 1: Top Dashboard (Sentiment & Suggestion) -->
        <div class="p-4 bg-[#242424] border-b border-gray-700 shrink-0">
            <h2 class="text-white font-bold mb-4 flex items-center gap-2">
                <span class="w-2 h-6 bg-blue-500 rounded"></span>
                大盤氣氛 & 建議
                <span class="text-[10px] text-gray-400 ml-2">{{ marketSentimentDate || '-' }}</span>
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
            
            <div class="grid grid-cols-2 gap-2 flex-1 min-h-0 bg-black p-2">
                <div class="flex flex-col min-h-0 border border-gray-800 rounded">
                    <div class="px-3 py-2 text-xs font-semibold text-gray-300 bg-[#2d2d2d] border-b border-gray-800">
                        今日
                        <span class="ml-2 text-[10px] text-gray-400">{{ turnoverTodayDate || '-' }}</span>
                    </div>
                    <div class="grid grid-cols-5 text-center py-2 bg-[#242424] text-xs font-medium text-gray-400 shrink-0">
                        <div>代號</div>
                        <div>股名</div>
                        <div>現價</div>
                        <div>較昨日</div>
                        <div>技術分析</div>
                    </div>
                    <div class="overflow-y-auto flex-1">
                        <div
                            v-for="(stock, index) in turnoverToday"
                            :key="stock.id"
                            class="grid grid-cols-5 text-center py-3 border-b border-gray-900 hover:bg-gray-900 transition-colors text-sm"
                        >
                            <div class="font-medium text-white">{{ stock.code }}</div>
                            <div class="font-medium text-white">{{ stock.name }}</div>
                            <div class="text-yellow-400">{{ stock.price }}</div>
                            <div :class="getRankDeltaClass(stock.name, index + 1)">
                                {{ getRankDeltaLabel(stock.name, index + 1) }}
                            </div>
                            <div
                                :class="isTechSignal(stock.code) ? 'text-green-400' : 'text-red-400'"
                            >
                                {{ isTechSignal(stock.code) ? '✓' : 'x' }}
                            </div>
                        </div>
                    </div>
                </div>

                <div class="flex flex-col min-h-0 border border-gray-800 rounded">
                    <div class="px-3 py-2 text-xs font-semibold text-gray-300 bg-[#2d2d2d] border-b border-gray-800">
                        昨日
                        <span class="ml-2 text-[10px] text-gray-400">{{ turnoverYesterdayDate || '-' }}</span>
                    </div>
                    <div class="grid grid-cols-2 text-center py-2 bg-[#242424] text-xs font-medium text-gray-400 shrink-0">
                        <div>股名</div>
                        <div>現價</div>
                    </div>
                    <div class="overflow-y-auto flex-1">
                        <div
                            v-for="stock in turnoverYesterday"
                            :key="stock.id"
                            class="grid grid-cols-2 text-center py-3 border-b border-gray-900 hover:bg-gray-900 transition-colors text-sm"
                        >
                            <div class="font-medium text-white">{{ stock.name }}</div>
                            <div class="text-yellow-400">{{ stock.price }}</div>
                        </div>
                    </div>
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
                    交叉建議
                    <span class="text-[10px] text-gray-400 ml-2">{{ turnoverTodayDate || '-' }}</span>
                </h3>
            </div>
            
            <div class="grid grid-cols-2 text-center py-2 bg-[#2d2d2d] text-xs font-medium text-gray-400 shrink-0">
                <div>標的</div>
                <div>現價</div>
            </div>

            <div class="overflow-y-auto flex-1 bg-black">
                <div v-for="item in crossSuggestions" :key="item.id" class="grid grid-cols-2 text-center py-3 border-b border-gray-900 hover:bg-gray-900 transition-colors text-sm">
                    <div class="font-bold text-blue-300">{{ item.name }}</div>
                    <div class="text-yellow-400">{{ item.price }}</div>
                </div>
            </div>
        </div>

    </div>
</template>
