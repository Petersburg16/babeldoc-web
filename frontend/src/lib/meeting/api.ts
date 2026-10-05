import { ApiError, describeDetail, notifyUnauthorized, request } from '../api';
import type { ModelPublic } from '../types';
import type {
  ChatMessage,
  ExportContent,
  ExportFormat,
  GlossaryTerm,
  Meeting,
  MeetingCreate,
  MeetingDetail,
  MeetingOp,
  MeetingOptions,
  MeetingPage,
  MeetingUpload,
  ProviderAdmin,
  ProviderCheck,
  ProviderKind,
  Segment,
} from './types';

const get = <T>(url: string) => request<T>('GET', url);
const post = <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {});
const patch = <T>(url: string, body: unknown) => request<T>('PATCH', url, body);
const del = (url: string) => request<void>('DELETE', url);

export const meetingApi = {
  options: () => get<MeetingOptions>('/api/meetings/options'),
  models: () => get<ModelPublic[]>('/api/models'),
  list: (limit: number, offset: number) => get<MeetingPage>(`/api/meetings?limit=${limit}&offset=${offset}`),
  get: (id: string) => get<MeetingDetail>(`/api/meetings/${id}`),
  patch: (
    id: string,
    body: Partial<{ title: string; template: string; extra_instructions: string; model_id: number }>,
  ) => patch<MeetingDetail>(`/api/meetings/${id}`, body),
  cancel: (id: string) => post<Meeting>(`/api/meetings/${id}/cancel`),
  retry: (id: string) => post<Meeting>(`/api/meetings/${id}/retry`),
  remove: (id: string) => del(`/api/meetings/${id}`),
  segments: (id: string) => get<Segment[]>(`/api/meetings/${id}/segments`),

  // 编辑（M4）：改文字会让纪要标记为过期；改名、合并只换显示的名字，不需要重新生成纪要
  editSegment: (id: string, idx: number, body: { text?: string; speaker?: string }) =>
    patch<Segment>(`/api/meetings/${id}/segments/${idx}`, body),
  revertSegment: (id: string, idx: number) => post<Segment>(`/api/meetings/${id}/segments/${idx}/revert`),
  renameSpeaker: (id: string, speaker: string, name: string) =>
    post<MeetingDetail>(`/api/meetings/${id}/speakers/${speaker}/rename`, { name }),
  mergeSpeakers: (id: string, source: string, target: string) =>
    post<MeetingDetail>(`/api/meetings/${id}/speakers/merge`, { source, target }),
  unmergeSpeaker: (id: string, speaker: string) =>
    post<MeetingDetail>(`/api/meetings/${id}/speakers/unmerge`, { speaker }),
  /** 采纳大模型猜的名字；不传 speakers 表示全部采纳 */
  acceptGuesses: (id: string, speakers?: string[]) =>
    post<MeetingDetail>(`/api/meetings/${id}/speakers/accept-guesses`, { speakers: speakers ?? null }),
  /** 后台重跑：speakers 重新猜名字；polish 重新整理（不覆盖手动改过的句子）；minutes 重新生成纪要 */
  runOp: (id: string, op: MeetingOp, body: { template?: string; extra_instructions?: string } = {}) =>
    post<Meeting>(`/api/meetings/${id}/ops/${op}`, body),

  // 对话（M5）
  messages: (id: string) => get<ChatMessage[]>(`/api/meetings/${id}/messages`),
  clearMessages: (id: string) => del(`/api/meetings/${id}/messages`),

  admin: {
    kinds: () => get<ProviderKind[]>('/api/admin/asr/kinds'),
    providers: () => get<ProviderAdmin[]>('/api/admin/asr/providers'),
    createProvider: (body: Record<string, unknown>) => post<ProviderAdmin>('/api/admin/asr/providers', body),
    patchProvider: (id: number, body: Record<string, unknown>) =>
      patch<ProviderAdmin>(`/api/admin/asr/providers/${id}`, body),
    deleteProvider: (id: number) => del(`/api/admin/asr/providers/${id}`),
    /** 只检查密钥，几秒内返回 */
    checkProvider: (id: number) => post<ProviderCheck>(`/api/admin/asr/providers/${id}/check`),
    /** 完整测试：结果通过事件流的 asr_test 事件推送 */
    testProvider: (id: number) => post<{ started: boolean }>(`/api/admin/asr/providers/${id}/test`),
    glossary: () => get<GlossaryTerm[]>('/api/admin/glossary'),
    createTerm: (body: { term: string; wrong_forms: string[]; note: string }) =>
      post<GlossaryTerm>('/api/admin/glossary', body),
    patchTerm: (id: number, body: { term: string; wrong_forms: string[]; note: string }) =>
      patch<GlossaryTerm>(`/api/admin/glossary/${id}`, body),
    deleteTerm: (id: number) => del(`/api/admin/glossary/${id}`),
  },
};

