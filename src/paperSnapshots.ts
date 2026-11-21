// A tab may restore private snapshots only after the server confirms its user.
const prefix = 'finpulse:paper:v1:';
const maxAge = 24 * 60 * 60_000;
let activeOwner: string | null | undefined;

function ownerPrefix(owner: string) {
  return prefix + encodeURIComponent(owner) + ':';
}

function removeSnapshots(keepPrefix?: string) {
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith(prefix) && !(keepPrefix && key.startsWith(keepPrefix))) sessionStorage.removeItem(key);
    }
  } catch { /* Private browsing may disable storage. */ }
}

export function setPaperSnapshotOwner(owner: string | null) {
  if (activeOwner === owner) return;
  activeOwner = owner;
  removeSnapshots(owner ? ownerPrefix(owner) : undefined);
}

export function clearPaperSnapshots() {
  removeSnapshots();
}

export function readPaperSnapshot<T>(owner: string, key: string): { value: T; savedAt: number } | null {
  if (!owner || owner !== activeOwner) return null;
  try {
    const name = ownerPrefix(owner) + key;
    const raw = sessionStorage.getItem(name);
    if (!raw) return null;
    const snapshot = JSON.parse(raw);
    const age = Date.now() - snapshot?.savedAt;
    if (snapshot?.value != null && Number.isFinite(age) && age >= 0 && age < maxAge) return snapshot;
    sessionStorage.removeItem(name);
  } catch { /* Read from the server if the snapshot is unusable. */ }
  return null;
}

export function writePaperSnapshot<T>(owner: string, key: string, value: T) {
  if (!owner || owner !== activeOwner) return;
  try {
    sessionStorage.setItem(ownerPrefix(owner) + key, JSON.stringify({ value, savedAt: Date.now() }));
  } catch { /* A quota or browser setting must not block the live response. */ }
}
