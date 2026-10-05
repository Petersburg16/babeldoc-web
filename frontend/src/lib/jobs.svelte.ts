import { api } from './api';
import { events } from './events.svelte';
import type { Job, LiveProgress } from './types';

export type JobFilter = 'all' | 'active' | 'succeeded' | 'failed';

const PAGE = 20;

class JobsStore {
  items = $state<Job[]>([]);
  total = $state(0);
  filter = $state<JobFilter>('all');
  loading = $state(false);
  loaded = $state(false);
  live = $state<Record<string, LiveProgress>>({});
  private onFinish: ((job: Job) => void) | null = null;

  constructor() {
    // 事件流由 App.svelte 统一连接；这里只订阅翻译任务相关的事件
    events.on('job', (data) => this.upsert(data.job));
    events.on('progress', (data) => {
      this.live[data.id] = data;
      const job = this.items.find((j) => j.id === data.id);
      if (job && job.status !== 'running') job.status = 'running';
    });
    events.on('queue', (data) => {
      const positions: Record<string, number> = data.positions;
      for (const job of this.items) if (job.status === 'queued') job.queue_position = positions[job.id] ?? null;
    });
    events.onReconnect(() => {
      if (this.loaded) void this.load();
    });
  }

  get hasMore() {
    return this.items.length < this.total;
  }

  async load(more = false) {
    this.loading = true;
    try {
      const page = await api.jobs(this.filter, PAGE, more ? this.items.length : 0);
      this.items = more ? [...this.items, ...page.items.filter((j) => !this.items.some((x) => x.id === j.id))] : page.items;
      this.total = page.total;
      this.loaded = true;
    } finally {
      this.loading = false;
    }
  }

  setFilter(filter: JobFilter) {
    if (this.filter === filter) return;
    this.filter = filter;
    this.items = [];
    this.loaded = false;
    void this.load();
  }

  upsert(job: Job) {
    const index = this.items.findIndex((j) => j.id === job.id);
    if (index >= 0) {
      const before = this.items[index];
      this.items[index] = job;
      if (before.status !== job.status && (job.status === 'succeeded' || job.status === 'failed')) this.onFinish?.(job);
    } else if (this.filter === 'all' || this.filter === 'active') {
      this.items = [job, ...this.items];
      this.total += 1;
    }
    if (job.status !== 'running') {
      const { [job.id]: _, ...rest } = this.live;
      this.live = rest;
    }
  }

  remove(id: string) {
    const before = this.items.length;
    this.items = this.items.filter((j) => j.id !== id);
    if (this.items.length < before) this.total = Math.max(0, this.total - 1);
  }

  /** 翻译任务完成或失败时的回调（翻译页用来刷新额度、弹提示） */
  setOnFinish(onFinish: ((job: Job) => void) | null) {
    this.onFinish = onFinish;
  }

  /** 退出登录时清空 */
  reset() {
    this.items = [];
    this.loaded = false;
    this.live = {};
    this.onFinish = null;
  }

  progressOf(job: Job) {
    const live = this.live[job.id];
    return live ? { ...live } : { progress: job.progress, stage: job.stage, current: null, total: null, part: null, parts: null };
  }
}

export const jobs = new JobsStore();
