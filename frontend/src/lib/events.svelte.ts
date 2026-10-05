/**
 * 全站唯一的事件流（/api/events）。登录后由 App.svelte 连接，退出登录或 401 时断开。
 * 各个 store 用 on() 订阅自己关心的事件类型，用 onReconnect() 在断线重连后重新拉一遍数据。
 */

type Handler = (data: any) => void; // eslint-disable-line @typescript-eslint/no-explicit-any

class EventStream {
  connected = $state(false);
  private source: EventSource | null = null;
  private handlers = new Map<string, Set<Handler>>();
  private reconnectHandlers = new Set<() => void>();
  private everConnected = false;
  private wanted = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private retryDelay = 3000;

  /** 订阅一种事件，返回取消订阅的函数 */
  on(type: string, handler: Handler) {
    let set = this.handlers.get(type);
    if (!set) {
      set = new Set();
      this.handlers.set(type, set);
      this.source?.addEventListener(type, (e) => this.dispatch(type, e as MessageEvent));
    }
    set.add(handler);
    return () => set.delete(handler);
  }

  /** 断线重连成功后调用（第一次连上不调用） */
  onReconnect(handler: () => void) {
    this.reconnectHandlers.add(handler);
    return () => this.reconnectHandlers.delete(handler);
  }

  connect() {
    this.wanted = true;
    if (this.source) return;
    const source = new EventSource('/api/events');
    this.source = source;
    source.addEventListener('hello', () => {
      this.connected = true;
      this.retryDelay = 3000;
      if (this.everConnected) for (const handler of this.reconnectHandlers) handler();
      this.everConnected = true;
    });
    for (const type of this.handlers.keys()) source.addEventListener(type, (e) => this.dispatch(type, e as MessageEvent));
    source.onerror = () => {
      this.connected = false;
      if (source.readyState !== EventSource.CLOSED || this.source !== source) return;
      source.close();
      this.source = null;
      if (!this.wanted || this.retryTimer) return;
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null;
        if (this.wanted) this.connect();
      }, this.retryDelay);
      this.retryDelay = Math.min(30_000, this.retryDelay * 2);
    };
  }

  disconnect() {
    this.wanted = false;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.retryDelay = 3000;
    this.source?.close();
    this.source = null;
    this.connected = false;
    this.everConnected = false;
  }

  private dispatch(type: string, e: MessageEvent) {
    let data: unknown;
    try {
      data = JSON.parse(e.data);
    } catch {
      return;
    }
    for (const handler of this.handlers.get(type) ?? []) handler(data);
  }
}

export const events = new EventStream();
