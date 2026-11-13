<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { catalogApi } from '../api/client';
import type { ProductPage, PrelistingSelection } from '../api/types';
import { useCatalog } from '../composables/useCatalog';
import { errorMessage, priceRange, productStatus } from '../utils/display';
import AppIcon from '../components/AppIcon.vue';
import FilterSelect from '../components/FilterSelect.vue';
import ProductCard from '../components/ProductCard.vue';
import ListingCoverage from '../components/ListingCoverage.vue';
import ListingCountFilter from '../components/ListingCountFilter.vue';
import ProductImage from '../components/ProductImage.vue';
import StatusBadge from '../components/StatusBadge.vue';
import PrelistingComposer from '../components/PrelistingComposer.vue';
import { useAuth } from '../composables/useAuth';
const { auth } = useAuth();
const canEdit = computed(
  () => !isGiga.value && (!auth.enabled || ['admin', 'editor'].includes(auth.user?.role || '')),
);
const selected = ref(new Set<string>()),
  allFiltered = ref(false),
  composer = ref(false);
const composerSelection = ref<PrelistingSelection>({}),
  composerCount = ref(0);

const route = useRoute(),
  router = useRouter();
const isGiga = computed(() => route.path === '/giga-products');
const source = computed(() => (isGiga.value ? 'GigaB2B' : 'Amazon'));
const libraryPath = computed(() => (isGiga.value ? '/giga-products' : '/products'));
const libraryName = computed(() => (isGiga.value ? '大建云仓商品库' : '亚马逊商品库'));
const { stats, refreshStats } = useCatalog(source);
const detailPath = (id: string) => (isGiga.value ? '/giga-product/' : '/product/') + id;
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
    'listing_count_min',
    'listing_count_max',
    'page',
  ]) {
    const value = route.query[key];
    if (typeof value === 'string' && value) params.set(key, value);
  }
  if (route.path === '/review' && !params.has('status')) params.set('status', 'review');
  params.set('source', source.value);
  return params;
});
const categoryOptions = computed(() => {
  const currentCategory = filters.value.get('category') || '';
  const categories = stats.value?.categories || [];

  return [
    { value: '', label: '全部商品类目' },
    ...(currentCategory && !categories.some((item) => item.value === currentCategory)
      ? [{ value: currentCategory, label: '当前类目已无商品' }]
      : []),
    ...categories.map((item) => ({
      value: item.value,
      label: `${item.label} · ${item.count}`,
    })),
  ];
});
const storeOptions = computed(() => [
  { value: '', label: '全部来源店铺' },
  ...(stats.value?.stores.map((item) => ({ value: item.store, label: item.store })) || []),
]);
const brandOptions = computed(() => [
  { value: '', label: '全部品牌' },
  ...(stats.value?.brands.map((brand) => ({ value: brand, label: brand })) || []),
]);
const dateOptions = computed(() => [
  { value: '', label: '全部上传日期' },
  ...(stats.value?.dates.map((date) => ({ value: date, label: date })) || []),
]);
const selectionFilters = computed(() => {
  const values = new URLSearchParams(filters.value);
  values.delete('page');
  values.set('deduplicate', 'true');
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
const pageIds = computed(() => data.value?.items.map((p) => p.id) || []);

const allPageSelected = computed(
  () =>
    pageIds.value.length > 0 &&
    (allFiltered.value || pageIds.value.every((id) => selected.value.has(id))),
);

const somePageSelected = computed(
  () => !allPageSelected.value && pageIds.value.some((id) => selected.value.has(id)),
);

function togglePage() {
  if (loading.value || allFiltered.value) return;

  const next = new Set(selected.value);
  const shouldSelect = !allPageSelected.value;

  for (const id of pageIds.value) {
    if (shouldSelect) next.add(id);
    else next.delete(id);
  }

  selected.value = next;
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
watch(
  () => JSON.stringify(selectionFilters.value),
  () => {
    clearSelection();
    composer.value = false;
  },
);
const pages = computed(() =>
  Math.max(1, Math.ceil((data.value?.total || 0) / (data.value?.page_size || 24))),
);
const metrics = computed(() =>
  stats.value
    ? isGiga.value
      ? [
          {
            label: '供应商商品',
            value: stats.value.products,
            icon: 'Package',
            foot: '大建云仓独立来源库',
            type: 'products',
            progress: null,
          },
          {
            label: '商品记录',
            value: stats.value.listings,
            icon: 'Layers',
            foot: '按账号与 SKU 去重',
            type: 'variants',
            progress: null,
          },
          {
            label: '来源类目',
            value: stats.value.categories.filter((c) => c.value !== 'missing').length,
            icon: 'LayoutGrid',
            foot: '大建云仓原始类目',
            type: 'saved',
            progress: null,
          },
          {
            label: '来源品牌',
            value: stats.value.brands.filter(Boolean).length,
            icon: 'Package',
            foot: '接口已提供的品牌',
            type: 'review',
            progress: null,
          },
        ]
      : [
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
const tabs = computed(() =>
  isGiga.value
    ? [{ value: '', label: '全部云仓商品', count: stats.value?.unique_counts?.all_products }]
    : [
        { value: '', label: '全部商品', count: stats.value?.unique_counts?.all_products },
        { value: 'complete', label: '核验完整', count: stats.value?.unique_counts?.complete },
        { value: 'review', label: '待复核', count: stats.value?.unique_counts?.review },
      ],
);

function changeFilter(key: string, value: string) {
  clearTimeout(searchTimer);
  const query = Object.fromEntries(filters.value);
  if (value) query[key] = value;
  else delete query[key];
  delete query.page;
  return router.push({ path: libraryPath.value, query });
}
function changeListingCount(value: { minimum: string; maximum: string }) {
  clearTimeout(searchTimer);
  const query = Object.fromEntries(filters.value);
  for (const [key, count] of [
    ['listing_count_min', value.minimum],
    ['listing_count_max', value.maximum],
  ]) {
    if (count) query[key] = count;
    else delete query[key];
  }
  delete query.page;
  return router.push({ path: libraryPath.value, query });
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
    path: libraryPath.value,
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
  data.value = undefined;
  error.value = '';
  try {
    await refreshStats();
    if (request.signal.aborted) return;
    const params = new URLSearchParams(filters.value);
    params.set('deduplicate', 'true');
    const result = await catalogApi.products(params.toString(), request.signal);
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
        {{ libraryName }}<span class="heading-count">{{ data?.total ?? '—' }}</span>
      </h1>
    </div>
    <div class="actions">
      <RouterLink v-if="!isGiga" class="btn" to="/prelisting">预上架批次</RouterLink>
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
          :placeholder="
            isGiga ? '搜索供应商 SKU、商品名称、卖家…' : '搜索 ASIN、SKU、商品名称、卖家…'
          "
          aria-label="搜索商品"
          @input="searchInput"
        />
      </div>
      <FilterSelect
        label="筛选卖家黑名单"
        :model-value="filters.get('seller_status') || ''"
        :options="[
          { value: '', label: '全部卖家状态' },
          { value: 'blocked', label: '命中卖家黑名单' },
          { value: 'unblocked', label: '未命中黑名单' },
        ]"
        @update:model-value="changeFilter('seller_status', $event)"
      />
      <FilterSelect
        class="category-filter"
        label="筛选商品类目"
        title="按已记录的 Product Type 或供应商来源类目筛选；括号内数量为全库记录数"
        :model-value="filters.get('category') || ''"
        :options="categoryOptions"
        @update:model-value="changeFilter('category', $event)"
      />
      <FilterSelect
        label="筛选店铺"
        :model-value="filters.get('store') || ''"
        :options="storeOptions"
        @update:model-value="changeFilter('store', $event)"
      />
      <FilterSelect
        label="筛选品牌"
        :model-value="filters.get('brand') || ''"
        :options="brandOptions"
        @update:model-value="changeFilter('brand', $event)"
      />
      <FilterSelect
        label="筛选日期"
        :model-value="filters.get('date') || ''"
        :options="dateOptions"
        @update:model-value="changeFilter('date', $event)"
      />
      <ListingCountFilter
        v-if="!isGiga"
        :minimum="filters.get('listing_count_min') || ''"
        :maximum="filters.get('listing_count_max') || ''"
        @change="changeListingCount"
      />
      <button
        class="btn ghost small"
        id="reset"
        type="button"
        title="重置筛选"
        aria-label="重置筛选"
        @click="router.push(libraryPath)"
      >
        <AppIcon name="RotateCcw" />
      </button>
    </form>
    <div v-if="canEdit" class="batch-toolbar">
      <div class="actions">
        <label class="btn small">
          <input
            type="checkbox"
            :checked="allPageSelected"
            :indeterminate="somePageSelected"
            :disabled="loading || !data?.total || allFiltered"
            @change="togglePage"
          />
          全选本页
        </label>
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
          >共 <strong>{{ data.total }}</strong> 个商品 <span class="dot-separator">·</span>
          {{ filters.size > 1 ? '已应用筛选' : '按上传时间排序' }}</template
        ><template v-else>正在读取商品…</template></span
      ><span class="data-note"
        ><i></i
        >{{
          isGiga ? '按大建云仓账号与 SKU 独立建档' : '按 ASIN 去重 · 上架次数按亚马逊店铺模板统计'
        }}</span
      >
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
              <th v-if="!isGiga">上架次数 / 店铺</th>
              <th>{{ isGiga ? '供应商 SKU' : 'ASIN / SKU' }}</th>
              <th>店铺 / 品牌</th>
              <th>来源 / 卖家</th>
              <th>价格 / 币种</th>
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
                <RouterLink class="table-product" :to="detailPath(product.id)"
                  ><ProductImage :src="product.image" image-class="table-thumb" /><span
                    class="table-product-copy"
                    ><span class="table-product-title">{{ product.title || '暂无标题' }}</span
                    ><span class="category-label" :title="product.category || '未记录类目'">{{
                      product.category || '未记录类目'
                    }}</span></span
                  ></RouterLink
                >
              </td>
              <td v-if="!isGiga" class="listing-coverage-cell">
                <ListingCoverage :product="product" />
              </td>
              <td class="mono">
                <template v-if="!isGiga">{{ product.asin }}<br /></template
                ><span class="muted">{{ product.sku }}</span>
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
              <td>{{ priceRange(product.prices, product.currency ?? 'USD') }}</td>
              <td>
                <StatusBadge :type="productStatus(product).type">{{
                  productStatus(product).label
                }}</StatusBadge>
              </td>
              <td>
                <RouterLink class="detail-link" :to="detailPath(product.id)">详情 ↗</RouterLink>
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
      <button class="btn small" @click="router.push(libraryPath)">重置筛选</button>
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
    <div>
      {{
        isGiga
          ? '大建云仓 API 来源资料 · 原价、运费与可售库存分开记录。'
          : '历史快照 · 商品价格、库存与文案以详情中的回读记录为准。'
      }}
    </div>
  </div>
</template>
