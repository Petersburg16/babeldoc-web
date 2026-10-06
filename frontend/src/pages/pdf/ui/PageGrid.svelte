<script module lang="ts">
  import type { PageInfo, Slot } from '../../../lib/pdf/ops/organize';

  // 整理操作都是纯函数：返回新数组，方便上层记录撤销历史
  let seq = 0;
  export const newSlotId = () => `n${++seq}`;

  /** 用户旋转后是否横竖互换 */
  export const turned = (rotate: number) => Math.abs(Math.round(rotate / 90)) % 2 === 1;

  /** 显示尺寸（计入页面原有的 /Rotate 和用户的旋转） */
  export function shownSize(slot: Slot, pages: PageInfo[]) {
    const base = slot.kind === 'page' ? pages[slot.index] : slot;
    return turned(slot.rotate) ? { width: base.height, height: base.width } : { width: base.width, height: base.height };
  }

  export function rotateSlots(items: Slot[], ids: Set<string>, delta: number) {
    return items.map((s) => (ids.has(s.id) ? { ...s, rotate: s.rotate + delta } : s));
  }

  export function duplicateSlots(items: Slot[], ids: Set<string>) {
    return items.flatMap((s) => (ids.has(s.id) ? [s, { ...s, id: newSlotId() }] : [s]));
  }

  /** 把 ids（按当前顺序成块）移到 target 前或后；位置没变时返回 null */
  export function moveSlots(items: Slot[], ids: string[], target: string, side: 'before' | 'after') {
    const moving = new Set(ids);
    if (moving.has(target)) return null;
    const block = items.filter((s) => moving.has(s.id));
    const rest = items.filter((s) => !moving.has(s.id));
    const at = rest.findIndex((s) => s.id === target) + (side === 'after' ? 1 : 0);
    const next = [...rest.slice(0, at), ...block, ...rest.slice(at)];
    return next.every((s, i) => s === items[i]) ? null : next;
  }
</script>

