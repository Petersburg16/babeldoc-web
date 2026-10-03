// 打开加密 PDF 时向用户要密码（对话框由 PasswordHost 渲染）
export interface PasswordRequest {
  filename: string;
  incorrect: boolean;
  resolve: (password: string | null) => void;
}

class PasswordState {
  current = $state<PasswordRequest | null>(null);

  ask(filename: string, incorrect = false): Promise<string | null> {
    this.current?.resolve(null);
    return new Promise((resolve) => {
      this.current = { filename, incorrect, resolve };
    });
  }

  answer(password: string | null) {
    this.current?.resolve(password);
    this.current = null;
  }
}

export const passwordState = new PasswordState();
export const askPassword = (filename: string, incorrect = false) => passwordState.ask(filename, incorrect);
