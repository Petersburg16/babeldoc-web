// 文件读写与下载的小工具：处理结果都在浏览器内存里，用 Blob URL 触发下载。

export interface OutputFile {
  name: string;
  blob: Blob;
}

/** 处理进度回调：fraction 为 0–1，null 表示无法估计；text 为当前步骤说明 */
export type Report = (fraction: number | null, text?: string) => void;

export const PDF_TYPE = 'application/pdf';

export function stem(name: string) {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(0, dot) : name;
}

export function extension(name: string) {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
}

/** 「论文.pdf」+「已压缩」→「论文-已压缩.pdf」，可换扩展名 */
export function renamed(name: string, suffix: string, ext = extension(name) || 'pdf') {
  return `${stem(name)}${suffix ? `-${suffix}` : ''}.${ext}`;
}

export function pdfBlob(bytes: Uint8Array) {
  return new Blob([bytes as BlobPart], { type: PDF_TYPE });
}

export async function readBytes(file: Blob) {
  return new Uint8Array(await file.arrayBuffer());
}

export function download(output: OutputFile) {
  const url = URL.createObjectURL(output.blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = output.name;
  a.rel = 'noopener';
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** 多个结果打成一个 zip（不再压缩 PDF/图片这类已压缩的内容，速度快很多） */
export async function zipOutputs(outputs: OutputFile[], name: string): Promise<OutputFile> {
  const { zip } = await import('fflate');
  const entries: Record<string, [Uint8Array, { level: 0 }]> = {};
  const used = new Set<string>();
  for (const out of outputs) {
    let entry = out.name;
    for (let i = 2; used.has(entry); i++) entry = `${stem(out.name)} (${i}).${extension(out.name)}`;
    used.add(entry);
    entries[entry] = [await readBytes(out.blob), { level: 0 }];
  }
  const data = await new Promise<Uint8Array>((resolve, reject) =>
    zip(entries, (err, result) => (err ? reject(err) : resolve(result))),
  );
  return { name, blob: new Blob([data as BlobPart], { type: 'application/zip' }) };
}