<script lang="ts">
  import { tick } from 'svelte';
  import { flip } from 'svelte/animate';
  import { Check, Copy, RotateCcw, Trash2 } from '../../../lib/icons';
  import { ChevronLeft, ChevronRight, RotateCw } from '../../../lib/pdf/icons';

  interface Props {
    items: Slot[];
    pages: PageInfo[];
    /** 原页序号 → 缩略图地址，空字符串表示渲染失败 */
    thumbs: ReadonlyMap<number, string>;
    selected: string[];
    disabled?: boolean;
    onchange: (next: Slot[], message: string) => void;
    /** 卡片进入或离开可视区，用于懒加载缩略图 */
    onvisible?: (index: number, visible: boolean) => void;
  }

  let { items, pages, thumbs, selected = $bindable(), disabled = false, onchange, onvisible }: Props = $props();

  let scroller = $state<HTMLElement>();
  let dragIds = $state<string[] | null>(null);
  let drop = $state<{ id: string; side: 'before' | 'after' } | null>(null);
  let anchor: string | null = null;

  const chosen = $derived(new Set(selected));
  const dragging = $derived(new Set(dragIds ?? []));

  // 缩略图在 4:5 的格子里按比例缩放；旋转用 CSS，不重新渲染
  function fit(slot: Slot) {
    const base = slot.kind === 'page' ? pages[slot.index] : slot;
    const view = shownSize(slot, pages);
    const s = Math.min(1 / view.width, 1.25 / view.height) * 0.9;
    return `width:${base.width * s * 100}%;height:${((base.height * s) / 1.25) * 100}%;transform:translate(-50%,-50%) rotate(${slot.rotate}deg)`;
  }

  const watchers = new WeakMap<Element, (shown: boolean) => void>();
  let io: IntersectionObserver | null = null;

  $effect(() => () => io?.disconnect());

  /** 只渲染滚动区里可见（及上下 400px 内）的缩略图 */
  function lazy(node: HTMLElement, index: number | null) {
    if (index === null || !onvisible) return;
    let shown = false;
    const report = (value: boolean) => {
      if (value === shown) return;
      shown = value;
      onvisible?.(index, value);
    };
    io ??= new IntersectionObserver(
      (entries) => {
        for (const entry of entries) watchers.get(entry.target)?.(entry.isIntersecting);
      },
      { root: node.closest('[data-scroller]'), rootMargin: '400px 0px' },
    );
    const observer = io;
    watchers.set(node, report);
    observer.observe(node);
    return {
      destroy() {
        observer.unobserve(node);
        watchers.delete(node);
        report(false);
      },
    };
  }

  function card(id: string) {
    return scroller?.querySelector<HTMLElement>(`[data-slot="${id}"]`);
  }

  function toggle(event: MouseEvent, id: string) {
    if (disabled) return;
    if (event.shiftKey && anchor && anchor !== id) {
      const a = items.findIndex((s) => s.id === anchor);
      const b = items.findIndex((s) => s.id === id);
      if (a >= 0 && b >= 0) {
        const range = items.slice(Math.min(a, b), Math.max(a, b) + 1).map((s) => s.id);
        selected = [...new Set([...selected, ...range])];
        return;
      }
    }
    anchor = id;
    selected = chosen.has(id) ? selected.filter((x) => x !== id) : [...selected, id];
  }

  function rotate(id: string, delta: number) {
    const at = items.findIndex((s) => s.id === id);
    onchange(rotateSlots(items, new Set([id]), delta), `第 ${at + 1} 页已${delta < 0 ? '向左' : '向右'}旋转`);
  }

  function duplicate(id: string) {
    const at = items.findIndex((s) => s.id === id);
    onchange(duplicateSlots(items, new Set([id])), `已复制第 ${at + 1} 页`);
  }

  async function remove(id: string) {
    const at = items.findIndex((s) => s.id === id);
    onchange(
      items.filter((s) => s.id !== id),
      `已删除第 ${at + 1} 页`,
    );
    selected = selected.filter((x) => x !== id);
    // 焦点留在原位置的下一张卡片上，键盘用户可以接着操作
    await tick();
    const cards = scroller?.querySelectorAll<HTMLElement>('[data-slot]');
    cards?.[Math.min(at, cards.length - 1)]?.querySelector<HTMLButtonElement>('[data-select]')?.focus();
  }

  async function move(id: string, step: -1 | 1) {
    const from = items.findIndex((s) => s.id === id);
    const to = from + step;
    if (from < 0 || to < 0 || to >= items.length) return;
    const next = [...items];
    [next[from], next[to]] = [next[to], next[from]];
    onchange(next, `已移到第 ${to + 1} 位`);
    // 卡片挪动后浏览器会丢焦点，挪完再聚焦回同一张卡片的按钮
    await tick();
    const el = card(id);
    const same = el?.querySelector<HTMLButtonElement>(`[data-move="${step}"]`);
    (same && !same.disabled ? same : el?.querySelector<HTMLButtonElement>(`[data-move="${-step}"]`))?.focus();
  }

  function dragStart(event: DragEvent, id: string) {
    if (disabled) {
      event.preventDefault();
      return;
    }
    // 拖动已选中的页时整组一起移动
    dragIds = chosen.has(id) ? items.filter((s) => chosen.has(s.id)).map((s) => s.id) : [id];
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', id);
    }
  }

  function dragOver(event: DragEvent, id: string) {
    if (!dragIds) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const side = event.clientX < rect.left + rect.width / 2 ? 'before' : 'after';
    if (drop?.id !== id || drop.side !== side) drop = { id, side };
  }

  function dragOverList(event: DragEvent) {
    if (!dragIds) return;
    event.preventDefault();
    if (event.target !== event.currentTarget || !items.length) return;
    // 指针在最后一张卡片之后的空白处：放到末尾
    const last = card(items[items.length - 1].id)?.getBoundingClientRect();
    if (last && (event.clientY > last.bottom || (event.clientY > last.top && event.clientX > last.right))) {
      drop = { id: items[items.length - 1].id, side: 'after' };
    }
  }

  function dropOnList(event: DragEvent) {
    if (!dragIds) return;
    event.preventDefault();
    const ids = dragIds;
    const next = drop ? moveSlots(items, ids, drop.id, drop.side) : null;
    dragIds = drop = null;
    if (!next) return;
    const at = next.findIndex((s) => s.id === ids[0]);
    onchange(next, ids.length > 1 ? `已把 ${ids.length} 页移到第 ${at + 1} 位起` : `已移到第 ${at + 1} 位`);
  }
</script>

