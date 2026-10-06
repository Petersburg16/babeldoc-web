import { expiryLabel } from '../format';
import type { LlmStep, Meeting, MeetingStatus, Segment, SpeakerInfo } from './types';

/** 毫秒 → 1:02:03 或 02:03 */
export function clock(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** 时长的口语说法：1 小时 20 分、35 分钟、45 秒 */
export function spoken(ms: number) {
  const total = Math.round(ms / 1000);
  if (total < 60) return `${total} 秒`;
  if (total < 600) {
    const s = total % 60;
    return s ? `${Math.floor(total / 60)} 分 ${s} 秒` : `${total / 60} 分钟`;
  }
  const minutes = Math.round(total / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m} 分钟`;
  return m ? `${h} 小时 ${m} 分` : `${h} 小时`;
}

/** 录音保留期：录音 3 天后自动删除、录音即将自动删除（卡片和详情页共用） */
export function audioExpiryText(ms: number) {
  const label = expiryLabel(ms);
  return /^\d/.test(label) ? `录音 ${label}` : `录音${label}`;
}

/** 解析 [1:02:03]、[02:03] 这样的时间戳，返回毫秒；不合法返回 null */
export function parseClock(text: string): number | null {
  const parts = text.split(':').map((p) => Number(p));
  if (parts.length < 2 || parts.length > 3 || parts.some((p) => !Number.isFinite(p) || p < 0)) return null;
  const [h, m, s] = parts.length === 3 ? parts : [0, ...parts];
  if (m >= 60 || s >= 60) return null;
  return ((h * 60 + m) * 60 + s) * 1000;
}

/** 纪要和对话里的说话人占位符：[[S3]] */
export const SPEAKER_PLACEHOLDER = /\[\[(S\d+)\]\]/g;

/** 沿着合并关系找到最终的说话人编号 */
export function resolveSpeaker(speakers: Record<string, SpeakerInfo>, id: string) {
  let current = id;
  for (let i = 0; i < 20; i++) {
    const next = speakers[current]?.merged_into;
    if (!next || next === current) break;
    current = next;
  }
  return current;
}

/** 带缓存的 resolveSpeaker，逐句遍历逐字稿时用：同一个编号只沿合并关系找一次 */
export function speakerResolver(speakers: Record<string, SpeakerInfo>) {
  const cache = new Map<string, string>();
  return (id: string) => {
    let who = cache.get(id);
    if (who === undefined) {
      who = resolveSpeaker(speakers, id);
      cache.set(id, who);
    }
    return who;
  };
}

/** 说话人编号：S3 → 3，不是数字时为 0 */
function speakerNumber(id: string) {
  return Number(id.replace(/^S/, '')) || 0;
}

/** 说话人表和逐字稿里出现过的全部说话人（含已合并掉的），按编号排序 */
export function speakerIds(speakers: Record<string, SpeakerInfo>, segments: Segment[]) {
  const all = new Set(Object.keys(speakers));
  for (const seg of segments) all.add(seg.speaker);
  return [...all].sort((a, b) => speakerNumber(a) - speakerNumber(b));
}

/** 说话人显示名：已命名用名字，否则“说话人 N” */
export function speakerName(speakers: Record<string, SpeakerInfo>, id: string) {
  const final = resolveSpeaker(speakers, id);
  const name = speakers[final]?.name?.trim();
  return name || `说话人 ${final.replace(/^S/, '')}`;
}

/** 把文本里的 [[S3]] 换成显示名 */
export function fillSpeakers(text: string, speakers: Record<string, SpeakerInfo>) {
  return text.replace(SPEAKER_PLACEHOLDER, (_, id: string) => speakerName(speakers, id));
}

/** 说话人配色（循环使用），用于说话人标签和逐字稿左侧色条 */
const SPEAKER_TONES = [
  'bg-sky-500',
  'bg-amber-500',
  'bg-emerald-500',
  'bg-rose-500',
  'bg-violet-500',
  'bg-teal-500',
  'bg-orange-500',
  'bg-fuchsia-500',
];

export function speakerTone(id: string) {
  const n = speakerNumber(id) || 1;
  return SPEAKER_TONES[(n - 1) % SPEAKER_TONES.length];
}

/** 大模型的四个用途；前三个也是会议的处理阶段和可以单独重跑的操作，进度、整理方案、操作提示都用这一份中文名 */
export const STEP_LABELS: Record<LlmStep, string> = {
  speakers: '识别说话人',
  polish: '整理逐字稿',
  minutes: '生成纪要',
  chat: '对话问答',
};

const STAGES: Record<string, string> = {
  upload: '上传中',
  transcode: '转换音频格式',
  split: '切分长录音',
  asr_submit: '提交给识别服务',
  asr_wait: '语音识别中',
  asr_merge: '合并识别结果',
  speakers: STEP_LABELS.speakers,
  polish: STEP_LABELS.polish,
  minutes: STEP_LABELS.minutes,
};

export function meetingStageLabel(stage: string) {
  return STAGES[stage] ?? stage;
}

const STATUS: Record<MeetingStatus, string> = {
  uploading: '上传中',
  queued: '排队中',
  transcoding: '处理中',
  transcribing: '识别中',
  processing: '整理中',
  done: '已完成',
  failed: '失败',
  canceled: '已取消',
};

export function statusLabel(status: MeetingStatus) {
  return STATUS[status] ?? status;
}

export function isActive(m: Pick<Meeting, 'status'>) {
  return ['uploading', 'queued', 'transcoding', 'transcribing', 'processing'].includes(m.status);
}

const ERROR_KINDS: Record<string, string> = {
  auth: '识别服务的密钥无效，请联系管理员',
  quota: '识别服务额度不足或请求太频繁',
  config: '识别服务配置有问题，请联系管理员',
  input: '录音文件有问题',
  provider: '识别服务出错',
  internal: '内部错误',
};

export function errorKindLabel(kind: string | null) {
  return kind ? (ERROR_KINDS[kind] ?? kind) : '';
}

/** 与后端一致：每条术语最多 20 个听错写法，每个最多 64 字 */
export const MAX_WRONG_FORMS = 20;

/** 拆开输入的听错写法：逗号、顿号、分号或换行都算分隔，去掉首尾空白和空项，每个截到 64 字 */
export function splitWrongForms(text: string) {
  return text
    .split(/[,，、;；\n]/)
    .map((s) => s.trim().slice(0, 64))
    .filter(Boolean);
}

export const AUDIO_ACCEPT =
  '.mp3,.m4a,.wav,.aac,.flac,.ogg,.oga,.opus,.wma,.amr,.aiff,.aif,.caf,.webm,.mp4,.m4v,.mov,.mkv,.avi,.3gp,.mpeg,.mpg,.ts,.flv,.wmv,audio/*,video/*';
