export interface ConfirmOptions {
  title: string;
  message?: string;
  confirmText?: string;
  danger?: boolean;
}

class ConfirmState {
  current = $state<(ConfirmOptions & { resolve: (ok: boolean) => void }) | null>(null);

  ask(options: ConfirmOptions): Promise<boolean> {
    this.current?.resolve(false);
    return new Promise((resolve) => {
      this.current = { ...options, resolve };
    });
  }

  answer(ok: boolean) {
    this.current?.resolve(ok);
    this.current = null;
  }
}

export const confirmState = new ConfirmState();
export const confirm = (options: ConfirmOptions) => confirmState.ask(options);
