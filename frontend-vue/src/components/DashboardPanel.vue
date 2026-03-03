<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { resolveApiUrl } from '../utils/api'

type TurnoverItem = {
    id: number | string
    name: string
    price: string | number
    volume: string | number
    code?: string
    close?: string | number
    low?: string | number
}

type TurnoverTechItem = {
    code: string
    heikin_Ashi?: string | number
    ma_UpperAll?: string | number
    sqzmom_stronger_2d?: string | number
    ma5_1d?: string | number
    ma10_1d?: string | number
    ma20_1d?: string | number
    volumeCombo?: string | number
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

type EtfHoldingsInfo = {
    count: number
    etfs: string[]
}

type EtfCommonTechItem = {
    code: string
    name?: string
    close?: string | number
    target_price?: string | number
    volumeCombo?: string | number
    sqzmom_stronger_1d?: string | number
    heikin_Ashi?: string | number
    ma5_1d?: string | number
    ma10_1d?: string | number
    ma50_1d?: string | number
    ma100_1d?: string | number
    entry_signal?: string | number
    add_position_signal?: string | number
    buyback_signal?: string | number
    reduce_1_signal?: string | number
    reduce_2_signal?: string | number
    clear_position_signal?: string | number
    has_position_signal?: string | number
    strong_buy_score?: string | number
    buy_score?: string | number
    hold_score?: string | number
    sell_score?: string | number
    strong_sell_score?: string | number
    no?: number
}

const props = defineProps<{
    highest20: MarketItem[]
    lowest20: MarketItem[]
    tradeSuggestion: string
}>()
const emit = defineEmits<{
    (event: 'update:crossSuggestions', items: CrossSuggestion[]): void
    (event: 'update:turnoverDate', date: string): void
}>()

// 1. Turnover Ranking (大盤成交值排行)
const turnoverToday = ref<TurnoverItem[]>([])
const turnoverYesterday = ref<TurnoverItem[]>([])
const turnoverTodayDate = ref<string>('')
const turnoverYesterdayDate = ref<string>('')
const turnoverTechMap = ref<Map<string, TurnoverTechItem>>(new Map())
const etfHoldingsMap = ref<Map<string, EtfHoldingsInfo>>(new Map())
const etfCommonHoldings = ref<EtfCommonTechItem[]>([])
const etfCommonHoldingsTime = ref<string>('')
const commonIndexHoldings = ref<EtfCommonTechItem[]>([])
const commonIndexHoldingsTime = ref<string>('')
const turnoverTechDate = ref<string>('')
const turnoverTechLastSlot = ref<string>('')
const TURNOVER_API_URL =
    resolveApiUrl('/api/turnover', import.meta.env.VITE_TURNOVER_API_URL)
const TURNOVER_TECH_API_URL =
    resolveApiUrl('/api/turnover_tech', import.meta.env.VITE_TURNOVER_TECH_API_URL)
const ETF_HOLDINGS_API_URL =
    resolveApiUrl('/api/etf_holdings_counts', import.meta.env.VITE_ETF_HOLDINGS_API_URL)
const ETF_COMMON_TECH_API_URL =
    resolveApiUrl('/api/etf_common_holdings_tech', import.meta.env.VITE_ETF_COMMON_TECH_API_URL)
const FUTURE_INDEX_TECH_API_URL =
    resolveApiUrl('/api/future_index_tech', import.meta.env.VITE_FUTURE_INDEX_TECH_API_URL)
const CHAT_LLM_API_URL =
    resolveApiUrl('/api/chat_llm', import.meta.env.VITE_CHAT_LLM_API_URL)

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
    const [year = 1970, month = 1, day = 1] = dateString.split('-').map(Number)
    return new Date(year, month - 1, day)
}

const normalizeCode = (code?: string) => String(code ?? '').trim()
const parseNumber = (value?: string | number) => {
    if (value === null || value === undefined) return NaN
    if (typeof value === 'number') return value
    const cleaned = String(value).replace(/,/g, '').trim()
    if (!cleaned) return NaN
    return Number(cleaned)
}

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
            code: item.code ?? '- ',
            close: item.close ?? '-',
            low: (item as { low?: string; Low?: string }).low ?? (item as { Low?: string }).Low ?? '-',
        }))
    } catch (error) {
        console.error('Failed to load turnover ranking:', error)
        return []
    }
}

