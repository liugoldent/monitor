<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

type MarkdownSection = {
  title: string
  body: string
}

type StockRow = {
  code: string
  name: string
  diff: string
  supplyChain: string
  interpretation: string
  position: string
  nearSupport: string
  nextSupport: string
  supportNote: string
}

type UpstreamRow = {
  layer: string
  stocks: string
  interpretation: string
}

type ReportOption = {
  date: string
  file: string
  label: string
}

const markdown = ref('')
const supportMarkdown = ref('')
const supportSourceDate = ref('')
const loading = ref(true)
const errorMessage = ref('')
const reports = ref<ReportOption[]>([])
const route = useRoute()
const router = useRouter()

const reportsUrl = '/institutional/reports.json'

const routeDate = computed(() => {
  const value = route.params.date
  return Array.isArray(value) ? value[0] : value
})

const selectedDate = computed(() => {
  return routeDate.value || reports.value[0]?.date || ''
})

const selectedReport = computed(() => {
  return reports.value.find((item) => item.date === selectedDate.value) ?? reports.value[0]
})

const markdownUrl = computed(() => {
  return selectedReport.value ? `/institutional/${selectedReport.value.file}` : ''
})

const title = computed(() => {
  return markdown.value.match(/^#\s+(.+)$/m)?.[1] ?? '法人操作總結'
})

const reportDate = computed(() => {
  return title.value.match(/\d{4}-\d{2}-\d{2}/)?.[0] ?? '-'
})

const supplementalDiffByCode: Record<string, string> = {
  '2327': '+2,221,000',
  C_NTD: '-24,519,562,997',
}

const parseMarkdownSections = (source: string): MarkdownSection[] => {
  const matches = [...source.matchAll(/^##\s+(.+)$/gm)]
  return matches.map((match, index) => {
    const next = matches[index + 1]
    const start = (match.index ?? 0) + match[0].length
    const end = next?.index ?? source.length
    return {
      title: match[1] ?? '',
      body: source.slice(start, end).trim(),
    }
  })
}

const findSupportSection = (items: MarkdownSection[]) => {
  return items.find((item) => /股價支撐$/.test(item.title))
}

const sections = computed<MarkdownSection[]>(() => {
  return parseMarkdownSections(markdown.value)
})

const supportSections = computed<MarkdownSection[]>(() => {
  return parseMarkdownSections(supportMarkdown.value)
})

const activeSupportSection = computed(() => {
  return findSupportSection(sections.value) ?? findSupportSection(supportSections.value)
})

const supportSourceLabel = computed(() => {
  if (!activeSupportSection.value) return '由 ETF 持有交集自動整理；尚無股價支撐表格'
  const fallbackLabel =
    supportSourceDate.value && supportSourceDate.value !== selectedDate.value
      ? `（沿用 ${supportSourceDate.value}）`
      : ''
  return `由 ETF 持有交集與 ${activeSupportSection.value.title} 自動整理${fallbackLabel}`
})

const stockRows = computed<StockRow[]>(() => {
  const section = sections.value.find((item) => item.title === 'ETF 持有交集')
  if (!section) return []

  const supportRows =
    activeSupportSection.value?.body
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.startsWith('|'))
      .slice(2)
      .map((line) =>
        line
          .split('|')
          .slice(1, -1)
          .map((cell) => cell.trim()),
      )
      .filter((cells) => cells[0]) ?? []
  const supportByCode = new Map(
    supportRows.map((cells) => [
      cells[0],
      {
        name: cells[1] ?? '',
        nearSupport: cells[2] ?? '',
        nextSupport: cells[3] ?? '',
        supportNote: cells[4] ?? '',
      },
    ]),
  )

  const tableLines = section.body
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|'))

  if (tableLines.length < 3) return []

  const rows = tableLines.slice(2).map((line) => {
    const cells = line
      .split('|')
      .slice(1, -1)
      .map((cell) => cell.trim())
    const support = supportByCode.get(cells[0] ?? '')

    return {
      code: cells[0] ?? '',
      name: cells[1] ?? '',
      diff: cells[2] ?? '',
      supplyChain: cells[3] ?? '',
      interpretation: cells[4] ?? '',
      position: cells[5] ?? '',
      nearSupport: support?.nearSupport ?? '',
      nextSupport: support?.nextSupport ?? '',
      supportNote: support?.supportNote ?? '',
    }
  })

  const stockCodes = new Set(rows.map((row) => row.code))
  const supportOnlyRows = supportRows
    .filter((cells) => !stockCodes.has(cells[0] ?? ''))
    .map((cells) => ({
      code: cells[0] ?? '',
      name: cells[1] ?? '',
      diff: supplementalDiffByCode[cells[0] ?? ''] ?? '',
      supplyChain: '',
      interpretation: '',
      position: '',
      nearSupport: cells[2] ?? '',
      nextSupport: cells[3] ?? '',
      supportNote: cells[4] ?? '',
    }))

  return [...rows, ...supportOnlyRows]
})

const strongestRows = computed(() => {
  return stockRows.value.filter((row) => row.diff.startsWith('+')).slice(0, 8)
})

const reducedRows = computed(() => {
  return stockRows.value.filter((row) => row.diff.startsWith('-'))
})

const industryRows = computed(() => {
  return Object.entries(
    stockRows.value.reduce<Record<string, number>>((acc, row) => {
      if (!row.supplyChain) return acc
      acc[row.supplyChain] = (acc[row.supplyChain] ?? 0) + 1
      return acc
    }, {}),
  )
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-Hant'))
})

const upstreamRows = computed<UpstreamRow[]>(() => {
  const section = sections.value.find((item) => item.title === '上下游關係')
  if (!section) return []

  return section.body
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|'))
    .slice(2)
    .map((line) =>
      line
        .split('|')
        .slice(1, -1)
        .map((cell) => cell.trim()),
    )
    .filter((cells) => cells[0] && cells[1])
    .map((cells) => ({
      layer: cells[0] ?? '',
      stocks: cells[1] ?? '',
      interpretation: cells[2] ?? '',
    }))
})

const technicalUrl = (code: string) => {
  return `https://tw.stock.yahoo.com/quote/${code}.TW/technical-analysis`
}

const parseTechnicalValue = (source: string, label: string) => {
  const escapedLabel = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = source.match(new RegExp(`${escapedLabel}\\s*([0-9,]+(?:\\.\\d+)?)`))
  if (!match?.[1]) return null
  return Number(match[1].replace(/,/g, ''))
}

const parseDiffValue = (source: string) => {
  const normalized = source.replace(/,/g, '').trim()
  if (!normalized) return null
  const value = Number(normalized)
  return Number.isFinite(value) ? value : null
}

const parseSupportRange = (source: string) => {
  const match = source.match(/([0-9,]+(?:\.\d+)?)(?:\s*-\s*([0-9,]+(?:\.\d+)?))?/)
  if (!match?.[1]) return null
  const first = Number(match[1].replace(/,/g, ''))
  const second = match[2] ? Number(match[2].replace(/,/g, '')) : first
  if (!Number.isFinite(first) || !Number.isFinite(second)) return null
  return {
    low: Math.min(first, second),
    high: Math.max(first, second),
  }
}

const isBetween = (value: number, first: number, second: number) => {
  const high = Math.max(first, second)
  const low = Math.min(first, second)
  return value <= high && value >= low
}

const recommendationInfo = (row: StockRow) => {
  const close = parseTechnicalValue(row.position, '收盤')
  const ma5 = parseTechnicalValue(row.position, 'MA5')
  const ma10 = parseTechnicalValue(row.position, 'MA10')
  const ma20 = parseTechnicalValue(row.position, 'MA20')
  const ma60 = parseTechnicalValue(row.position, 'MA60')
  const diff = parseDiffValue(row.diff)
  const support = parseSupportRange(row.nearSupport)
  const reasons: string[] = []
  let score = 2

  if (diff === null && [close, ma5, ma10, ma20, ma60].some((value) => value === null)) {
    return {
      score: 0,
      label: '資料不足',
      icon: '-',
      reason: '缺少 ETF 差異與均線資料，先不給推薦程度。',
    }
  }

  if (diff !== null && diff > 0) {
    score += diff >= 100000 ? 2 : 1
    reasons.push(diff >= 100000 ? 'ETF 明顯加碼' : 'ETF 小幅加碼')
  } else if (diff !== null && diff < 0) {
    score -= 2
    reasons.push('ETF 減碼，先保守')
  } else {
    reasons.push('ETF 持平或無差異資料')
  }

  if ([close, ma5, ma10, ma20, ma60].some((value) => value === null)) {
    if (support) {
      score += 1
      reasons.push('已有支撐區間，但缺少均線資料，推薦程度保守上修')
    } else {
      reasons.push('缺少均線資料，無法判斷追高或回測位階')
    }
  } else {
    const price = close as number

    if (price > Math.max(ma5 as number, ma10 as number, ma20 as number, ma60 as number)) {
      score -= 1
      reasons.push('高於所有均線，偏強但較容易追高')
    } else if (price < Math.min(ma5 as number, ma10 as number, ma20 as number, ma60 as number)) {
      score += diff !== null && diff > 0 ? 1 : -1
      reasons.push(diff !== null && diff > 0 ? '低位階且有 ETF 買盤' : '低於所有均線，尚未轉強')
    } else if (isBetween(price, ma20 as number, ma60 as number)) {
      score += diff !== null && diff > 0 ? 2 : 1
      reasons.push('介於月線與季線，位階較適合等轉強')
    } else if (isBetween(price, ma10 as number, ma20 as number)) {
      score += 1
      reasons.push('介於 MA10 與 MA20，中段位階')
    } else if (isBetween(price, ma5 as number, ma10 as number)) {
      reasons.push('靠近短均，適合觀察回測是否守住')
    }

    if (support) {
      if (price >= support.low * 0.97 && price <= support.high * 1.05) {
        score += 1
        reasons.push('價格接近近期支撐，風險報酬較好抓')
      } else if (price > support.high * 1.15) {
        score -= 1
        reasons.push('距離近期支撐偏遠，追價風險較高')
      }
    }
  }

  const finalScore = Math.max(1, Math.min(5, score))
  const label =
    finalScore >= 5
      ? '高'
      : finalScore === 4
        ? '偏高'
        : finalScore === 3
          ? '中'
          : finalScore === 2
            ? '低'
            : '很低'

  return {
    score: finalScore,
    label,
    icon: '★'.repeat(finalScore) + '☆'.repeat(5 - finalScore),
    reason: reasons.join('；'),
  }
}

const sortedStockRows = computed(() => {
  return [...stockRows.value].sort((a, b) => {
    const scoreDiff = recommendationInfo(b).score - recommendationInfo(a).score
    if (scoreDiff !== 0) return scoreDiff

    const diffA = parseDiffValue(a.diff) ?? Number.NEGATIVE_INFINITY
    const diffB = parseDiffValue(b.diff) ?? Number.NEGATIVE_INFINITY
    if (diffB !== diffA) return diffB - diffA

    return a.code.localeCompare(b.code)
  })
})

const fetchReports = async () => {
  const response = await fetch(`${reportsUrl}?t=${Date.now()}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const payload = await response.json()
  reports.value = Array.isArray(payload) ? payload : []
}

const fetchSupportReport = async () => {
  supportMarkdown.value = ''
  supportSourceDate.value = ''

  if (findSupportSection(sections.value)) {
    supportSourceDate.value = selectedDate.value
    return
  }

  const candidates = reports.value
    .filter((report) => report.date !== selectedDate.value && report.date <= selectedDate.value)
    .sort((a, b) => b.date.localeCompare(a.date))

  for (const report of candidates) {
    const response = await fetch(`/institutional/${report.file}?t=${Date.now()}`)
    if (!response.ok) continue

    const source = await response.text()
    if (findSupportSection(parseMarkdownSections(source))) {
      supportMarkdown.value = source
      supportSourceDate.value = report.date
      return
    }
  }
}

const fetchReport = async () => {
  if (!markdownUrl.value) return

  loading.value = true
  errorMessage.value = ''
  supportMarkdown.value = ''
  supportSourceDate.value = ''

  try {
    const response = await fetch(`${markdownUrl.value}?t=${Date.now()}`)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    markdown.value = await response.text()
    await fetchSupportReport()
  } catch (error) {
    console.error(error)
    errorMessage.value = '讀取法人操作 markdown 失敗'
  } finally {
    loading.value = false
  }
}

const loadInitialReport = async () => {
  loading.value = true
  errorMessage.value = ''

  try {
    await fetchReports()
    if (!routeDate.value && reports.value[0]?.date) {
      await router.replace({ name: 'institutional', params: { date: reports.value[0].date } })
      return
    }
    await fetchReport()
  } catch (error) {
    console.error(error)
    errorMessage.value = '讀取法人操作日期清單失敗'
    loading.value = false
  }
}

const selectReport = (date: string) => {
  if (date === selectedDate.value) return
  void router.push({ name: 'institutional', params: { date } })
}

onMounted(loadInitialReport)

watch(
  () => route.params.date,
  () => {
    if (reports.value.length === 0) return
    void fetchReport()
  },
)
</script>

<template>
  <main
    class="institutional-page min-h-screen w-screen overflow-x-hidden bg-[#080d14] text-slate-100"
  >
    <header class="border-b border-slate-700/70 bg-[#1d2839] px-5 py-4 md:px-8">
      <div class="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
        <div>
          <div class="mb-2 flex items-center gap-3">
            <span class="h-8 w-2 rounded-full bg-emerald-400"></span>
            <span
              class="rounded-full border border-emerald-300/70 px-3 py-1 text-xs font-semibold uppercase tracking-[0.35em] text-emerald-200"
            >
              Flow
            </span>
          </div>
          <h1 class="text-2xl font-semibold tracking-wide text-white md:text-3xl">{{ title }}</h1>
          <p class="mt-1 max-w-3xl text-sm leading-6 text-slate-300">
            彙整 ETF 持有交集、資金流向、上下游關係、均線位階與加碼風險，作為每日盤後研究入口。
          </p>
        </div>

        <div class="flex items-center gap-3">
          <RouterLink
            to="/"
            class="rounded-full border border-slate-500 px-4 py-2 text-sm text-slate-200 transition hover:border-emerald-300 hover:text-white"
          >
            返回首頁
          </RouterLink>
          <button
            type="button"
            class="rounded-full border border-emerald-300/70 bg-emerald-400/10 px-4 py-2 text-sm text-emerald-100 transition hover:bg-emerald-400/20"
            @click="fetchReport"
          >
            重新讀取
          </button>
        </div>
      </div>
    </header>

    <section class="grid gap-3 border-b border-slate-800 px-5 py-4 md:grid-cols-4 md:px-8">
      <div class="stat-tile">
        <span>資料日</span>
        <strong>{{ reportDate }}</strong>
      </div>
      <div class="stat-tile">
        <span>交集股票</span>
        <strong>{{ stockRows.length }}</strong>
      </div>
      <div class="stat-tile">
        <span>加碼股票</span>
        <strong>{{ strongestRows.length }}</strong>
      </div>
      <div class="stat-tile">
        <span>減碼股票</span>
        <strong>{{ reducedRows.length }}</strong>
      </div>
    </section>

    <section class="border-b border-slate-800 px-5 py-3 md:px-8">
      <div class="mb-2 text-xs font-semibold uppercase tracking-[0.25em] text-slate-400">
        Report Dates
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="report in reports"
          :key="report.date"
          type="button"
          class="rounded-full border px-4 py-2 text-sm transition"
          :class="
            report.date === selectedDate
              ? 'border-emerald-300 bg-emerald-400/15 text-emerald-100'
              : 'border-slate-600 bg-slate-900/70 text-slate-300 hover:border-emerald-300 hover:text-white'
          "
          @click="selectReport(report.date)"
        >
          {{ report.date }}
        </button>
      </div>
    </section>

    <div v-if="loading" class="px-8 py-16 text-center text-slate-300">載入 markdown...</div>
    <div v-else-if="errorMessage" class="px-8 py-16 text-center text-rose-200">
      {{ errorMessage }}
    </div>

    <div v-else class="px-5 py-5 lg:px-8">
      <div class="space-y-4">
        <section class="context-grid">
          <div class="panel compact-panel">
            <div class="panel-title-row">
              <h2>產業分布</h2>
              <span>{{ industryRows.length }} 類</span>
            </div>
            <div class="chip-row">
              <span v-for="item in industryRows" :key="item.name" class="industry-chip">
                {{ item.name }}
                <strong>{{ item.count }}</strong>
              </span>
            </div>
          </div>

          <div class="panel compact-panel">
            <div class="panel-title-row">
              <h2>上下游關係</h2>
              <span>{{ upstreamRows.length }} 層</span>
            </div>
            <div class="upstream-list">
              <div v-for="item in upstreamRows" :key="item.layer" class="upstream-item">
                <strong>{{ item.layer }}</strong>
                <span>{{ item.stocks }}</span>
              </div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <h2>加碼檢查表</h2>
            <span class="text-xs text-slate-400">{{ supportSourceLabel }}</span>
          </div>

          <div class="mt-4 overflow-x-auto">
            <table class="stock-table">
              <thead>
                <tr>
                  <th>股票</th>
                  <th>推薦程度</th>
                  <th>差異</th>
                  <th>近期支撐</th>
                  <th>下一層支撐</th>
                  <th>備註</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in sortedStockRows" :key="`${row.code}-${row.name}`">
                  <td>
                    <a
                      :href="technicalUrl(row.code)"
                      target="_blank"
                      rel="noreferrer"
                      class="font-semibold text-white underline-offset-4 transition hover:text-emerald-100 hover:underline"
                    >
                      {{ row.code }} {{ row.name }}
                    </a>
                    <div v-if="row.supplyChain" class="mt-1 text-xs text-emerald-200">
                      {{ row.supplyChain }}
                    </div>
                    <div class="mt-1 text-xs text-slate-500">{{ row.interpretation }}</div>
                  </td>
                  <td>
                    <span class="recommendation-pill" :title="recommendationInfo(row).reason">
                      <strong>{{ recommendationInfo(row).label }}</strong>
                      <span>{{ recommendationInfo(row).icon }}</span>
                    </span>
                  </td>
                  <td
                    :class="
                      row.diff.startsWith('-')
                        ? 'text-rose-300'
                        : row.diff === '0'
                          ? 'text-slate-300'
                          : 'text-emerald-300'
                    "
                  >
                    {{ row.diff }}
                  </td>
                  <td class="support-price">{{ row.nearSupport || '-' }}</td>
                  <td class="support-price">{{ row.nextSupport || '-' }}</td>
                  <td class="support-note">{{ row.supportNote || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  </main>
</template>

<style scoped>
.institutional-page {
  font-family:
    Inter,
    -apple-system,
    BlinkMacSystemFont,
    'Segoe UI',
    sans-serif;
}

.panel {
  border: 1px solid rgba(51, 65, 85, 0.9);
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.72);
  padding: 18px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.25);
}

.panel h2 {
  color: #f8fafc;
  font-size: 16px;
  font-weight: 700;
}

.compact-panel {
  padding: 14px 16px;
}

.context-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.35fr);
}

.panel-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.panel-title-row span {
  color: #94a3b8;
  font-size: 12px;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.industry-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid rgba(52, 211, 153, 0.35);
  border-radius: 999px;
  background: rgba(16, 185, 129, 0.08);
  padding: 5px 9px;
  color: #d1fae5;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.industry-chip strong {
  color: #f8fafc;
  font-size: 11px;
}

.upstream-list {
  display: grid;
  gap: 7px 10px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 12px;
}

.upstream-item {
  display: grid;
  gap: 3px;
  border-bottom: 1px solid rgba(51, 65, 85, 0.7);
  padding-bottom: 7px;
}

.upstream-item strong {
  color: #e2e8f0;
  font-size: 12px;
}

.upstream-item span {
  overflow: hidden;
  color: #94a3b8;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stat-tile {
  border: 1px solid rgba(51, 65, 85, 0.9);
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.72);
  padding: 10px 14px;
}

.stat-tile span {
  display: block;
  color: #94a3b8;
  font-size: 12px;
}

.stat-tile strong {
  display: block;
  margin-top: 2px;
  color: #f8fafc;
  font-size: 21px;
  font-weight: 800;
}

.stock-table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
}

.stock-table th,
.stock-table td {
  border-bottom: 1px solid rgba(51, 65, 85, 0.9);
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
}

.stock-table th {
  background: rgba(51, 65, 85, 0.75);
  color: #e2e8f0;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.stock-table td {
  color: #cbd5e1;
  font-size: 13px;
  line-height: 1.55;
}

.recommendation-pill {
  display: grid;
  gap: 3px;
  min-width: 86px;
}

.recommendation-pill strong {
  color: #f8fafc;
  font-size: 13px;
  line-height: 1;
}

.recommendation-pill span {
  color: #facc15;
  font-size: 12px;
  letter-spacing: 0;
  white-space: nowrap;
}

.support-price {
  color: #f8fafc;
  font-weight: 700;
  white-space: nowrap;
}

.support-note {
  min-width: 260px;
}

@media (max-width: 768px) {
  .context-grid {
    grid-template-columns: 1fr;
  }

  .upstream-list {
    grid-template-columns: 1fr;
  }

  .stock-table {
    min-width: 760px;
  }
}
</style>
