import { type FormEvent, useState } from 'react'
import { useAssistant } from '../hooks/useAssistant'

const suggestions = [
  '目前的營運 BI 與資料品質如何？',
  '目前有哪些策略持倉？',
  '最近有哪些反轉訊號？',
  '整理最近五筆事件',
]

export function AssistantPage() {
  const [question, setQuestion] = useState(suggestions[0])
  const { status, ask } = useAssistant()

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const normalized = question.trim()
    if (normalized.length >= 2 && status.state !== 'streaming') void ask(normalized)
  }

  return (
    <div className="assistant-page">
      <header className="assistant-header">
        <div className="eyebrow-row">
          <span>唯讀工具呼叫</span>
          <span className="read-only-badge">不能下單</span>
        </div>
        <h1>策略小助手</h1>
        <p>
          數字問題由資料庫工具回答；設定 OpenAI API key 後使用 Responses API，沒有 key
          時仍可使用本機 deterministic 模式。
        </p>
      </header>

      <div className="assistant-layout">
        <form className="assistant-form" onSubmit={handleSubmit}>
          <label htmlFor="assistant-question">想了解什麼？</label>
          <textarea
            id="assistant-question"
            maxLength={1000}
            onChange={(event) => setQuestion(event.target.value)}
            rows={6}
            value={question}
          />
          <div className="suggestion-list">
            {suggestions.map((suggestion) => (
              <button key={suggestion} onClick={() => setQuestion(suggestion)} type="button">
                {suggestion}
              </button>
            ))}
          </div>
          <button
            className="primary-button assistant-submit"
            disabled={status.state === 'streaming' || question.trim().length < 2}
            type="submit"
          >
            {status.state === 'streaming' ? '正在查詢唯讀工具……' : '詢問小助手'}
          </button>
        </form>

        <section className="assistant-answer" aria-live="polite">
          <div className="assistant-answer-heading">
            <strong>回答</strong>
            {status.mode && <span>{status.mode}</span>}
          </div>
          {status.state === 'idle' ? (
            <p className="assistant-placeholder">回答會以 SSE 串流顯示在這裡，並附上資料引用。</p>
          ) : (
            <div className="assistant-response-text">{status.text}</div>
          )}
          {status.state === 'error' && <p className="assistant-error">{status.message}</p>}
          {status.citations.length > 0 && (
            <div className="citation-list">
              <strong>資料引用</strong>
              {status.citations.map((citation) => (
                <a href={citation.href} key={citation.id}>
                  {citation.label}
                </a>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
