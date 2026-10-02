class Router {
  path = $state(location.pathname);
  search = $state(location.search);

  constructor() {
    addEventListener('popstate', () => this.sync());
  }

  get query() {
    return new URLSearchParams(this.search);
  }

  sync() {
    this.path = location.pathname;
    this.search = location.search;
  }

  go(to: string, { replace = false } = {}) {
    if (to === location.pathname + location.search) return;
    history[replace ? 'replaceState' : 'pushState'](null, '', to);
    this.sync();
    scrollTo({ top: 0 });
  }
}

export const router = new Router();

/** 拦截站内 <a href="/..."> 的普通点击，交给前端路由。 */
export function interceptLinks(event: MouseEvent) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
    return;
  const anchor = (event.target as Element | null)?.closest('a');
  if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
  const href = anchor.getAttribute('href');
  if (!href || !href.startsWith('/') || href.startsWith('/api/')) return;
  event.preventDefault();
  router.go(href);
}
