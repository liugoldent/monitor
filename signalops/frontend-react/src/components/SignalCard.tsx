import type { SignalEvent } from '../types'

type SignalCardProps = {
  event: SignalEvent
}

const actionLabels = {
  enter: '進場',
  exit: '出場',
  reverse: '反轉',
}

function positionLabel(position: SignalEvent['new_position']) {
  if (position === 1) return '多單'
  if (position === -1) return '空單'
  return '空手'
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-TW', {
    dateStyle: 'medium',
    timeStyle: 'medium',
    timeZone: 'Asia/Taipei',
  }).format(new Date(value))
}

export function SignalCard({ event }: SignalCardProps) {
  const latencySeconds = Math.max(
    0,
    Math.round((Date.parse(event.received_at) - Date.parse(event.occurred_at)) / 1000),
  )

  return (
    <article className={`signal-card ${event.side}`}>
      <div className="signal-card-topline">
        <span className={`action-pill ${event.action}`}>{actionLabels[event.action]}</span>
        <span className="event-source">{event.source === 'legacy_csv' ? '舊版 CSV' : event.source}</span>
      </div>

      <div>
        <h2>{event.strategy_name}</h2>
        <p className="strategy-code">{event.strategy_code}</p>
      </div>

      <div className="position-transition" aria-label="持倉轉換">
        <span>{positionLabel(event.previous_position)}</span>
        <b aria-hidden="true">→</b>
        <strong className={event.new_position === 0 ? '' : event.side}>
          {positionLabel(event.new_position)}
        </strong>
      </div>

      <dl className="signal-metadata">
        <div>
          <dt>商品</dt>
          <dd>{event.instrument}</dd>
        </div>
        <div>
          <dt>數量</dt>
          <dd>{Number(event.quantity).toLocaleString()}</dd>
        </div>
        <div>
          <dt>事件時間</dt>
          <dd>{formatTime(event.occurred_at)}</dd>
        </div>
        <div>
          <dt>接收延遲</dt>
          <dd>{latencySeconds} 秒</dd>
        </div>
      </dl>
    </article>
  )
}
