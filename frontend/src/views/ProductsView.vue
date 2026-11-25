<script setup lang="ts">
import { sourceFromPath, isSupplier, sourceInfo } from '../utils/productSources';
import ProductStoreHistory from '../components/ProductStoreHistory.vue';
import SaleyeeQuickFacts from '../components/SaleyeeQuickFacts.vue';
import GigaQuickFacts from '../components/GigaQuickFacts.vue';
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { catalogApi } from '../api/client';
import type { ProductPage, PrelistingSelection, ListingStore } from '../api/types';
import { prelistingApi } from '../api/prelisting';
import { useCatalog } from '../composables/useCatalog';
import { errorMessage, priceRange, productStatus } from '../utils/display';
import AppIcon from '../components/AppIcon.vue';
import FilterSelect from '../components/FilterSelect.vue';
import FloatingDock from '../components/FloatingDock.vue';
import ProductCard from '../components/ProductCard.vue';
import ProductVariantsPreview from '../components/ProductVariantsPreview.vue';
import { useVariantPreview } from '../composables/useVariantPreview';
import ListingCoverage from '../components/ListingCoverage.vue';
import ListingCountFilter from '../components/ListingCountFilter.vue';
import ProductRangeFilter from '../components/ProductRangeFilter.vue';
import ProductImage from '../components/ProductImage.vue';
import StatusBadge from '../components/StatusBadge.vue';
import PrelistingComposer from '../components/PrelistingComposer.vue';
import { parseBatchIdentifiers, productSearch } from '../utils/batchSearch';
import { filterGroups, selectedValues } from '../utils/filterSelection';
import { useAuth } from '../composables/useAuth';
const { auth } = useAuth();
const variants = useVariantPreview();
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
const isWarehouse = computed(() => isSupplier(source.value));
const isGiga = computed(() => ['/giga-products', '/giga-canada-products'].includes(route.path));
const currentSource = computed(() => sourceFromPath(route.path));
const source = computed(() => currentSource.value.source);
const libraryPath = computed(() => currentSource.value.path);
const libraryName = computed(() => currentSource.value.name);
const { stats, refreshStats } = useCatalog(source);
const detailPath = (id: string) => currentSource.value.detail + id;
const data = ref<ProductPage>();
const listingStores = ref<ListingStore[]>([]);
const storeFilterError = ref('');
onMounted(async () => {
  try {
    listingStores.value = await prelistingApi.stores();
  } catch (e) {
    storeFilterError.value = errorMessage(e);
  }
});
const loading = ref(true),
  error = ref(''),
  search = ref('');
