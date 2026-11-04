<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { catalogApi } from '../api/client';
import type { ProductPage, PrelistingSelection } from '../api/types';
import { useCatalog } from '../composables/useCatalog';
import { errorMessage, priceRange, productStatus } from '../utils/display';
import AppIcon from '../components/AppIcon.vue';
import ProductCard from '../components/ProductCard.vue';
import ProductImage from '../components/ProductImage.vue';
import StatusBadge from '../components/StatusBadge.vue';
import PrelistingComposer from '../components/PrelistingComposer.vue';
import { useAuth } from '../composables/useAuth';
const { auth } = useAuth();
const canEdit = computed(
  () => !auth.enabled || ['admin', 'editor'].includes(auth.user?.role || ''),
);
const selected = ref(new Set<string>()),
  allFiltered = ref(false),
  composer = ref(false);
const composerSelection = ref<PrelistingSelection>({}),
  composerCount = ref(0);

const route = useRoute(),
  router = useRouter();
const { stats, refreshStats } = useCatalog();
const data = ref<ProductPage>();
const loading = ref(true),
  error = ref(''),
  search = ref('');
const view = ref(localStorage.getItem('catalog-view') === 'table' ? 'table' : 'grid');
let controller: AbortController | undefined, searchTimer: ReturnType<typeof setTimeout> | undefined;
const filters = computed(() => {
  const params = new URLSearchParams();
  for (const key of [
    'q',
    'store',
    'brand',
    'status',
    'date',
    'category',
    'seller_status',
    'page',
  ]) {
    const value = route.query[key];
    if (typeof value === 'string' && value) params.set(key, value);
  }
  if (route.path === '/review' && !params.has('status')) params.set('status', 'review');
  return params;
});
const selectionFilters = computed(() => {
  const values = new URLSearchParams(filters.value);
  values.delete('page');
  return Object.fromEntries(values);
});
const selectedCount = computed(() =>
  allFiltered.value ? data.value?.total || 0 : selected.value.size,
);
function clearSelection() {
  selected.value = new Set();
  allFiltered.value = false;
}
function toggleProduct(id: string) {
  const next = new Set(selected.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selected.value = next;
}
function selectPage() {
  allFiltered.value = false;
  selected.value = new Set([...selected.value, ...(data.value?.items.map((p) => p.id) || [])]);
}
function openComposer() {
  composerSelection.value = allFiltered.value
    ? { filters: { ...selectionFilters.value } }
    : { listing_ids: [...selected.value] };
  composerCount.value = selectedCount.value;
  composer.value = true;
}
function created(id: string) {
  composer.value = false;
  clearSelection();
  void router.push('/prelisting/' + id);
}
watch(() => JSON.stringify(selectionFilters.value), clearSelection);
const pages = computed(() =>
  Math.max(1, Math.ceil((data.value?.total || 0) / (data.value?.page_size || 24))),
);
const metrics = computed(() =>
  stats.value
    ? [
        {
          label: '商品模板',
          value: stats.value.listings,
          icon: 'Package',
          foot: `${stats.value.products} 个来源商品 · ${stats.value.stores.length} 家店铺`,
          type: 'products',
          progress: null,
        },
        {
          label: '子变体',
          value: stats.value.variants,
          icon: 'Layers',
          foot: '按 SKU 独立记录',
          type: 'variants',
          progress: null,
        },
        {
          label: '保存已确认',
          value: stats.value.saved,
          icon: 'ShieldCheck',
          foot: `${stats.value.reopened} 个模板含重开回读`,
          type: 'saved',
          progress: stats.value.saved,
        },
        {
          label: '待复核',
          value: stats.value.review,
          icon: 'ScanLine',
          foot: `${stats.value.complete} 个模板已核验完整`,
          type: 'review',
          progress: stats.value.review,
        },
      ]
    : [],
);
const tabs = computed(() => [
  { value: '', label: '全部商品', count: stats.value?.listings },
  { value: 'complete', label: '核验完整', count: stats.value?.complete },
  { value: 'review', label: '待复核', count: stats.value?.review },
]);

function changeFilter(key: string, value: string) {
  clearTimeout(searchTimer);
  const query = Object.fromEntries(filters.value);
  if (value) query[key] = value;
  else delete query[key];
  delete query.page;
  return router.push({ path: '/products', query });
}
function searchInput(event: Event) {
  search.value = (event.target as HTMLInputElement).value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => changeFilter('q', search.value.trim()), 450);
}
function changeView(value: string) {
  view.value = value;
  localStorage.setItem('catalog-view', value);
}
function changePage(offset: number) {
  router.push({
    path: '/products',
    query: { ...Object.fromEntries(filters.value), page: String((data.value?.page || 1) + offset) },
  });
  window.scrollTo(0, 0);
}
async function load() {
  controller?.abort();
  const request = new AbortController();
  controller = request;
  clearTimeout(searchTimer);
  search.value = filters.value.get('q') || '';
  loading.value = true;
  error.value = '';
  try {
    if (!stats.value) await refreshStats();
    if (request.signal.aborted) return;
    const result = await catalogApi.products(filters.value.toString(), request.signal);
    if (!request.signal.aborted) data.value = result;
  } catch (reason) {
    if (!request.signal.aborted) error.value = errorMessage(reason);
  } finally {
    if (!request.signal.aborted) loading.value = false;
  }
}
watch(() => route.fullPath, load, { immediate: true });
onBeforeUnmount(() => {
  controller?.abort();
  clearTimeout(searchTimer);
});
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">CATALOG / 商品管理</p>
      <h1>
        商品库<span class="heading-count">{{ stats?.listings ?? '—' }}</span>
      </h1>
    </div>
    <div class="actions">
      <RouterLink class="btn" to="/prelisting">预上架批次</RouterLink>
      <a
        class="btn"
        id="export-button"
        :href="'/api/export.csv?' + filters"
        title="导出清单"
        aria-label="导出清单"
        ><AppIcon name="Download" /><span class="button-label">导出清单</span></a
      ><RouterLink class="btn primary" to="/integrations"
        ><AppIcon name="Plus" />接入数据</RouterLink
      >
    </div>
  </div>
  <div class="stats">
    <div v-for="metric in metrics" :key="metric.type" class="stat" :class="'stat-' + metric.type">
      <span class="stat-icon"><AppIcon :name="metric.icon" /></span>
      <div class="stat-content">
        <div class="stat-label">{{ metric.label }}</div>
        <div class="stat-number">{{ metric.value }}</div>
        <div class="stat-foot">{{ metric.foot }}</div>
        <progress
          v-if="metric.progress !== null"
          class="stat-track"
          :value="metric.progress"
          :max="stats?.listings || 1"
          :aria-label="metric.label + '占全部模板'"
        />
      </div>
    </div>
  </div>
  <section class="library-panel">
    <div class="panel-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        class="tab"
        :class="{ active: (filters.get('status') || '') === tab.value }"
        :aria-pressed="(filters.get('status') || '') === tab.value"
        @click="changeFilter('status', tab.value)"
      >
        {{ tab.label }}<span>{{ tab.count ?? '—' }}</span>
      </button>
      <div class="view-controls" role="group" aria-label="显示方式">
        <button
          v-for="mode in ['grid', 'table']"
          :key="mode"
          class="view-toggle"
          :class="{ active: view === mode }"
          :data-view="mode"
          :aria-label="mode === 'grid' ? '卡片视图' : '表格视图'"
          :title="mode === 'grid' ? '卡片视图' : '表格视图'"
          :aria-pressed="view === mode"
          @click="changeView(mode)"
        >
          <AppIcon :name="mode === 'grid' ? 'LayoutGrid' : 'List'" />
        </button>
      </div>
    </div>
    <form class="filter-bar" id="filters" @submit.prevent="changeFilter('q', search.trim())">
      <div class="search-wrap">
        <span><AppIcon name="Search" /></span
        ><input
          id="search"
          type="search"
          name="q"
          :value="search"
          placeholder="搜索 ASIN、SKU、商品名称、卖家…"
          aria-label="搜索商品"
          @input="searchInput"
        />
      </div>
      <select
        class="filter"
        aria-label="筛选卖家黑名单"
        :value="filters.get('seller_status') || ''"
        @change="changeFilter('seller_status', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">全部卖家状态</option>
        <option value="blocked">命中卖家黑名单</option>
        <option value="unblocked">未命中黑名单</option>
      </select>
      <select
        class="filter category-filter"
        name="category"
        aria-label="筛选商品类目"
        title="按已记录的 Product Type 筛选；括号内数量为全库模板数"
        :value="filters.get('category') || ''"
        @change="changeFilter('category', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">全部商品类目</option>
        <option
          v-if="
            filters.get('category') &&
            !stats?.categories.some((item) => item.value === filters.get('category'))
          "
          :value="filters.get('category')!"
        >
          当前类目已无商品
        </option>
        <option v-for="category in stats?.categories" :key="category.value" :value="category.value">
          {{ category.label }} · {{ category.count }}
        </option>
      </select>
      <select
        class="filter"
        name="store"
        aria-label="筛选店铺"
        :value="filters.get('store') || ''"
        @change="changeFilter('store', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">全部来源店铺</option>
        <option v-for="store in stats?.stores" :key="store.store">{{ store.store }}</option>
      </select>
      <select
        class="filter"
        name="brand"
        aria-label="筛选品牌"
        :value="filters.get('brand') || ''"
        @change="changeFilter('brand', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">全部品牌</option>
        <option v-for="brand in stats?.brands" :key="brand">{{ brand }}</option>
      </select>
      <select
        class="filter"
        name="date"
        aria-label="筛选日期"
        :value="filters.get('date') || ''"
        @change="changeFilter('date', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">全部上传日期</option>
        <option v-for="date in stats?.dates" :key="date">{{ date }}</option></select
      ><button
        class="btn ghost small"
        id="reset"
        type="button"
        title="重置筛选"
        aria-label="重置筛选"
        @click="router.push('/products')"
      >
        <AppIcon name="RotateCcw" />
      </button>
    </form>
    <div v-if="canEdit" class="batch-toolbar">
      <div class="actions">
        <button class="btn small" :disabled="loading || !data?.total" @click="selectPage">
          选择本页
        </button>
        <button
          class="btn small"
          :disabled="loading || !data?.total || data.total > 500"
          @click="
            allFiltered = true;
            selected = new Set();
          "
        >
          选择筛选结果（跨页 {{ data?.total || 0 }} 件）
        </button>
        <button class="btn small" :disabled="!selectedCount" @click="clearSelection">清空</button>
      </div>
      <div class="actions">
        <span>已选 {{ selectedCount }} 件</span
        ><button
          class="btn primary"
          :disabled="loading || selectedCount < 1 || selectedCount > 500"
          @click="openComposer"
        >
          预上架
        </button>
      </div>
      <small v-if="(data?.total || 0) > 500">每批最多 500 件，请缩小筛选或手动勾选。</small>
    </div>
    <div class="result-line">
      <span id="result-count"
        ><template v-if="!loading && data"
          >共 <strong>{{ data.total }}</strong> 个商品模板 <span class="dot-separator">·</span>
          {{ filters.size ? '已应用筛选' : '按上传时间排序' }}</template
        ><template v-else>正在读取商品…</template></span
      ><span class="data-note"><i></i>历史记录快照 · 来源可追溯</span>
    </div>
    <div v-if="error" class="error-box">
      加载失败：{{ error }} <button class="btn small" @click="load">重试</button>
    </div>
    <div v-else-if="loading" class="product-grid">
      <div v-for="index in 6" :key="index" class="skeleton"></div>
    </div>
    <template v-else-if="data?.total">
      <div v-if="view === 'grid'" class="product-grid">
        <div v-for="product in data.items" :key="product.id" class="selectable-product">
          <label v-if="canEdit" class="product-select"
            ><input
              type="checkbox"
              :aria-label="'选择商品 ' + product.sku"
              :checked="allFiltered || selected.has(product.id)"
              :disabled="allFiltered"
              @change="toggleProduct(product.id)"
            />选择</label
          >
          <ProductCard :product="product" />
        </div>
      </div>
      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th v-if="canEdit">选择</th>
              <th>商品</th>
              <th>ASIN / SKU</th>
              <th>店铺 / 品牌</th>
              <th>来源 / 卖家</th>
              <th>售价 USD</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="product in data.items" :key="product.id">
              <td v-if="canEdit">
                <input
                  type="checkbox"
                  :aria-label="'选择商品 ' + product.sku"
                  :checked="allFiltered || selected.has(product.id)"
                  :disabled="allFiltered"
                  @change="toggleProduct(product.id)"
                />
              </td>
              <td>
                <RouterLink class="table-product" :to="'/product/' + product.id"
                  ><ProductImage :src="product.image" image-class="table-thumb" /><span
                    class="table-product-copy"
                    ><span class="table-product-title">{{ product.title || '暂无标题' }}</span
                    ><span class="category-label" :title="product.category || '未记录类目'">{{
                      product.category || '未记录类目'
                    }}</span></span
                  ></RouterLink
                >
              </td>
              <td class="mono">
                {{ product.asin }}<br /><span class="muted">{{ product.sku }}</span>
              </td>
              <td>
                {{ product.store }}<br /><span class="muted">{{ product.brand }}</span>
              </td>
              <td>
                {{ product.source }}<br /><span class="muted">{{
                  product.seller || '卖家未采集'
                }}</span>
                <div v-if="product.seller_blacklist.length" class="seller-risk-label">
                  卖家已拉黑 · {{ product.seller_blacklist.length }} 位
                </div>
              </td>
              <td>{{ priceRange(product.prices) }}</td>
              <td>
                <StatusBadge :type="productStatus(product).type">{{
                  productStatus(product).label
                }}</StatusBadge>
              </td>
              <td>
                <RouterLink class="detail-link" :to="'/product/' + product.id">详情 ↗</RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span>每页 {{ data.page_size }} 条 · 共 {{ data.total }} 条</span>
        <div class="page-buttons">
          <button
            class="btn"
            id="prev"
            title="上一页"
            aria-label="上一页"
            :disabled="data.page <= 1"
            @click="changePage(-1)"
          >
            <AppIcon name="ChevronLeft" /></button
          ><span class="page-number">{{ data.page }}</span
          ><span>/ {{ pages }}</span
          ><button
            class="btn"
            id="next"
            title="下一页"
            aria-label="下一页"
            :disabled="data.page >= pages"
            @click="changePage(1)"
          >
            <AppIcon name="ChevronRight" />
          </button>
        </div>
      </div>
    </template>
    <div v-else class="empty">
      没有找到对应商品
      <p>试试其他 ASIN、SKU，或重置筛选条件。</p>
      <button class="btn small" @click="router.push('/products')">重置筛选</button>
    </div>
  </section>
  <PrelistingComposer
    v-if="composer"
    :selection="composerSelection"
    :count="composerCount"
    @close="composer = false"
    @created="created"
  />
  <div class="notice">
    <AppIcon name="Database" />
    <div>历史快照 · 商品价格、库存与文案以详情中的回读记录为准。</div>
  </div>
</template>
