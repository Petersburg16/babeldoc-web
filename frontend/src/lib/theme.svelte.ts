export type ThemeChoice = 'system' | 'light' | 'dark';

const KEY = 'bdw-theme';

function readChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    if (value === 'light' || value === 'dark' || value === 'system') return value;
  } catch {
    /* 隐私模式下 localStorage 可能不可用 */
  }
  return 'system';
}

class Theme {
  choice = $state<ThemeChoice>(readChoice());

  constructor() {
    this.apply();
  }

  apply() {
    const root = document.documentElement;
    if (this.choice === 'system') delete root.dataset.theme;
    else root.dataset.theme = this.choice;
  }

  set(choice: ThemeChoice) {
    this.choice = choice;
    try {
      localStorage.setItem(KEY, choice);
    } catch {
      /* 忽略 */
    }
    this.apply();
  }

  cycle() {
    this.set(this.choice === 'system' ? 'light' : this.choice === 'light' ? 'dark' : 'system');
  }
}

export const theme = new Theme();
