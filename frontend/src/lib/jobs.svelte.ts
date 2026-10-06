import { api } from './api';
import { events } from './events.svelte';
import { PagedList } from './paged.svelte';
import type { Job, LiveProgress } from './types';

export type JobFilter = 'all' | 'active' | 'succeeded' | 'failed';

class JobsStore extends PagedList<Job> {
  filter = $state<JobFilter>('all');
  live = $state<Record<string, LiveProgress>>({});
  private onFinish: ((job: Job) => void) | null = null;

  constructor() {
    super();
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

  load(more = false) {
    return this.loadPage((limit, offset) => api.jobs(this.filter, limit, offset), more);
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
      this.insertIfNewer(job);
    }
    if (job.status !== 'running') {
      const { [job.id]: _, ...rest } = this.live;
      this.live = rest;
    }
  }

  /** 翻译任务完成或失败时的回调（翻译页用来刷新额度、弹提示） */
  setOnFinish(onFinish: ((job: Job) => void) | null) {
    this.onFinish = onFinish;
  }

  /** 退出登录时清空 */
  reset() {
    super.reset();
    this.live = {};
    this.onFinish = null;
  }

  progressOf(job: Job) {
    const live = this.live[job.id];
    return live ? { ...live } : { progress: job.progress, stage: job.stage, current: null, total: null, part: null, parts: null };
  }
}

export const jobs = new JobsStore();
