<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { resolveApiUrl } from '../utils/api'

type MarketItem = {
    id: string | number
    name: string
    nearMonth: number
    farMonth: number
    combine: number
}

const props = defineProps<{
    highest20: MarketItem[]
    lowest20: MarketItem[]
    tradeSuggestion: string
}>()

const CHAT_LLM_API_URL =
    resolveApiUrl('/api/chat_llm', import.meta.env.VITE_CHAT_LLM_API_URL)
const ETF_HOLDING_CHANGES_API_URL =
    resolveApiUrl('/api/etf_holding_changes', import.meta.env.VITE_ETF_HOLDING_CHANGES_API_URL)

const ETF_OPTIONS = [
    { value: 'etf_00981A', label: '00981A' },
    { value: 'etf_00982A', label: '00982A' },
    { value: 'etf_00991A', label: '00991A' },
    { value: 'etf_00992A', label: '00992A' },
]

type EtfHoldingChangeItem = {
    code: string
    name: string
    latest_holding_count: number
    previous_holding_count: number
    delta: number
    etfs: EtfHoldingDetail[]
}

type EtfHoldingDetail = {
    etf: string
    latest_holding_count: number
    previous_holding_count: number
    delta: number
    weight?: string
    status: '新增' | '增加' | '減少' | '持平'
}

const stockName = ref('')
const stockCode = ref('')
const stockPrice = ref('')
const selectedQuestion = ref('分析技術面趨勢')
const llmResponse = ref('')
const llmLoading = ref(false)
const selectedStock = ref<{
    name: string
    code?: string
    price: string
} | null>(null)
const etfChanges = ref<EtfHoldingChangeItem[]>([])
const etfLatestDate = ref('')
const etfPreviousDate = ref('')
const selectedEtfs = ref<string[]>(ETF_OPTIONS.map((item) => item.value))
const etfPickerOpen = ref(false)
const showAiModal = ref(false)
const showShareModal = ref(false)
const shareLoading = ref(false)
const shareStatus = ref('')
const shareWebhookUrl = ref('')
let shareStatusTimer: ReturnType<typeof setTimeout> | null = null
const isContrastMode = ref(true)
const THEME_STORAGE_KEY = 'dashboard-etf-theme'
const DISCORD_WEBHOOK_STORAGE_KEY = 'dashboard-discord-webhook-url'

const questions = [
    '分析技術面趨勢',
    '分析籌碼面',
    '預測下週走勢',
    '給出操作建議 (做多/做空)',
    '分析是否有主力介入',
]

const applyStock = () => {
    const name = stockName.value.trim()
    const code = stockCode.value.trim()
    const price = stockPrice.value.trim()

    if (!name && !code && !price) {
        selectedStock.value = null
        return
    }

    selectedStock.value = {
        name: name || code || '未命名股票',
        code: code || undefined,
        price: price || '-',
    }
}

const formatCount = (value: number) => {
    if (!Number.isFinite(value)) return '-'
    return new Intl.NumberFormat('en-US').format(value)
}

const formatDelta = (value: number) => {
    if (!Number.isFinite(value) || value === 0) return '0'
    const sign = value > 0 ? '+' : ''
    return `${sign}${new Intl.NumberFormat('en-US').format(value)}`
}

const formatDateString = (date: Date) => {
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    return `${year}-${month}-${day}`
}

const fetchEtfChanges = async () => {
    try {
        const today = formatDateString(new Date())
        const params = new URLSearchParams({
            date: today,
            etfs: selectedEtfs.value.join(','),
        })
        const response = await fetch(`${ETF_HOLDING_CHANGES_API_URL}?${params.toString()}`)
        const payload = await response.json()
        const data = Array.isArray(payload?.data) ? payload.data : []
        etfChanges.value = data.map((item: any) => ({
            code: String(item.code ?? '').trim(),
            name: String(item.name ?? '').trim(),
            latest_holding_count: Number(item.latest_holding_count ?? 0),
            previous_holding_count: Number(item.previous_holding_count ?? 0),
            delta: Number(item.delta ?? 0),
            etfs: Array.isArray(item.etfs)
                ? item.etfs.map((detail: any) => ({
                    etf: String(detail.etf ?? '').trim(),
                    latest_holding_count: Number(detail.latest_holding_count ?? 0),
                    previous_holding_count: Number(detail.previous_holding_count ?? 0),
                    delta: Number(detail.delta ?? 0),
                    weight: detail.weight ?? '',
                    status: detail.status ?? '增加',
                }))
                : [],
        }))
        etfLatestDate.value = String(payload?.latest_date ?? '')
        etfPreviousDate.value = String(payload?.previous_date ?? '')
    } catch (error) {
        console.error('Failed to load ETF 00981A changes:', error)
        etfChanges.value = []
    }
}

