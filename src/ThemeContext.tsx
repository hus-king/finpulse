import { createContext, useContext, useLayoutEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

type Theme = 'dark' | 'light';
const ThemeContext = createContext<{ theme: Theme; toggleTheme: () => void } | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // index.html applies the saved preference before the page's first paint.
  const [theme, setTheme] = useState<Theme>(() => document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#101419' : '#f8fafc');
    try { localStorage.setItem('finpulse-theme', theme); } catch { /* The switch still works when browser storage is unavailable. */ }
  }, [theme]);

  return <ThemeContext.Provider value={{ theme, toggleTheme: () => setTheme(previous => previous === 'dark' ? 'light' : 'dark') }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used within ThemeProvider');
  return context;
}

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const label = theme === 'dark' ? '切换到浅色模式' : '切换到深色模式';
  return <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label={label} title={label}>
    {theme === 'dark' ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}
    <span>{theme === 'dark' ? '浅色' : '深色'}</span>
  </button>;
}
