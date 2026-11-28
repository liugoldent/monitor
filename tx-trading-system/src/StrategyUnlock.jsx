import React, { useState } from 'react';
import { LockKeyhole } from 'lucide-react';

export default function StrategyUnlock({ message = '' }) {
  const [code, setCode] = useState('');
  const [error, setError] = useState(message);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const response = await fetch('/api/strategy-login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '解鎖失敗，請稍後再試');
      setCode('');
      window.dispatchEvent(new Event('strategy-unlocked'));
    } catch (failure) { setError(failure.message); }
    finally { setSubmitting(false); }
  }

  return <section className="strategy-unlock" aria-label="解鎖策略資料">
    <LockKeyhole size={23} aria-hidden="true" />
    <div><strong>策略資料已上鎖</strong><p>輸入存取碼後，才能查看 h2／h3 與策略進出場資料。</p></div>
    <form onSubmit={submit}>
      <input type="password" aria-label="策略存取碼" autoComplete="off" value={code} onChange={event => setCode(event.target.value)} placeholder="策略存取碼" maxLength={128} required />
      <button type="submit" disabled={submitting}>{submitting ? '驗證中…' : '解鎖'}</button>
    </form>
    {error && <p className="strategy-unlock-error" role="alert">{error}</p>}
  </section>;
}
