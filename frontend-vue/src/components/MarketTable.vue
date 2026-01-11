<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type MarketItem = {
    id: string | number
    name: string
    nearMonth: number
    farMonth: number
    combine: number
}

const marketData = ref<MarketItem[]>([])
const previousMarketData = ref<MarketItem[]>([])

const CHIP_URL = 'https://market-data-api.futures-ai.com/stkfut_tradeinfo/'
const NAME_URL = 'https://storage.googleapis.com/symbol-config/code_to_chinese.json'

const toNumber = (value: unknown) => {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : 0
}

const STORAGE_KEY = 'combineCrossings'
const TOP10_STORAGE_KEY = 'combineTop10Snapshots'
const BOTTOM10_STORAGE_KEY = 'combineBottom10Snapshots'

const getStoredCrossings = () => {
    try {
        const raw = localStorage.getItem(STORAGE_KEY)
        if (!raw) return []
        const parsed = JSON.parse(raw)
        return Array.isArray(parsed) ? parsed : []
    } catch (error) {
        console.warn('Failed to parse stored crossings:', error)
        return []
    }
}

const getStoredTop10Snapshots = () => {
    try {
        const raw = localStorage.getItem(TOP10_STORAGE_KEY)
        if (!raw) return []
        const parsed = JSON.parse(raw)
        return Array.isArray(parsed) ? parsed : []
    } catch (error) {
        console.warn('Failed to parse stored top10 snapshots:', error)
        return []
    }
}

const getStoredBottom10Snapshots = () => {
    try {
        const raw = localStorage.getItem(BOTTOM10_STORAGE_KEY)
        if (!raw) return []
        const parsed = JSON.parse(raw)
        return Array.isArray(parsed) ? parsed : []
    } catch (error) {
        console.warn('Failed to parse stored bottom10 snapshots:', error)
        return []
    }
}

const storeCombineCrossings = (items: MarketItem[]) => {
    if (!items.length) return
    const existing = getStoredCrossings()
    const timestamp = new Date().toISOString()
    const payload = items.map((item) => ({ ...item, timestamp }))
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...existing, ...payload]))
}

const storeTop10Snapshot = (items: MarketItem[]) => {
    if (!items.length) return
    const timestamp = new Date().toISOString()
    const snapshot = items.slice(0, 10).map((item) => ({ ...item }))
    const existing = getStoredTop10Snapshots()
    const payload = [...existing, { timestamp, items: snapshot }]
    localStorage.setItem(TOP10_STORAGE_KEY, JSON.stringify(payload))
}

const storeBottom10Snapshot = (items: MarketItem[]) => {
    const negativeItems = items.filter((item) => item.combine < 0)
    if (!negativeItems.length) return
    const timestamp = new Date().toISOString()
    const snapshot = [...negativeItems]
        .sort((a, b) => a.combine - b.combine)
        .slice(0, 10)
        .map((item) => ({ ...item }))
    const existing = getStoredBottom10Snapshots()
    const payload = [...existing, { timestamp, items: snapshot }]
    localStorage.setItem(BOTTOM10_STORAGE_KEY, JSON.stringify(payload))
}

const detectCombineCrossings = (nextData: MarketItem[], previousData: MarketItem[]) => {
    if (!previousData.length) return
    const previousById = new Map(previousData.map((item) => [item.id, item]))
    const crossings: MarketItem[] = []

    for (const item of nextData) {
        const previousItem = previousById.get(item.id)
        if (previousItem && previousItem.combine < 0 && item.combine > 0) {
            crossings.push(item)
        }
    }

    storeCombineCrossings(crossings)
}

const positiveCount = computed(() => marketData.value.filter((item) => item.combine > 0).length)
const negativeCount = computed(() => marketData.value.filter((item) => item.combine < 0).length)

