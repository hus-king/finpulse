import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

export default function WorkspaceView({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = root.current;
    if (!container || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    // Content stays visible without JS; only below-the-fold sections are staged.
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) {
        entry.target.classList.remove('reveal-pending');
        observer.unobserve(entry.target);
      }
    }, { rootMargin: '0px 0px 40px 0px', threshold: 0 });
    const stage = () => {
      for (const element of container.querySelectorAll<HTMLElement>('.panel, .peg-page, .research-assistant-card')) {
        if (element.dataset.reveal === 'ready') continue;
        element.dataset.reveal = 'ready';
        if (element.getBoundingClientRect().top > window.innerHeight + 40) {
          element.classList.add('reveal-pending');
          observer.observe(element);
        }
      }
    };
    const mutation = new MutationObserver(stage);
    mutation.observe(container, { childList: true, subtree: true });
    stage();
    return () => {
      observer.disconnect();
      mutation.disconnect();
      container.querySelectorAll('.reveal-pending').forEach(element => element.classList.remove('reveal-pending'));
    };
  }, []);
  return <div ref={root} className="workspace-view">{children}</div>;
}
