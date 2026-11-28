import React from 'react';
import { RefreshCw } from 'lucide-react';

export default function LoadingState({ label }) {
  return <section className="initial-loading" role="status" aria-live="polite">
    <RefreshCw size={30} aria-hidden="true" />
    <strong>{label}</strong>
    <span>正在從 MongoDB 讀取，請稍候。</span>
  </section>;
}
