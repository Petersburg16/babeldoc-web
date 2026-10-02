<script lang="ts">
  let { class: klass = 'size-7' }: { class?: string } = $props();
  const id = `logo-${Math.random().toString(36).slice(2, 8)}`;
</script>

<svg class={klass} viewBox="0 0 32 32" aria-hidden="true">
  <defs>
    <linearGradient {id} x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#3987e5" />
      <stop offset="1" stop-color="#4a3aa7" />
    </linearGradient>
  </defs>
  <rect width="32" height="32" rx="8.5" fill="url(#{id})" />
  <path d="M10 7.5h8.6l4.4 4.4V23a1.5 1.5 0 0 1-1.5 1.5h-11.5A1.5 1.5 0 0 1 8.5 23V9a1.5 1.5 0 0 1 1.5-1.5Z" fill="#fff" />
  <path d="M18.4 7.6v3.3c0 .6.5 1 1 1h3.4" fill="none" stroke="#c9d8f5" stroke-width="1.2" />
  <path d="M11.4 14.6h7.2M11.4 17.4h5" stroke="#2a78d6" stroke-width="1.7" stroke-linecap="round" />
  <path d="M11.4 20.6h8.4" stroke="#4a3aa7" stroke-width="1.7" stroke-linecap="round" />
</svg>
