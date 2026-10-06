import type {
  AdminUser,
  Invite,
  Job,
  JobPage,
  Me,
  Meta,
  ModelAdmin,
  ModelPublic,
  ModelTest,
  Stats,
  SystemSettings,
} from './types';

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function onUnauthorized(handler: () => void) {
  unauthorizedHandler = handler;
}

/** 给不经过 request() / xhrSend() 的请求（流式对话）用：遇到 401 时同样回到登录页 */
export function notifyUnauthorized() {
  unauthorizedHandler?.();
}

const PYDANTIC_MESSAGES: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/String should have at least (\d+) characters?/, (m) => `至少需要 ${m[1]} 个字符`],
  [/String should have at most (\d+) characters?/, (m) => `最多 ${m[1]} 个字符`],
  [/Input should be greater than or equal to (\d+)/, (m) => `不能小于 ${m[1]}`],
  [/Input should be less than or equal to (\d+)/, (m) => `不能大于 ${m[1]}`],
  [/Field required/, () => '必填'],
  [/Input should be a valid integer/, () => '需要整数'],
];

const FIELD_NAMES: Record<string, string> = {
  term: '术语',
  title: '标题',
  description: '说明',
  sort_order: '排序',
  public_base_url: '站点公网地址',
  max_audio_upload_mb: '单个录音文件上限',
  max_audio_hours: '录音时长上限',
  meeting_context_chars: '大模型上下文预算',
  username: '用户名',
  password: '密码',
  new_password: '新密码',
  old_password: '当前密码',
  display_name: '显示名称',
  name: '名称',
  model: '模型名',
  base_url: '接口地址',
  site_name: '站点名称',
};

export function describeDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string; loc?: (string | number)[] };
    let msg = (first.msg ?? '').replace(/^Value error, /, '');
    for (const [pattern, render] of PYDANTIC_MESSAGES) {
      const m = msg.match(pattern);
      if (m) {
        msg = render(m);
        break;
      }
    }
    const field = first.loc?.[first.loc.length - 1];
    const label = typeof field === 'string' ? FIELD_NAMES[field] : undefined;
    return label ? `${label}：${msg}` : msg;
  }
  return '';
}

/** 写操作必须带的头（后端 GuardMiddleware 检查它）；所有请求都从这里取，不要另写字面量 */
export const GUARD_HEADERS: Readonly<Record<string, string>> = { 'X-Requested-With': 'babeldoc-web' };

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { ...GUARD_HEADERS };
  const init: RequestInit = { method, headers, credentials: 'same-origin' };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError('网络连接失败，请检查网络后重试', 0);
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && !url.startsWith('/api/auth/')) unauthorizedHandler?.();
    throw new ApiError(describeDetail(data?.detail) || `请求失败（${res.status}）`, res.status);
  }
  return data as T;
}

const get = <T>(url: string) => request<T>('GET', url);
const post = <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {});
const patch = <T>(url: string, body: unknown) => request<T>('PATCH', url, body);
const put = <T>(url: string, body: unknown) => request<T>('PUT', url, body);
const del = (url: string) => request<void>('DELETE', url);

/** 各功能模块的接口封装共用这几个请求函数 */
export const http = { get, post, patch, put, del };

/** 拼查询串：跳过空值，没有参数时返回空字符串 */
export function qs(params: Record<string, string | number | null | undefined>) {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') search.set(k, String(v));
  const s = search.toString();
  return s ? `?${s}` : '';
}

interface XhrOptions {
  /** 额外的请求头（防跨站头总会带上） */
  headers?: Record<string, string>;
  onProgress?: (e: ProgressEvent) => void;
  signal?: AbortSignal;
  /** 网络错误时的提示 */
  networkError: string;
  /** 按状态码和响应正文给出专门的提示；返回空时用接口的错误说明或“上传失败（状态码）” */
  errorMessage?: (status: number, data: unknown) => string | undefined;
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null; // 没有 JSON 正文
  }
}

/**
 * 需要上传进度的请求用 XHR 发：同样带防跨站头、遇到 401 回登录页、用 describeDetail 解析错误。
 * 成功时返回解析后的 JSON 正文（没有则为 null）；传了 signal 才能中途取消，取消时抛 AbortError。
 */
