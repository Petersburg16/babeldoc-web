import { api } from './api';
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
  connected = $state(false);
  private source: EventSource | null = null;
  private onFinish: ((job: Job) => void) | null = null;

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

  connect(onFinish: (job: Job) => void) {
    this.onFinish = onFinish;
    if (this.source) return;
    const source = new EventSource('/api/events');
    this.source = source;
    source.addEventListener('hello', () => {
      const reconnect = this.loaded;
      this.connected = true;
      if (reconnect) void this.load();
    });
    source.addEventListener('job', (e) => this.upsert(JSON.parse((e as MessageEvent).data).job));
    source.addEventListener('progress', (e) => {
      const data = JSON.parse((e as MessageEvent).data);
      this.live[data.id] = data;
      const job = this.items.find((j) => j.id === data.id);
      if (job && job.status !== 'running') job.status = 'running';
    });
    source.addEventListener('queue', (e) => {
      const positions: Record<string, number> = JSON.parse((e as MessageEvent).data).positions;
      for (const job of this.items) if (job.status === 'queued') job.queue_position = positions[job.id] ?? null;
    });
    source.onerror = () => {
      this.connected = false;
    };
  }

  disconnect() {
    this.source?.close();
    this.source = null;
    this.connected = false;
    this.items = [];
    this.loaded = false;
    this.live = {};
  }

  progressOf(job: Job) {
    const live = this.live[job.id];
    return live ? { ...live } : { progress: job.progress, stage: job.stage, current: null, total: null, part: null, parts: null };
  }
}

export const jobs = new JobsStore();