export function audioUrl(id: string) {
  return `/api/meetings/${id}/audio`;
}

/** 导出（M7）：直接用作下载链接 */
export function exportUrl(id: string, format: ExportFormat, content: ExportContent = 'both') {
  return `/api/meetings/${id}/export?format=${format}&content=${content}`;
}

function putPart(url: string, blob: Blob, onProgress: (loaded: number) => void, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.setRequestHeader('X-Requested-With', 'babeldoc-web');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve();
      if (xhr.status === 401) notifyUnauthorized();
      let detail = '';
      try {
        detail = describeDetail(JSON.parse(xhr.responseText)?.detail);
      } catch {
        /* 没有 JSON 正文 */
      }
      reject(new ApiError(detail || `上传失败（${xhr.status}）`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError('网络连接失败', 0));
    xhr.onabort = () => reject(new DOMException('已取消', 'AbortError'));
    signal?.addEventListener('abort', () => xhr.abort(), { once: true });
    xhr.send(blob);
  });
}

/**
 * 分片上传：先建会议拿到分片大小，再并发 PUT 各片（网络错误每片重试 3 次），最后 complete。
 * 绕开 Cloudflare 单个请求 100 MB 的上限。onProgress 收到 0–1。
 */
export async function uploadMeeting(
  file: File,
  options: Omit<MeetingCreate, 'filename' | 'size'>,
  onProgress: (ratio: number) => void,
  signal?: AbortSignal,
): Promise<Meeting> {
  const created = await post<MeetingUpload>('/api/meetings', { ...options, filename: file.name, size: file.size });
  const { id } = created.meeting;
  const loaded = new Array<number>(created.parts).fill(0);
  const report = () => onProgress(Math.min(1, loaded.reduce((a, b) => a + b, 0) / file.size));
  let next = 0;
  const worker = async () => {
    while (next < created.parts) {
      const index = next++;
      const blob = file.slice(index * created.part_size, Math.min(file.size, (index + 1) * created.part_size));
      for (let attempt = 1; ; attempt++) {
        try {
          await putPart(`/api/meetings/${id}/upload/${index}`, blob, (n) => ((loaded[index] = n), report()), signal);
          loaded[index] = blob.size;
          report();
          break;
        } catch (e) {
          if (signal?.aborted || (e instanceof ApiError && e.status !== 0 && e.status < 500) || attempt >= 3) throw e;
          await new Promise((r) => setTimeout(r, 1000 * attempt));
        }
      }
    }
  };
  try {
    await Promise.all(Array.from({ length: Math.min(3, created.parts) }, worker));
    return await post<Meeting>(`/api/meetings/${id}/upload/complete`);
  } catch (e) {
    // 传不完就把这条“上传中”的记录删掉，免得列表里留一条半截的
    void request('DELETE', `/api/meetings/${id}`).catch(() => {});
    throw e;
  }
}

export interface ChatHandlers {
  /** 服务器收到问题、存好之后立刻发 start，带上这条用户消息 */
  onStart?: (userMessage: ChatMessage) => void;
  onDelta: (text: string) => void;
  /** 回答完整存好后发 done，带上这条助手消息 */
  onDone: (message: ChatMessage) => void;
  onError: (message: string) => void;
}

/**
 * 对话（M5）：POST /api/meetings/{id}/chat，返回 text/event-stream。
 * 事件：start {user_message}、delta {text}、done {message}、error {message}；首字前每 10 秒一个注释心跳。
 */
export async function streamChat(id: string, content: string, handlers: ChatHandlers, signal?: AbortSignal) {
  let res: Response;
  try {
    res = await fetch(`/api/meetings/${id}/chat`, {
      method: 'POST',
      headers: { 'X-Requested-With': 'babeldoc-web', 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ content }),
      signal,
    });
  } catch (e) {
    if (signal?.aborted) return;
    handlers.onError(e instanceof Error ? e.message : '网络连接失败');
    return;
  }
  if (!res.ok || !res.body) {
    if (res.status === 401) notifyUnauthorized();
    const data = await res.json().catch(() => null);
    handlers.onError(describeDetail(data?.detail) || `请求失败（${res.status}）`);
    return;
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let cut: number;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        let event = 'message';
        const data: string[] = [];
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        }
        if (!data.length) continue;
        const payload = JSON.parse(data.join('\n'));
        if (event === 'start') handlers.onStart?.(payload.user_message);
        else if (event === 'delta') handlers.onDelta(payload.text);
        else if (event === 'done') handlers.onDone(payload.message);
        else if (event === 'error') handlers.onError(payload.message);
      }
    }
  } catch (e) {
    if (!signal?.aborted) handlers.onError(e instanceof Error ? e.message : '连接中断');
  }
}
