// 选好文件就先看一眼：是否要密码、有几页（qpdf 很小，顺便预先下载）。拆分、旋转用它校验页码、预告结果。
// 读的是 ops/split 的 inspectPdf，按需加载；换文件时丢掉旧文件还没回来的结果。
import type { PdfInfo } from './ops/split';

export class PdfProbe {
  #file: () => File | undefined;
  #result = $state<{ file: File; info: PdfInfo | null } | null>(null);

  /** 当前文件的探测结果；没有文件、还没读完，或读不了（离线、文件太大）时为 null */
  info = $derived.by(() => {
    const file = this.#file();
    return this.#result && this.#result.file === file ? this.#result.info : null;
  });
  /** 正在读当前文件 */
  probing = $derived.by(() => {
    const file = this.#file();
    return !!file && this.#result?.file !== file;
  });
  /** 当前文件的页数；需要密码、读不出来或还不知道时为 undefined */
  total = $derived(this.info?.pages);

  /** 里面有 $effect，要在组件初始化时创建 */
  constructor(file: () => File | undefined) {
    this.#file = file;
    $effect(() => {
      const current = file();
      if (!current) return;
      let stale = false;
      void import('./ops/split')
        .then(({ inspectPdf }) => inspectPdf(current))
        .catch(() => null)
        .then((info) => {
          if (!stale) this.#result = { file: current, info };
        });
      return () => {
        stale = true;
      };
    });
  }

  /** 开始处理时读到了页数（加密的文件解开后才知道），记下来，页码校验和提示随之更新 */
  learned(file: File, pages: number) {
    this.#result = { file, info: { pages, locked: false, broken: false } };
  }
}
