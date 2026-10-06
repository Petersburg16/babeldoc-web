// 首页拖放区（Dropzone）和通用文件选择器（FilePicker）共用的“添加文件”规则；
// 文件类型各自判断、各自提示，这里只管大小、重复和数量。
import { toast } from './toast.svelte';

interface AddOptions {
  maxMb: number;
  maxFiles: number;
  /** 只要一个文件：新选的替换原来的，不按数量截断 */
  single?: boolean;
  /** 超出数量时的提示，默认“最多 N 个文件，多出的已忽略” */
  tooMany?: string;
}

/**
 * 把新选的文件加进列表，返回新列表：超过大小的提示后不加，与已有文件同名同大小的跳过，超出数量的提示后截掉。
 * single 时没有可用的新文件就原样返回 current。
 */
export function addFiles(current: File[], incoming: File[], { maxMb, maxFiles, single = false, tooMany }: AddOptions) {
  const limit = maxMb * 1024 * 1024;
  const tooBig = incoming.filter((f) => f.size > limit);
  if (tooBig.length) toast.error(`${tooBig.map((f) => f.name).join('、')} 超过 ${maxMb} MB，未添加`);
  const fresh = incoming.filter((f) => f.size <= limit && !current.some((x) => x.name === f.name && x.size === f.size));
  if (single) return fresh.length ? [fresh[0]] : current;
  const room = maxFiles - current.length;
  if (fresh.length > room) toast.info(tooMany ?? `最多 ${maxFiles} 个文件，多出的已忽略`);
  return [...current, ...fresh.slice(0, Math.max(0, room))];
}