export function xhrSend(method: string, url: string, body: XMLHttpRequestBodyInit, options: XhrOptions) {
  const { signal } = options;
  return new Promise<unknown>((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('已取消', 'AbortError'));
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    for (const [k, v] of Object.entries({ ...GUARD_HEADERS, ...options.headers })) xhr.setRequestHeader(k, v);
    if (options.onProgress) xhr.upload.onprogress = options.onProgress;
    xhr.onload = () => {
      const data = parseJson(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
      if (xhr.status === 401) unauthorizedHandler?.();
      const detail = (data as { detail?: unknown } | null)?.detail;
      const message = options.errorMessage?.(xhr.status, data) || describeDetail(detail) || `上传失败（${xhr.status}）`;
      reject(new ApiError(message, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError(options.networkError, 0));
    if (signal) {
      xhr.onabort = () => reject(new DOMException('已取消', 'AbortError'));
      signal.addEventListener('abort', () => xhr.abort(), { once: true });
    }
    xhr.send(body);
  });
}

export async function uploadJobs(form: FormData, onProgress: (ratio: number) => void): Promise<Job[]> {
  const created = await xhrSend('POST', '/api/jobs', form, {
    onProgress: (e) => e.lengthComputable && onProgress(e.loaded / e.total),
    networkError: '网络连接失败，上传中断',
    // 被代理层拦下的 413 没有 JSON 正文
    errorMessage: (status, data) => (status === 413 && !data ? '文件太大，超过了服务器限制' : undefined),
  });
  return created as Job[];
}

export const api = {
  meta: () => get<Meta>('/api/meta'),
  me: () => get<Me>('/api/auth/me'),
  login: (username: string, password: string) => post<Me>('/api/auth/login', { username, password }),
  register: (body: { username: string; password: string; display_name: string; invite_code: string }) =>
    post<Me>('/api/auth/register', body),
  logout: () => post<{ ok: boolean }>('/api/auth/logout'),
  updateProfile: (display_name: string) => patch<Me>('/api/auth/me', { display_name }),
  changePassword: (old_password: string, new_password: string) =>
    post<{ ok: boolean }>('/api/auth/password', { old_password, new_password }),

  models: () => get<ModelPublic[]>('/api/models'),
  jobs: (status: string, limit: number, offset: number) => get<JobPage>(`/api/jobs${qs({ status, limit, offset })}`),
  job: (id: string) => get<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
  retryJob: (id: string) => post<Job>(`/api/jobs/${id}/retry`),
  deleteJob: (id: string) => del(`/api/jobs/${id}`),

  admin: {
    stats: () => get<Stats>('/api/admin/stats'),
    users: () => get<AdminUser[]>('/api/admin/users'),
    createUser: (body: Record<string, unknown>) => post<AdminUser>('/api/admin/users', body),
    patchUser: (id: number, body: Record<string, unknown>) => patch<AdminUser>(`/api/admin/users/${id}`, body),
    resetPassword: (id: number) => post<{ password: string }>(`/api/admin/users/${id}/reset-password`),
    deleteUser: (id: number) => del(`/api/admin/users/${id}`),
    invites: () => get<Invite[]>('/api/admin/invites'),
    createInvite: (body: { note: string; max_uses: number; expires_in_days: number | null }) =>
      post<Invite>('/api/admin/invites', body),
    revokeInvite: (id: number) => post<Invite>(`/api/admin/invites/${id}/revoke`),
    deleteInvite: (id: number) => del(`/api/admin/invites/${id}`),
    models: () => get<ModelAdmin[]>('/api/admin/models'),
    createModel: (body: Record<string, unknown>) => post<ModelAdmin>('/api/admin/models', body),
    patchModel: (id: number, body: Record<string, unknown>) => patch<ModelAdmin>(`/api/admin/models/${id}`, body),
    deleteModel: (id: number) => del(`/api/admin/models/${id}`),
    testModel: (id: number) => post<ModelTest>(`/api/admin/models/${id}/test`),
    probeModels: (body: { base_url: string; api_key: string; model_id: number | null }) =>
      post<{ models: string[] }>('/api/admin/models/probe', body),
    jobs: (params: { status: string; q: string; limit: number; offset: number }) =>
      get<JobPage>(`/api/admin/jobs${qs(params)}`),
    cancelJob: (id: string) => post<{ ok: boolean }>(`/api/admin/jobs/${id}/cancel`),
    retryJob: (id: string) => post<{ ok: boolean }>(`/api/admin/jobs/${id}/retry`),
    deleteJob: (id: string) => del(`/api/admin/jobs/${id}`),
    jobLog: (id: string) => get<{ log: string }>(`/api/admin/jobs/${id}/log`),
    settings: () => get<SystemSettings>('/api/admin/settings'),
    saveSettings: (body: SystemSettings) => put<SystemSettings>('/api/admin/settings', body),
  },
};

export function fileUrl(job: Pick<Job, 'id'>, kind: string, inline = false) {
  return `/api/jobs/${job.id}/files/${kind}${inline ? '?inline=1' : ''}`;
}
