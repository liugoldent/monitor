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

const MARKET_API_URL =
    import.meta.env.VITE_MARKET_API_URL || 'http://localhost:5050/api/stkfut_tradeinfo'
const NAME_URL = 'https://storage.googleapis.com/symbol-config/code_to_chinese.json'

const toNumber = (value: unknown) => {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : 0
}

const positiveCount = computed(() => marketData.value.filter((item) => item.combine > 0).length)
const negativeCount = computed(() => marketData.value.filter((item) => item.combine < 0).length)
const lowest10 = computed(() =>
    [...marketData.value].sort((a, b) => a.combine - b.combine).slice(0, 10),
)
const highest10 = computed(() =>
    [...marketData.value].sort((a, b) => b.combine - a.combine).slice(0, 10),
)

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
        const [chipResponse, nameResponse] = await Promise.all([fetch(MARKET_API_URL), fetch(NAME_URL)])
        const [chipJson, nameJson] = await Promise.all([chipResponse.json(), nameResponse.json()])

        const payload = chipJson?.data && typeof chipJson.data === 'object' ? chipJson.data : chipJson
        const sorted = Object.entries(payload)
            .filter(([, value]) => value && typeof value === 'object')
            .map(([key, value]) => {
                const nearMonth = toNumber((value as { near_month?: number }).near_month)
                const farMonth = toNumber((value as { next_month?: number }).next_month)

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
    <div class="flex flex-col w-full h-full bg-[#1a1a1a] text-gray-300 font-sans shadow-xl overflow-hidden">
        <!-- Top Toolbar -->
        <div class="p-2 grid grid-cols-1 gap-4 items-center bg-[#242424] shrink-0">
            <div class="flex items-center justify-between px-4">
                <span class="font-bold text-white">Market Monitor</span>
                <div class="flex items-center gap-2">
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
        </div>

        <!-- Main Content Grid -->
        <div class="flex-1 grid grid-cols-2 gap-1 overflow-hidden">
            
            <!-- Left Panel: Highest 10 -->
            <div class="flex flex-col flex-1 h-full min-h-0 bg-[#1a1a1a]">
                <div class="p-2 bg-[#d63031] text-white font-bold text-center shrink-0">
                    多方 ({{ positiveCount }}家)
                </div>
                
                <div class="grid grid-cols-4 text-center py-2 bg-[#242424] text-xs border-b border-gray-700 shrink-0">
                    <div class="col-span-1">商品名稱</div>
                    <div class="col-span-1">近月</div>
                    <div class="col-span-1">分數</div>
                    <div class="col-span-1">遠月</div>
                </div>

                <div class="overflow-y-auto flex-1 min-h-0 bg-black">
                    <div v-for="item in highest10" :key="`high-${item.id}`"
                        class="grid grid-cols-4 text-center py-3 border-b border-gray-900 items-center text-sm hover:bg-gray-900 transition-colors">
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

            <!-- Right Panel: Lowest 10 -->
            <div class="flex flex-col flex-1 h-full min-h-0 bg-[#1a1a1a]">
                <div class="p-2 bg-[#55efc4] text-[#2d3436] font-bold text-center shrink-0">
                    空方 ({{ negativeCount }}家)
                </div>

                <div class="grid grid-cols-4 text-center py-2 bg-[#242424] text-xs border-b border-gray-700 shrink-0">
                    <div class="col-span-1">商品名稱</div>
                    <div class="col-span-1">近月</div>
                    <div class="col-span-1">分數</div>
                    <div class="col-span-1">遠月</div>
                </div>

                <div class="overflow-y-auto flex-1 min-h-0 bg-black">
                    <div v-for="item in lowest10" :key="`low-${item.id}`"
                        class="grid grid-cols-4 text-center py-3 border-b border-gray-900 items-center text-sm hover:bg-gray-900 transition-colors">
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
