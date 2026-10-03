export type Role = 'admin' | 'user';
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled';
export type FileKind = 'dual' | 'mono' | 'glossary' | 'original';

export interface Language {
  code: string;
  label: string;
  native: string;
}

export interface Meta {
  site_name: string;
  announcement: string;
  registration: 'invite' | 'open' | 'closed';
  needs_setup: boolean;
  languages: Language[];
  default_lang_in: string;
  default_lang_out: string;
  max_upload_mb: number;
  max_pages_per_job: number;
  max_files: number;
  file_retention_days: number;
  engine: string;
  version: string;
}

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: Role;
}

export interface Usage {
  month_pages: number;
  quota: number;
  month_tokens: number;
  total_jobs: number;
}

export interface Me {
  user: User;
  usage: Usage;
}

export interface JobOptions {
  lang_in: string;
  lang_out: string;
  model_id: number | null;
  term_model_id: number | null;
  pages: string | null;
  output: 'both' | 'dual' | 'mono';
  dual_mode: 'side_by_side' | 'alternating';
  dual_translate_first: boolean;
  translate_table_text: boolean;
  enhance_compatibility: boolean;
  ocr_workaround: boolean;
  auto_enable_ocr_workaround: boolean;
  skip_scanned_detection: boolean;
  primary_font_family: 'serif' | 'sans-serif' | 'script' | null;
  only_include_translated_page: boolean;
  auto_extract_glossary: boolean;
  custom_system_prompt: string | null;
}

export interface Job {
  id: string;
  status: JobStatus;
  filename: string;
  file_size: number;
  page_count: number;
  pages: string | null;
  billed_pages: number;
  lang_in: string;
  lang_out: string;
  model_name: string;
  options: Partial<JobOptions> & { term_model_name?: string };
  progress: number;
  stage: string;
  error: string | null;
  error_kind: string | null;
  warning: string | null;
  files: FileKind[];
  stats: { seconds?: number; total_tokens?: number; valid_chars?: number } | null;
  tokens: number;
  attempts: number;
  queue_position: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  files_purged: boolean;
  username?: string | null;
}

export interface JobPage {
  items: Job[];
  total: number;
}

export interface LiveProgress {
  progress: number;
  stage: string;
  current: number | null;
  total: number | null;
  part: number | null;
  parts: number | null;
}

export interface ModelPublic {
  id: number;
  name: string;
  description: string;
  is_default: boolean;
}

export interface ModelAdmin extends ModelPublic {
  base_url: string;
  model: string;
  term_model: string | null;
  qps: number;
  pool_max_workers: number | null;
  send_temperature: boolean;
  json_mode: boolean;
  thinking: 'enabled' | 'disabled' | null;
  reasoning: string | null;
  enabled: boolean;
  sort_order: number;
  api_key_set: boolean;
  api_key_masked: string;
  updated_at: string;
}

export interface ModelTest {
  ok: boolean;
  latency_ms: number | null;
  status: number | null;
  reply: string | null;
  error: string | null;
}

export interface Invite {
  id: number;
  code: string;
  note: string;
  max_uses: number;
  used_count: number;
  expires_at: string | null;
  revoked: boolean;
  created_at: string;
  state: 'active' | 'used' | 'expired' | 'revoked';
}

export interface AdminUser {
  id: number;
  username: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  page_quota: number | null;
  effective_quota: number;
  note: string;
  month_pages: number;
  total_jobs: number;
  created_at: string;
  last_login_at: string | null;
}

export interface SystemSettings {
  site_name: string;
  announcement: string;
  registration: 'invite' | 'open' | 'closed';
  max_concurrent_jobs: number;
  max_upload_mb: number;
  max_pages_per_job: number;
  max_active_jobs_per_user: number;
  default_page_quota: number;
  file_retention_days: number;
  default_lang_in: string;
  default_lang_out: string;
  watermark_mode: 'no_watermark' | 'watermarked' | 'both';
}

export interface DailyPoint {
  day: string;
  jobs: number;
  succeeded: number;
  failed: number;
  pages: number;
  tokens: number;
}

export interface Stats {
  jobs: {
    by_status: Partial<Record<JobStatus, number>>;
    today: number;
    month: number;
    month_pages: number;
    month_tokens: number;
  };
  users: { total: number; active: number };
  daily: DailyPoint[];
  top_users: { username: string; display_name: string; pages: number; jobs: number }[];
  running: {
    id: string;
    filename: string;
    username: string;
    progress: number;
    stage: string;
    billed_pages: number;
    started_at: string | null;
  }[];
  queued: { id: string; filename: string; username: string; billed_pages: number; queued_at: string }[];
  failures: { id: string; filename: string; username: string; error: string | null; finished_at: string | null }[];
  engine: { mode: string; version: string; max_concurrent: number; subscribers: number };
  system: {
    cpu_percent: number;
    cpu_count: number;
    load: number[] | null;
    memory_total: number;
    memory_used: number;
    disk_total: number;
    disk_free: number;
    data_size: number;
  };
}