const isSelectedEtf = (value: string) => selectedEtfs.value.includes(value)

const toggleEtf = (value: string) => {
    if (isSelectedEtf(value)) {
        if (selectedEtfs.value.length === 1) return
        selectedEtfs.value = selectedEtfs.value.filter((item) => item !== value)
    } else {
        selectedEtfs.value = [...selectedEtfs.value, value]
    }
    fetchEtfChanges()
}

const setEtfSelection = (values: string[]) => {
    selectedEtfs.value = values.length ? values : ETF_OPTIONS.map((item) => item.value)
    fetchEtfChanges()
}

const selectAllEtfs = () => setEtfSelection(ETF_OPTIONS.map((item) => item.value))
const clearEtfs = () => setEtfSelection(ETF_OPTIONS.map((item) => item.value))

const toggleTheme = () => {
    isContrastMode.value = !isContrastMode.value
    if (typeof window !== 'undefined') {
        window.localStorage.setItem(THEME_STORAGE_KEY, isContrastMode.value ? 'contrast' : 'dark')
    }
}

const loadDiscordWebhookUrl = () => {
    if (typeof window === 'undefined') return
    shareWebhookUrl.value = window.localStorage.getItem(DISCORD_WEBHOOK_STORAGE_KEY) || ''
}

const setShareStatus = (message: string) => {
    shareStatus.value = message
    if (shareStatusTimer) {
        clearTimeout(shareStatusTimer)
    }
    shareStatusTimer = setTimeout(() => {
        shareStatus.value = ''
        shareStatusTimer = null
    }, 3000)
}

const shareEtfToDiscord = async () => {
    if (shareLoading.value) return
    const webhookUrl = shareWebhookUrl.value.trim()
    if (!webhookUrl) {
        setShareStatus('請先輸入 Discord Webhook')
        return
    }

    shareLoading.value = true
    try {
        const today = formatDateString(new Date())
        const response = await fetch(`${ETF_HOLDING_CHANGES_API_URL}/share`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                date: today,
                etfs: selectedEtfs.value,
                webhook_url: webhookUrl,
            }),
        })

        const payload = await response.json().catch(() => ({}))
        if (!response.ok) {
            throw new Error(payload?.error || 'Discord 分享失敗')
        }

        if (typeof window !== 'undefined') {
            window.localStorage.setItem(DISCORD_WEBHOOK_STORAGE_KEY, webhookUrl)
        }
        showShareModal.value = false
        setShareStatus('已送出到 Discord')
    } catch (error: any) {
        console.error('Failed to share ETF intersection to Discord:', error)
        setShareStatus(error?.message || '送出失敗')
    } finally {
        shareLoading.value = false
    }
}

const loadTheme = () => {
    if (typeof window === 'undefined') return
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY)
    isContrastMode.value = savedTheme ? savedTheme === 'contrast' : true
}

const askLLM = async () => {
    if (!selectedStock.value || llmLoading.value) return
    llmLoading.value = true

    try {
        const context = {
            price: selectedStock.value.price,
            code: selectedStock.value.code,
            trade_suggestion: props.tradeSuggestion,
            market_snapshot: {
                highest20: props.highest20.slice(0, 5).map((item) => ({
                    name: item.name,
                    combine: item.combine,
                })),
                lowest20: props.lowest20.slice(0, 5).map((item) => ({
                    name: item.name,
                    combine: item.combine,
                })),
            },
        }

        const response = await fetch(CHAT_LLM_API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                stock_name: selectedStock.value.name,
                question: selectedQuestion.value,
                context: JSON.stringify(context),
            }),
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

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
    loadTheme()
    loadDiscordWebhookUrl()
    fetchEtfChanges()
    refreshTimer = setInterval(fetchEtfChanges, 60_000)
})

