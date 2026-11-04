import { readonly, ref } from 'vue';
import { catalogApi } from '../api/client';
import type { Stats } from '../api/types';

const stats = ref<Stats>();
let pending: Promise<void> | undefined;
export function useCatalog() {
  function refreshStats(): Promise<void> {
    if (!pending)
      pending = catalogApi
        .stats()
        .then((value) => {
          stats.value = value;
        })
        .finally(() => {
          pending = undefined;
        });
    return pending;
  }
  return { stats: readonly(stats), refreshStats };
}
