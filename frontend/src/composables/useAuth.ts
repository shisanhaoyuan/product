import { reactive, readonly } from 'vue';

export interface User {
  id: number;
  username: string;
  role: 'admin' | 'editor' | 'viewer';
  active: number;
  must_change_password: number;
}
interface AuthState {
  enabled: boolean;
  user: User | null;
  csrf: string;
}
const state = reactive<AuthState & { ready: boolean }>({
  enabled: false,
  user: null,
  csrf: '',
  ready: false,
});
let pending: Promise<void> | undefined;

export function setAuth(value: AuthState) {
  Object.assign(state, value, { ready: true });
}
export function clearSession() {
  state.user = null;
  state.csrf = '';
}
export function csrfToken() {
  return state.csrf;
}
export async function refreshAuth() {
  if (pending) return pending;
  pending = (async () => {
    const response = await fetch('/api/auth/me', { cache: 'no-store' });
    if (!response.ok) throw new Error('无法确认登录状态，请刷新重试');
    setAuth(await response.json());
  })().finally(() => {
    pending = undefined;
  });
  return pending;
}
export function useAuth() {
  return { auth: readonly(state), refreshAuth };
}