onBeforeUnmount(() => {
    if (refreshTimer) {
        clearInterval(refreshTimer)
        refreshTimer = null
    }
    if (shareStatusTimer) {
        clearTimeout(shareStatusTimer)
        shareStatusTimer = null
    }
})
</script>

<template>
    <div
        class="relative flex flex-col h-full min-h-0 font-sans overflow-hidden transition-colors duration-300"
        :class="isContrastMode ? 'bg-[#0b1220] text-slate-100' : 'bg-[#171717] text-gray-300'"
    >
        <div class="flex flex-col flex-1 min-h-0 border-b border-gray-700">
            <div
                class="p-3 border-b shrink-0 flex items-center justify-between gap-3 transition-colors duration-300"
                :class="isContrastMode ? 'bg-[#253149] border-slate-400/70' : 'bg-[#242424] border-gray-700'"
            >
                <div>
                    <h2 class="flex items-center gap-3 text-2xl font-black tracking-wide">
                        <span class="w-3 h-10 bg-gradient-to-b from-emerald-300 to-emerald-500 rounded-full shadow-[0_0_18px_rgba(52,211,153,0.55)]"></span>
                        <span class="bg-gradient-to-r from-white via-emerald-100 to-cyan-100 bg-clip-text text-transparent drop-shadow-[0_0_10px_rgba(255,255,255,0.22)]">
                            ETF 每日持有交集變化
                        </span>
                        <span class="px-2 py-0.5 rounded-full border text-[10px] tracking-[0.2em]"
                            :class="isContrastMode ? 'border-emerald-300/60 text-emerald-200 bg-emerald-500/10' : 'border-emerald-400/60 text-emerald-200 bg-emerald-500/10'">
                            SCAN
                        </span>
                    </h2>
                    <p class="text-[10px] mt-1 tracking-wide" :class="isContrastMode ? 'text-slate-200/90' : 'text-gray-400'">
                        多選 ETF 後，只顯示共同持有的股票，再看各 ETF 今天做了什麼操作
                    </p>
                </div>
                <div class="flex items-center gap-3">
                    <button
                        type="button"
                        class="px-3 py-1 rounded-full border text-xs transition-colors"
                        :class="isContrastMode
                            ? 'bg-white/10 border-slate-300 text-white hover:bg-white/15'
                            : 'bg-transparent border-gray-600 text-gray-300 hover:border-gray-400 hover:text-white'"
                        @click="toggleTheme"
                    >
                        {{ isContrastMode ? '高對比' : '深色' }}
                    </button>
                    <div class="text-[10px] text-right leading-relaxed" :class="isContrastMode ? 'text-slate-200/90' : 'text-gray-400'">
                        <div>今天：{{ etfLatestDate || '-' }}</div>
                        <div>昨天：{{ etfPreviousDate || '-' }}</div>
                    </div>
                </div>
            </div>

            <div
                class="relative px-3 py-2 border-b overflow-visible transition-colors duration-300"
                :class="isContrastMode ? 'bg-[#0f1724] border-slate-500/70' : 'bg-black/30 border-gray-700'"
            >
                <div class="flex flex-wrap items-center gap-2">
                    <span class="text-xs mr-2" :class="isContrastMode ? 'text-slate-100' : 'text-gray-400'">ETF 選擇</span>
                    <button
                        type="button"
                        class="px-3 py-1 rounded-full border text-xs bg-transparent transition-colors"
                        :class="isContrastMode
                            ? 'border-slate-300 text-slate-100 hover:border-white hover:text-white'
                            : 'border-gray-600 text-gray-300 hover:border-gray-400 hover:text-white'"
                        @click="etfPickerOpen = !etfPickerOpen"
                    >
                        選擇 ETF
                    </button>
                    <div class="flex flex-wrap items-center gap-2">
                        <span
                            v-for="option in ETF_OPTIONS"
                            :key="option.value"
                            class="flex items-center gap-1 px-3 py-1 rounded-full border text-xs"
                            :class="isSelectedEtf(option.value)
                                ? 'bg-emerald-400/20 border-emerald-300 text-emerald-100'
                                : (isContrastMode ? 'bg-transparent border-slate-400/70 text-slate-200' : 'bg-transparent border-gray-700 text-gray-500')"
                        >
                            {{ option.label }}
                        </span>
                    </div>
                </div>
                <div class="mt-2 text-[10px]" :class="isContrastMode ? 'text-slate-200/85' : 'text-gray-500'">
                    目前交集：{{ selectedEtfs.map((etf) => etf.replace('etf_', '')).join(' + ') || '-' }}
                </div>

                <div
                    v-if="etfPickerOpen"
                    class="absolute left-3 top-full z-30 mt-2 w-[min(520px,calc(100vw-2rem))] rounded-xl border shadow-2xl"
                    :class="isContrastMode ? 'border-slate-400/80 bg-[#0a1018]' : 'border-gray-700 bg-[#111111]'"
                >
                    <div
                        class="flex items-center justify-between px-3 py-2 border-b text-xs"
                        :class="isContrastMode ? 'border-slate-700 text-slate-100' : 'border-gray-800 text-gray-400'"
                    >
                        <span>可複選 ETF</span>
                        <div class="flex items-center gap-2">
                            <button class="text-emerald-300 hover:text-emerald-200" @click="selectAllEtfs">
                                全選
                            </button>
                            <button class="text-gray-300 hover:text-white" @click="clearEtfs">
                                預設
                            </button>
                        </div>
                    </div>
                    <div class="max-h-56 overflow-y-auto p-2">
                        <label
                            v-for="option in ETF_OPTIONS"
                            :key="option.value"
                            class="flex items-center justify-between gap-3 px-3 py-2 rounded-lg cursor-pointer text-sm"
                            :class="isContrastMode ? 'hover:bg-white/12' : 'hover:bg-white/5'"
                        >
                            <div class="flex items-center gap-3">
                                <input
                                    type="checkbox"
                                    class="checkbox checkbox-sm checkbox-success"
                                    :checked="isSelectedEtf(option.value)"
                                    @change="toggleEtf(option.value)"
                                />
                                <span class="text-gray-200">{{ option.label }}</span>
                            </div>
                            <span class="text-[10px] text-gray-500">{{ option.value }}</span>
                        </label>
                    </div>
                </div>
            </div>

            <div class="flex flex-col flex-1 min-h-0 overflow-hidden bg-black">
                <div class="flex-1 min-h-0 overflow-y-auto">
                    <div
                        v-for="item in etfChanges"
                        :key="item.code"
                        class="border-b px-3 py-3 text-sm"
                        :class="isContrastMode ? 'border-slate-700/80 bg-[#060a10]' : 'border-gray-900'"
                    >
                        <div class="flex items-center justify-center gap-3 text-center">
                            <div class="font-semibold tracking-wide text-white">{{ item.code }}</div>
                            <div class="font-medium text-lg text-white">{{ item.name }}</div>
                        </div>

                        <div class="mt-3 ml-4 pl-4 border-l-2" :class="isContrastMode ? 'border-slate-600' : 'border-gray-800'">
                            <div
                                class="grid grid-cols-5 text-center py-2 text-[11px] font-medium rounded-t"
                                :class="isContrastMode ? 'bg-[#2e3848] text-slate-100' : 'bg-[#242424] text-gray-400'"
                            >
                                <div>ETF</div>
                                <div>昨日持有</div>
                                <div>今日持有</div>
                                <div>差異</div>
                                <div>狀態</div>
                            </div>
                            <div
                                v-for="detail in item.etfs"
                                :key="`${item.code}-${detail.etf}`"
                                class="grid grid-cols-5 text-center py-2 border-b text-xs items-center"
                                :class="isContrastMode ? 'border-slate-700/70 bg-[#090d13]' : 'border-gray-800 bg-black/20'"
                            >
                                <div :class="isContrastMode ? 'text-slate-100' : 'text-gray-300'">{{ detail.etf.replace('etf_', '') }}</div>
                                <div :class="isContrastMode ? 'text-slate-200' : 'text-gray-400'">{{ formatCount(detail.previous_holding_count) }}</div>
                                <div class="text-amber-300">{{ formatCount(detail.latest_holding_count) }}</div>
                                <div :class="detail.delta > 0 ? 'text-red-300' : 'text-emerald-300'">
                                    {{ formatDelta(detail.delta) }}
                                </div>
                                <div
                                    class="font-semibold"
                                    :class="{
                                        'text-red-400': detail.status === '增加',
                                        'text-green-400': detail.status === '減少',
                                        'text-blue-300': detail.status === '新增',
                                        'text-gray-300': detail.status === '持平',
                                    }"
                                >
                                    {{ detail.status }}
                                </div>
                            </div>
                        </div>
                    </div>

                    <div v-if="!etfChanges.length" class="text-center text-xs text-gray-500 py-6">
                        尚無可比較的 ETF 持有變化資料
                    </div>
                </div>
            </div>
        </div>
        <div class="fixed bottom-4 right-4 z-40 flex flex-col items-end gap-3">
            <div
                v-if="shareStatus"
                class="rounded-full border px-3 py-1 text-[10px] shadow-lg backdrop-blur-sm"
                :class="isContrastMode ? 'border-emerald-400/50 bg-[#09131f]/95 text-emerald-200' : 'border-gray-600 bg-[#111111]/95 text-gray-200'"
            >
                {{ shareStatus }}
            </div>
            <button
                type="button"
                class="flex h-12 min-w-36 items-center justify-center gap-2 rounded-full border px-4 shadow-2xl transition"
                :class="shareLoading
                    ? 'border-cyan-400/60 bg-cyan-400/10 text-cyan-200 opacity-80'
                    : 'border-cyan-300/60 bg-[#111111]/95 text-gray-100 hover:border-cyan-300 hover:text-cyan-200'"
                :disabled="shareLoading"
                @click="showShareModal = true"
                aria-label="分享到 Discord"
            >
                <span class="text-sm">{{ shareLoading ? '傳送中' : 'Discord 分享' }}</span>
                <span class="text-lg">✈</span>
            </button>
            <button
                type="button"
                class="flex h-12 w-12 items-center justify-center rounded-full border border-gray-600 bg-[#111111]/95 text-gray-100 shadow-2xl hover:border-emerald-400 hover:text-emerald-300 transition"
                @click="showAiModal = true"
                aria-label="打開 AI 股票分析"
            >
                <span class="text-xl">⚙</span>
            </button>
        </div>

        <div
            v-if="showShareModal"
            class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
            @click.self="showShareModal = false"
        >
            <div class="w-full max-w-xl rounded-2xl border border-gray-700 bg-[#151515] shadow-2xl overflow-hidden">
                <div class="p-4 bg-[#242424] border-b border-gray-700 flex items-start justify-between gap-3">
                    <div>
                        <h2 class="text-white font-bold text-lg flex items-center gap-2">
                            <span class="w-2 h-6 bg-cyan-400 rounded"></span>
                            分享到 Discord
                        </h2>
                        <p class="text-[10px] text-gray-400 mt-1">
                            輸入你自己的 webhook 後，後端才會實際送出訊息
                        </p>
                    </div>
                    <button
                        type="button"
                        class="text-gray-400 hover:text-white text-lg"
                        @click="showShareModal = false"
                    >
                        ×
                    </button>
                </div>

                <div class="p-4 space-y-3">
                    <div>
                        <label class="mb-1 block text-[10px] text-gray-400">Discord Webhook URL</label>
                        <input
                            v-model="shareWebhookUrl"
                            type="text"
                            class="input input-sm input-bordered w-full bg-[#1a1a1a] text-white border-gray-600 text-[11px]"
                            placeholder="https://discord.com/api/webhooks/..."
                        />
                    </div>

                    <div class="text-[10px] text-gray-500 leading-relaxed">
                        會傳送目前選取的 ETF 交集結果，內容由後端整理後送到 Discord。
                    </div>

                    <div class="flex items-center justify-end gap-2 pt-1">
                        <button
                            type="button"
                            class="btn btn-sm btn-ghost"
                            @click="showShareModal = false"
                        >
                            取消
                        </button>
                        <button
                            type="button"
                            class="btn btn-sm btn-primary"
                            :disabled="shareLoading"
                            @click="shareEtfToDiscord"
                        >
                            <span v-if="shareLoading" class="loading loading-spinner loading-xs"></span>
                            {{ shareLoading ? '傳送中...' : '送出' }}
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <div
            v-if="showAiModal"
            class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
            @click.self="showAiModal = false"
        >
            <div class="w-full max-w-4xl max-h-[90vh] rounded-2xl border border-gray-700 bg-[#151515] shadow-2xl overflow-hidden flex flex-col">
                <div class="p-4 bg-[#242424] border-b border-gray-700 flex items-start justify-between gap-3">
                    <div>
                        <h2 class="text-white font-bold text-lg flex items-center gap-2">
                            <span class="w-2 h-6 bg-blue-500 rounded"></span>
                            AI 股票分析
                        </h2>
                        <p class="text-[10px] text-gray-400 mt-1">
                            透過右下角按鈕開啟，避免擠壓 ETF 表格
                        </p>
                    </div>
                    <button
                        type="button"
                        class="text-gray-400 hover:text-white text-lg"
                        @click="showAiModal = false"
                    >
                        ×
                    </button>
                </div>

                <div class="p-4 bg-black/20 border-b border-gray-800">
                    <div class="flex items-start justify-between gap-3">
                        <div
                            class="flex items-center justify-center px-3 py-2 rounded border text-sm"
                            :class="{
                                'bg-gradient-to-br from-red-900/50 to-red-600/20 border-red-500/30': tradeSuggestion === '做多',
                                'bg-gradient-to-br from-green-900/50 to-green-600/20 border-green-500/30': tradeSuggestion === '做空',
                                'bg-gradient-to-br from-gray-800/60 to-gray-700/30 border-gray-500/30': tradeSuggestion === '混沌',
                            }"
                        >
                            <span class="text-xs text-gray-300 mr-2">操作建議</span>
                            <span class="text-white font-black tracking-widest">{{ tradeSuggestion }}</span>
                        </div>

                        <button
                            class="btn btn-sm btn-primary"
                            @click="applyStock"
                        >
                            更新股票
                        </button>
                    </div>

                    <div class="grid grid-cols-1 gap-2 mt-3 md:grid-cols-3">
                        <input
                            v-model="stockName"
                            type="text"
                            class="input input-sm input-bordered w-full bg-[#1a1a1a] text-white border-gray-600"
                            placeholder="股票名稱"
                        />
                        <input
                            v-model="stockCode"
                            type="text"
                            class="input input-sm input-bordered w-full bg-[#1a1a1a] text-white border-gray-600"
                            placeholder="股票代號"
                        />
                        <input
                            v-model="stockPrice"
                            type="text"
                            class="input input-sm input-bordered w-full bg-[#1a1a1a] text-white border-gray-600"
                            placeholder="現價"
                        />
                    </div>

                    <div class="mt-2 text-xs text-gray-400">
                        <span v-if="selectedStock">
                            目前：<span class="text-blue-300 font-bold">{{ selectedStock.name }}</span>
                            <span v-if="selectedStock.code" class="ml-1 text-yellow-400">{{ selectedStock.code }}</span>
                            <span class="ml-1">/</span>
                            <span class="ml-1">{{ selectedStock.price }}</span>
                        </span>
                        <span v-else>先輸入股票資訊再進行分析</span>
                    </div>

                </div>

                <div class="grid grid-cols-1 gap-4 p-4 lg:grid-cols-[0.35fr_0.65fr] flex-1 min-h-0 overflow-hidden">
                    <div class="flex flex-col gap-3">
                        <label class="text-xs text-gray-400">選擇問題</label>
                        <select
                            v-model="selectedQuestion"
                            class="select select-sm select-bordered w-full bg-[#1a1a1a] text-white border-gray-600 focus:border-blue-500"
                        >
                            <option v-for="q in questions" :key="q">
                                {{ q }}
                            </option>
                        </select>

                        <button
                            @click="askLLM"
                            :disabled="!selectedStock || llmLoading"
                            class="btn btn-sm btn-primary w-full mt-auto"
                            :class="{ 'opacity-50': !selectedStock || llmLoading }"
                        >
                            <span v-if="llmLoading" class="loading loading-spinner loading-xs"></span>
                            {{ llmLoading ? '分析中...' : '開始分析' }}
                        </button>
                    </div>

                    <div
                        class="min-h-[320px] max-h-[60vh] bg-[#151515] rounded border border-gray-700 p-4 overflow-y-auto font-mono text-sm leading-relaxed text-gray-300"
                    >
                        <div v-if="llmResponse" class="whitespace-pre-wrap">{{ llmResponse }}</div>
                        <div
                            v-else-if="llmLoading"
                            class="flex items-center justify-center h-full text-gray-500 animate-pulse"
                        >
                            正在思考中...
                        </div>
                        <div v-else class="flex items-center justify-center h-full text-gray-600">
                            輸入股票資訊並提問以獲取分析
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</template>
