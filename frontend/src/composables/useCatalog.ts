import { computed, ref, toValue, type MaybeRefOrGetter } from 'vue';
import { catalogApi } from '../api/client';
import type { Stats } from '../api/types';

const bySource = ref<Record<string, Stats>>({});
const pending = new Map<string, Promise<void>>();
export function useCatalog(source: MaybeRefOrGetter<string> = 'Amazon') {
  const stats = computed(() => bySource.value[toValue(source)]);
  function refreshStats(): Promise<void> {
    const key = toValue(source);
    const existing = pending.get(key);
    if (existing) return existing;
    const task = catalogApi
      .stats(key)
      .then((value) => {
        bySource.value[key] = value;
      })
      .finally(() => {
        pending.delete(key);
      });
    pending.set(key, task);
    return task;
  }
  return { stats, refreshStats };
}