const shouldFetchNow = () => {
    const now = new Date()
    const minutes = now.getHours() * 60 + now.getMinutes()
    const startMinutes = 8 * 60 + 45
    const endMinutes = 13 * 60 + 45
    return minutes >= startMinutes && minutes <= endMinutes
}

const fetchMarketData = async (force = false) => {
    if (!force && !shouldFetchNow()) {
        return
    }
    try {
        const [chipResponse, nameResponse] = await Promise.all([fetch(CHIP_URL), fetch(NAME_URL)])
        const [chipJson, nameJson] = await Promise.all([chipResponse.json(), nameResponse.json()])

        const sorted = Object.entries(chipJson)
            .map(([key, value]) => {
                const nearMonth = toNumber(value.near_month)
                const farMonth = toNumber(value.next_month)

                return {
                    id: key,
                    name: nameJson[`${key}-1`] || key,
                    nearMonth,
                    farMonth,
                    combine: nearMonth + farMonth,
                }
            })
            .sort((a, b) => b.combine - a.combine)

        previousMarketData.value = marketData.value.map((item) => ({ ...item }))
        // detectCombineCrossings(sorted, previousMarketData.value)
        storeTop10Snapshot(sorted)
        storeBottom10Snapshot(sorted)
        marketData.value = sorted
    } catch (error) {
        console.error('Failed to load market data:', error)
    }
}

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
    fetchMarketData(true)
    if (shouldFetchNow()) {
        refreshTimer = setInterval(fetchMarketData, 30_000)
    }
})

onBeforeUnmount(() => {
    if (refreshTimer) {
        clearInterval(refreshTimer)
        refreshTimer = null
    }
})
</script>

<template>
    <div class="flex flex-col w-full max-w-md mx-auto bg-[#1a1a1a] text-gray-300 font-sans shadow-xl overflow-hidden">

        <div class="p-4 grid grid-cols-1 gap-4 items-center bg-[#242424]">
            <div class="flex flex-col items-center gap-1">
                <span class="text-xs">分數排序</span>
                <button class="btn btn-ghost btn-xs">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24"
                        stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                            d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12" />
                    </svg>
                </button>
            </div>
        </div>

        <div class="flex w-full h-12">
            <button class="flex-1 bg-[#d63031] text-white font-bold text-lg">{{ positiveCount }}家</button>
            <button class="flex-1 bg-[#55efc4] text-[#2d3436] font-bold text-lg">{{ negativeCount }}家</button>
        </div>

        <div class="grid grid-cols-4 text-center py-2 bg-[#1a1a1a] text-xs border-b border-gray-700">
            <div class="col-span-1">商品名稱</div>
            <div class="col-span-1">近月</div>
            <div class="col-span-1">分數</div>
            <div class="col-span-1">遠月</div>
        </div>

        <div class="overflow-y-auto max-h-[600px] bg-black">
            <div v-for="item in marketData" :key="item.id"
                class="grid grid-cols-4 text-center py-3 border-b border-gray-900 items-center text-sm">
                <div class="col-span-1 font-medium">{{ item.name }}</div>

                <div class="col-span-1" :class="item.nearMonth < 0 ? 'text-red-500' : 'text-blue-400'">
                    {{ item.nearMonth }}
                </div>

                <div class="col-span-1 relative mx-1">
                    <div class="bg-red-500/80 rounded py-1 px-1 text-white text-xs"
                        :style="{ opacity: item.combine > 100 ? 1 : 0.5 }">
                        {{ item.combine }}
                    </div>
                </div>

                <div class="col-span-1" :class="item.farMonth < 0 ? 'text-red-500' : 'text-blue-400'">
                    {{ item.farMonth }}
                </div>
            </div>
        </div>
    </div>
</template>

<style scoped>
/* 隱藏滾動條但保持功能 */
.scrollbar-hide::-webkit-scrollbar {
    display: none;
}

.scrollbar-hide {
    -ms-overflow-style: none;
    scrollbar-width: none;
}
</style>
