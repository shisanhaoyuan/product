import { onScopeDispose, reactive } from 'vue';
import { catalogApi } from '../api/client';
import type { Variant } from '../api/types';
import { errorMessage } from '../utils/display';

export interface VariantPreviewState {
  expanded: boolean;
  loading: boolean;
  error: string;
  variants?: Variant[];
}

export function useVariantPreview() {
  const states = reactive(new Map<string, VariantPreviewState>());
  const controllers = new Map<string, AbortController>();
  const closed: VariantPreviewState = { expanded: false, loading: false, error: '' };
  const state = (id: string) => states.get(id) ?? closed;

  async function load(id: string) {
    const current = states.get(id);
    if (!current || current.loading || current.variants !== undefined) return;
    const controller = new AbortController();
    controllers.set(id, controller);
    current.loading = true;
    current.error = '';
    try {
      const detail = await catalogApi.product(id, controller.signal);
      if (!controller.signal.aborted) current.variants = detail.variants;
    } catch (error) {
      if (!controller.signal.aborted) current.error = errorMessage(error);
    } finally {
      current.loading = false;
      controllers.delete(id);
    }
  }

  async function toggle(id: string) {
    if (!states.has(id)) states.set(id, { ...closed });
    const current = states.get(id)!;
    current.expanded = !current.expanded;
    if (current.expanded) await load(id);
  }

  onScopeDispose(() => {
    controllers.forEach((controller) => controller.abort());
    controllers.clear();
  });

  return { state, toggle, retry: load };
}
