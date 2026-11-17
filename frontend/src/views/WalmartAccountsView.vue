<script setup lang="ts">
import StoreManagementNav from '../components/StoreManagementNav.vue';
import ListingPreparationSettings from '../components/ListingPreparationSettings.vue';
import { computed, onMounted, onBeforeUnmount, ref } from 'vue';
import { walmartApi, walmartScopeLabels, walmartAccessLabels } from '../api/walmart';
import type { WalmartConnections, WalmartStore, WalmartVerification } from '../api/walmart';
import { errorMessage } from '../utils/display';
import FilterSelect from '../components/FilterSelect.vue';
import AppIcon from '../components/AppIcon.vue';
const data = ref<WalmartConnections>(),
  loading = ref(true),
  busy = ref(false),
  error = ref(''),
  message = ref(''),
  search = ref(''),
  statusFilter = ref('');
const selected = ref<WalmartStore | null>(null),
  clientId = ref(''),
  secret = ref(''),
  verified = ref<WalmartVerification | null>(null),
  identityConfirmed = ref(false),
  expired = ref(false),
  unbindTarget = ref<WalmartStore | null>(null);
const preparationStore = ref<WalmartStore | null>(null),
  preparationOpen = ref(false);
let expiryTimer: ReturnType<typeof setTimeout> | undefined;
const stores = computed(() =>
  (data.value?.stores || []).filter((s) => {
    const q = search.value.trim().toLowerCase();
    return (
      (!q ||
        [s.name, s.account?.profile.partnerDisplayName, s.account?.profile.partnerId].some((v) =>
          v?.toLowerCase().includes(q),
        )) &&
      (!statusFilter.value ||
        (statusFilter.value === 'unbound'
          ? !s.account?.bound
          : s.account?.status === statusFilter.value))
    );
  }),
);
const boundCount = computed(() => data.value?.stores.filter((s) => s.account?.bound).length || 0);
const failedCount = computed(
  () => data.value?.stores.filter((s) => s.account?.status === 'error').length || 0,
);
function clearSecrets() {
  clientId.value = '';
  secret.value = '';
}
function clearPreview() {
  verified.value = null;
  identityConfirmed.value = false;
  expired.value = false;
  clearTimeout(expiryTimer);
}
function open(store: WalmartStore) {
  if (busy.value) return;
  selected.value = store;
  clearSecrets();
  clearPreview();
  error.value = '';
  message.value = '';
}
function close() {
  if (busy.value) return;
  selected.value = null;
  clearSecrets();
  clearPreview();
}
async function load() {
  data.value = await walmartApi.accounts();
}
async function refresh() {
  loading.value = true;
  error.value = '';
  try {
    await load();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loading.value = false;
  }
}
async function preview() {
  if (!selected.value) return;
  busy.value = true;
  error.value = '';
  clearPreview();
  try {
    verified.value = await walmartApi.preview(
      selected.value.store_id,
      clientId.value.trim(),
      secret.value.trim(),
    );
    expiryTimer = setTimeout(
      () => {
        expired.value = true;
      },
      Math.max(0, verified.value.expires_at * 1000 - Date.now()),
    );
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    clearSecrets();
    busy.value = false;
  }
}
async function bind() {
  if (!selected.value || !verified.value || !identityConfirmed.value || expired.value) return;
  busy.value = true;
  error.value = '';
  try {
    await walmartApi.bind(selected.value.store_id, verified.value.verification_id);
    const name = selected.value.name;
    selected.value = null;
    clearPreview();
    clearSecrets();
    await load();
    message.value = `${name} 已绑定沃尔玛美国站。`;
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function reverify(s: WalmartStore) {
  if (!s.account) return;
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    const account = await walmartApi.verify(s.store_id, s.account.revision);
    await load();
    if (account.status === 'error') error.value = account.last_error;
    else message.value = `${s.name} 连接验证通过。`;
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function unbind() {
  if (!unbindTarget.value?.account) return;
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    await walmartApi.unbind(unbindTarget.value.store_id, unbindTarget.value.account.revision);
    const name = unbindTarget.value.name;
    unbindTarget.value = null;
    await load();
    message.value = `${name} 已解绑，已删除系统保存的 API 凭证。`;
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
const date = (v?: string) => (v ? new Date(v).toLocaleString('zh-CN') : '尚未验证');
function escape(e: KeyboardEvent) {
  if (e.key === 'Escape' && !busy.value) {
    close();
    unbindTarget.value = null;
  }
}
onMounted(() => {
  void refresh();
  document.addEventListener('keydown', escape);
});
onBeforeUnmount(() => {
  clearSecrets();
  clearTimeout(expiryTimer);
  document.removeEventListener('keydown', escape);
});
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">STORES / 店铺管理</p>
      <h1>店铺管理</h1>
      <p class="subtitle">各店铺独立授权，验证实际账号后确认绑定。</p>
    </div>
  </div>
  <StoreManagementNav />
  <div class="wm-filters">
    <span class="muted">默认发货仓与已批准豁免可在各店铺卡片配置。</span>
  </div>
  <div class="wm-summary">
    <div>
      <span>系统店铺</span><strong>{{ data?.stores.length ?? '—' }}</strong>
    </div>
    <div>
      <span>已绑定</span><strong>{{ boundCount }}</strong>
    </div>
    <div>
      <span>需检查连接</span
      ><strong :class="{ 'wm-error-text': failedCount }">{{ failedCount }}</strong>
    </div>
    <div>
      <span>当前站点</span><strong class="wm-market">Walmart US</strong
      ><small>美国站 · 仅授权管理</small>
    </div>
  </div>
  <p class="notice notice-space">
    本模块管理 API 连接。绑定和验证不会上架商品，也不代表已通过商品刊登权限或类目校验。
  </p>
  <div v-if="data && !data.vault.ready" class="notice warning notice-space">
    <strong>需要初始化一次加密主密钥</strong>
    <p>{{ data.vault.message }}。只需初始化一次，之后每家店铺均可在网页绑定。</p>
    <p>
      本机：<code>python -m catalog.credential_vault init</code>；服务器请参照项目文档运行
      <code>deploy/init-walmart-vault.sh</code>。
    </p>
  </div>
  <div v-if="error" class="error-box" role="alert">{{ error }}</div>
  <p v-if="message" class="notice notice-space" role="status">{{ message }}</p>
  <div class="wm-filters">
    <input
      v-model="search"
      type="search"
      placeholder="搜索系统店铺、沃尔玛名称或 Partner ID"
      aria-label="搜索绑定店铺"
    /><FilterSelect
      label="筛选连接状态"
      v-model="statusFilter"
      :options="[
        { value: '', label: '全部连接状态' },
        { value: 'verified', label: '验证通过' },
        { value: 'error', label: '验证异常' },
        { value: 'unbound', label: '未绑定' },
      ]"
    /><button class="btn" :disabled="busy || loading" @click="refresh">刷新</button>
  </div>
  <p v-if="loading" role="status">正在读取店铺连接…</p>
  <div v-else class="wm-stores">
    <article v-for="s in stores" :key="s.store_id" class="wm-store">
      <header>
        <div>
          <h2>{{ s.name }}</h2>
          <span>{{ s.sku_code ? 'SKU 前缀 MY' + s.sku_code : '未设置 SKU 店铺编码' }}</span>
        </div>
        <span
          class="wm-badge"
          :class="{
            'wm-badge-good': s.account?.status === 'verified',
            'wm-badge-error': s.account?.status === 'error',
          }"
          >{{
            !s.active
              ? '系统已停用'
              : s.account?.status === 'verified'
                ? '最近验证通过'
                : s.account?.status === 'error'
                  ? '连接需检查'
                  : '未绑定'
          }}</span
        >
      </header>
      <template v-if="s.account?.bound"
        ><div class="wm-profile">
          <span>沃尔玛实际店铺</span
          ><strong>{{
            s.account.profile.partnerDisplayName || s.account.profile.partnerName
          }}</strong
          ><small>Partner ID：{{ s.account.profile.partnerId }} · 美国站</small>
        </div>
        <dl class="wm-info">
          <div>
            <dt>API Client ID</dt>
            <dd>{{ s.account.client_hint }}</dd>
          </div>
          <div>
            <dt>上次验证通过</dt>
            <dd>{{ date(s.account.last_verified_at) }}</dd>
          </div>
          <div>
            <dt>最近尝试</dt>
            <dd>{{ date(s.account.last_attempt_at) }}</dd>
          </div>
          <div>
            <dt>平台账号状态</dt>
            <dd>{{ s.account.profile.accountStatus || '未返回' }}</dd>
          </div>
        </dl>
        <p v-if="s.account.last_error" class="wm-error-text">{{ s.account.last_error }}</p>
        <details>
          <summary>查看已核验权限</summary>
          <div class="wm-scopes">
            <span v-for="(access, key) in s.account.scopes" :key="key"
              >{{ walmartScopeLabels[key] || key
              }}<b>{{ walmartAccessLabels[access] || access }}</b></span
            >
            <p v-if="!Object.keys(s.account.scopes).length">
              接口未返回可识别权限，正式接入刊登前需单独核对。
            </p>
          </div>
        </details></template
      >
      <p v-else class="wm-empty">绑定该店铺的 API 密钥，核对沃尔玛账号名称和编号后保存。</p>
      <footer>
        <button
          v-if="s.account?.bound"
          class="btn"
          :disabled="busy || !s.active"
          @click="
            preparationStore = s;
            preparationOpen = true;
          "
        >
          生成前提配置
        </button>
        <button
          class="btn primary"
          :disabled="busy || !s.active || !data?.vault.ready"
          @click="open(s)"
        >
          {{ s.account?.bound ? '更换密钥' : '绑定店铺' }}</button
        ><button
          v-if="s.account?.bound"
          class="btn"
          :disabled="busy || !s.active || !data?.vault.ready"
          @click="reverify(s)"
        >
          重新验证</button
        ><button
          v-if="s.account?.bound"
          class="btn wm-unbind"
          :disabled="busy"
          @click="unbindTarget = s"
        >
          解绑
        </button>
      </footer>
    </article>
    <p v-if="!stores.length" class="wm-empty">没有匹配店铺。可先到“管理系统店铺”添加。</p>
  </div>
  <ListingPreparationSettings
    v-if="preparationOpen"
    :store="preparationStore"
    @close="preparationOpen = false"
  />
  <div v-if="selected" class="wm-overlay" @click.self="close">
    <section class="wm-dialog" role="dialog" aria-modal="true" aria-labelledby="wm-bind-title">
      <header>
        <div>
          <p class="eyebrow">WALMART US</p>
          <h2 id="wm-bind-title">
            {{ selected.account?.bound ? '更换店铺密钥' : '绑定店铺' }} · {{ selected.name }}
          </h2>
        </div>
        <button class="wm-close" aria-label="关闭绑定窗口" :disabled="busy" @click="close">
          <AppIcon name="X" />
        </button>
      </header>
      <template v-if="!verified"
        ><p>填写这家店铺在沃尔玛开发者后台的个人 API 密钥。系统只向沃尔玛官方美国站接口验证。</p>
        <form autocomplete="off" @submit.prevent="preview">
          <label
            >Client ID<input
              v-model="clientId"
              type="password"
              autocomplete="new-password"
              required
              maxlength="1000"
              :disabled="busy" /></label
          ><label
            >Client Secret<input
              v-model="secret"
              type="password"
              autocomplete="new-password"
              required
              maxlength="1000"
              :disabled="busy" /></label
          ><small>密钥不会回显；验证后清空输入框。更换失败时保留原绑定。</small
          ><button class="btn primary" :disabled="busy || !clientId.trim() || !secret.trim()">
            {{ busy ? '正在验证…' : '验证密钥并读取店铺' }}
          </button>
        </form></template
      >
      <template v-else
        ><div class="wm-verification">
          <span class="wm-badge wm-badge-good">沃尔玛身份验证通过</span>
          <h3>{{ verified.profile.partnerDisplayName || verified.profile.partnerName }}</h3>
          <dl class="wm-info">
            <div>
              <dt>Partner ID</dt>
              <dd>{{ verified.profile.partnerId }}</dd>
            </div>
            <div>
              <dt>Store ID</dt>
              <dd>{{ verified.profile.partnerStoreId || '未返回' }}</dd>
            </div>
            <div>
              <dt>系统目标店铺</dt>
              <dd>{{ selected.name }}</dd>
            </div>
            <div>
              <dt>站点</dt>
              <dd>美国站 Walmart US</dd>
            </div>
          </dl>
        </div>
        <p>请对照卖家后台核对实际店铺身份。验证预览 10 分钟内有效。</p>
        <label class="wm-confirm"
          ><input
            v-model="identityConfirmed"
            type="checkbox"
            :disabled="busy || expired"
          />我确认以上沃尔玛账号属于系统店铺 {{ selected.name }}</label
        >
        <p v-if="expired" class="wm-error-text">预览已过期，请重新输入密钥验证。</p>
        <div class="wm-dialog-actions">
          <button class="btn" :disabled="busy" @click="clearPreview">重新填写</button
          ><button
            class="btn primary"
            :disabled="busy || !identityConfirmed || expired"
            @click="bind"
          >
            {{ busy ? '正在保存…' : '确认绑定' }}
          </button>
        </div></template
      >
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
    </section>
  </div>
  <div v-if="unbindTarget" class="wm-overlay" @click.self="!busy && (unbindTarget = null)">
    <section
      class="wm-dialog wm-dialog-small"
      role="dialog"
      aria-modal="true"
      aria-labelledby="wm-unbind-title"
    >
      <header>
        <h2 id="wm-unbind-title">解绑 {{ unbindTarget.name }}？</h2>
        <button
          class="wm-close"
          aria-label="关闭解绑窗口"
          :disabled="busy"
          @click="unbindTarget = null"
        >
          <AppIcon name="X" />
        </button>
      </header>
      <p>
        将删除系统保存的 API
        凭证，保留操作记录。不会删除沃尔玛商品，也不会撤销沃尔玛后台密钥；可重新绑定。
      </p>
      <div class="wm-dialog-actions">
        <button class="btn" :disabled="busy" @click="unbindTarget = null">取消</button
        ><button class="btn primary" :disabled="busy" @click="unbind">确认解绑</button>
      </div>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
    </section>
  </div>
</template>
<style scoped>
.wm-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin: 24px 0;
}
.wm-summary > div {
  padding: 20px;
  border: 1px solid #dce4e9;
  background: #fff;
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.wm-summary span {
  font-size: 14px;
  color: #667d8c;
}
.wm-summary strong {
  font-size: 30px;
}
.wm-summary strong.wm-market {
  font-size: 22px;
}
.wm-summary small {
  color: #667d8c;
}
.wm-filters {
  display: flex;
  gap: 12px;
  margin: 24px 0;
}
.wm-filters > input {
  flex: 1;
  min-width: 160px;
  border: 1px solid #d5e0e7;
  border-radius: 9px;
  padding: 10px 14px;
  background: #fff;
  color: #2e4352;
  font: inherit;
}
.wm-stores {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 20px;
}
.wm-store {
  background: #fff;
  border: 1px solid #dce4e9;
  padding: 24px;
  border-radius: 16px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
}
.wm-store header,
.wm-dialog header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.wm-store h2,
.wm-dialog h2 {
  font-size: 20px;
  margin: 0 0 6px;
}
.wm-store header div > span {
  font-size: 12px;
  color: #748692;
}
.wm-badge {
  font-size: 12px;
  padding: 5px 9px;
  border-radius: 6px;
  background: #eef2f5;
  color: #657784;
  white-space: nowrap;
}
.wm-badge-good {
  background: #edf6f0;
  color: #32734d;
}
.wm-badge-error {
  background: #fff0f0;
  color: #b42332;
}
.wm-profile {
  padding: 16px;
  background: #f1f6f9;
  border-radius: 10px;
  display: grid;
  gap: 6px;
}
.wm-profile > span {
  font-size: 12px;
  color: #627b8e;
}
.wm-profile strong {
  font-size: 18px;
  overflow-wrap: anywhere;
}
.wm-profile small {
  color: #607483;
  overflow-wrap: anywhere;
}
.wm-info {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin: 0;
}
.wm-info dt {
  font-size: 12px;
  color: #718391;
  margin-bottom: 5px;
}
.wm-info dd {
  font-size: 14px;
  margin: 0;
  overflow-wrap: anywhere;
}
.wm-store footer {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: auto;
}
.wm-unbind {
  margin-left: auto;
  color: #b42332;
}
.wm-store summary {
  cursor: pointer;
  font-size: 14px;
  color: #496b82;
}
.wm-scopes {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}
.wm-scopes > span {
  font-size: 12px;
  padding: 5px 8px;
  background: #f4f7f9;
  border-radius: 6px;
}
.wm-scopes b {
  margin-left: 8px;
  font-weight: 500;
}
.wm-error-text {
  color: #b42332 !important;
  font-size: 14px;
  line-height: 1.6;
}
.wm-empty {
  color: #748692;
  font-size: 14px;
  line-height: 1.8;
}
.wm-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(27, 43, 57, 0.3);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.wm-dialog {
  background: #fff;
  border: 1px solid #dce4e9;
  border-radius: 18px;
  width: 600px;
  max-width: 100%;
  max-height: 90vh;
  overflow-y: auto;
  padding: 28px;
  box-shadow: 0 24px 80px #172d4924;
  display: grid;
  gap: 20px;
}
.wm-dialog-small {
  width: 480px;
}
.wm-dialog p {
  font-size: 14px;
  line-height: 1.7;
  margin: 0;
  color: #627888;
}
.wm-close {
  border: 0;
  background: transparent;
  color: #6a7d8a;
  cursor: pointer;
  padding: 4px;
  display: grid;
  place-items: center;
}
.wm-close svg {
  width: 20px;
  height: 20px;
}
.wm-dialog form {
  display: grid;
  gap: 18px;
}
.wm-dialog form label {
  display: grid;
  gap: 8px;
  font-size: 14px;
}
.wm-dialog small {
  font-size: 12px;
  color: #6d8190;
  line-height: 1.7;
}
.wm-verification {
  display: grid;
  gap: 18px;
  padding: 18px;
  background: #f4f8fa;
  border-radius: 12px;
}
.wm-verification .wm-badge {
  justify-self: start;
}
.wm-verification h3 {
  margin: 0;
  font-size: 22px;
}
.wm-confirm {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  font-size: 14px;
  line-height: 1.6;
}
.wm-confirm input {
  margin-top: 3px;
  flex-shrink: 0;
}
.wm-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
@media (max-width: 760px) {
  .wm-summary {
    grid-template-columns: 1fr 1fr;
  }
  .wm-stores {
    grid-template-columns: 1fr;
  }
  .wm-filters {
    flex-wrap: wrap;
  }
  .wm-dialog {
    padding: 20px;
  }
  .wm-overlay {
    padding: 16px;
  }
}
</style>

<style scoped>
.wm-dialog input[type='password'] {
  width: 100%;
  border: 1px solid #d5e0e7;
  border-radius: 9px;
  padding: 11px 14px;
  background: #f8fafc;
  font: inherit;
  color: #263d4f;
}
.wm-filters > input:focus,
.wm-dialog input[type='password']:focus {
  outline: 2px solid #a6bed0;
  outline-offset: 2px;
  border-color: #6f97b3;
}
</style>
