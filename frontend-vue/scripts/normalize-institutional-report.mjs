import fs from 'node:fs'
import path from 'node:path'

const baseDir = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..')
const reportDir = path.join(baseDir, 'public', 'institutional')
const reportsPath = path.join(reportDir, 'reports.json')
const stockTechDir = path.resolve(baseDir, '..', 'backend-futures-py', 'stockTech')

const tableHeaders = ['代號', '股票', '差異', '供應鏈位置', '法人動態解讀', '目前位階']

const args = process.argv.slice(2)
const dateArg = args.find((arg) => /^\d{4}-\d{2}-\d{2}$/.test(arg))

const formatNumber = (value) => {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  return number.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

const readJson = (filePath, fallback) => {
  if (!fs.existsSync(filePath)) return fallback
  return JSON.parse(fs.readFileSync(filePath, 'utf8'))
}

const resolveReportDate = () => {
  if (dateArg) return dateArg
  const reports = readJson(reportsPath, [])
  return Array.isArray(reports) && reports[0]?.date ? reports[0].date : ''
}

const parseTableRow = (line) => {
  return line
    .split('|')
    .slice(1, -1)
    .map((cell) => cell.trim())
}

const buildRowMap = (headers, cells) => {
  const pick = (candidates, fallbackIndex) => {
    const index = headers.findIndex((header) => candidates.some((candidate) => header.includes(candidate)))
    return cells[index >= 0 ? index : fallbackIndex] ?? ''
  }

  return {
    code: pick(['代號'], 0),
    name: pick(['股票', '名稱'], 1),
    diff: pick(['差異'], 2),
    supplyChain: pick(['供應鏈'], 3),
    interpretation: pick(['法人動態', '解讀'], 4),
    position: pick(['目前位階', '均線'], 5),
  }
}

const classifyPosition = (technical, diff) => {
  if (!technical) return '待更新均線'

  const close = Number(technical.close)
  const ma5 = Number(technical.ma5)
  const ma10 = Number(technical.ma10)
  const ma20 = Number(technical.ma20)
  const ma60 = Number(technical.ma60)
  if (![close, ma5, ma10, ma20, ma60].every(Number.isFinite)) return '待更新均線'

  const averages = [
    ['MA5', ma5],
    ['MA10', ma10],
    ['MA20', ma20],
    ['MA60', ma60],
  ].sort((a, b) => b[1] - a[1])

  let zone = ''
  if (close > averages[0][1]) {
    zone = '高於所有均線'
  } else if (close < averages[averages.length - 1][1]) {
    zone = '低於所有均線'
  } else {
    for (let index = 0; index < averages.length - 1; index += 1) {
      const upper = averages[index]
      const lower = averages[index + 1]
      if (close <= upper[1] && close >= lower[1]) {
        zone = `介於 ${upper[0]} 與 ${lower[0]} 間`
        break
      }
    }
  }

  const bought = String(diff).trim().startsWith('+')
  let bias = '觀察'
  if (bought && close < ma20) {
    bias = '法人買且低於月線，積極候選'
  } else if (bought && close > ma5 && close > ma10 && close > ma20 && close > ma60) {
    bias = '法人買但高於所有均線，降低資金'
  } else if (bought) {
    bias = '法人買，分批觀察'
  } else if (close < ma20) {
    bias = '低於月線，等籌碼確認'
  }

  return [
    `收盤 ${formatNumber(close)}，${zone}`,
    `MA5 ${formatNumber(ma5)} / MA10 ${formatNumber(ma10)} / MA20 ${formatNumber(ma20)} / MA60 ${formatNumber(ma60)}`,
    bias,
  ].join('；')
}

const normalizeEtfSection = (section, techByCode) => {
  const lines = section.split('\n')
  const tableStart = lines.findIndex((line) => line.trim().startsWith('|'))
  if (tableStart < 0 || tableStart + 2 >= lines.length) return section

  let tableEnd = tableStart
  while (tableEnd < lines.length && lines[tableEnd].trim().startsWith('|')) {
    tableEnd += 1
  }

  const originalHeaders = parseTableRow(lines[tableStart])
  const rowLines = lines.slice(tableStart + 2, tableEnd)
  const normalizedRows = rowLines
    .map((line) => buildRowMap(originalHeaders, parseTableRow(line)))
    .filter((row) => row.code && row.name)
    .filter((row) => techByCode.size === 0 || techByCode.has(row.code))
    .map((row) => {
      const tech = techByCode.get(row.code)?.technical
      const position = classifyPosition(tech, row.diff || '')
      return `| ${[row.code, row.name, row.diff, row.supplyChain, row.interpretation, position].join(' | ')} |`
    })

  const normalizedTable = [
    `| ${tableHeaders.join(' | ')} |`,
    '| --- | --- | ---: | --- | --- | --- |',
    ...normalizedRows,
  ]

  return lines
    .map((line) => (line.startsWith('股票數：') ? `股票數：${normalizedRows.length}` : line))
    .slice(0, tableStart)
    .concat(normalizedTable, lines.slice(tableEnd))
    .join('\n')
}

const normalizeMovingAverageSection = (markdown, date) => {
  const replacement = `## 均線位階判讀

均線資料已由 \`backend-futures-py/stockTech/${date}.json\` 回填到上方 ETF 持有交集表格。

判讀方式：

- 法人買且收盤價低於 MA20（月線）：優先列入積極候選，因為籌碼正在買、價格還沒有站回月線，資金效率通常比追高更好。
- 法人買且收盤價高於所有均線：趨勢很強，但已經偏熱，採降低資金或等拉回 MA5 / MA10 的方式處理。
- 法人買且收盤價在 MA5、MA10、MA20 之間：趨勢仍可觀察，適合分批或等量價轉強。
- 收盤價低於所有均線：先看成弱勢修復，不因單日 ETF 買超就重押。
- 法人減碼且低於 MA20：除非有基本面或籌碼反轉，先放在風險觀察。

## 明天要補的資料`

  if (/## 均線位階判讀[\s\S]*?## 明天要補的資料/.test(markdown)) {
    return markdown.replace(/## 均線位階判讀[\s\S]*?## 明天要補的資料/, replacement)
  }
  return `${markdown.trim()}\n\n${replacement}\n`
}

const normalizeReport = () => {
  const date = resolveReportDate()
  if (!date) throw new Error('Unable to resolve report date')

  const reportPath = path.join(reportDir, `${date}.md`)
  const stockTechPath = path.join(stockTechDir, `${date}.json`)
  if (!fs.existsSync(reportPath)) throw new Error(`Report not found: ${reportPath}`)

  const stockTech = readJson(stockTechPath, { data: [] })
  const techByCode = new Map((Array.isArray(stockTech.data) ? stockTech.data : []).map((item) => [String(item.code), item]))
  const markdown = fs.readFileSync(reportPath, 'utf8')
  const sectionMatch = markdown.match(/## ETF 持有交集[\s\S]*?(?=\n## |\n?$)/)
  if (!sectionMatch) throw new Error('Missing ETF 持有交集 section')

  let output = markdown.replace(sectionMatch[0], normalizeEtfSection(sectionMatch[0], techByCode))
  output = normalizeMovingAverageSection(output, date)
  output = output.replace(/- 每檔最新收盤價。\n- 5 日、10 日、20 日、60 日均線位階。\n/g, '')

  fs.writeFileSync(reportPath, `${output.trim()}\n`)
  console.log(`Normalized ${reportPath}`)
  console.log(`Stock tech rows: ${techByCode.size}`)
}

normalizeReport()