const view = ref(localStorage.getItem('catalog-view') === 'table' ? 'table' : 'grid');
const pageSizes = Array.from({ length: 10 }, (_, index) => (index + 1) * 50);
const savedPageSize = ref(Number(localStorage.getItem('catalog-page-size')) || 50);
const pageSize = computed(() => {
  const value = Number(route.query.page_size || savedPageSize.value);
  return pageSizes.includes(value) ? value : 50;
});
const batchSearchCount = computed(
  () => parseBatchIdentifiers(filters.value.get('identifiers') || '').identifiers.length,
);
let controller: AbortController | undefined, searchTimer: ReturnType<typeof setTimeout> | undefined;
const filters = computed(() => {
  const params = new URLSearchParams();
  for (const key of [
    'q',
    'identifiers',
    'store',
    'listed_store',
    'price_change',
    'shipping_address',
    'return_address',
    'stock_min',
    'stock_max',
    'price_min',
    'price_max',
    'multi',
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
  if (['out_of_stock', 'in_stock'].includes(String(route.query.inventory_status)))
    params.set('inventory_status', String(route.query.inventory_status));
  params.set('source', source.value);
  params.set('page_size', String(pageSize.value));
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
const rangeValues = computed(() => ({
  stock_min: filters.value.get('stock_min') || '',
  stock_max: filters.value.get('stock_max') || '',
  price_min: filters.value.get('price_min') || '',
  price_max: filters.value.get('price_max') || '',
}));
function changeRanges(values: typeof rangeValues.value) {
  clearTimeout(searchTimer);
  const query = Object.fromEntries(filters.value);
  for (const [key, value] of Object.entries(values)) {
    if (value) query[key] = value;
    else delete query[key];
  }
  delete query.page;
  return router.push({ path: libraryPath.value, query });
}
const storeOptions = computed(() => [
  { value: '', label: '全部来源店铺' },
  ...(stats.value?.stores.map((item) => ({ value: item.store, label: item.store })) || []),
]);
const listedStoreOptions = computed(() => [
  { value: '', label: '全部上架店铺' },
  ...listingStores.value.map((store) => ({
    value: String(store.id),
    label:
      store.name + (store.marketplace_name !== store.name ? ' · ' + store.marketplace_name : ''),
  })),
]);
const brandOptions = computed(() => [
  { value: '', label: '全部品牌' },
  ...(stats.value?.brands.map((brand) => ({ value: brand, label: brand })) || []),
]);
const dateOptions = computed(() => [
  { value: '', label: '全部上传日期' },
  ...(stats.value?.dates.map((date) => ({ value: date, label: date })) || []),
]);
function addressOptions(kind: 'shipping_addresses' | 'return_addresses') {
  return [
    { value: '', label: kind === 'shipping_addresses' ? '全部发货地址' : '全部退货地址' },
    ...(stats.value?.[kind] || []).map((item) => ({
      value: item.value,
      label: `${item.label} · ${item.count} 件商品`,
    })),
  ];
}
const selectionFilters = computed(() => {
  const values = new URLSearchParams(filters.value);
  values.delete('page');
  values.delete('page_size');
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
  Math.max(1, Math.ceil((data.value?.total || 0) / pageSize.value)),
);
const metrics = computed(() =>
  stats.value
    ? isWarehouse.value
      ? [
          {
            label: '供应商商品',
            value: stats.value.products,
            icon: 'Package',
            foot: sourceInfo(source.value).name,
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
            foot: '供应商原始类目',
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
  isWarehouse.value
    ? [{ value: '', label: '全部供应商商品', count: stats.value?.unique_counts?.all_products }]
    : [
        { value: '', label: '全部商品', count: stats.value?.unique_counts?.all_products },
        { value: 'complete', label: '核验完整', count: stats.value?.unique_counts?.complete },
        { value: 'review', label: '待复核', count: stats.value?.unique_counts?.review },
      ],
);

const multiFilters = computed(() => filterGroups(filters.value.get('multi') || ''));
function filterValue(key: string) {
  return multiFilters.value[key]?.length
    ? JSON.stringify(multiFilters.value[key])
    : filters.value.get(key) || '';
}
function changeMultiFilter(key: string, value: string) {
  clearTimeout(searchTimer);
  const groups = { ...multiFilters.value };
  const values = selectedValues(value);
  if (values.length) groups[key] = values;
  else delete groups[key];
  const query = Object.fromEntries(filters.value);
  delete query[key];
  delete query.page;
  if (Object.keys(groups).length) query.multi = JSON.stringify(groups);
  else delete query.multi;
  return router.push({ path: libraryPath.value, query });
}
function changeFilter(key: string, value: string) {
  clearTimeout(searchTimer);
  const query = Object.fromEntries(filters.value);
  if (value) query[key] = value;
  else delete query[key];
  delete query.page;
  return router.push({ path: libraryPath.value, query });
}
async function submitSearch() {
  clearTimeout(searchTimer);
  const value = productSearch(search.value);
  if (value.error) {
    controller?.abort();
    clearSelection();
    data.value = undefined;
    loading.value = false;
    error.value = value.error;
    return;
  }
  const query = Object.fromEntries(filters.value);
  delete query.q;
  delete query.identifiers;
  if (value.q) query.q = value.q;
  if (value.identifiers) query.identifiers = value.identifiers;
  delete query.page;
  const destination = { path: libraryPath.value, query };
  if (router.resolve(destination).fullPath === route.fullPath) {
    if (error.value) await load();
    return;
  }
  await router.push(destination);
}
function pasteSearch(event: ClipboardEvent) {
  const text = event.clipboardData?.getData('text') || '';
  if (/[\r\n\t]/u.test(text)) {
    event.preventDefault();
    const input = event.target as HTMLInputElement;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;
    search.value =
      input.value.slice(0, start) +
      text.trim().replace(/[\r\n\t]+/gu, ', ') +
      input.value.slice(end);
    queueSearch();
  }
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
  queueSearch();
}
function queueSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(submitSearch, 450);
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
function changePageSize(event: Event) {
  const value = Number((event.target as HTMLSelectElement).value);
  if (!pageSizes.includes(value)) return;
  savedPageSize.value = value;
  localStorage.setItem('catalog-page-size', String(value));
  const query = Object.fromEntries(filters.value);
  delete query.page;
  query.page_size = String(value);
  void router.push({ path: libraryPath.value, query });
}
async function load() {
  controller?.abort();
  const request = new AbortController();
  controller = request;
  clearTimeout(searchTimer);
  search.value =
    filters.value
      .get('identifiers')
      ?.split(/[\s,，;；、]+/u)
      .filter(Boolean)
      .join(', ') ||
    filters.value.get('q') ||
    '';
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
  <p v-if="source === 'GigaB2B_CA'" class="notice">
    加拿大独立商品库，价格使用 CAD；可选择已绑定的 Walmart 加拿大店铺进行预上架。
  </p>
  <div class="page-heading">
    <div>
      <p class="eyebrow">CATALOG / 商品管理</p>
      <h1>
        {{ libraryName }}<span class="heading-count">{{ data?.total ?? '—' }}</span>
      </h1>
    </div>
    <div class="actions">
      <RouterLink
        v-if="isWarehouse"
        class="btn"
        :to="
          source === 'Saleyee'
            ? '/saleyee-patrol'
            : source === 'GigaB2B_CA'
              ? '/giga-canada-patrol'
              : '/giga-patrol'
        "
        >价格与库存巡查</RouterLink
      >
      <RouterLink class="btn" to="/prelisting">预上架批次</RouterLink>
      <a
        class="btn"
        id="export-button"
        :href="'/api/export.csv?' + filters"
        title="导出清单"
        aria-label="导出清单"
        ><AppIcon name="Download" /><span class="button-label">导出清单</span></a
      ><RouterLink class="btn primary" :to="source === 'Amazon_CA' ? '/integrations?marketplace=CA' : '/integrations'"
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
    <form class="filter-bar" id="filters" @submit.prevent="submitSearch">
      <div class="search-wrap">
        <span><AppIcon name="Search" /></span
        ><input
          id="search"
          type="search"
          name="q"
          :value="search"
          :placeholder="
            isWarehouse
              ? '搜索 SKU、商品名称、卖家…支持批量粘贴'
              : '搜索 ASIN、SKU、商品名称、卖家…支持批量粘贴'
          "
          aria-label="搜索商品"
          title="SKU / ASIN 可用逗号、空格分隔，也可直接粘贴多行；按回车立即搜索"
          @input="searchInput"
          @paste="pasteSearch"
        />
      </div>
      <FilterSelect
        label="筛选库存"
        title="按已记录的可售库存筛选；未返回库存不计入有库存或无库存"
        multiple
        :model-value="filterValue('inventory_status')"
        :options="[
          { value: '', label: '全部库存状态' },
          { value: 'in_stock', label: '有库存（可售库存大于 0）' },
          { value: 'out_of_stock', label: '无库存（可售库存为 0）' },
        ]"
        @update:model-value="changeMultiFilter('inventory_status', $event)"
      />
      <FilterSelect
        v-if="source === 'Saleyee'"
        class="address-filter"
        label="发货地址"
        title="从已入库商品的全部发货仓库地址中筛选"
        multiple
        searchable
        search-placeholder="搜索州、城市、街道或邮编"
        :model-value="filterValue('shipping_address')"
        :options="addressOptions('shipping_addresses')"
        @update:model-value="changeMultiFilter('shipping_address', $event)"
      />
      <FilterSelect
        v-if="source === 'Saleyee'"
        class="address-filter"
        label="退货地址"
        title="从已入库商品的退货地址中筛选"
        multiple
        searchable
        search-placeholder="搜索州、城市、街道或邮编"
        :model-value="filterValue('return_address')"
        :options="addressOptions('return_addresses')"
        @update:model-value="changeMultiFilter('return_address', $event)"
      />
      <FilterSelect
        label="筛选卖家黑名单"
        multiple
        :model-value="filterValue('seller_status')"
        :options="[
          { value: '', label: '全部卖家状态' },
          { value: 'blocked', label: '命中卖家黑名单' },
          { value: 'unblocked', label: '未命中黑名单' },
        ]"
        @update:model-value="changeMultiFilter('seller_status', $event)"
      />
      <FilterSelect
        class="category-filter"
        label="筛选商品类目"
        title="按已记录的 Product Type 或供应商来源类目筛选；括号内数量为全库记录数"
        multiple
        :model-value="filterValue('category')"
        :options="categoryOptions"
        @update:model-value="changeMultiFilter('category', $event)"
      />
      <FilterSelect
        label="筛选店铺"
        multiple
        :model-value="filterValue('store')"
        :options="storeOptions"
        @update:model-value="changeMultiFilter('store', $event)"
      />
      <FilterSelect
        label="筛选品牌"
        multiple
        :model-value="filterValue('brand')"
        :options="brandOptions"
        @update:model-value="changeMultiFilter('brand', $event)"
      />
      <FilterSelect
        label="上架店铺（含预上架记录）"
        multiple
        :model-value="filterValue('listed_store')"
        :options="listedStoreOptions"
        searchable
        search-placeholder="搜索上架店铺"
        @update:model-value="changeMultiFilter('listed_store', $event)"
      />
      <FilterSelect
        label="来源价格涨跌"
        multiple
        :model-value="filterValue('price_change')"
        :options="[
          { value: '', label: '全部价格变化' },
          { value: 'up', label: '涨价' },
          { value: 'down', label: '降价' },
          { value: 'unchanged', label: '未变化' },
          { value: 'unknown', label: '暂无可比记录' },
        ]"
        @update:model-value="changeMultiFilter('price_change', $event)"
      />
      <FilterSelect
        label="筛选日期"
        multiple
        :model-value="filterValue('date')"
        :options="dateOptions"
        @update:model-value="changeMultiFilter('date', $event)"
      />
      <ListingCountFilter
        v-if="!isWarehouse"
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
      <ProductRangeFilter
        :values="rangeValues"
        :currency="['GigaB2B_CA','Amazon_CA'].includes(source) ? 'CAD' : 'USD'"
        @change="changeRanges"
      />
    </form>
    <p class="batch-search-hint">
      范围包含上下限，留空不限。{{
        isGiga
          ? '价格按预计总额筛选，价格区间有交集即可。'
          : source === 'Saleyee'
            ? '价格按采购价筛选。'
            : '价格按当前售价筛选；库存和价格须由同一子变体满足。'
      }}未知值不按 0 计算。
    </p>
    <p v-if="storeFilterError" class="error-box">上架店铺列表加载失败：{{ storeFilterError }}</p>
    <p
      v-if="filterValue('listed_store') || filterValue('price_change') || filters.get('multi')"
      class="batch-search-hint"
    >
      同一筛选项可多选，匹配任一选中值即可；不同筛选项需同时满足。上架店铺仅匹配沃尔玛 API
      最近一次回读确认为已发布的商品，不含预上架、审核中或失败记录。 价格比较最近两次同
      SKU、同币种的来源单价（不含运费、倍率）；云仓使用同步或巡查记录，亚马逊使用来源采集记录。
      多变体只要有一个涨价或降价即可命中；全部可比较且相同才算未变化。
    </p>
    <p v-if="batchSearchCount" class="batch-search-hint" role="status">
      已按 {{ batchSearchCount }} 个 SKU / ASIN 精确搜索 · 自动去重，命中子变体时显示所属商品
    </p>
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
          isWarehouse
            ? '按供应商账号与 SKU 独立建档'
            : '按 ASIN 去重 · 上架次数按亚马逊店铺模板统计'
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
          <ProductCard :product="product">
            <template v-if="product.variant_count" #variants>
              <div class="product-card-variants">
                <button
                  type="button"
                  class="variant-toggle"
                  :aria-expanded="variants.state(product.id).expanded"
                  :aria-controls="'grid-variants-' + product.id"
                  @click="variants.toggle(product.id)"
                >
                  <AppIcon name="ChevronRight" />
                  {{ variants.state(product.id).expanded ? '收起子变体' : '展开子变体' }}
                  <span>{{ product.variant_count }}</span>
                </button>
                <ProductVariantsPreview
                  v-show="variants.state(product.id).expanded"
                  :id="'grid-variants-' + product.id"
                  :product="product"
                  :state="variants.state(product.id)"
                  @retry="variants.retry(product.id)"
                />
              </div>
            </template>
          </ProductCard>
        </div>
      </div>
      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th v-if="canEdit">选择</th>
              <th>商品</th>
              <th v-if="!isWarehouse">上架次数 / 店铺</th>
              <th>{{ isWarehouse ? '供应商 SKU' : 'ASIN / SKU' }}</th>
              <th>店铺 / 品牌</th>
              <th>来源 / 卖家</th>
              <th>价格 / 币种</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="product in data.items" :key="product.id">
              <tr>
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
                    ><ProductImage
                      :listing-id="product.id"
                      :src="product.image"
                      :fallbacks="product.image_fallbacks"
                      image-class="table-thumb"
                    /><span class="table-product-copy"
                      ><span class="table-product-title">{{ product.title || '暂无标题' }}</span
                      ><span class="category-label" :title="product.category || '未记录类目'">{{
                        product.category || '未记录类目'
                      }}</span></span
                    ></RouterLink
                  >
                  <button
                    v-if="product.variant_count"
                    type="button"
                    class="variant-toggle table-variant-toggle"
                    :aria-expanded="variants.state(product.id).expanded"
                    :aria-controls="'table-variants-' + product.id"
                    @click="variants.toggle(product.id)"
                  >
                    <AppIcon name="ChevronRight" />
                    {{ variants.state(product.id).expanded ? '收起子变体' : '展开子变体' }}
                    <span>{{ product.variant_count }}</span>
                  </button>
                </td>
                <td v-if="!isWarehouse" class="listing-coverage-cell">
                  <ListingCoverage :product="product" />
                </td>
                <td class="mono">
                  <template v-if="!isWarehouse">{{ product.asin }}<br /></template
                  ><span class="muted">{{ product.sku }}</span>
                </td>
                <td>
                  {{ product.store }}<br /><span class="muted">{{ product.brand }}</span>
                  <ProductStoreHistory
                    v-if="product.store_history?.length"
                    :entries="product.store_history"
                  />
                </td>
                <td>
                  {{ product.source }}<br /><span class="muted">{{
                    product.seller || '卖家未采集'
                  }}</span>
                  <div v-if="product.seller_blacklist.length" class="seller-risk-label">
                    卖家已拉黑 · {{ product.seller_blacklist.length }} 位
                  </div>
                </td>
                <td>
                  <span v-if="isGiga" class="giga-table-price">预计总额（含打包）</span
                  >{{
                    priceRange(
                      ['GigaB2B', 'GigaB2B_CA'].includes(product.source || '')
                        ? product.giga_summary?.estimated_totals || []
                        : product.prices,
                      product.currency ?? 'USD',
                    )
                  }}<GigaQuickFacts
                    v-if="isGiga"
                    :summary="product.giga_summary"
                  /><SaleyeeQuickFacts
                    v-if="source === 'Saleyee'"
                    :summary="product.saleyee_summary"
                  />
                </td>
                <td>
                  <StatusBadge :type="productStatus(product).type">{{
                    productStatus(product).label
                  }}</StatusBadge>
                </td>
                <td>
                  <RouterLink class="detail-link" :to="detailPath(product.id)">详情 ↗</RouterLink>
                </td>
              </tr>
              <tr
                v-if="product.variant_count"
                v-show="variants.state(product.id).expanded"
                class="product-variants-row"
              >
                <td :colspan="7 + Number(canEdit) + Number(!isWarehouse)">
                  <ProductVariantsPreview
                    :id="'table-variants-' + product.id"
                    :product="product"
                    :state="variants.state(product.id)"
                    @retry="variants.retry(product.id)"
                  />
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </template>
    <div v-else class="empty">
      没有找到对应商品
      <p>试试其他 ASIN、SKU，或重置筛选条件。</p>
      <button class="btn small" @click="router.push(libraryPath)">重置筛选</button>
    </div>
  </section>
  <FloatingDock label="商品库分页" storage-key="catalog-pagination-position">
    <nav class="product-pagination-dock" aria-label="商品库分页">
      <label class="page-size-control">
        每页
        <select :value="pageSize" aria-label="每页显示数量" @change="changePageSize">
          <option v-for="size in pageSizes" :key="size" :value="size">{{ size }} 条</option>
        </select>
      </label>
      <span class="page-total" aria-live="polite">{{ loading ? '加载中…' : `共 ${data?.total ?? 0} 条` }}</span>
      <div class="page-buttons">
        <button class="btn" id="prev" title="上一页" aria-label="上一页"
          :disabled="loading || !data || data.page <= 1" @click="changePage(-1)">
          <AppIcon name="ChevronLeft" />
        </button>
        <span class="page-number">{{ data?.page || 1 }} / {{ pages }}</span>
        <button class="btn" id="next" title="下一页" aria-label="下一页"
          :disabled="loading || !data || data.page >= pages" @click="changePage(1)">
          <AppIcon name="ChevronRight" />
        </button>
      </div>
    </nav>
  </FloatingDock>
  <div class="product-pagination-space" aria-hidden="true"></div>
  <PrelistingComposer
    v-if="composer"
    :selection="composerSelection"
    :count="composerCount"
    :source="source"
    @close="composer = false"
    @created="created"
  />
  <div class="notice">
    <AppIcon name="Database" />
    <div>
      {{
        isWarehouse
          ? '供应商 API 来源资料 · 原价、运费与可售库存分开记录。'
          : '历史快照 · 商品价格、库存与文案以详情中的回读记录为准。'
      }}
    </div>
  </div>
</template>
<style scoped>
.product-pagination-dock {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  max-width: calc(100vw - 20px);
  padding: 8px 10px 8px 24px;
  border: 1px solid #cbdbe980;
  border-radius: 12px;
  background: #f0f6fc80;
  box-shadow: 0 3px 14px #34536b14;
  pointer-events: none;
  color: #456078;
  font-size: 12px;
}
.page-size-control {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.page-size-control select {
  width: 82px;
  height: 30px;
  min-height: 30px;
  padding: 4px 7px;
  border: 1px solid #bfd0e1;
  border-radius: 7px;
  background: #ffffffb3;
  color: #34536b;
  font: inherit;
  cursor: pointer;
  pointer-events: auto;
}
.page-size-control select:hover,
.page-size-control select:focus-visible { background: #fff; }
.page-size-control select:focus-visible {
  outline: 2px solid #638bb1;
  outline-offset: 2px;
}
.product-pagination-dock .page-buttons { gap: 6px; }
.product-pagination-dock .btn { min-width: 28px; width: 28px; height: 28px; min-height: 28px; padding: 5px; background: #ffffffb3; pointer-events: auto; }
.product-pagination-dock .btn:hover { background: #fff; }
.product-pagination-dock .btn .icon { width: 16px; height: 16px; }
.product-pagination-dock .page-number { height: 26px; font-size: 12px; background: #edf3fa80; }
.product-pagination-space { height: 70px; }
@media (max-width: 480px) {
  .product-pagination-dock { gap: 6px 10px; padding: 7px 9px 7px 24px; }
  .page-total { font-size: 12px; }
  .product-pagination-space { height: 100px; }
}
.filter-select.address-filter {
  width: 280px;
}
.product-grid {
  align-items: start;
}
.batch-search-hint {
  color: var(--muted);
  font-size: 12px;
  margin: 0 0 16px;
}
.variant-toggle {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 36px;
  padding: 7px 11px;
  border: 1px solid #cfdeef;
  border-radius: 8px;
  background: #edf4fb;
  color: #456c99;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.variant-toggle:hover {
  border-color: #91afd1;
  background: #e2edfa;
}
.variant-toggle:focus-visible {
  outline: 2px solid #567fad;
  outline-offset: 2px;
}
.variant-toggle .icon {
  width: 15px;
  height: 15px;
  transition: transform 0.15s;
}
.variant-toggle[aria-expanded='true'] .icon {
  transform: rotate(90deg);
}
.variant-toggle > span {
  padding: 1px 6px;
  border-radius: 5px;
  background: #fff;
}
.table-variant-toggle {
  margin: 10px 0 0 56px;
}
.product-card-variants {
  border-top: 1px solid #dfe8f3;
  background: #f3f7fc;
}
.product-card-variants > .variant-toggle {
  display: flex;
  width: calc(100% - 24px);
  margin: 12px;
}
.product-card-variants > .variant-toggle > span {
  margin-left: auto;
}
.product-variants-row > td {
  padding: 0 12px 12px;
  background: #f3f7fc;
}
.product-variants-row:hover > td {
  background: #f3f7fc;
}
</style>
