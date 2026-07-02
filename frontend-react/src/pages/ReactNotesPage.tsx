const concepts = [
  {
    title: 'Render Model',
    body: 'State changes rerun component functions; React reconciles the next tree before committing DOM updates.',
  },
  {
    title: 'Derived Data',
    body: 'Visible candles, summary stats, and line paths are calculated from CSV rows with useMemo instead of copied into state.',
  },
  {
    title: 'Effects',
    body: 'CSV loading is synchronized with the browser fetch API in an effect, with AbortController cleanup on unmount.',
  },
  {
    title: 'Performance',
    body: 'The chart limits visible rows and memoizes domains, ticks, and indicator paths so dense OHLC data stays responsive.',
  },
]

export function ReactNotesPage() {
  return (
    <div className="notes-page">
      <section className="notes-intro">
        <h1>React Interview Notes</h1>
        <p>Trading UI implementation notes mapped to React fundamentals.</p>
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
