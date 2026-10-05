import { uploadMeeting } from '../../lib/meeting/api';
import type { Meeting, MeetingCreate } from '../../lib/meeting/types';
import { meetings } from '../../lib/meetings.svelte';
import { toast } from '../../lib/toast.svelte';

type UploadOptions = Omit<MeetingCreate, 'filename' | 'size'>;

function blockUnload(event: BeforeUnloadEvent) {
  event.preventDefault();
  event.returnValue = '';
}

/**
 * 正在进行的录音上传。状态放在模块里而不是组件里：切到别的标签再回来，上传照常进行，进度和“取消”都还在。
 */
class UploadTask {
  active = $state(false);
  name = $state('');
  ratio = $state(0);
  canceling = $state(false);
  /** 服务器为这次上传建的会议，好让列表里那张卡片也显示上传进度 */
  meetingId = $state<string | null>(null);
  private controller: AbortController | null = null;

  async start(file: File, options: UploadOptions): Promise<Meeting | null> {
    if (this.active) return null;
    const controller = new AbortController();
    this.controller = controller;
    this.active = true;
    this.name = file.name;
    this.ratio = 0;
    this.canceling = false;
    this.meetingId = null;
    addEventListener('beforeunload', blockUnload);
    try {
      const meeting = await uploadMeeting(
        file,
        options,
        (ratio) => (this.ratio = ratio),
        controller.signal,
        (created) => {
          this.meetingId = created.id;
          meetings.upsert(created);
        },
      );
      meetings.upsert(meeting);
      toast.success(`「${meeting.title}」已上传，开始识别`);
      return meeting;
    } catch (e) {
      // 半截的记录 uploadMeeting 已经让服务器删掉了；删除事件也会到，这里先去掉免得闪一下
      if (this.meetingId) meetings.remove(this.meetingId);
      if (controller.signal.aborted) toast.info('已取消上传');
      else toast.error(e);
      return null;
    } finally {
      removeEventListener('beforeunload', blockUnload);
      this.active = false;
      this.ratio = 0;
      this.canceling = false;
      this.meetingId = null;
      this.controller = null;
    }
  }

  cancel() {
    if (!this.controller || this.canceling) return;
    this.canceling = true;
    this.controller.abort();
  }
}

export const upload = new UploadTask();

const VIDEO_EXT = /\.(mp4|m4v|mov|mkv|avi|3gp|mpeg|mpg|ts|flv|wmv|webm)$/i;

/**
 * 在浏览器里读出音视频时长（秒）。格式浏览器不认识或超时就返回 null，交给服务器检查。
 */
export function probeDuration(file: File, timeoutMs = 20_000): Promise<number | null> {
  return new Promise((resolve) => {
    const video = file.type.startsWith('video/') || VIDEO_EXT.test(file.name);
    const el = document.createElement(video ? 'video' : 'audio');
    const url = URL.createObjectURL(file);
    let settled = false;
    const finish = (value: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      el.removeAttribute('src');
      el.load();
      URL.revokeObjectURL(url);
      resolve(value);
    };
    const known = () => Number.isFinite(el.duration) && el.duration > 0;
    const timer = setTimeout(() => finish(null), timeoutMs);
    el.preload = 'metadata';
    el.muted = true;
    el.addEventListener('error', () => finish(null));
    el.addEventListener('loadedmetadata', () => {
      if (known()) return finish(el.duration);
      // 网页录音机录的 webm 头里没写时长，跳到末尾让浏览器自己算出来
      const settle = () => known() && finish(el.duration);
      el.addEventListener('durationchange', settle);
      el.addEventListener('timeupdate', settle);
      el.currentTime = Number.MAX_SAFE_INTEGER;
    });
    el.src = url;
  });
}
