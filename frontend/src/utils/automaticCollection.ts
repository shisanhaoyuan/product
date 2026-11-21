// Remember automatic attempts across component remounts and browser restarts.
// Manual collection never consults this marker.
const attempted = new Set<string>();
export function claimAutomaticCollection(scope: string, identity: unknown): boolean {
  const key = 'jiwu-auto-collection-v1:' + scope + ':' + JSON.stringify(identity);
  if (attempted.has(key)) return false;
  try {
    if (localStorage.getItem(key)) return false;
    localStorage.setItem(key, 'attempted');
  } catch {
    /* Memory still prevents repeated attempts within this app session. */
  }
  attempted.add(key);
  return true;
}
