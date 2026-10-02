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

const PYDANTIC_MESSAGES: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/String should have at least (\d+) characters?/, (m) => `至少需要 ${m[1]} 个字符`],
  [/String should have at most (\d+) characters?/, (m) => `最多 ${m[1]} 个字符`],
  [/Input should be greater than or equal to (\d+)/, (m) => `不能小于 ${m[1]}`],
  [/Input should be less than or equal to (\d+)/, (m) => `不能大于 ${m[1]}`],
  [/Field required/, () => '必填'],
  [/Input should be a valid integer/, () => '需要整数'],
];

const FIELD_NAMES: Record<string, string> = {
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

function describeDetail(detail: unknown): string {
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

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'X-Requested-With': 'babeldoc-web' };
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

function qs(params: Record<string, string | number | null | undefined>) {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') search.set(k, String(v));
  const s = search.toString();
  return s ? `?${s}` : '';
}

export function uploadJobs(form: FormData, onProgress: (ratio: number) => void): Promise<Job[]> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs');
    xhr.setRequestHeader('X-Requested-With', 'babeldoc-web');
    xhr.responseType = 'json';
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as Job[]);
      else {
        if (xhr.status === 401) unauthorizedHandler?.();
        const msg = describeDetail(xhr.response?.detail) || `上传失败（${xhr.status}）`;
        reject(new ApiError(xhr.status === 413 && !xhr.response ? '文件太大，超过了服务器限制' : msg, xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError('网络连接失败，上传中断', 0));
    xhr.send(form);
  });
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
