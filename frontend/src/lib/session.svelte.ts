import { api } from './api';
import { jobs } from './jobs.svelte';
import { meetings } from './meetings.svelte';
import type { Me, Meta } from './types';

class Session {
  meta = $state<Meta | null>(null);
  me = $state<Me | null>(null);
  ready = $state(false);
  error = $state<string | null>(null);

  get user() {
    return this.me?.user ?? null;
  }

  get isAdmin() {
    return this.me?.user.role === 'admin';
  }

  /** 站点名称，元信息还没拿到时用默认名 */
  get siteName() {
    return this.meta?.site_name ?? 'BabelDOC Web';
  }

  languageLabel(code: string) {
    return this.meta?.languages.find((l) => l.code === code)?.label ?? code;
  }

  async init() {
    try {
      this.meta = await api.meta();
      this.me = await api.me().catch(() => null);
    } catch (e) {
      this.error = e instanceof Error ? e.message : '无法连接服务器';
    } finally {
      this.ready = true;
    }
  }

  async refreshMe() {
    this.me = await api.me().catch(() => null);
  }

  async refreshMeta() {
    this.meta = await api.meta();
  }

  /** 退出登录或登录失效：清掉登录态和按用户隔离的列表；事件流由 App.svelte 跟着 me 断开 */
  signOut() {
    this.me = null;
    jobs.reset();
    meetings.reset();
  }
}

export const session = new Session();