const fetchTurnoverTech = async (date?: string) => {
    try {
        const url = date ? `${TURNOVER_TECH_API_URL}?date=${date}` : TURNOVER_TECH_API_URL
        const response = await fetch(url)
        const payload = await response.json()
        const data = Array.isArray(payload?.data) ? payload.data : Array.isArray(payload) ? payload : []
        const nextMap = new Map<string, TurnoverTechItem>()

        data.forEach((item: TurnoverTechItem) => {
            const code = normalizeCode(item.code)
            if (!code) return
            nextMap.set(code, {
                code,
                heikin_Ashi: item.heikin_Ashi,
                ma_UpperAll: item.ma_UpperAll,
                sqzmom_stronger_2d: item.sqzmom_stronger_2d,
                ma5_1d: item.ma5_1d,
                ma10_1d: item.ma10_1d,
                ma20_1d: item.ma20_1d,
                volumeCombo: item.volumeCombo,
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

const fetchEtfHoldingsCounts = async () => {
    try {
        const response = await fetch(ETF_HOLDINGS_API_URL)
        const payload = await response.json()
        const data = payload?.data && typeof payload.data === 'object' ? payload.data : {}
        const nextMap = new Map<string, EtfHoldingsInfo>()

        Object.entries(data).forEach(([code, info]) => {
            const normalized = normalizeCode(code)
            if (!normalized) return
            if (!info || typeof info !== 'object') return
            const countValue = Number((info as { count?: number }).count)
            if (!Number.isFinite(countValue)) return
            const etfsRaw = (info as { etfs?: string[] }).etfs ?? []
            const etfs = Array.isArray(etfsRaw) ? etfsRaw.map((item) => String(item).trim()).filter(Boolean) : []
            nextMap.set(normalized, { count: countValue, etfs })
        })

        etfHoldingsMap.value = nextMap
    } catch (error) {
        console.error('Failed to load ETF holdings counts:', error)
    }
}

const fetchEtfCommonHoldings = async () => {
    try {
        const response = await fetch(ETF_COMMON_TECH_API_URL)
        const payload = await response.json()
        const data = Array.isArray(payload?.data) ? payload.data : []
        etfCommonHoldings.value = data.map((item: any) => ({
            code: normalizeCode(item.code),
            name: item.name ?? '',
            close: item.close ?? '',
            target_price: item.target_price ?? '',
            volumeCombo: item.volumeCombo ?? '',
            sqzmom_stronger_1d: item.sqzmom_stronger_1d ?? '',
            heikin_Ashi: item.heikin_Ashi ?? '',
            ma5_1d: item.ma5_1d,
            ma10_1d: item.ma10_1d,
            ma50_1d: item.ma50_1d,
            ma100_1d: item.ma100_1d,
            entry_signal: item.entry_signal,
            add_position_signal: item.add_position_signal,
            buyback_signal: item.buyback_signal,
            reduce_1_signal: item.reduce_1_signal,
            reduce_2_signal: item.reduce_2_signal,
            clear_position_signal: item.clear_position_signal,
            has_position_signal: item.has_position_signal,
            strong_buy_score: item.strong_buy_score,
            buy_score: item.buy_score,
            hold_score: item.hold_score,
            sell_score: item.sell_score,
            strong_sell_score: item.strong_sell_score,
            no: item.no
        }))
        etfCommonHoldingsTime.value = payload?.time ?? ''
    } catch (error) {
        console.error('Failed to load ETF common holdings:', error)
    }
}

const fetchCommonIndexHoldings = async () => {
    try {
        const response = await fetch(FUTURE_INDEX_TECH_API_URL)
        const payload = await response.json()
        const data = Array.isArray(payload?.data) ? payload.data : []
        commonIndexHoldings.value = data.map((item: any) => ({
            code: normalizeCode(item.code),
            name: item.name ?? '',
            close: item.close ?? '',
            target_price: item.target_price ?? '',
            volumeCombo: item.volumeCombo ?? '',
            sqzmom_stronger_1d: item.sqzmom_stronger_1d ?? '',
            heikin_Ashi: item.heikin_Ashi ?? '',
            ma5_1d: item.ma5_1d,
            ma10_1d: item.ma10_1d,
            ma50_1d: item.ma50_1d,
            ma100_1d: item.ma100_1d,
            entry_signal: item.entry_signal,
            add_position_signal: item.add_position_signal,
            buyback_signal: item.buyback_signal,
            reduce_1_signal: item.reduce_1_signal,
            reduce_2_signal: item.reduce_2_signal,
            clear_position_signal: item.clear_position_signal,
            has_position_signal: item.has_position_signal,
            strong_buy_score: item.strong_buy_score,
            buy_score: item.buy_score,
            hold_score: item.hold_score,
            sell_score: item.sell_score,
            strong_sell_score: item.strong_sell_score,
            no: item.no
        }))
        commonIndexHoldingsTime.value = payload?.time ?? ''
    } catch (error) {
        console.error('Failed to load future index tech data:', error)
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

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
    const refreshTurnover = async () => {
        const { latestList, previousList, latestDate, previousDate } = await findLatestTurnoverData()
        turnoverToday.value = latestList
        turnoverYesterday.value = previousList
        turnoverTodayDate.value = latestDate
        turnoverYesterdayDate.value = previousDate
        await refreshTurnoverTech(latestDate)
        await fetchEtfHoldingsCounts()
        await fetchEtfCommonHoldings()
        await fetchCommonIndexHoldings()
    }
    refreshTurnover()
    refreshTimer = setInterval(() => {
        refreshTurnover()
    }, 60_000)
})

onBeforeUnmount(() => {
    if (refreshTimer) {
        clearInterval(refreshTimer)
        refreshTimer = null
    }
})

const normalizeName = (name: string) => {
    const firstPart = name.split(' ')[0] ?? ''
    return firstPart.replace(/小型|期/g, '').trim()
}

// 4. Cross Analysis (交叉建議股票)
const crossSuggestions = computed<CrossSuggestion[]>(() => {
    const targetList =
        props.tradeSuggestion === '做空'
            ? props.lowest20
            : props.tradeSuggestion === '做多'
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

const turnoverRankMap = computed(() => {
    return new Map(
        turnoverToday.value.map((stock, index) => [normalizeCode(stock.code), index + 1]),
    )
})

const getTurnoverRank = (code?: string) => {
    const key = normalizeCode(code)
    if (!key) return '-'
    return turnoverRankMap.value.get(key) ?? '-'
}

const etfCommonHoldingsFiltered = computed(() => {
    return etfCommonHoldings.value.filter((item) => {
        const rank = turnoverRankMap.value.get(normalizeCode(item.code))
        return typeof rank === 'number' && rank <= 100
    }).sort((a, b) => {
        const rankA = turnoverRankMap.value.get(normalizeCode(a.code)) ?? Infinity
        const rankB = turnoverRankMap.value.get(normalizeCode(b.code)) ?? Infinity
        return rankA - rankB
    })
})

const oddLotOrders = ref<Record<string, { price: string; qty: string }>>({})

const getOddLotOrder = (code?: string, price?: string | number) => {
    const key = normalizeCode(code)
    if (!key) return { price: '', qty: '1' }
    if (!oddLotOrders.value[key]) {
        const parsed = parseNumber(price)
        const initialPrice = Number.isFinite(parsed) ? String(parsed) : (price ?? '')
        oddLotOrders.value[key] = {
            price: initialPrice === '-' ? '' : String(initialPrice),
            qty: '1',
        }
    }
    return oddLotOrders.value[key]
}

// 5. LLM Integration
const selectedStock = ref<{ name: string; code?: string; price: string | number } | null>(null)
const selectedQuestion = ref('分析技術面趨勢')
const llmResponse = ref('')
const llmLoading = ref(false)
const activeTechTab = ref<'commonEtf' | 'commonIndex'>('commonEtf')

watch(
    crossSuggestions,
    (items) => {
        emit('update:crossSuggestions', items)
    },
    { immediate: true },
)

watch(
    turnoverTodayDate,
    (date) => {
        emit('update:turnoverDate', date)
    },
    { immediate: true },
)
const questions = [
    '分析技術面趨勢',
    '分析籌碼面',
    '預測下週走勢',
    '給出操作建議 (做多/做空)',
    '分析是否有主力介入'
]

const selectStock = (stock: { name: string; code?: string; price: string | number }) => {
    selectedStock.value = stock
    llmResponse.value = ''
}

const askLLM = async () => {
    if (!selectedStock.value || llmLoading.value) return
    llmLoading.value = true
    try {
        const code = normalizeCode(selectedStock.value.code)
        const tech = turnoverTechMap.value.get(code)

        const context = {
            price: selectedStock.value.price,
            code: selectedStock.value.code,
            tech_indicators: tech ? {
                heikin_Ashi: tech.heikin_Ashi,
                ma_UpperAll: tech.ma_UpperAll,
                sqzmom_stronger_2d: tech.sqzmom_stronger_2d
            } : 'Not available'
        }

        const response = await fetch(CHAT_LLM_API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                stock_name: selectedStock.value.name,
                question: selectedQuestion.value,
                context: JSON.stringify(context)
            })
        })

        const data = await response.json()
        if (data.error) throw new Error(data.error)
        llmResponse.value = data.answer
    } catch (e: any) {
        llmResponse.value = `Error: ${e.message}`
    } finally {
        llmLoading.value = false
    }
}

// Helper methods for calculations
const getBias = (closeStr: string | number | undefined, maStr: string | number | undefined) => {
    const close = parseNumber(closeStr)
    const ma = parseNumber(maStr)

    if (isNaN(close) || isNaN(ma) || ma === 0) return '-'

    // (Close - MA) / MA * 100
    const bias = ((close - ma) / ma) * 100
    return bias.toFixed(2) + '%'
}

const isBiasLessThan = (
    closeStr: string | number | undefined,
    maStr: string | number | undefined,
    threshold = 10
) => {
    const close = parseNumber(closeStr)
    const ma = parseNumber(maStr)
    if (isNaN(close) || isNaN(ma) || ma === 0) return false
    const bias = ((close - ma) / ma) * 100
    return bias > 0 && bias < threshold
}

const signalLabel = (value?: string | number) => Number(value) === 1 ? '是' : '否'
const signalClass = (value?: string | number) => Number(value) === 1 ? 'text-green-400' : 'text-red-400'

const getAnalystRatingLabel = (item: EtfCommonTechItem) => {
    const scores = [
        { label: '強力買入', value: parseNumber(item.strong_buy_score) },
        { label: '買入', value: parseNumber(item.buy_score) },
        { label: '中立', value: parseNumber(item.hold_score) },
        { label: '賣出', value: parseNumber(item.sell_score) },
        { label: '強力賣出', value: parseNumber(item.strong_sell_score) },
    ]
    let bestLabel = '-'
    let bestValue = Number.NEGATIVE_INFINITY
    scores.forEach((score) => {
        if (Number.isFinite(score.value) && score.value > bestValue) {
            bestValue = score.value
            bestLabel = score.label
        }
    })
    return bestLabel
}

</script>

<template>
    <div class="flex flex-col h-full bg-[#1a1a1a] text-gray-300 font-sans overflow-hidden">
        <!-- Section 2: Turnover Ranking (Table) -->
        <div class="flex flex-col min-h-[28vh] flex-[0_0_28vh] border-b border-gray-700">
            <div class="p-2 bg-[#1f1f1f] flex items-center justify-between shrink-0">
                <h3 class="font-bold text-sm text-white flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-purple-400" viewBox="0 0 20 20"
                        fill="currentColor">
                        <path fill-rule="evenodd"
                            d="M12 7a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0V8.414l-4.293 4.293a1 1 0 01-1.414 0L8 10.414l-4.293 4.293a1 1 0 01-1.414-1.414l5-5a1 1 0 011.414 0L11 10.586 14.586 7H12z"
                            clip-rule="evenodd" />
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
                    <div
                        class="grid grid-cols-5 text-center py-2 bg-[#242424] text-xs font-medium text-gray-400 shrink-0">
                        <div>代號</div>
                        <div>股名</div>
                        <div>現價</div>
                        <div>較昨日</div>
                        <div>技術分析</div>
                    </div>
                    <div class="overflow-y-auto flex-1">
                        <div v-for="(stock, index) in turnoverToday" :key="stock.id"
                            class="grid grid-cols-5 text-center py-3 border-b border-gray-900 transition-colors text-sm cursor-pointer"
                            :class="selectedStock?.name === stock.name ? 'bg-blue-900/40 hover:bg-blue-900/50' : 'hover:bg-gray-900'"
                            @click="selectStock(stock)">
                            <div class="font-medium text-white">{{ stock.code }}</div>
                            <div class="font-medium text-white">{{ stock.name }}</div>
                            <div class="text-yellow-400">{{ stock.price }}</div>
                            <div :class="getRankDeltaClass(stock.name, index + 1)">
                                {{ getRankDeltaLabel(stock.name, index + 1) }}
                            </div>
                            <div :class="isTechSignal(stock.code) ? 'text-green-400' : 'text-red-400'">
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
                    <div
                        class="grid grid-cols-2 text-center py-2 bg-[#242424] text-xs font-medium text-gray-400 shrink-0">
                        <div>股名</div>
                        <div>現價</div>
                    </div>
                    <div class="overflow-y-auto flex-1">
                        <div v-for="stock in turnoverYesterday" :key="stock.id"
                            class="grid grid-cols-2 text-center py-3 border-b border-gray-900 hover:bg-gray-900 transition-colors text-sm">
                            <div class="font-medium text-white">{{ stock.name }}</div>
                            <div class="text-yellow-400">{{ stock.price }}</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Section 3: Turnover Tech (Table) -->
        <div class="flex flex-col min-h-[40vh] flex-[0_0_40vh]">
            <div class="p-2 bg-[#1f1f1f] flex items-center justify-between shrink-0">
                <h3 class="font-bold text-sm text-white flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-green-400" viewBox="0 0 20 20"
                        fill="currentColor">
                        <path
                            d="M5 3a2 2 0 00-2 2v2a2 2 0 002 2h2a2 2 0 002-2V5a2 2 0 00-2-2H5zM5 11a2 2 0 00-2 2v2a2 2 0 002 2h2a2 2 0 002-2v-2a2 2 0 00-2-2H5zM11 5a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V5zM11 13a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                    </svg>
                    成交值技術分析
                    <span class="text-[10px] text-gray-400 ml-2">{{ turnoverTodayDate || '-' }}</span>
                </h3>
            </div>

            <div class="flex-1 min-h-0 bg-black p-2">
                <div class="flex flex-col lg:flex-row gap-2 min-h-0 h-full">
                    <div class="flex flex-col min-h-0 border border-gray-800 rounded h-full flex-1">
                        <div
                            class="px-3 py-2 text-xs font-semibold text-gray-300 bg-[#2d2d2d] border-b border-gray-800 flex items-center justify-between gap-2">
                            <span>成交值技術分析</span>
                            <div class="flex items-center gap-1 text-[10px]">
                                <button class="px-2 py-1 rounded border" :class="activeTechTab === 'commonEtf'
                                    ? 'bg-blue-600/40 border-blue-500 text-white'
                                    : 'bg-transparent border-gray-600 text-gray-400'"
                                    @click="activeTechTab = 'commonEtf'">
                                    ETF 共同持股
                                </button>
                                <button class="px-2 py-1 rounded border" :class="activeTechTab === 'commonIndex'
                                    ? 'bg-blue-600/40 border-blue-500 text-white'
                                    : 'bg-transparent border-gray-600 text-gray-400'"
                                    @click="activeTechTab = 'commonIndex'">
                                    指數
                                </button>
                            </div>
                        </div>


                        <div class="overflow-y-auto flex-1 bg-black">
                            <div v-if="activeTechTab === 'commonEtf' || activeTechTab === 'commonIndex'">
                                <div
                                    class="grid grid-cols-17 text-center py-2 bg-[#242424] text-xs font-medium text-gray-400 shrink-0 sticky top-0 z-10">
                                    <div>成交值排行</div>
                                    <div>代號</div>
                                    <div>名稱</div>
                                    <div>成交價</div>
                                    <div>目標價</div>
                                    <div>動能增強</div>
                                    <div>平均K棒</div>
                                    <div>5日乖離率</div>
                                    <div>10日乖離率</div>
                                    <div>評級</div>
                                    <div>持有部位</div>
                                    <div>今日進場</div>
                                    <div>加碼</div>
                                    <div>認錯買回</div>
                                    <div>減碼1</div>
                                    <div>減碼2</div>
                                    <div>清倉</div>
                                </div>
                                <div class="px-3 py-2 text-[10px] text-gray-400 border-b border-gray-900">
                                    {{ activeTechTab === 'commonEtf'
                                        ? (etfCommonHoldingsTime || '-')
                                        : (commonIndexHoldingsTime || '-') }}
                                </div>
                                <div v-for="stock in activeTechTab === 'commonEtf' ? etfCommonHoldingsFiltered : commonIndexHoldings"
                                    :key="stock.code"
                                    class="grid grid-cols-17 text-center py-3 border-b border-gray-900 text-sm">
                                    <div class="text-gray-300">{{ getTurnoverRank(stock.code) }}
                                    </div>
                                    <div class="font-medium text-white">{{ stock.code }}</div>
                                    <div class="font-medium text-white">{{ stock.name }}</div>
                                    <div class="text-yellow-400">{{ stock.close || '-' }}</div>
                                    <div class="text-cyan-300">{{ stock.target_price || '-' }}</div>
                                    <div
                                        :class="Number(stock.sqzmom_stronger_1d) === 1 ? 'text-green-400' : 'text-red-400'">
                                        {{ Number(stock.sqzmom_stronger_1d) === 1 ? 'v' : 'x' }}
                                    </div>
                                    <div :class="Number(stock.heikin_Ashi) === 1 ? 'text-green-400' : 'text-red-400'">
                                        {{ Number(stock.heikin_Ashi) === 1 ? 'v' : 'x' }}
                                    </div>
                                    <div class="text-gray-300">{{ getBias(stock.close, stock.ma5_1d) }}</div>
                                    <div
                                        :class="isBiasLessThan(stock.close, stock.ma10_1d)
                                            ? 'bg-yellow-500/20 text-yellow-200 font-semibold'
                                            : 'text-gray-300'">
                                        {{ getBias(stock.close, stock.ma10_1d) }}
                                    </div>
                                    <div class="text-gray-300">{{ getAnalystRatingLabel(stock) }}</div>
                                    <div :class="signalClass(stock.has_position_signal)">{{ signalLabel(stock.has_position_signal) }}</div>
                                    <div :class="signalClass(stock.entry_signal)">{{ signalLabel(stock.entry_signal) }}</div>
                                    <div :class="signalClass(stock.add_position_signal)">{{ signalLabel(stock.add_position_signal) }}</div>
                                    <div :class="signalClass(stock.buyback_signal)">{{ signalLabel(stock.buyback_signal) }}</div>
                                    <div :class="signalClass(stock.reduce_1_signal)">{{ signalLabel(stock.reduce_1_signal) }}</div>
                                    <div :class="signalClass(stock.reduce_2_signal)">{{ signalLabel(stock.reduce_2_signal) }}</div>
                                    <div :class="signalClass(stock.clear_position_signal)">{{ signalLabel(stock.clear_position_signal) }}</div>
                                </div>
                                <div v-if="activeTechTab === 'commonEtf' && !etfCommonHoldingsFiltered.length"
                                    class="text-center text-xs text-gray-500 py-6">
                                    尚無符合前25名的共同持股資料
                                </div>
                                <div v-if="activeTechTab === 'commonIndex' && !commonIndexHoldings.length"
                                    class="text-center text-xs text-gray-500 py-6">
                                    尚無指數資料
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Section 4: AI Analysis Panel -->
        <div class="flex flex-col min-h-[28vh] flex-[0_0_28vh] bg-[#1f1f1f] border-t border-gray-700">
            <div class="p-2 border-b border-gray-800 flex items-center gap-4">
                <h3 class="font-bold text-sm text-white flex items-center gap-2">
                    <span class="text-xl">🤖</span> AI 股票分析對話框
                </h3>

                <div v-if="selectedStock"
                    class="flex items-center gap-2 bg-gray-800 px-3 py-1 rounded-full border border-gray-600">
                    <span class="text-blue-300 font-bold">{{ selectedStock.name }}</span>
                    <span class="text-xs text-yellow-500">{{ selectedStock.code }}</span>
                </div>
                <div v-else class="text-gray-500 text-sm italic">
                    (請點選上方列表選擇股票)
                </div>
            </div>

            <div class="flex-1 flex gap-4 p-4 overflow-hidden">
                <div class="w-1/3 flex flex-col gap-3">
                    <label class="text-xs text-gray-400">選擇問題</label>
                    <select v-model="selectedQuestion"
                        class="select select-sm select-bordered w-full bg-[#1a1a1a] text-white border-gray-600 focus:border-blue-500">
                        <option v-for="q in questions" :key="q">{{ q }}</option>
                    </select>

                    <button @click="askLLM" :disabled="!selectedStock || llmLoading"
                        class="btn btn-sm btn-primary w-full mt-auto"
                        :class="{ 'opacity-50': !selectedStock || llmLoading }">
                        <span v-if="llmLoading" class="loading loading-spinner loading-xs"></span>
                        {{ llmLoading ? '分析中...' : '開始分析' }}
                    </button>
                </div>

                <div
                    class="flex-1 bg-[#151515] rounded border border-gray-700 p-4 overflow-y-auto font-mono text-sm leading-relaxed text-gray-300">
                    <div v-if="llmResponse" class="whitespace-pre-wrap">{{ llmResponse }}</div>
                    <div v-else-if="llmLoading"
                        class="flex items-center justify-center h-full text-gray-500 animate-pulse">
                        正在思考中...
                    </div>
                    <div v-else class="flex items-center justify-center h-full text-gray-600">
                        選擇股票並提問以獲取分析
                    </div>
                </div>
            </div>
        </div>

    </div>
</template>
