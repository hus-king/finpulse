import { useEffect } from 'react';

let locks = 0;
let previousOverflow = '';
// Nested login and AI dialogs share one body scroll lock.
export default function useDialogScroll() {
  useEffect(() => {
    if (locks++ === 0) { previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; }
    return () => { if (--locks === 0) document.body.style.overflow = previousOverflow; };
  }, []);
}