<div bind:this={scroller} data-scroller class="max-h-[min(72vh,780px)] overflow-y-auto overscroll-contain p-3 sm:p-4">
  <ul
    class="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3"
    aria-label="页面缩略图"
    ondragover={dragOverList}
    ondrop={dropOnList}
  >
    {#each items as item, i (item.id)}
      {@const on = chosen.has(item.id)}
      {@const url = item.kind === 'page' ? thumbs.get(item.index) : undefined}
      <li
        data-slot={item.id}
        class="group relative select-none"
        draggable={!disabled}
        ondragstart={(e) => dragStart(e, item.id)}
        ondragover={(e) => dragOver(e, item.id)}
        ondragend={() => (dragIds = drop = null)}
        use:lazy={item.kind === 'page' ? item.index : null}
        animate:flip={{ duration: 180 }}
      >
        <div
          class="rounded-xl border bg-surface p-1.5 shadow-card transition-[border-color,box-shadow,opacity]
            {on ? 'border-accent ring-2 ring-accent/25' : 'border-line hover:border-line-strong'}
            {dragging.has(item.id) ? 'opacity-40' : ''}"
        >
          <!-- 鼠标点缩略图即可选中；键盘与读屏用左上角的选择按钮 -->
          <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
          <div
            class="relative aspect-[4/5] w-full overflow-hidden rounded-lg bg-surface-2 {disabled ? '' : 'cursor-grab active:cursor-grabbing'}"
            onclick={(e) => toggle(e, item.id)}
          >
            {#if item.kind === 'blank'}
              <span
                class="absolute top-1/2 left-1/2 grid place-items-center bg-white text-[11.5px] text-[#898781] shadow-sm ring-1 ring-black/10 transition-[transform,width,height] duration-200"
                style={fit(item)}>空白页</span
              >
            {:else if url}
              <img
                src={url}
                alt=""
                draggable="false"
                class="absolute top-1/2 left-1/2 max-w-none bg-white shadow-sm ring-1 ring-black/10 transition-[transform,width,height] duration-200"
                style={fit(item)}
              />
            {:else if url === ''}
              <span
                class="absolute top-1/2 left-1/2 grid place-items-center bg-surface-3 text-[11.5px] text-muted transition-[transform,width,height] duration-200"
                style={fit(item)}>无法预览</span
              >
            {:else}
              <span class="absolute top-1/2 left-1/2 animate-pulse bg-surface-3 transition-[transform,width,height] duration-200" style={fit(item)}></span>
            {/if}
          </div>

          <!-- 触屏上点缩略图本身就能选中，未选中时选择按钮不接收触摸，免得和下方操作按钮抢点击 -->
          <button
            type="button"
            data-select
            class="absolute top-2.5 left-2.5 grid size-6 place-items-center rounded-md border transition-opacity
              {on
              ? 'border-accent bg-accent text-white'
              : 'border-line-strong bg-surface/95 text-transparent opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 pointer-coarse:pointer-events-none'}"
            aria-pressed={on}
            aria-label="选择第 {i + 1} 页"
            {disabled}
            onclick={(e) => toggle(e, item.id)}
          >
            <Check class="size-3.5" strokeWidth={3} />
          </button>

          <!-- 鼠标悬停时浮在缩略图右上角；触屏没有悬停，改为缩略图下方常驻的一行，不遮住页面内容 -->
          <div
            data-actions
            class="absolute top-2.5 right-2.5 flex rounded-lg bg-surface/95 p-0.5 shadow-card ring-1 ring-line transition-opacity
              opacity-0 group-hover:opacity-100 group-focus-within:opacity-100
              pointer-coarse:static pointer-coarse:mt-1 pointer-coarse:justify-around pointer-coarse:bg-transparent pointer-coarse:p-0 pointer-coarse:opacity-100 pointer-coarse:shadow-none pointer-coarse:ring-0"
          >
            <button
              class="btn btn-ghost btn-icon size-7 pointer-coarse:size-8"
              title="向左旋转"
              aria-label="向左旋转第 {i + 1} 页"
              {disabled}
              onclick={() => rotate(item.id, -90)}
            >
              <RotateCcw class="size-3.5" />
            </button>
            <button
              class="btn btn-ghost btn-icon size-7 pointer-coarse:size-8"
              title="向右旋转"
              aria-label="向右旋转第 {i + 1} 页"
              {disabled}
              onclick={() => rotate(item.id, 90)}
            >
              <RotateCw class="size-3.5" />
            </button>
            <button
              class="btn btn-ghost btn-icon size-7 pointer-coarse:size-8"
              title="复制此页"
              aria-label="复制第 {i + 1} 页"
              {disabled}
              onclick={() => duplicate(item.id)}
            >
              <Copy class="size-3.5" />
            </button>
            <button
              class="btn btn-ghost btn-icon size-7 pointer-coarse:size-8 hover:text-bad-ink"
              title="删除此页"
              aria-label="删除第 {i + 1} 页"
              {disabled}
              onclick={() => remove(item.id)}
            >
              <Trash2 class="size-3.5" />
            </button>
          </div>

          <div class="mt-1 flex items-center gap-0.5">
            <button
              data-move="-1"
              class="btn btn-ghost btn-icon size-7 pointer-coarse:size-8"
              title="前移"
              aria-label="第 {i + 1} 页前移一位"
              disabled={disabled || i === 0}
              onclick={() => move(item.id, -1)}
            >
              <ChevronLeft class="size-4" />
            </button>
            <p class="min-w-0 flex-1 truncate text-center text-[12.5px]">
              <span class="font-medium tabular">{i + 1}</span>
              {#if item.kind === 'blank'}
                <span class="text-muted">· 空白页</span>
              {:else if item.index !== i}
                <span class="text-muted">· 原第 {item.index + 1} 页</span>
              {/if}
            </p>
            <button
              data-move="1"
              class="btn btn-ghost btn-icon size-7 pointer-coarse:size-8"
              title="后移"
              aria-label="第 {i + 1} 页后移一位"
              disabled={disabled || i === items.length - 1}
              onclick={() => move(item.id, 1)}
            >
              <ChevronRight class="size-4" />
            </button>
          </div>
        </div>

        {#if drop?.id === item.id && !dragging.has(item.id)}
          <span
            class="pointer-events-none absolute inset-y-4 w-[3px] rounded-full bg-accent {drop.side === 'before' ? '-left-[8px]' : '-right-[8px]'}"
            aria-hidden="true"
          ></span>
        {/if}
      </li>
    {/each}
  </ul>
</div>
