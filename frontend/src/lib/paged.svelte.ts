/**
 * 按 created_at 倒序、offset 分页的列表（翻译任务、会议记录共用）。
 * 子类负责订阅事件、upsert 的其余规则，以及退出登录时额外要清的状态。
 */

const PAGE = 20;

export interface Page<T> {
  items: T[];
  total: number;
}

export class PagedList<T extends { id: string; created_at: string }> {
  items = $state<T[]>([]);
  total = $state(0);
  loading = $state(false);
  loaded = $state(false);

  get hasMore() {
    return this.items.length < this.total;
  }

  /** 加载第一页；more 时接着已有的条目往后加载，去掉重复的 */
  protected async loadPage(fetchPage: (limit: number, offset: number) => Promise<Page<T>>, more: boolean) {
    this.loading = true;
    try {
      const page = await fetchPage(PAGE, more ? this.items.length : 0);
      this.items = more ? [...this.items, ...page.items.filter((x) => !this.items.some((y) => y.id === x.id))] : page.items;
      this.total = page.total;
      this.loaded = true;
    } finally {
      this.loading = false;
    }
  }

  /**
   * 列表里还没有的条目：比第一条新（或列表还没加载、是空的）才插到顶部并计数。
   * 更旧的条目本该在后面的页里，插到顶部会打乱顺序，“加载更多”的偏移也会多算一条，所以忽略。
   */
  protected insertIfNewer(item: T) {
    if (this.loaded && this.items.length) {
      const a = Date.parse(item.created_at);
      const b = Date.parse(this.items[0].created_at);
      if (Number.isFinite(a) && Number.isFinite(b) && a < b) return;
    }
    this.items = [item, ...this.items];
    this.total += 1;
  }

  remove(id: string) {
    const before = this.items.length;
    this.items = this.items.filter((x) => x.id !== id);
    if (this.items.length < before) this.total = Math.max(0, this.total - 1);
  }

  /** 退出登录时清空 */
  reset() {
    this.items = [];
    this.total = 0;
    this.loaded = false;
  }
}
