// 会议记录的接口类型，和 backend/app/meeting/schemas.py 一一对应

export type MeetingStatus =
  | 'uploading'
  | 'queued'
  | 'transcoding'
  | 'transcribing'
  | 'processing'
  | 'done'
  | 'failed'
  | 'canceled';

/** 逐字稿整理状态：none 还没有逐字稿；raw 未整理；polishing 整理中；polished 已整理；partial 部分整理失败、保留了原文 */
export type TranscriptState = 'none' | 'raw' | 'polishing' | 'polished' | 'partial';
export type MinutesState = 'none' | 'generating' | 'ready' | 'failed';
export type MeetingOp = 'speakers' | 'polish' | 'minutes';

export interface SpeakerGuess {
  name: string;
  evidence: string;
  confidence: 'high' | 'medium' | 'low';
}

export interface SpeakerInfo {
  /** 用户确认过的名字；空字符串表示还没命名，显示成“说话人 N” */
  name: string;
  /** 大模型根据称呼、自我介绍猜的名字，等用户采纳 */
  guess: SpeakerGuess | null;
  /** 被合并到哪位说话人（合并后这位的发言都归到目标名下） */
  merged_into: string | null;
  /** 切段对齐后，大模型提示“可能和这位是同一人” */
  merge_hint?: { with: string; reason: string } | null;
}

export interface MeetingPart {
  index: number;
  state: 'pending' | 'submitting' | 'submitted' | 'done' | 'failed';
  offset_ms: number;
  duration_ms: number;
}

export interface Meeting {
  id: string;
  title: string;
  status: MeetingStatus;
  /** 阶段代码：transcode、split、asr_submit、asr_wait、asr_merge、speakers、polish、minutes */
  stage: string;
  progress: number;
  transcript_state: TranscriptState;
  minutes_state: MinutesState;
  /** 逐字稿内容改过、纪要还是旧的 */
  minutes_stale: boolean;
  /** 逐字稿版本，变了就重新拉取逐字稿 */
  transcript_rev: number;
  op: MeetingOp | null;
  error: string | null;
  /** auth / quota / input / provider / config / internal */
  error_kind: string | null;
  warning: string | null;
  filename: string;
  file_size: number;
  duration_ms: number;
  language: string;
  provider_id: number | null;
  provider_kind: string;
  provider_name: string;
  model_id: number | null;
  model_name: string;
  template: string;
  extra_instructions: string;
  expected_speakers: number | null;
  asr_seconds: number;
  speakers: Record<string, SpeakerInfo>;
  parts: MeetingPart[];
  tokens: number;
  audio_available: boolean;
  audio_expires_at: string | null;
  minutes_template: string | null;
  minutes_at: string | null;
  created_at: string;
  /** 服务器每次写库都会更新；用来丢掉晚到的旧数据（接口返回值和事件流可能乱序） */
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface MeetingDetail extends Meeting {
  /** 纪要 Markdown；说话人写成占位符 [[S3]]，时间戳写成 [hh:mm:ss] 或 [mm:ss] */
  minutes_md: string | null;
}

export interface MeetingPage {
  items: Meeting[];
  total: number;
}

export interface Segment {
  idx: number;
  start_ms: number;
  end_ms: number;
  speaker: string;
  asr_speaker: string;
  text: string;
  raw_text: string;
  edited: boolean;
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface ProviderPublic {
  id: number;
  name: string;
  kind: string;
  label: string;
  description: string;
  is_default: boolean;
  max_part_seconds: number;
  hotwords: boolean;
  speaker_count: boolean;
}

export interface MinutesTemplate {
  id: string;
  name: string;
  description: string;
}

export interface MeetingOptions {
  providers: ProviderPublic[];
  templates: MinutesTemplate[];
  default_template: string;
  max_audio_upload_mb: number;
  max_audio_hours: number;
  retention_days: number;
  /** 超过服务单次上限的录音能否自动切段；不能时上传前就要拦下 */
  split_supported: boolean;
}

export interface MeetingCreate {
  filename: string;
  size: number;
  title?: string;
  provider_id?: number | null;
  model_id?: number | null;
  template?: string;
  extra_instructions?: string;
  expected_speakers?: number | null;
  language?: 'zh' | 'en' | 'auto';
}

export interface MeetingUpload {
  meeting: Meeting;
  part_size: number;
  parts: number;
}

export type ExportFormat = 'docx' | 'md' | 'txt' | 'srt';
export type ExportContent = 'minutes' | 'transcript' | 'both';

// ---------- 管理后台 ----------

export interface FieldSpec {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  default: string;
  placeholder: string;
  hint: string;
  /** 非空时渲染成下拉框：[值, 显示名] */
  options: [string, string][];
}

export interface ProviderKind {
  kind: string;
  label: string;
  description: string;
  max_part_seconds: number;
  hotwords: boolean;
  speaker_count: boolean;
  fields: FieldSpec[];
}

export interface ProviderAdmin {
  id: number;
  kind: string;
  kind_label: string;
  name: string;
  description: string;
  config: Record<string, string>;
  secrets_set: Record<string, boolean>;
  secrets_masked: Record<string, string>;
  enabled: boolean;
  is_default: boolean;
  sort_order: number;
  updated_at: string;
}

export interface ProviderCheck {
  ok: boolean;
  message: string;
}

export interface ProviderTestStep {
  name: string;
  ok: boolean;
  message: string;
}

/** 事件流里的 asr_test：后台“完整测试”的进度和结果 */
export interface ProviderTestEvent {
  provider_id: number;
  done: boolean;
  ok?: boolean;
  steps: ProviderTestStep[];
}

export interface GlossaryTerm {
  id: number;
  term: string;
  wrong_forms: string[];
  note: string;
  updated_at: string;
}
