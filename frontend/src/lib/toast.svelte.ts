import { errorText } from './format';

export type ToastKind = 'success' | 'error' | 'info';

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

class Toasts {
  items = $state<Toast[]>([]);
  private seq = 0;

  push(kind: ToastKind, message: string, ms = kind === 'error' ? 6000 : 3500) {
    const id = ++this.seq;
    this.items = [...this.items, { id, kind, message }].slice(-4);
    setTimeout(() => this.dismiss(id), ms);
  }

  dismiss(id: number) {
    this.items = this.items.filter((t) => t.id !== id);
  }

  success(message: string) {
    this.push('success', message);
  }

  error(error: unknown) {
    this.push('error', errorText(error));
  }

  info(message: string) {
    this.push('info', message);
  }
}

export const toast = new Toasts();
