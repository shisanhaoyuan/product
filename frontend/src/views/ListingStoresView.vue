<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useAuth } from '../composables/useAuth';
import { prelistingApi } from '../api/prelisting';
import type { ListingStore } from '../api/types';
import { errorMessage } from '../utils/display';
import AppIcon from '../components/AppIcon.vue';
const { auth } = useAuth();
const canEdit = computed(
  () => !auth.enabled || ['admin', 'editor'].includes(auth.user?.role || ''),
);
const stores = ref<ListingStore[]>([]),
  name = ref(''),
  alias = ref(''),
  code = ref(''),
  codes = ref<Record<number, string>>({}),
  error = ref(''),
  busy = ref(false);
async function load() {
  try {
    stores.value = await prelistingApi.stores();
    codes.value = Object.fromEntries(stores.value.map((s) => [s.id, s.sku_code || '']));
  } catch (e) {
    error.value = errorMessage(e);
  }
}
async function add() {
  busy.value = true;
  error.value = '';
  try {
    await prelistingApi.addStore(name.value.trim(), alias.value.trim(), code.value || undefined);
    name.value = '';
    alias.value = '';
    code.value = '';
    await load();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function saveCode(s: ListingStore) {
  busy.value = true;
  error.value = '';
  try {
    await prelistingApi.setStoreCode(s.id, codes.value[s.id] || '');
    await load();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function toggle(s: ListingStore) {
  busy.value = true;
  error.value = '';
  try {
    await prelistingApi.setStoreActive(s.id, !s.active);
    await load();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">STORES / 目标店铺</p>
      <h1>上架店铺</h1>
    </div>
    <RouterLink class="btn" to="/prelisting">返回预上架</RouterLink>
  </div>
  <p class="notice notice-space">
    已录入截图可见店铺及商品库已有店铺，未推测缺失名称。店铺名称用于创建批次，不代表已连接平台账号。
  </p>
  <section class="section">
    <form v-if="canEdit" class="section-body plan-form" @submit.prevent="add">
      <label
        >系统店铺名<input
          v-model="name"
          required
          maxlength="100"
          placeholder="例如 MYZ-L-24"
          :disabled="busy" /></label
      ><label
        >平台显示名（可选）<input
          v-model="alias"
          maxlength="100"
          placeholder="与系统名称不同时填写"
          :disabled="busy" /></label
      ><label
        >SKU 店铺编码（可选）<input
          v-model="code"
          pattern="[A-Za-z0-9]{2}"
          maxlength="2"
          placeholder="如 21、H2"
          :disabled="busy" /></label
      ><button class="btn primary" :disabled="busy || !name.trim()">添加店铺</button>
    </form>
    <div v-if="error" class="error-box">{{ error }}</div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>系统店铺名</th>
            <th>平台显示名</th>
            <th>SKU 店铺编码</th>
            <th>来源</th>
            <th>状态</th>
            <th v-if="canEdit">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in stores" :key="s.id">
            <td>{{ s.name }}</td>
            <td>{{ s.marketplace_name }}</td>
            <td>
              <form v-if="canEdit" class="store-code-editor" @submit.prevent="saveCode(s)">
                <span>MY</span>
                <input
                  v-model="codes[s.id]"
                  :aria-label="s.name + ' SKU 店铺编码'"
                  pattern="[A-Za-z0-9]{2}"
                  maxlength="2"
                  required
                  placeholder="--"
                  :disabled="busy"
                />
                <button
                  class="btn small"
                  :aria-label="'保存 ' + s.name + ' SKU 编码'"
                  title="保存店铺编码"
                  :disabled="busy || codes[s.id] === s.sku_code"
                >
                  <AppIcon name="Save" />
                </button>
              </form>
              <span v-else>{{ s.sku_code ? 'MY' + s.sku_code : '未配置' }}</span>
            </td>
            <td>
              {{
                s.origin === 'user_screenshot'
                  ? '用户截图'
                  : s.origin === 'catalog'
                    ? '商品库记录'
                    : '手动添加'
              }}
            </td>
            <td>{{ s.active ? '启用' : '停用' }}</td>
            <td v-if="canEdit">
              <button class="btn small" :disabled="busy" @click="toggle(s)">
                {{ s.active ? '停用' : '启用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
