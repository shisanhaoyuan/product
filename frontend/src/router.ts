import { createRouter, createWebHashHistory } from 'vue-router';
import { useAuth } from './composables/useAuth';

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/products' },
    { path: '/login', component: () => import('./views/LoginView.vue') },
    { path: '/account', component: () => import('./views/AccountView.vue') },
    { path: '/users', component: () => import('./views/UsersView.vue') },
    { path: '/products', component: () => import('./views/ProductsView.vue') },
    { path: '/review', component: () => import('./views/ProductsView.vue') },
    { path: '/product/:id', component: () => import('./views/ProductDetailView.vue') },
    { path: '/prelisting', component: () => import('./views/PrelistingView.vue') },
    { path: '/prelisting/:id', component: () => import('./views/PrelistingView.vue') },
    { path: '/listing-stores', component: () => import('./views/ListingStoresView.vue') },
    { path: '/blacklist', component: () => import('./views/BlacklistView.vue') },
    { path: '/imports', component: () => import('./views/ImportsView.vue') },
    { path: '/integrations', component: () => import('./views/IntegrationsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/products' },
  ],
  scrollBehavior: () => ({ top: 0 }),
});

router.beforeEach(async (to) => {
  const { auth, refreshAuth } = useAuth();
  if (!auth.ready) {
    try {
      await refreshAuth();
    } catch {
      return to.path === '/login' ? true : '/login';
    }
  }
  if (!auth.enabled) return ['/login', '/account', '/users'].includes(to.path) ? '/products' : true;
  if (!auth.user) return to.path === '/login' ? true : '/login';
  if (auth.user.must_change_password && to.path !== '/account') return '/account';
  if (to.path === '/login') return '/products';
  if (to.path === '/users' && auth.user.role !== 'admin') return '/products';
  return true;
});
