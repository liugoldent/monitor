const concepts = [
  {
    title: '渲染模型',
    body: 'State 改變後會重新執行 component function；React 比較下一棵 tree 後才提交 DOM 更新。',
  },
  {
    title: '衍生資料',
    body: '畫面 K 線、摘要統計與指標路徑由 CSV 搭配 useMemo 計算，不會重複複製進 state。',
  },
  {
    title: '副作用',
    body: 'CSV 載入在 effect 中與瀏覽器 fetch 同步，unmount 時用 AbortController 完成清理。',
  },
  {
    title: '效能',
    body: '圖表限制可見筆數並快取 domain、刻度與指標路徑，讓密集 OHLC 資料維持順暢。',
  },
]

export function ReactNotesPage() {
  return (
    <div className="notes-page">
      <section className="notes-intro">
        <h1>React 工程筆記</h1>
        <p>把交易介面的實作決策對應到 React 核心觀念。</p>
      </section>

      <section className="concept-grid">
        {concepts.map((concept) => (
          <article key={concept.title}>
            <span>{concept.title}</span>
            <p>{concept.body}</p>
          </article>
        ))}
      </section>
    </div>
  )
}
