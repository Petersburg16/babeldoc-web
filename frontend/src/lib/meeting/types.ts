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
  /** 0.4.0 用翻译模型整理时留下的；新会议为 null */
  model_id: number | null;
  /** 整理方案的名称快照（老会议里是翻译模型名） */
  model_name: string;
  /** 整理方案；null 表示老会议（按默认方案处理） */
  llm_preset_id: number | null;
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
  /** 整理方案（只含启用的），默认方案 is_default 为 true */
  presets: PresetPublic[];
}

export interface PresetPublic {
  id: number;
  name: string;
  description: string;
  is_default: boolean;
}

export interface MeetingCreate {
  filename: string;
  size: number;
  title?: string;
  provider_id?: number | null;
  llm_preset_id?: number | null;
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

// ---------- 管理后台：会议用的大模型与整理方案 ----------

/** 思考强度（reasoning_effort）档位，从低到高 */
export type EffortLevel = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
/** 方案里选的思考强度：default 表示不发送，由模型自己决定 */
export type EffortChoice = 'default' | EffortLevel;
export type LlmStep = 'speakers' | 'polish' | 'minutes' | 'chat';

export interface LlmModelAdmin {
  id: number;
  name: string;
  description: string;
  base_url: string;
  model: string;
  /** 这个模型接受的档位；空表示不发思考参数 */
  effort_levels: EffortLevel[];
  /** 按模型名查到的内置档位表（参考用） */
  builtin_effort_levels: EffortLevel[];
  qps: number;
  json_mode: boolean;
  /** 一次能放进多少字逐字稿；null 用系统设置 */
  context_chars: number | null;
  enabled: boolean;
  sort_order: number;
  api_key_set: boolean;
  api_key_masked: string;
  /** 用到它的方案名 */
  used_by: string[];
  updated_at: string;
}

export interface LlmModelIn {
  name: string;
  description?: string;
  base_url?: string;
  api_key?: string;
  /** 从翻译模型复制接口地址和 Key（在服务器端复制）；自己填了的以填的为准 */
  copy_from_model_id?: number | null;
  model: string;
  /** 不传则按模型名用内置档位表 */
  effort_levels?: EffortLevel[] | null;
  qps?: number;
  json_mode?: boolean;
  context_chars?: number | null;
  enabled?: boolean;
  sort_order?: number;
  /** 顺带建一个四个用途都用这个模型的方案 */
  create_preset?: boolean;
}

export type LlmModelPatch = Partial<Omit<LlmModelIn, 'create_preset'>> & { clear_api_key?: boolean };

/** 拉取模型列表、检测档位：Key 留空时用已保存的会议模型（model_id）或翻译模型（copy_from_model_id）的 Key */
export interface LlmProbeIn {
  base_url?: string;
  api_key?: string;
  model?: string;
  model_id?: number | null;
  copy_from_model_id?: number | null;
}

export interface EffortDetect {
  detected: EffortLevel[];
  builtin: EffortLevel[];
  suggested: EffortLevel[];
  message: string;
}

export interface LlmTestResult {
  ok: boolean;
  latency_ms: number | null;
  reply: string | null;
  error: string | null;
  tokens: number;
  reasoning_tokens: number;
  finish_reason: string | null;
  /** 实际发出的参数（不含消息） */
  sent: Record<string, unknown>;
}

export interface Toggle<T extends number> {
  on: boolean;
  value: T;
}

export interface CustomParam {
  name: string;
  type: 'string' | 'number' | 'boolean' | 'json';
  /** 都按字符串存，发送时按类型解析 */
  value: string;
}

export interface StepConfig {
  model_id: number | null;
  effort: EffortChoice;
  temperature: Toggle<number>;
  top_p: Toggle<number>;
  max_tokens: Toggle<number>;
  /** 多久没收到数据算超时（秒，30–7200） */
  timeout_s: number;
  params: CustomParam[];
}

export type PresetSteps = Record<LlmStep, StepConfig>;

export interface PresetAdmin {
  id: number;
  name: string;
  description: string;
  steps: PresetSteps;
  is_default: boolean;
  enabled: boolean;
  sort_order: number;
  /** 例如某个用途的模型被停用 */
  problems: string[];
  updated_at: string;
}

export interface PresetIn {
  name: string;
  description?: string;
  steps: PresetSteps;
  is_default?: boolean;
  enabled?: boolean;
  sort_order?: number;
}
