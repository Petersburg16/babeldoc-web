// PDF 工具的引擎（WASM、语言包、字体）按需下载：第一次用到某个工具时才下载它需要的引擎，
// 存进 Cache Storage（浏览器 HTTP 缓存放不下几十 MB 的单个文件，也查不到是否已缓存），之后离线可用。
// /pdf-sw.js 让各个库自己发出的 /pdf-assets/ 请求也直接命中 Cache Storage。
// 资源清单和版本来自 frontend/pdf-assets.lock.json，每个引擎单独定版本，升级一个不影响其他引擎的缓存。
import lockData from '../../../pdf-assets.lock.json';

interface LockFile {
  sha256: string;
  bytes: number;
  gzip: number;
  br: number;
  /** 由库按需自己去取（例如按浏览器能力三选一的 OCR 核心），不预先下载 */
  lazy?: boolean;
}

interface LockEngine {
  label: string;
  version: string;
  bytes: number;
  transfer: number;
  lazy?: boolean;
  files: Record<string, LockFile>;
}

const LOCK = lockData.engines as unknown as Record<string, LockEngine>;

/** core / render 的代码随前端一起打包；其余引擎的文件由本站 /pdf-assets/ 提供 */
export type EngineId = 'core' | 'render' | (keyof typeof lockData.engines & string);

export interface Progress {
  loaded: number;
  total: number;
  label: string;
}

const CACHE_NAME = 'bdw-pdf-assets';

function spec(id: string): LockEngine | undefined {
  return LOCK[id];
}

export function assetBase(id: EngineId) {
  const e = spec(id);
  if (!e) throw new Error(`未知的引擎：${id}`);
  return `/pdf-assets/${id}/${e.version}/`;
}

export function assetUrl(id: EngineId, path: string) {
  return assetBase(id) + path;
}

async function openCache(): Promise<Cache | null> {
  try {
    return 'caches' in globalThis ? await caches.open(CACHE_NAME) : null;
  } catch {
    return null; // 隐私模式等情况下不可用，退回普通网络请求
  }
}

let controller: Promise<boolean> | null = null;

/** 注册只处理 /pdf-assets/ 的 service worker，并等它接管页面（首次访问时最多等 3 秒） */
export function ensureController(): Promise<boolean> {
  controller ??= (async () => {
    if (!('serviceWorker' in navigator)) return false;
    try {
      await navigator.serviceWorker.register('/pdf-sw.js', { scope: '/' });
      await navigator.serviceWorker.ready;
      if (navigator.serviceWorker.controller) return true;
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 3000);
        navigator.serviceWorker.addEventListener(
          'controllerchange',
          () => {
            clearTimeout(timer);
            resolve();
          },
          { once: true },
        );
      });
      return !!navigator.serviceWorker.controller;
    } catch {
      return false;
    }
  })();
  return controller;
}

class Engines {
  /** 引擎文件是否已全部在 Cache Storage 里 */
  ready = $state<Record<string, boolean>>({});
  private refreshing: Promise<void> | null = null;

  /** 检查各引擎的缓存状态（只查本机，不发请求） */
  refresh() {
    this.refreshing ??= (async () => {
      const cache = await openCache();
      const next: Record<string, boolean> = {};
      for (const [id, e] of Object.entries(LOCK)) {
        if (e.lazy) continue; // 按需零散加载的引擎不预先下载，也就不用查
        let all = !!cache;
        for (const [path, file] of Object.entries(e.files)) {
          if (!all || !cache) break;
          if (file.lazy) continue;
          all = !!(await cache.match(`/pdf-assets/${id}/${e.version}/${path}`));
        }
        next[id] = all;
      }
      this.ready = next;
    })().finally(() => (this.refreshing = null));
    return this.refreshing;
  }

  /** 这些引擎里还需要下载的字节数（按预压缩后的传输大小估算；随前端打包的和按需零散加载的不计） */
  pendingBytes(ids: EngineId[]) {
    let total = 0;
    for (const id of new Set(ids)) {
      const e = spec(id);
      if (e && !e.lazy && !this.ready[id]) total += e.transfer;
    }
    return total;
  }

