import { events } from './events.svelte';
import { meetingApi } from './meeting/api';
import type { Meeting, MeetingDetail, MeetingOptions } from './meeting/types';
import { PagedList } from './paged.svelte';

export interface MeetingLive {
  progress: number;
  stage: string;
}

class MeetingsStore extends PagedList<Meeting> {
  live = $state<Record<string, MeetingLive>>({});
  /** 详情页正在看的会议（带纪要正文）；事件到来时就地更新 */
  detail = $state<MeetingDetail | null>(null);
  options = $state<MeetingOptions | null>(null);
  private wantedDetail: string | null = null;

  constructor() {
    super();
    events.on('meeting', (data) => this.upsert(data.meeting));
    events.on('meeting_removed', (data) => this.remove(data.id));
    events.on('meeting_progress', (data) => {
      this.live[data.id] = { progress: data.progress, stage: data.stage };
    });
    events.onReconnect(() => {
      if (this.loaded) void this.load();
      const id = this.detail?.id;
      if (id) void this.openDetail(id).catch(() => this.remove(id));
    });
  }

  async loadOptions() {
    this.options = await meetingApi.options();
    return this.options;
  }

  load(more = false) {
    return this.loadPage((limit, offset) => meetingApi.list(limit, offset), more);
  }

  async openDetail(id: string) {
    this.wantedDetail = id;
    const detail = await meetingApi.get(id);
    // 快速切换会议时，先发出的请求可能后返回：只认最后打开的那场
    if (this.wantedDetail !== id) return detail;
    this.detail = detail;
    this.upsert(detail, false);
    return detail;
  }

  closeDetail() {
    this.detail = null;
    this.wantedDetail = null;
  }

  upsert(m: Meeting, touchDetail = true) {
    const index = this.items.findIndex((x) => x.id === m.id);
    const known = index >= 0 ? this.items[index] : this.detail?.id === m.id ? this.detail : null;
    if (known && isStale(m, known)) return;
    if (index >= 0) this.items[index] = { ...this.items[index], ...m };
    else this.insertIfNewer(m);
    if (['done', 'failed', 'canceled'].includes(m.status) && !m.op) {
      const { [m.id]: _, ...rest } = this.live;
      this.live = rest;
    }
    if (touchDetail && this.detail?.id === m.id) {
      const before = this.detail;
      // 事件里不带纪要正文：纪要重新生成后再单独拉一次
      const minutesChanged = before.minutes_at !== m.minutes_at || before.minutes_state !== m.minutes_state;
      this.detail = { ...before, ...m };
      if (minutesChanged) void this.refreshMinutes(m.id);
    }
  }

  private async refreshMinutes(id: string) {
    try {
      const fresh = await meetingApi.get(id);
      if (this.detail?.id === id) this.detail = fresh;
    } catch {
      /* 详情页自己会处理错误 */
    }
  }

  remove(id: string) {
    super.remove(id);
    if (this.detail?.id === id) this.detail = null;
  }

  progressOf(m: Meeting): MeetingLive {
    return this.live[m.id] ?? { progress: m.progress, stage: m.stage };
  }

  reset() {
    super.reset();
    this.live = {};
    this.detail = null;
    this.options = null;
  }
}

/** 比手里的数据旧（接口返回值晚于事件到达），丢掉 */
function isStale(incoming: Meeting, known: Meeting) {
  const a = Date.parse(incoming.updated_at);
  const b = Date.parse(known.updated_at);
  return Number.isFinite(a) && Number.isFinite(b) && a < b;
}

export const meetings = new MeetingsStore();
