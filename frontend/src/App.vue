<script setup lang="ts">
import { computed, onErrorCaptured, ref } from 'vue';
import { useRoute } from 'vue-router';
import AppIcon from './components/AppIcon.vue';
import { useCatalog } from './composables/useCatalog';
import { useAuth } from './composables/useAuth';
import { request } from './api/client';
const { auth } = useAuth();
async function logout() {
  try {
    await request('/api/auth/logout', { method: 'POST' });
    window.location.replace('/#/login');
    window.location.reload();
  } catch {
    fatalError.value = '退出失败，请重试';
  }
}
const route = useRoute();
const { stats } = useCatalog();
const fatalError = ref('');
const navigation = [
  { path: '/products', name: '商品库', icon: 'LayoutGrid' },
  { path: '/review', name: '待复核', icon: 'CircleDashed' },
  { path: '/integrations', name: '数据接入', icon: 'Cable' },
  { path: '/imports', name: '导入记录', icon: 'History' },
];
const current = computed(() =>
  route.path.startsWith('/product/')
    ? '商品详情'
    : navigation.find((item) => item.path === route.path)?.name || '商品库',
);
function active(path: string) {
  if (path === '/review') return route.path === '/review' || route.query.status === 'review';
  if (path === '/products')
    return route.path.startsWith('/product') && route.query.status !== 'review';
  return route.path === path;
}
onErrorCaptured((error) => {
  fatalError.value = error.message;
  return false;
});
</script>
<template>
  <header class="site-header">
    <div class="header-inner">
      <RouterLink class="logo" to="/products" title="集物 · 商品库"
        ><span class="logo-symbol"><AppIcon name="Layers" /></span
        ><span>集物<span class="logo-sub">Product library</span></span></RouterLink
      >
      <nav
        v-if="auth.ready && (!auth.enabled || (auth.user && !auth.user.must_change_password))"
        class="top-nav"
        aria-label="主导航"
      >
        <RouterLink
          v-for="item in navigation"
          :key="item.path"
          :to="item.path"
          :class="{ active: active(item.path) }"
          :title="item.name"
          ><span class="nav-icon"><AppIcon :name="item.icon" /></span><span>{{ item.name }}</span
          ><span v-if="item.path === '/products' || item.path === '/review'" class="nav-count">{{
            item.path === '/products' ? (stats?.listings ?? '—') : (stats?.review ?? '—')
          }}</span></RouterLink
        >
      </nav>
      <div class="header-status">
        <template v-if="auth.enabled && auth.user">
          <RouterLink
            v-if="auth.user.role === 'admin' && !auth.user.must_change_password"
            class="detail-link"
            to="/users"
            >团队</RouterLink
          >
          <RouterLink class="detail-link" to="/account">{{ auth.user.username }}</RouterLink>
          <button class="btn small" @click="logout">退出</button>
        </template>
        <template v-else
          ><span class="local-pill"><i></i> {{ auth.enabled ? '团队商品库' : '本机数据库' }}</span
          ><span class="avatar">集</span></template
        >
      </div>
    </div>
  </header>
  <main>
    <div class="topbar">
      <div class="breadcrumb">
        <span>工作空间</span><AppIcon name="ChevronRight" /><span id="breadcrumb-current">{{
          current
        }}</span>
      </div>
      <span class="workspace-caption">JIWU / PRODUCT WORKSPACE</span>
    </div>
    <div id="app" aria-live="polite">
      <div v-if="fatalError" class="error-box">
        页面加载失败：{{ fatalError }}<button class="btn" @click="fatalError = ''">重试</button>
      </div>
      <RouterView v-else />
    </div>
  </main>
</template>
