const taipeiTime = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/;

export function elapsedSeconds(time, now = Date.now()) {
  if (!taipeiTime.test(time ?? '')) return null;
  const value = Date.parse(time.replace(' ', 'T') + '+08:00');
  return Number.isFinite(value) ? Math.floor((now - value) / 1000) : null;
}

export function relativeTime(time, now = Date.now()) {
  const seconds = elapsedSeconds(time, now);
  if (seconds === null) return '時間未記錄';
  if (seconds < -60) return '時間尚未到';
  if (seconds < 60) return '剛剛';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分鐘前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小時 ${Math.floor(seconds % 3600 / 60)} 分鐘前`;
  return `${Math.floor(seconds / 86400)} 天 ${Math.floor(seconds % 86400 / 3600)} 小時前`;
}