  label(ids: EngineId[]) {
    return [...new Set(ids)]
      .map((id) => spec(id))
      .filter((e): e is LockEngine => !!e && !e.lazy)
      .map((e) => e.label)
      .join('、');
  }

  /** 下载并缓存这些引擎，进度按原始字节计；同一引擎并发调用只下载一次 */
  async ensure(ids: EngineId[], onProgress?: (p: Progress) => void, signal?: AbortSignal) {
    const wanted = [...new Set(ids)].filter((id) => spec(id) && !spec(id)!.lazy);
    await ensureController();
    const cache = await openCache();
    const todo: { id: EngineId; path: string; file: LockFile }[] = [];
    let loaded = 0;
    let total = 0;
    for (const id of wanted) {
      const e = spec(id)!;
      for (const [path, file] of Object.entries(e.files)) {
        if (file.lazy) continue;
        total += file.bytes;
        if (cache && (await cache.match(assetUrl(id, path)))) loaded += file.bytes;
        else todo.push({ id, path, file });
      }
    }
    const label = this.label(wanted);
    onProgress?.({ loaded, total, label });
    for (const item of todo) {
      const url = assetUrl(item.id, item.path);
      await (inflight.get(url) ??
        trackInflight(url, download(cache, url, item.file, signal, (n) => {
          loaded += n;
          onProgress?.({ loaded, total, label });
        })));
    }
    for (const id of wanted) this.ready = { ...this.ready, [id]: true };
    if (cache) void prune(cache);
  }
}

const inflight = new Map<string, Promise<void>>();

function trackInflight(url: string, job: Promise<void>) {
  inflight.set(url, job);
  return job.finally(() => inflight.delete(url));
}

async function download(
  cache: Cache | null,
  url: string,
  file: LockFile,
  signal: AbortSignal | undefined,
  onChunk: (bytes: number) => void,
) {
  const res = await fetch(url, { signal });
  if (!res.ok || !res.body) throw new Error(`下载 ${url.split('/').pop()} 失败（HTTP ${res.status}）`);
  let got = 0;
  const counted = res.body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, ctl) {
        got += chunk.byteLength;
        onChunk(chunk.byteLength);
        ctl.enqueue(chunk);
      },
    }),
  );
  if (!cache) {
    // 没有 Cache Storage 时只是把文件过一遍，让浏览器 HTTP 缓存尽量留住
    const reader = counted.getReader();
    while (!(await reader.read()).done);
  } else {
    // 保留 Content-Type 和跨源隔离头：worker 脚本从缓存取出时没有 COEP 会被拒绝加载
    const headers = new Headers(res.headers);
    headers.delete('content-encoding');
    headers.set('content-length', String(file.bytes));
    await cache.put(url, new Response(counted, { status: 200, headers }));
  }
  if (got !== file.bytes) {
    await cache?.delete(url);
    throw new Error(`${url.split('/').pop()} 下载不完整，请重试`);
  }
}

/** 删掉缓存里已不在锁文件中的旧版本文件 */
async function prune(cache: Cache) {
  const current = new Set(Object.entries(LOCK).map(([id, e]) => `/pdf-assets/${id}/${e.version}/`));
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname;
    const base = path.split('/').slice(0, 4).join('/') + '/';
    if (!current.has(base)) await cache.delete(req);
  }
}

export const engines = new Engines();

/** 取一个资源文件：优先 Cache Storage，否则走网络（用于字体、语言包等需要直接拿字节的场合） */
export async function fetchAsset(id: EngineId, path: string): Promise<Response> {
  const url = assetUrl(id, path);
  const hit = await (await openCache())?.match(url);
  if (hit) return hit;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`加载 ${path} 失败（HTTP ${res.status}）`);
  return res;
}

export async function assetBytes(id: EngineId, path: string) {
  return new Uint8Array(await (await fetchAsset(id, path)).arrayBuffer());
}

/** 给只接受网址的库（例如 LibreOffice）用：从缓存生成 blob URL，不依赖 service worker 是否接管 */
export async function assetBlobUrl(id: EngineId, path: string) {
  return URL.createObjectURL(await (await fetchAsset(id, path)).blob());
}
