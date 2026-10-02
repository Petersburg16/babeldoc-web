import { api } from './api';
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
}

export const session = new Session();
