<script setup lang="ts">
import { useAuth } from '../composables/useAuth';
import AppIcon from './AppIcon.vue';
import { ref } from 'vue';
import ListingPreparationSettings from './ListingPreparationSettings.vue';
const { auth } = useAuth();
const codePoolOpen = ref(false);
</script>
<template>
  <nav class="store-management-nav" aria-label="店铺管理栏目">
    <RouterLink to="/listing-stores" active-class="selected">
      <AppIcon name="Store" />店铺资料
    </RouterLink>
    <RouterLink
      v-if="!auth.enabled || auth.user?.role === 'admin'"
      to="/walmart-accounts"
      active-class="selected"
    >
      <AppIcon name="ShieldCheck" />沃尔玛绑定
    </RouterLink>
    <button
      v-if="!auth.enabled || auth.user?.role === 'admin'"
      class="btn"
      @click="codePoolOpen = true"
    >
      EAN 编码池 / 导入表格
    </button>
  </nav>
  <ListingPreparationSettings v-if="codePoolOpen" @close="codePoolOpen = false" />
</template>
<style scoped>
.store-management-nav {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.store-management-nav a {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: 8px;
  color: var(--muted);
  text-decoration: none;
  transition:
    background 150ms,
    color 150ms;
}
.store-management-nav a:hover {
  background: #eef2f7;
}
.store-management-nav a.selected {
  background: #e4ecf6;
  color: #365779;
  font-weight: 600;
}
</style>
