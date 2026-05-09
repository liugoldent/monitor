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
}

type ReportOption = {
  date: string
  file: string
  label: string
}

const markdown = ref('')
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

const escapeHtml = (value: string) => {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

const formatInline = (value: string) => {
  return escapeHtml(value)
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

const parseTable = (lines: string[], startIndex: number) => {
  const tableLines: string[] = []
  let index = startIndex

  while (index < lines.length && (lines[index] ?? '').trim().startsWith('|')) {
    tableLines.push(lines[index] ?? '')
    index += 1
  }

  if (tableLines.length < 2) return { html: '', nextIndex: startIndex }

  const headers = (tableLines[0] ?? '')
    .split('|')
    .slice(1, -1)
    .map((cell) => formatInline(cell.trim()))
  const rows = tableLines.slice(2).map((line) =>
    line
      .split('|')
      .slice(1, -1)
      .map((cell) => formatInline(cell.trim())),
  )

  const headerHtml = headers.map((header) => `<th>${header}</th>`).join('')
  const rowsHtml = rows
    .map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`)
    .join('')

  return {
    html: `<div class="markdown-table-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${rowsHtml}</tbody></table></div>`,
    nextIndex: index,
  }
}

const markdownToHtml = (source: string) => {
  const lines = source.split('\n')
  const html: string[] = []
  let listOpen = false
  let index = 0

  const closeList = () => {
    if (!listOpen) return
    html.push('</ul>')
    listOpen = false
  }

  while (index < lines.length) {
    const rawLine = lines[index] ?? ''
    const line = rawLine.trim()

    if (!line) {
      closeList()
      index += 1
      continue
    }

    if (line.startsWith('|')) {
      closeList()
      const table = parseTable(lines, index)
      html.push(table.html)
      index = table.nextIndex
      continue
    }

    if (line.startsWith('# ')) {
      closeList()
      html.push(`<h1>${formatInline(line.slice(2).trim())}</h1>`)
    } else if (line.startsWith('## ')) {
      closeList()
      html.push(`<h2>${formatInline(line.slice(3).trim())}</h2>`)
    } else if (line.startsWith('### ')) {
      closeList()
      html.push(`<h3>${formatInline(line.slice(4).trim())}</h3>`)
    } else if (line.startsWith('> ')) {
      closeList()
      html.push(`<blockquote>${formatInline(line.slice(2).trim())}</blockquote>`)
    } else if (line.startsWith('- ')) {
      if (!listOpen) {
        html.push('<ul>')
        listOpen = true
      }
      html.push(`<li>${formatInline(line.slice(2).trim())}</li>`)
    } else {
      closeList()
      html.push(`<p>${formatInline(line)}</p>`)
    }

    index += 1
  }

  closeList()
  return html.join('')
}

const title = computed(() => {
  return markdown.value.match(/^#\s+(.+)$/m)?.[1] ?? '法人操作總結'
})

const reportDate = computed(() => {
  return title.value.match(/\d{4}-\d{2}-\d{2}/)?.[0] ?? '-'
})

const sections = computed<MarkdownSection[]>(() => {
  const matches = [...markdown.value.matchAll(/^##\s+(.+)$/gm)]
  return matches.map((match, index) => {
    const next = matches[index + 1]
    const start = (match.index ?? 0) + match[0].length
    const end = next?.index ?? markdown.value.length
    return {
      title: match[1] ?? '',
      body: markdown.value.slice(start, end).trim(),
    }
  })
})

const conclusionBullets = computed(() => {
  const section = sections.value.find((item) => item.title === '今日結論')
  if (!section) return []

  return section.body
    .split('\n')
    .filter((line) => line.trim().startsWith('- '))
    .map((line) => line.trim().slice(2))
})

const stockRows = computed<StockRow[]>(() => {
  const section = sections.value.find((item) => item.title === 'ETF 持有交集')
  if (!section) return []

  const tableLines = section.body
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|'))

  if (tableLines.length < 3) return []

  return tableLines.slice(2).map((line) => {
    const cells = line
      .split('|')
      .slice(1, -1)
      .map((cell) => cell.trim())

    return {
      code: cells[0] ?? '',
      name: cells[1] ?? '',
      diff: cells[2] ?? '',
      supplyChain: cells[3] ?? '',
      interpretation: cells[4] ?? '',
      position: cells[5] ?? '',
    }
  })
})

const strongestRows = computed(() => {
  return stockRows.value
    .filter((row) => row.diff.startsWith('+'))
    .slice(0, 8)
})

const reducedRows = computed(() => {
  return stockRows.value.filter((row) => row.diff.startsWith('-'))
})

const topicCounts = computed(() => {
  return stockRows.value.reduce<Record<string, number>>((acc, row) => {
    const topic = row.supplyChain || '未分類'
    acc[topic] = (acc[topic] ?? 0) + 1
    return acc
  }, {})
})

const fullHtml = computed(() => markdownToHtml(markdown.value))

const technicalUrl = (code: string) => {
  return `https://tw.stock.yahoo.com/quote/${code}.TW/technical-analysis`
}

const parseTechnicalValue = (source: string, label: string) => {
  const escapedLabel = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = source.match(new RegExp(`${escapedLabel}\\s*([0-9,]+(?:\\.\\d+)?)`))
  if (!match?.[1]) return null
  return Number(match[1].replace(/,/g, ''))
}

const isBetween = (value: number, first: number, second: number) => {
  const high = Math.max(first, second)
  const low = Math.min(first, second)
  return value <= high && value >= low
}

const technicalSignalInfo = (position: string) => {
  const close = parseTechnicalValue(position, '收盤')
  const ma5 = parseTechnicalValue(position, 'MA5')
  const ma10 = parseTechnicalValue(position, 'MA10')
  const ma20 = parseTechnicalValue(position, 'MA20')
  const ma60 = parseTechnicalValue(position, 'MA60')

  if ([close, ma5, ma10, ma20, ma60].some((value) => value === null)) {
    return { icon: '', reason: '缺少收盤價或均線資料，無法判斷位階' }
  }

  const price = close as number
  const averages = [ma5, ma10, ma20, ma60] as number[]

  if (price > Math.max(...averages)) {
    return { icon: '🔥', reason: '收盤價高於 MA5、MA10、MA20、MA60，代表價格在所有均線之上，偏強但可能較熱' }
  }
  if (price < Math.min(...averages)) {
    return { icon: '⭐⭐⭐⭐⭐', reason: '收盤價低於 MA5、MA10、MA20、MA60，代表價格在所有均線之下，若法人買超可優先觀察低位階轉強' }
  }
  if (isBetween(price, ma20 as number, ma60 as number)) {
    return { icon: '⭐⭐⭐⭐', reason: '收盤價介於 MA20（月線）與 MA60（季線）之間，位階低於月線但尚未跌破季線' }
  }
  if (isBetween(price, ma10 as number, ma20 as number)) {
    return { icon: '⭐⭐⭐', reason: '收盤價介於 MA10 與 MA20（月線）之間，屬於中段位階' }
  }
  if (isBetween(price, ma5 as number, ma10 as number)) {
    return { icon: '⭐⭐', reason: '收盤價介於 MA5 與 MA10 之間，短線仍靠近短均' }
  }
  return { icon: '', reason: '收盤價不在預設均線區間內，請直接查看均線位階欄位' }
}

const fetchReports = async () => {
  const response = await fetch(`${reportsUrl}?t=${Date.now()}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const payload = await response.json()
  reports.value = Array.isArray(payload) ? payload : []
}

const fetchReport = async () => {
  if (!markdownUrl.value) return

  loading.value = true
  errorMessage.value = ''

  try {
    const response = await fetch(`${markdownUrl.value}?t=${Date.now()}`)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    markdown.value = await response.text()
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
  <main class="institutional-page min-h-screen w-screen overflow-x-hidden bg-[#080d14] text-slate-100">
    <header class="border-b border-slate-700/70 bg-[#1d2839] px-5 py-5 md:px-8">
      <div class="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
        <div>
          <div class="mb-3 flex items-center gap-3">
            <span class="h-10 w-2 rounded-full bg-emerald-400"></span>
            <span class="rounded-full border border-emerald-300/70 px-3 py-1 text-xs font-semibold uppercase tracking-[0.35em] text-emerald-200">
              Flow
            </span>
          </div>
          <h1 class="text-3xl font-semibold tracking-wide text-white md:text-4xl">{{ title }}</h1>
          <p class="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
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

    <section class="grid gap-4 border-b border-slate-800 px-5 py-5 md:grid-cols-4 md:px-8">
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

    <section class="border-b border-slate-800 px-5 py-4 md:px-8">
      <div class="mb-3 text-xs font-semibold uppercase tracking-[0.25em] text-slate-400">Report Dates</div>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="report in reports"
          :key="report.date"
          type="button"
          class="rounded-full border px-4 py-2 text-sm transition"
          :class="report.date === selectedDate
            ? 'border-emerald-300 bg-emerald-400/15 text-emerald-100'
            : 'border-slate-600 bg-slate-900/70 text-slate-300 hover:border-emerald-300 hover:text-white'"
          @click="selectReport(report.date)"
        >
          {{ report.date }}
        </button>
      </div>
    </section>

    <div v-if="loading" class="px-8 py-16 text-center text-slate-300">載入 markdown...</div>
    <div v-else-if="errorMessage" class="px-8 py-16 text-center text-rose-200">{{ errorMessage }}</div>

    <div v-else class="grid gap-5 px-5 py-6 lg:grid-cols-[360px_minmax(0,1fr)] lg:px-8">
      <aside class="space-y-5">
        <section class="panel">
          <h2>今日結論</h2>
          <div class="mt-4 space-y-3">
            <div v-for="item in conclusionBullets" :key="item" class="rounded-lg border border-slate-700 bg-slate-950/45 p-3 text-sm leading-6 text-slate-200">
              {{ item }}
            </div>
          </div>
        </section>

        <section class="panel">
          <h2>題材集中度</h2>
          <div class="mt-4 space-y-3">
            <div v-for="(count, topic) in topicCounts" :key="topic" class="flex items-center justify-between gap-4 border-b border-slate-800 pb-2 last:border-b-0 last:pb-0">
              <span class="text-sm text-slate-300">{{ topic }}</span>
              <strong class="text-sm text-emerald-200">{{ count }}</strong>
            </div>
          </div>
        </section>
      </aside>

      <div class="space-y-5">
        <section class="panel">
          <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <h2>加碼檢查表</h2>
            <span class="text-xs text-slate-400">由 markdown 的 ETF 持有交集表格自動整理</span>
          </div>

          <div class="mt-4 overflow-x-auto">
            <table class="stock-table">
              <thead>
                <tr>
                  <th>股票</th>
                  <th>位階</th>
                  <th>差異</th>
                  <th>供應鏈</th>
                  <th>均線位階</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in stockRows" :key="`${row.code}-${row.name}`">
                  <td>
                    <a
                      :href="technicalUrl(row.code)"
                      target="_blank"
                      rel="noreferrer"
                      class="font-semibold text-white underline-offset-4 transition hover:text-emerald-100 hover:underline"
                    >
                      {{ row.code }} {{ row.name }}
                    </a>
                    <div class="mt-1 text-xs text-slate-500">{{ row.interpretation }}</div>
                  </td>
                  <td>
                    <span
                      class="inline-flex min-w-20 justify-center text-base leading-none"
                      :title="technicalSignalInfo(row.position).reason"
                    >
                      {{ technicalSignalInfo(row.position).icon || '-' }}
                    </span>
                  </td>
                  <td :class="row.diff.startsWith('-') ? 'text-rose-300' : row.diff === '0' ? 'text-slate-300' : 'text-emerald-300'">
                    {{ row.diff }}
                  </td>
                  <td>{{ row.supplyChain }}</td>
                  <td>{{ row.position }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="panel">
          <h2>完整 markdown 報告</h2>
          <article class="markdown-body mt-4" v-html="fullHtml"></article>
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
  padding: 20px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.25);
}

.panel h2 {
  color: #f8fafc;
  font-size: 16px;
  font-weight: 700;
}

.stat-tile {
  border: 1px solid rgba(51, 65, 85, 0.9);
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.72);
  padding: 14px 16px;
}

.stat-tile span {
  display: block;
  color: #94a3b8;
  font-size: 12px;
}

.stat-tile strong {
  display: block;
  margin-top: 4px;
  color: #f8fafc;
  font-size: 24px;
  font-weight: 800;
}

.stock-table,
:deep(.markdown-body table) {
  width: 100%;
  min-width: 880px;
  border-collapse: collapse;
}

.stock-table th,
.stock-table td,
:deep(.markdown-body th),
:deep(.markdown-body td) {
  border-bottom: 1px solid rgba(51, 65, 85, 0.9);
  padding: 12px;
  text-align: left;
  vertical-align: top;
}

.stock-table th,
:deep(.markdown-body th) {
  background: rgba(51, 65, 85, 0.75);
  color: #e2e8f0;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.stock-table td,
:deep(.markdown-body td) {
  color: #cbd5e1;
  font-size: 13px;
  line-height: 1.55;
}

:deep(.markdown-body) {
  color: #cbd5e1;
  font-size: 14px;
  line-height: 1.75;
}

:deep(.markdown-body h1) {
  display: none;
}

:deep(.markdown-body h2) {
  margin: 28px 0 12px;
  color: #f8fafc;
  font-size: 20px;
  font-weight: 800;
}

:deep(.markdown-body h2:first-child) {
  margin-top: 0;
}

:deep(.markdown-body h3) {
  margin: 22px 0 10px;
  color: #e2e8f0;
  font-size: 16px;
  font-weight: 700;
}

:deep(.markdown-body p) {
  margin: 10px 0;
}

:deep(.markdown-body ul) {
  margin: 10px 0 18px;
  padding-left: 20px;
}

:deep(.markdown-body li) {
  margin: 8px 0;
}

:deep(.markdown-body strong) {
  color: #f8fafc;
  font-weight: 800;
}

:deep(.markdown-body blockquote) {
  margin: 0 0 18px;
  border-left: 4px solid #34d399;
  background: rgba(16, 185, 129, 0.08);
  padding: 12px 14px;
  color: #d1fae5;
}

:deep(.markdown-table-wrap) {
  overflow-x: auto;
  margin: 14px 0 22px;
}

@media (max-width: 768px) {
  .stock-table,
  :deep(.markdown-body table) {
    min-width: 760px;
  }
}
</style>
