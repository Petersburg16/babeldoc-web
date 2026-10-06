import { meetings } from '../meetings.svelte';
import { toast } from '../toast.svelte';
import { meetingApi } from './api';

/** 重试失败或已取消的会议（会议卡片和详情页共用）；出错时抛出，由调用方提示 */
export async function retryMeeting(id: string) {
  meetings.upsert(await meetingApi.retry(id));
  toast.success('已重新开始处理');
}
