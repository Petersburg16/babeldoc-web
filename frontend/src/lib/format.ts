const STAGES: Record<string, string> = {
  'Check model API': '检查模型接口',
  'Parse PDF and Create Intermediate Representation': '解析 PDF 结构',
  DetectScannedFile: '检测扫描件',
  'Parse Page Layout': '分析页面版面',
  'Parse Table': '识别表格',
  'Parse Paragraphs': '识别段落',
  'Parse Formulas and Styles': '识别公式与样式',
  'Remove Char Descent': '校正字符',
  'Automatic Term Extraction': '提取术语',
  'Translate Paragraphs': '翻译段落',
  Typesetting: '重新排版',
  'Add Fonts': '嵌入字体',
  'Generate drawing instructions': '生成页面',
  'Subset font': '精简字体',
  'Save PDF': '保存 PDF',
  'Add Debug Information': '写入调试信息',
};

export function jobStageLabel(stage: string | null | undefined) {
  if (!stage) return '准备中';
  return STAGES[stage] ?? stage;
}

export function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function compact(n: number) {
  if (Math.abs(n) < 1000) return n.toLocaleString('zh-CN');
  if (Math.abs(n) < 10_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}K`;
  if (Math.abs(n) < 100_000_000) return `${(n / 10_000).toFixed(1).replace(/\.0$/, '')} 万`;
  return `${(n / 100_000_000).toFixed(2).replace(/\.?0+$/, '')} 亿`;
}

export function number(n: number) {
  return n.toLocaleString('zh-CN');
}

export function relativeTime(iso: string | null | undefined) {
  if (!iso) return '';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 45) return '刚刚';
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)} 天前`;
  return dateTime(iso);
}

export function dateTime(iso: string | null | undefined) {
  if (!iso) return '—';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return `${sameYear ? '' : `${d.getFullYear()}-`}${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function duration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}

export const DAY = 86_400_000;

export function expiryLabel(ms: number) {
  if (ms <= 0) return '即将自动删除';
  const hours = ms / 3_600_000;
  if (hours < 1) return '1 小时内自动删除';
  // 临近删除时往少里说，免得用户以为还来得及
  if (hours < 48) return `${Math.floor(hours)} 小时后自动删除`;
  const days = hours / 24;
  return `${days < 3 ? Math.floor(days) : Math.round(days)} 天后自动删除`;
}

export function elapsedSince(iso: string | null | undefined, now: number) {
  if (!iso) return null;
  return (now - new Date(iso).getTime()) / 1000;
}

export function greeting() {
  const h = new Date().getHours();
  if (h < 6) return '夜深了';
  if (h < 11) return '早上好';
  if (h < 13) return '中午好';
  if (h < 18) return '下午好';
  return '晚上好';
}

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  }
}
