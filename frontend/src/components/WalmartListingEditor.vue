<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch, onBeforeUnmount } from 'vue';
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router';
import {
  publishingApi,
  type ListingDocument,
  type ListingSpec,
  type ProductType,
  type ShipNode,
  type ListingRow,
  type JsonSchema,
  type Issue,
  type Attempt,
  type GenerationJob,
} from '../api/publishing';
import { catalogApi as api } from '../api/client';
import { useAuth } from '../composables/useAuth';
import { errorMessage, plain, money } from '../utils/display';
import { applyListingDefaults } from '../utils/listingDefaults';
import ListingSchemaField from './ListingSchemaField.vue';
import ProductImage from './ProductImage.vue';
import ListingProductDetails from './ListingProductDetails.vue';
import ProductStoreHistory from './ProductStoreHistory.vue';
const detailRow = ref<ListingRow>();
import AppIcon from './AppIcon.vue';
import ListingPreparationSettings from './ListingPreparationSettings.vue';
import { preparationApi } from '../api/preparation';
const props = defineProps<{ bid: string }>();
const route = useRoute();
const { auth } = useAuth();
const canEdit = computed(
  () => !auth.enabled || ['admin', 'editor'].includes(auth.user?.role || ''),
);
const canSubmit = computed(() => !auth.enabled || auth.user?.role === 'admin');
const doc = ref<ListingDocument | null>(null),
  busy = ref(false),
  saving = ref(false),
  error = ref(''),
  message = ref('');
const saved = ref(''),
  activeSku = ref(''),
  tab = ref('basic'),
  statusFilter = ref('all');
const taxonomy = ref<ProductType[]>([]),
  nodes = ref<ShipNode[]>([]),
  typeQuery = ref('');
const specs = ref<Record<string, ListingSpec>>({});
const selected = ref<string[]>([]),
  confirmDialog = ref<HTMLDialogElement>(),
  confirmation = ref('');
const optionalQuery = ref('');
const showAllOptional = ref(false);
const zoomDialog = ref<HTMLDialogElement>();
const zoomUrl = ref('');
const optionalMatches = (key: string) =>
  `${key} ${doc.value?.labels[key] || ''} ${visibleProperties.value[key]?.title || ''}`
    .toLowerCase()
    .includes(optionalQuery.value.toLowerCase());
function showOptional(key: string, section: 'visible' | 'orderable') {
  return (
    optionalMatches(key) &&
    (showAllOptional.value || !!optionalQuery.value || current.value?.[section][key] !== undefined)
  );
}
const photos = computed(() =>
  [
    current.value?.visible.mainImageUrl,
    ...((current.value?.visible.productSecondaryImageURL as string[]) || []),
  ].filter((url): url is string => typeof url === 'string' && !!url),
);
function setPhotos(urls: string[]) {
  update('visible', 'mainImageUrl', urls[0]);
  update('visible', 'productSecondaryImageURL', [...new Set(urls)].slice(1, 8));
}
function enlarge(url: string) {
  zoomUrl.value = url;
  zoomDialog.value?.showModal();
}
const unsupported = computed(() =>
  !currentSpec.value || !current.value
    ? []
    : Object.keys(current.value.visible).filter((key) => !visibleProperties.value[key]),
);
const submitKey = ref(''),
  reconcileIds = ref<Record<string, string>>({});
const root = ref<HTMLElement>();
let saveTimer: ReturnType<typeof setTimeout> | undefined, saveFlight: Promise<void> | undefined;
let live = true;
const serialized = () =>
  doc.value ? JSON.stringify({ version: doc.value.version, rows: doc.value.rows }) : '';
const dirty = computed(() => !!doc.value && serialized() !== saved.value);
const current = computed(() => doc.value?.rows.find((r) => r.platform_sku === activeSku.value));
const generation = ref<GenerationJob | null>(null);
const aiReady = ref(false);
const codePoolOpen = ref(false);
const allocatingCodes = ref(false);
let importedCodes = 0;
async function assignCodes(showMessage = true) {
  allocatingCodes.value = true;
  try {
    await save();
    if (!doc.value || generating.value) return;
    const result = await preparationApi.allocate(props.bid, doc.value.revision);
    if (!live) return;
    install(result.document);
    if (showMessage || result.assigned) {
      message.value = result.missing.length
        ? `已补齐 ${result.assigned} 个编码，仍有 ${result.missing.length} 个 SKU 缺码。当前服务器编码池可用于此品牌的编码不足，请导入编码表格。`
        : `已补齐 ${result.assigned} 个编码；已有编码保持原样，无需等待 AI。修改后请重新校验。`;
    }
  } finally {
    allocatingCodes.value = false;
  }
}
async function closeCodePool() {
  codePoolOpen.value = false;
  if (importedCodes > 0) {
    importedCodes = 0;
    await run(() => assignCodes());
  } else
    await run(async () => {
      await save();
      await load();
    });
}
const generating = computed(() => ['queued', 'running'].includes(generation.value?.state || ''));
const generationRow = computed(() => generation.value?.report.rows[activeSku.value]);
const generationStale = computed(
  () => !generating.value && generation.value && generation.value.revision !== doc.value?.revision,
);
const validationCurrent = computed(
  () =>
    !!doc.value?.validation.current &&
    !dirty.value &&
    doc.value.validation.revision === doc.value.revision,
);
const reusedTemplate = computed(() => doc.value?.basis.items.some((item) => item.reuse_template));
const preparationLabels: Record<string, string> = {
  collecting: '补全来源',
  generating: '生成资料',
  validating: '实际店铺校验',
  repairing: '定向修复',
  ready: '可提交',
  needs_prerequisite: '前提待补足',
  needs_review: '异常待处理',
  needs_validation: '需重新校验',
  skipped: '已有提交 · 已跳过',
};
function preparationLabel(sku: string) {
  const entry = generation.value?.report.rows[sku];
  if (entry?.preparation_state === 'ready' && (!validationCurrent.value || generationStale.value))
    return '需重新校验';
  return preparationLabels[entry?.preparation_state || ''] || '等待处理';
}
const readyCount = computed(() =>
  validationCurrent.value
    ? Object.values(doc.value?.validation.rows || {}).filter((r) => r.valid).length
    : 0,
);
let resultTimer: ReturnType<typeof setTimeout> | undefined;
async function pollResults() {
  clearTimeout(resultTimer);
  if (!live) return;
  try {
    if (!dirty.value && !generating.value && !busy.value && !saving.value) {
      await load();
      if (doc.value?.attempts.some((a) => a.polling?.state === 'repair_pending'))
        await pollGeneration();
    }
  } finally {
    if (
      live &&
      doc.value?.attempts.some(
        (a) => a.polling && ['pending', 'running', 'repair_pending'].includes(a.polling.state),
      )
    )
      resultTimer = setTimeout(() => {
        void pollResults().catch((e) => {
          error.value = errorMessage(e);
        });
      }, 15000);
  }
}
let generationTimer: ReturnType<typeof setTimeout> | undefined;

async function pollGeneration() {
  clearTimeout(generationTimer);
  const status = await publishingApi.generation(props.bid);
  if (!live) return;
  const wasRunning = generating.value;
  generation.value = status.job;
  aiReady.value = status.ai.configured;
  if (!dirty.value && (generating.value || wasRunning)) await load();
  if (wasRunning && !generating.value && canEdit.value) await loadMetadata();
  if (generating.value)
    generationTimer = setTimeout(() => {
      pollGeneration().catch((e) => {
        error.value = errorMessage(e);
      });
    }, 2500);
}
async function generate() {
  await run(async () => {
    await save();
    generation.value = await publishingApi.generate(props.bid);
    await pollGeneration();
  });
}
const currentSpec = computed(() =>
  current.value ? specs.value[current.value.product_type] : undefined,
);
const source = computed(() =>
  doc.value?.basis.items.find((i) => i.listing_id === current.value?.listing_id),
);
const sourceVariant = computed(() =>
  source.value?.variants.find((v) => v.platform_sku === activeSku.value),
);
const result = computed(() => doc.value?.results[activeSku.value]);
const locked = computed(
  () =>
    generating.value ||
    allocatingCodes.value ||
    !canEdit.value ||
    (!!result.value && !['failed', 'rejected', 'unpublished'].includes(result.value.status)),
);
const issues = computed(() => doc.value?.validation.rows?.[activeSku.value]?.errors || []);
const basic = ['productName', 'brand', 'condition', 'condition_notes'];
const copyKeys = [
  'shortDescription',
  'longDescription',
  'keyFeatures',
  'mainImageUrl',
  'productSecondaryImageURL',
];
const hiddenOrderable = ['sku', 'productIdentifiers', 'inventory', 'SkuUpdate', 'ProductIdUpdate'];
const visibleProperties = computed(() => currentSpec.value?.sections.visible?.properties || {});
const required = computed(() => [
  ...new Set([
    ...(currentSpec.value?.sections.visible?.required || []),
    ...(doc.value?.requirements?.[activeSku.value]?.visible || []),
  ]),
]);
const requiredOrderable = computed(() => [
  ...new Set([
    ...(currentSpec.value?.sections.orderable.required || []),
    ...(doc.value?.requirements?.[activeSku.value]?.orderable || []),
  ]),
]);
const mandatoryKeys = computed(() =>
  Object.keys(visibleProperties.value).filter(
    (k) => required.value.includes(k) && !basic.includes(k) && !copyKeys.includes(k),
  ),
);
const optionalKeys = computed(() =>
  Object.keys(visibleProperties.value).filter(
    (k) => !required.value.includes(k) && !basic.includes(k) && !copyKeys.includes(k),
  ),
);
const orderableKeys = computed(() =>
  Object.keys(currentSpec.value?.sections.orderable.properties || {}).filter(
    (k) =>
      !hiddenOrderable.includes(k) &&
      (requiredOrderable.value.includes(k) || ['price', 'msrp'].includes(k)),
  ),
);
const optionalOrderableKeys = computed(() =>
  Object.keys(currentSpec.value?.sections.orderable.properties || {}).filter(
    (k) => !hiddenOrderable.includes(k) && !orderableKeys.value.includes(k),
  ),
);
const typeOptions = computed(() =>
  taxonomy.value.filter((t) =>
    `${t.category} ${t.group} ${t.name}`.toLowerCase().includes(typeQuery.value.toLowerCase()),
  ),
);
const eligible = computed(
  () =>
    doc.value?.rows.filter((r) => {
      const s = doc.value?.results[r.platform_sku]?.status;
      return !s || ['failed', 'rejected', 'unpublished'].includes(s);
    }) || [],
);
const validSkus = computed(() =>
  (validationCurrent.value ? eligible.value : [])
    .filter((r) => doc.value?.validation.rows?.[r.platform_sku]?.valid)
    .map((r) => r.platform_sku),
);
const selectedValid = computed(() => selected.value.filter((s) => validSkus.value.includes(s)));
const rowsToShow = computed(() =>
  (doc.value?.rows || []).filter((r) => {
    if (statusFilter.value === 'issues')
      return doc.value?.validation.rows?.[r.platform_sku]?.valid === false;
    if (statusFilter.value === 'failed')
      return ['failed', 'rejected', 'unpublished', 'stock_pending'].includes(
        doc.value?.results[r.platform_sku]?.status || '',
      );
    return true;
  }),
);
const statusLabels: Record<string, string> = {
  processing: '处理中',
  unknown: '结果待确认',
  failed: '失败 · 可修正',
  rejected: '请求被拒绝',
  ingested: '处理成功 · 待发布',
  published: '已发布',
  unpublished: '未发布',
  stock_pending: '库存待核验',
};
const completed = computed(
  () => Object.values(doc.value?.results || {}).filter((r) => r.status === 'published').length,
);
function install(value: ListingDocument) {
  specs.value = { ...specs.value, ...value.specs };
  saved.value = JSON.stringify({ version: value.version, rows: value.rows });
  doc.value = value;
  if (!activeSku.value || !value.rows.some((r) => r.platform_sku === activeSku.value))
    activeSku.value = value.rows[0]?.platform_sku || '';
}
async function run(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    await work();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
async function load() {
  const value = await publishingApi.read(props.bid);
  if (live && value) install(value);
}
async function prepare() {
  await run(async () => {
    install(await publishingApi.prepare(props.bid));
    await pollGeneration();
    if (!generating.value) {
      await loadMetadata();
      if (current.value?.product_type) await getSpec();
    }
  });
  await pollGeneration();
  if (aiReady.value && !generation.value && !reusedTemplate.value) await generate();
}
async function loadMetadata() {
  const values = await Promise.allSettled([
    publishingApi.taxonomy(props.bid),
    publishingApi.nodes(props.bid),
  ]);
  if (!live) return;
  const [types, warehouses] = values;
  if (types.status === 'fulfilled') taxonomy.value = types.value.items;
  if (warehouses.status === 'fulfilled') nodes.value = warehouses.value.items;
  if (
    canEdit.value &&
    doc.value &&
    applyListingDefaults(
      doc.value,
      types.status === 'fulfilled' ? types.value.items : [],
      warehouses.status === 'fulfilled' ? warehouses.value.items : [],
    )
  ) {
    await save();
  }
  const errors = values.filter((v): v is PromiseRejectedResult => v.status === 'rejected');
  if (errors.length) error.value = errors.map((e) => errorMessage(e.reason)).join('；');
}
async function save() {
  if (root.value?.querySelector('[data-invalid-json="true"]'))
    throw new Error('有属性结构格式错误，请先修正标红字段');
  clearTimeout(saveTimer);
  if (saveFlight) await saveFlight;
  if (!dirty.value || !doc.value || !canEdit.value || generating.value) return;
  saving.value = true;
  const input = structuredClone(
    doc.value ? JSON.parse(JSON.stringify(doc.value)) : null,
  ) as ListingDocument;
  const before = JSON.stringify({ version: input.version, rows: input.rows });
  saveFlight = (async () => {
    try {
      const value = await publishingApi.save(props.bid, input);
      if (!live || !doc.value) return;
      if (serialized() === before) install(value);
      else {
        const localRows = doc.value.rows,
          localVersion = doc.value.version;
        install(value);
        doc.value.rows = localRows;
        doc.value.version = localVersion;
      }
      message.value = '资料已保存';
    } finally {
      saving.value = false;
      saveFlight = undefined;
    }
  })();
  await saveFlight;
  if (dirty.value) await save();
}
function queueSave() {
  clearTimeout(saveTimer);
  if (dirty.value && !busy.value && canEdit.value)
    saveTimer = setTimeout(() => {
      save().catch((e) => {
        error.value = errorMessage(e);
      });
    }, 1000);
}
watch(() => serialized(), queueSave);
function fieldIssues(key: string, section = 'Visible') {
  return issues.value
    .filter(
      (i) =>
        i.path.toLowerCase().includes(`.${key}`.toLowerCase()) &&
        (section !== 'Orderable' || i.path.includes('Orderable')),
    )
    .map((i) => i.message);
}
function update(section: 'visible' | 'orderable', key: string, value: unknown) {
  if (!current.value) return;
  if (section === 'visible' && key === 'productSecondaryImageURL' && Array.isArray(value)) {
    value = [...new Set(value)]
      .filter((u) => u !== current.value?.visible.mainImageUrl)
      .slice(0, current.value.visible.mainImageUrl ? 7 : 8);
  }
  if (value === undefined || value === '') delete current.value[section][key];
  else current.value[section][key] = value;
}
function fallbackSchema(key: string): JsonSchema {
  return (
    visibleProperties.value[key] || {
      type: ['keyFeatures', 'productSecondaryImageURL'].includes(key) ? 'array' : 'string',
      items: { type: 'string' },
    }
  );
}
async function getSpec() {
  if (!current.value?.product_type) return;
  const pt = current.value.product_type;
  const value = await publishingApi.spec(props.bid, JSON.parse(JSON.stringify(current.value)));
  if (!live) return;
  specs.value[pt] = value;
}
async function changeType() {
  await run(async () => {
    await save();
    await getSpec();
  });
}
watch(activeSku, async () => {
  tab.value = 'basic';
  typeQuery.value = '';
  if (!generating.value && current.value?.product_type && !specs.value[current.value.product_type])
    await run(getSpec);
});
async function validate() {
  await run(async () => {
    await save();
    if (!doc.value) return;
    await loadMetadata();
    const types = [...new Set(doc.value.rows.map((r) => r.product_type).filter(Boolean))];
    // Server caches specs across SKUs. Do not burst a 3/minute API with duplicate PT requests.
    for (const type of types) {
      const row = doc.value.rows.find((r) => r.product_type === type)!;
      specs.value[type] = await publishingApi.spec(props.bid, JSON.parse(JSON.stringify(row)));
    }
    install(await publishingApi.validate(props.bid, doc.value.revision));
    selected.value = validSkus.value;
    if (doc.value.validation.valid_count === doc.value.rows.length) {
      if (statusFilter.value === 'issues') statusFilter.value = 'all';
      message.value = '上架资料校验通过，请确认店铺及选中商品后提交';
    } else {
      statusFilter.value = 'issues';
      const first = rowsToShow.value[0];
      if (first) activeSku.value = first.platform_sku;
      message.value = `已校验，${doc.value.rows.length - (doc.value.validation.valid_count || 0)} 个 SKU 需要补齐资料`;
    }
  });
}
async function refreshSource() {
  await run(async () => {
    await save();
    if (doc.value) install(await publishingApi.refreshSource(props.bid, doc.value.revision));
    await loadMetadata();
    if (current.value?.product_type) await getSpec();
    message.value = '已读取商品库最新来源，并重新计算售价；文案和属性保留';
  });
}
async function openSubmit() {
  await run(async () => {
    await save();
    if (!doc.value?.validation.id || !validationCurrent.value || !selectedValid.value.length)
      throw new Error('请先校验资料并选择校验通过的商品');
    confirmation.value = '';
    submitKey.value = crypto.randomUUID();
    confirmDialog.value?.showModal();
  });
}
async function submit() {
  await run(async () => {
    if (!doc.value || confirmation.value !== doc.value.store.name) return;
    await publishingApi.submit(props.bid, doc.value, selectedValid.value, submitKey.value);
    confirmDialog.value?.close();
    await load();
    tab.value = 'results';
    void pollResults();
    message.value = '已记录提交请求，请查看沃尔玛逐项处理结果';
  });
}
async function refreshResults(a?: Attempt) {
  await run(async () => {
    if (!doc.value) return;
    const targets = a ? [a] : doc.value.attempts.filter((x) => x.feed_id);
    const errors: string[] = [];
    for (const attempt of targets) {
      try {
        await publishingApi.refresh(props.bid, attempt.id);
      } catch (e) {
        errors.push(errorMessage(e));
      }
    }
    await load();
    tab.value = 'results';
    if (errors.length) throw new Error(errors.join('；'));
    void pollResults();
    message.value = targets.length ? '处理结果已更新' : '还没有可查询的沃尔玛批次编号';
  });
}
async function reconcile(a: Attempt) {
  await run(async () => {
    await publishingApi.reconcile(props.bid, a.id, reconcileIds.value[a.id] || '');
    await load();
    message.value = '已关联并查询原沃尔玛批次';
  });
}
async function jump(issue: Issue) {
  const segments = issue.path.split('.');
  if (
    segments.some((k) => optionalOrderableKeys.value.includes(k) || optionalKeys.value.includes(k))
  ) {
    tab.value = 'optional';
    optionalQuery.value = '';
  } else if (
    issue.path === 'product_type' ||
    issue.path === 'identification' ||
    issue.path === 'fulfillment_center_id' ||
    issue.path.toLowerCase().includes('orderable') ||
    segments.some((k) => basic.includes(k))
  )
    tab.value = 'basic';
  else tab.value = 'basic';
  await nextTick();
  const normalizedPath = issue.path
    .toLowerCase()
    .replace(/^mpitem\.\d+\.visible\.[^.]+\./, 'visible.')
    .replace(/^mpitem\.\d+\.orderable\./, 'orderable.');
  const suffix = normalizedPath.split('.').slice(-2).join('.');
  const last = issue.path.toLowerCase().split('.').at(-1);
  const match = [...(root.value?.querySelectorAll<HTMLElement>('[data-listing-path]') || [])].find(
    (el) =>
      el.dataset.listingPath?.toLowerCase().endsWith(suffix) ||
      normalizedPath.startsWith(el.dataset.listingPath?.toLowerCase() + '.') ||
      el.dataset.listingPath === issue.path ||
      (!!last && el.dataset.listingPath?.toLowerCase().endsWith('.' + last)),
  );
  (match || root.value)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  match?.querySelector<HTMLElement>('input,select,textarea,[contenteditable="true"]')?.focus();
}
async function upload(e: Event) {
  const input = e.target as HTMLInputElement;
  const files = [...(input.files || [])];
  await run(async () => {
    const target = current.value;
    if (!target) return;
    const count = [
      target.visible.mainImageUrl,
      ...((target.visible.productSecondaryImageURL as string[]) || []),
    ].filter(Boolean).length;
    if (files.length > 8 - count) {
      throw new Error(
        `刊登图片最多8张，当前还可添加${Math.max(0, 8 - count)}张，请减少选择或先删除图片。`,
      );
    }
    for (const file of files) {
      const { url } = await api.uploadImage(target.listing_id, undefined, file);
      if (!target.visible.mainImageUrl) target.visible.mainImageUrl = url;
      else
        target.visible.productSecondaryImageURL = [
          ...((target.visible.productSecondaryImageURL as string[]) || []),
          url,
        ];
    }
    await save();
  });
  input.value = '';
}
onMounted(async () => {
  await run(async () => {
    await load();
    if (
      typeof route.query.sku === 'string' &&
      doc.value?.rows.some((r) => r.platform_sku === route.query.sku)
    ) {
      activeSku.value = route.query.sku;
      if (route.query.view === 'results') tab.value = 'results';
    }
    await pollGeneration();
    if (doc.value && canEdit.value) {
      if (!generating.value) {
        install(await publishingApi.prepare(props.bid));
        if (
          doc.value.rows.some(
            (row) => row.identification.mode === 'identifier' && !row.identification.value.trim(),
          )
        )
          await assignCodes(false);
        await loadMetadata();
        if (current.value?.product_type) await getSpec();
      }
    }
  });
  if (
    doc.value?.attempts.some(
      (a) => a.polling && ['pending', 'running', 'repair_pending'].includes(a.polling.state),
    )
  )
    void pollResults();
  if (
    live &&
    doc.value &&
    canEdit.value &&
    aiReady.value &&
    !generation.value &&
    !reusedTemplate.value
  )
    void generate();
});
async function saveBeforeNavigation() {
  try {
    await save();
    return true;
  } catch (e) {
    error.value = errorMessage(e);
    return false;
  }
}
onBeforeRouteLeave(saveBeforeNavigation);
onBeforeRouteUpdate(saveBeforeNavigation);
onBeforeUnmount(() => {
  live = false;
  clearTimeout(saveTimer);
  clearTimeout(generationTimer);
  clearTimeout(resultTimer);
});
</script>

<template>
  <div ref="root" class="walmart-listing" @focusout="queueSave">
    <header class="listing-module-header">
      <div>
        <p class="eyebrow">WALMART US</p>
        <h2>商品刊登资料</h2>
        <p>系统自动整理同 SKU 来源资料；检查生成结果，校验后提交到本批次指定店铺。</p>
        <p v-if="reusedTemplate" class="muted">
          已复用历史模板并替换目标品牌；本批次资料独立保存，修改不会影响原店铺。请核对本店发货仓与编码后重新校验。
        </p>
      </div>
      <span v-if="doc" class="listing-save-state">{{
        saving ? '正在保存…' : dirty ? '有未保存修改' : '资料已保存'
      }}</span>
    </header>
    <p v-if="error" class="error-box" role="alert">
      {{ error }} <button v-if="dirty" class="btn small" @click="run(save)">重试保存</button>
    </p>
    <p v-if="message" class="notice notice-space" role="status">{{ message }}</p>
    <div v-if="!doc" class="listing-start">
      <h3>补齐沃尔玛要求的信息，再正式上架</h3>
      <p>自动带入本批次商品、价格和库存；类目、商品编码及必填属性可在这里继续填写。</p>
      <button v-if="canEdit" class="btn primary" :disabled="busy" @click="prepare">
        填写上架资料
      </button>
    </div>
    <template v-else>
      <div class="listing-context-bar">
        <div>
          <span>目标店铺</span><strong>{{ doc.store.name }}</strong>
        </div>
        <div>
          <span>目标品牌</span><strong>{{ doc.brand }}</strong>
        </div>
        <div>
          <span>资料校验</span
          ><strong>{{
            dirty
              ? '修改后需重新校验'
              : `${doc.validation.valid_count ?? 0} / ${doc.rows.length} SKU`
          }}</strong>
        </div>
        <div>
          <span>已发布</span><strong>{{ completed }} / {{ doc.rows.length }}</strong>
        </div>
        <button
          class="btn small"
          :disabled="busy || saving || generating || !canEdit"
          @click="refreshSource"
        >
          刷新来源价格库存
        </button>
      </div>
      <section class="listing-generation notice-space" aria-label="自动生成状态" aria-live="polite">
        <div class="listing-subheading">
          <div>
            <strong>{{
              generating
                ? '正在自动生成刊登资料'
                : generation
                  ? '刊登资料生成结果'
                  : '自动生成刊登资料'
            }}</strong>
            <p>
              {{
                generation?.message ||
                (aiReady
                  ? '根据商品来源、官方类目与填写规则自动补齐'
                  : 'AI 未配置，来源资料仍可正常查看和编辑')
              }}
            </p>
            <small v-if="generation"
              >已处理 {{ Object.keys(generation.report.rows).length }} /
              {{ generation.report.total ?? doc.rows.length }} 个 SKU ·
              {{ generation.report.model }}</small
            >
          </div>
          <button
            v-if="canEdit"
            class="btn"
            :disabled="busy || saving || generating"
            @click="generate"
          >
            {{ generation ? '重新生成并校验' : '生成并校验' }}
          </button>
        </div>
        <div class="preparation-summary">
          <span
            >可提交 <strong>{{ readyCount }}</strong></span
          ><span
            >前提待补足
            <strong>{{
              Object.values(generation?.report.rows || {}).filter(
                (r) => r.preparation_state === 'needs_prerequisite',
              ).length
            }}</strong></span
          ><span
            >异常待处理
            <strong>{{
              Object.values(generation?.report.rows || {}).filter(
                (r) => r.preparation_state === 'needs_review',
              ).length
            }}</strong></span
          ><button class="btn small" @click="statusFilter = 'issues'">查看异常商品</button>
        </div>
        <p v-if="generationRow" class="notice">
          当前 SKU：<strong>{{ preparationLabel(activeSku) }}</strong
          ><span v-if="generationRow.rounds?.length">
            · 已尝试 {{ generationRow.rounds.length }} / 3 轮定向修复</span
          >
        </p>
        <p v-if="doc.validation.id && !validationCurrent" class="seller-risk-label">
          资料、来源、规格或店铺配置已变化，请重新校验后提交。
        </p>
        <p v-if="generationRow?.collection?.message" class="muted">
          来源补全：{{ generationRow.collection.message }}
          <RouterLink
            v-if="generationRow.collection.listing_id"
            :to="
              (source?.source === 'GigaB2B' ? '/giga-product/' : '/product/') +
              generationRow.collection.listing_id
            "
            >前往商品档案处理来源</RouterLink
          >
        </p>
        <p
          v-if="
            generationRow?.vision && !['disabled', 'complete'].includes(generationRow.vision.state)
          "
          class="muted"
        >
          图片理解：{{ generationRow.vision.message || '未取得可用图片证据' }}
        </p>
        <p v-for="(warning, index) in generationRow?.vision?.warnings" :key="index" class="muted">
          {{ warning }}
        </p>
        <details v-if="generationRow?.rounds?.length">
          <summary>查看定向修复记录</summary>
          <article v-for="(round, index) in generationRow.rounds" :key="index" class="notice">
            <strong
              >第 {{ index + 1 }} 轮 ·
              {{
                round.state === 'changed'
                  ? '已保存修改'
                  : round.state === 'no_progress'
                    ? '无进展，停止重复请求'
                    : '未能安全修复'
              }}</strong
            >
            <p v-if="round.message">{{ round.message }}</p>
            <p v-for="change in round.changes" :key="change.path">
              {{ doc.labels[change.path.split('.').at(-1)!] || change.path }}：{{
                JSON.stringify(change.before)
              }}
              → {{ JSON.stringify(change.after) }}
            </p>
          </article>
        </details>
        <p v-if="generationRow?.estimated" class="notice">
          当前 SKU 有 {{ generationRow.estimated }} 项 AI 估算，具体依据见下方生成记录。
        </p>
        <p v-if="generationStale" class="muted">
          资料已修改，生成记录为修改前的结果。请重新校验；仍有缺项时可重新生成。
        </p>
        <p v-if="!generationStale && generationRow?.issues.length">
          当前 SKU 还有 {{ generationRow.issues.length }} 项需要处理，同一字段的重复提示已合并。
        </p>
        <div
          v-if="!generationStale && generationRow?.issues.length"
          class="listing-generation-issues"
        >
          <button
            v-for="(issue, index) in generationRow.issues"
            :key="index"
            class="btn small"
            @click="jump(issue)"
          >
            {{ doc.labels[issue.path.split('.').at(-1)!] || issue.path || '生成异常' }}：{{
              issue.message
            }}
          </button>
        </div>
        <details
          v-if="
            !generationStale &&
            generationRow?.issues.some((issue) => (issue.details?.length || 0) > 1)
          "
        >
          <summary>查看原始校验详情</summary>
          <dl class="listing-generation-fields">
            <template v-for="(issue, index) in generationRow.issues" :key="index">
              <dt>{{ doc.labels[issue.path.split('.').at(-1)!] || issue.path || '生成异常' }}</dt>
              <dd v-for="(detail, detailIndex) in issue.details" :key="detailIndex">
                {{ detail.message }}
              </dd>
            </template>
          </dl>
        </details>
        <details v-if="generationRow && Object.keys(generationRow.fields).length">
          <summary>查看当前 SKU 的生成依据</summary>
          <dl class="listing-generation-fields">
            <template v-for="(field, path) in generationRow.fields" :key="path"
              ><dt>
                {{ doc.labels[String(path).split('.').at(-1)!] || path }} ·
                {{
                  field.kind === 'visual_inference'
                    ? '视觉推断'
                    : field.kind === 'verified_prerequisite'
                      ? '已核验前提'
                      : field.kind === 'estimate'
                        ? 'AI 估算'
                        : field.kind === 'user_rule'
                          ? '用户规则'
                          : field.kind === 'schema_cleanup'
                            ? '旧类目字段已移除'
                            : '来源映射'
                }}
              </dt>
              <dd>
                {{ field.reason
                }}<span v-if="field.refs?.length"> · {{ field.refs.join('、') }}</span>
                <details v-if="field.kind === 'schema_cleanup'">
                  <summary>查看保留的原值</summary>
                  <pre>{{ JSON.stringify(field.previous_value, null, 2) }}</pre>
                </details>
              </dd></template
            >
          </dl>
        </details>
      </section>
      <div class="listing-workspace">
        <aside class="listing-sku-sidebar">
          <div class="listing-sku-heading">
            <strong>商品 / 子 SKU</strong><span>{{ doc.rows.length }}</span>
          </div>
          <select v-model="statusFilter" aria-label="筛选批次商品">
            <option value="all">全部商品</option>
            <option value="issues">资料待补齐</option>
            <option value="failed">处理异常</option>
          </select>
          <button
            v-for="row in rowsToShow"
            :key="row.platform_sku"
            class="listing-sku-card"
            :class="{ active: row.platform_sku === activeSku }"
            :disabled="busy"
            @click="activeSku = row.platform_sku"
          >
            <ProductImage
              :src="String(row.visible.mainImageUrl || '')"
              image-class="listing-sku-image"
              role="button"
              tabindex="0"
              aria-label="查看商品信息详情"
              @click.stop="detailRow = row"
              @keydown.enter.stop.prevent="detailRow = row"
            />
            <span
              ><strong>{{ row.source_sku }}</strong
              ><small>{{ row.platform_sku }}</small
              ><span class="listing-sku-title">{{
                plain(String(row.visible.productName || '未填写标题'))
              }}</span
              ><small
                :class="{
                  'seller-risk-label':
                    ['failed', 'unpublished', 'rejected'].includes(
                      doc.results[row.platform_sku]?.status || '',
                    ) || doc.validation.rows?.[row.platform_sku]?.valid === false,
                }"
                >{{
                  doc.results[row.platform_sku]
                    ? statusLabels[doc.results[row.platform_sku].status] || '待确认'
                    : doc.validation.rows?.[row.platform_sku]?.valid === false
                      ? '资料待补齐'
                      : doc.validation.rows?.[row.platform_sku]?.valid
                        ? '校验通过'
                        : '未校验'
                }}</small
              ></span
            >
          </button>
          <p v-if="!rowsToShow.length" class="muted">没有符合条件的商品</p>
        </aside>
        <main v-if="current" class="listing-form-main">
          <ProductStoreHistory :entries="source?.store_history" />
          <div class="listing-product-heading">
            <div>
              <span class="muted"
                >{{ source?.source === 'GigaB2B' ? '大建云仓' : '亚马逊' }} ·
                {{ current.source_sku }}</span
              >
              <h3>{{ plain(String(current.visible.productName || '请填写商品标题')) }}</h3>
              <p>
                店铺 SKU <strong>{{ current.platform_sku }}</strong>
              </p>
            </div>
            <div class="listing-price-overview">
              <strong>{{ money(String(current.orderable.price ?? '')) }}</strong
              ><span>目标库存 {{ sourceVariant?.stock ?? '待核验' }}</span>
            </div>
          </div>
          <div v-if="issues.length && !dirty" class="listing-issue-list">
            <strong>此 SKU 有 {{ issues.length }} 项需要处理</strong
            ><button v-for="(issue, index) in issues.slice(0, 3)" :key="index" @click="jump(issue)">
              {{ issue.message }} <span>定位 →</span>
            </button>
            <details v-if="issues.length > 3">
              <summary>展开其余 {{ issues.length - 3 }} 项</summary>
              <button v-for="(issue, index) in issues.slice(3)" :key="index" @click="jump(issue)">
                {{ issue.message }} <span>定位 →</span>
              </button>
            </details>
          </div>
          <p v-if="locked && result" class="notice notice-space">
            {{ result.message }}。当前状态不允许修改或重复提交。
          </p>
          <div v-if="unsupported.length" class="notice notice-space">
            <p>有 {{ unsupported.length }} 项旧类目属性不在当前规格中，请核对后移除。</p>
            <details>
              <summary>查看旧属性</summary>
              <pre>{{ Object.fromEntries(unsupported.map((k) => [k, current!.visible[k]])) }}</pre>
            </details>
            <button
              v-if="!locked"
              class="btn small"
              @click="unsupported.forEach((k) => update('visible', k, undefined))"
            >
              移除这些旧类目属性
            </button>
          </div>
          <nav class="listing-tabs" aria-label="刊登资料分组">
            <button
              v-for="[key, label] in [
                ['basic', '商品刊登资料'],
                ['optional', '选填属性'],
                ['results', '处理结果'],
              ]"
              :key="key"
              :class="{ active: tab === key }"
              @click="tab = key"
            >
              {{ label }}
              <span v-if="key === 'basic' && mandatoryKeys.length">{{ mandatoryKeys.length }}</span>
            </button>
          </nav>
          <nav v-if="tab === 'basic'" class="listing-section-links" aria-label="资料分区定位">
            <a
              href="#listing-basic"
              @click.prevent="
                root?.querySelector('#listing-basic')?.scrollIntoView({ behavior: 'smooth' })
              "
              >基本信息</a
            >
            <a
              href="#listing-content"
              @click.prevent="
                root?.querySelector('#listing-content')?.scrollIntoView({ behavior: 'smooth' })
              "
              >图片与文案</a
            >
            <a
              href="#listing-required"
              @click.prevent="
                root?.querySelector('#listing-required')?.scrollIntoView({ behavior: 'smooth' })
              "
              >必填属性 {{ mandatoryKeys.length }}</a
            >
          </nav>
          <section v-if="tab === 'basic'" id="listing-basic" class="listing-form-section">
            <div class="listing-subheading"><h3>基本信息与价格</h3></div>
            <div class="listing-field" data-listing-path="product_type">
              <label class="listing-field-label"
                ><span><i class="listing-required">*</i>沃尔玛商品类型</span
                ><small>Product Type</small></label
              >
              <div class="listing-field-control">
                <input
                  v-model="typeQuery"
                  placeholder="搜索类目或商品类型"
                  :disabled="locked || busy"
                  aria-label="搜索沃尔玛商品类型"
                />
                <div class="listing-inline-controls">
                  <select
                    v-model="current.product_type"
                    :disabled="locked || busy"
                    aria-label="沃尔玛商品类型"
                    @change="changeType"
                  >
                    <option value="">请选择真实商品类型</option>
                    <option
                      v-if="
                        current.product_type &&
                        !typeOptions.some((t) => t.name === current!.product_type)
                      "
                      :value="current.product_type"
                    >
                      {{ current.product_type }}
                    </option>
                    <option v-for="type in typeOptions" :key="type.name" :value="type.name">
                      {{ type.category }} / {{ type.group }} / {{ type.name }}
                    </option></select
                  ><button
                    class="btn small"
                    :disabled="busy || generating"
                    @click="
                      run(async () => {
                        await loadMetadata();
                        await getSpec();
                      })
                    "
                  >
                    获取字段
                  </button>
                </div>
                <small v-if="source?.category" class="muted">来源类目：{{ source.category }}</small>
                <small class="muted"
                  >系统匹配当前店铺的实际商品类型；有冲突时记录原因，确认来源后可重新生成。</small
                >
              </div>
            </div>
            <div class="listing-field" data-listing-path="fulfillment_center_id">
              <label class="listing-field-label"
                ><span><i class="listing-required">*</i>沃尔玛发货仓</span
                ><small>Fulfillment Center</small></label
              >
              <div class="listing-field-control">
                <select
                  v-model="current.fulfillment_center_id"
                  :disabled="locked || busy"
                  aria-label="沃尔玛发货仓"
                >
                  <option value="">请选择目标店铺启用的发货仓</option>
                  <option v-for="node in nodes" :key="node.id" :value="node.id">
                    {{ node.name }} · {{ node.id }}
                  </option></select
                ><small class="muted">此处为沃尔玛店铺发货仓，与供应商分仓不同。</small>
              </div>
            </div>
            <div class="listing-field" data-listing-path="identification">
              <label class="listing-field-label"
                ><span><i class="listing-required">*</i>商品编码 / 豁免</span
                ><small>EAN / GTIN / UPC</small></label
              >
              <div class="listing-field-control">
                <div class="listing-inline-controls">
                  <select
                    v-model="current.identification.mode"
                    :disabled="locked"
                    aria-label="商品编码方式"
                  >
                    <option value="identifier">填写商品真实编码</option>
                    <option value="exemption">使用已获批的 GTIN 豁免</option></select
                  ><select
                    v-if="current.identification.mode === 'identifier'"
                    v-model="current.identification.type"
                    :disabled="locked"
                    aria-label="商品编码类型"
                  >
                    <option>UPC</option>
                    <option>GTIN</option>
                    <option>EAN</option>
                    <option>ISBN</option>
                  </select>
                </div>
                <input
                  v-if="current.identification.mode === 'identifier'"
                  v-model="current.identification.value"
                  :disabled="locked"
                  placeholder="真实编码，保留前导零"
                  aria-label="商品编码"
                /><template v-else
                  ><input
                    v-model="current.identification.submission_id"
                    :disabled="locked"
                    placeholder="沃尔玛已批准的豁免申请编号"
                    aria-label="豁免申请编号"
                  /><small class="muted"
                    >校验时会查询此店铺、品牌和商品类型是否已获批，不能仅填写“已豁免”。</small
                  ></template
                >
                <div
                  v-if="current.identification.mode === 'identifier'"
                  class="identifier-pool-tools"
                >
                  <small
                    :class="doc.identifier_pool?.available ? 'muted' : 'identifier-pool-empty'"
                  >
                    编码池：共 {{ doc.identifier_pool?.total ?? 0 }} 个，本品牌可分配
                    {{ doc.identifier_pool?.available ?? 0 }} 个。
                    <template v-if="!doc.identifier_pool?.total"
                      >请先在当前服务器导入表格，本机编码池不会随 Git 同步。</template
                    >
                  </small>
                  <div class="actions">
                    <button
                      v-if="canEdit"
                      class="btn small"
                      :disabled="locked || busy"
                      @click="run(() => assignCodes())"
                    >
                      从编码池补齐空白 EAN
                    </button>
                    <button
                      v-if="canSubmit"
                      class="btn small"
                      :disabled="locked || busy"
                      @click="codePoolOpen = true"
                    >
                      导入 EAN 编码表格
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <ListingSchemaField
              v-for="key in basic.filter(
                (k) => visibleProperties[k] || ['productName', 'brand'].includes(k),
              )"
              :key="key"
              :name="key"
              :schema="fallbackSchema(key)"
              :labels="doc.labels"
              :model-value="current.visible[key]"
              :disabled="locked || key === 'brand'"
              :required="required.includes(key) || key === 'productName' || key === 'brand'"
              :path="'visible.' + key"
              :issues="dirty ? [] : fieldIssues(key)"
              @update:model-value="update('visible', key, $event)"
            />
            <div class="listing-subheading">
              <h4>价格与履约</h4>
              <p>
                倍率 ×{{ doc.basis.multiplier }} · 已核验计价基准 {{ money(sourceVariant?.base) }} ·
                目标库存 {{ sourceVariant?.stock ?? '未核验' }}
              </p>
            </div>
            <template v-if="currentSpec"
              ><ListingSchemaField
                v-for="key in orderableKeys"
                :key="key"
                :name="key"
                :schema="currentSpec.sections.orderable.properties![key]"
                :labels="doc.labels"
                :model-value="current.orderable[key]"
                :disabled="locked"
                :required="requiredOrderable.includes(key)"
                :path="'orderable.' + key"
                :issues="dirty ? [] : fieldIssues(key, 'Orderable')"
                @update:model-value="update('orderable', key, $event)"
            /></template>
            <p v-else class="notice">先选择商品类型，加载该类目的价格、运输重量和原产国等要求。</p>
            <details class="listing-raw">
              <summary>来源价格及库存依据</summary>
              <pre>{{ sourceVariant }}</pre>
            </details>
          </section>
          <section v-if="tab === 'basic'" id="listing-content" class="listing-form-section">
            <div class="listing-subheading"><h3>图片与文案</h3></div>
            <div class="listing-subheading">
              <h4>商品图片</h4>
              <p>
                {{ photos.length }} / 8 张 · 第一张为主图，可点击放大。优先白底商品图、尺寸参数图。
              </p>
              <label v-if="!locked" class="btn small"
                >本地添加图片<input
                  type="file"
                  accept="image/*"
                  multiple
                  hidden
                  :disabled="busy"
                  @change="upload"
              /></label>
            </div>
            <div class="listing-photo-gallery">
              <figure v-for="(url, i) in photos" :key="url + i" class="listing-photo-card">
                <button
                  class="listing-photo-zoom"
                  :aria-label="'放大图片 ' + (i + 1)"
                  @click="enlarge(url)"
                >
                  <ProductImage :src="url" image-class="listing-product-image" />
                </button>
                <figcaption>
                  <span>{{ i === 0 ? '主图' : '附图 ' + i }}</span>
                  <template v-if="!locked">
                    <button
                      v-if="i > 0"
                      class="btn small"
                      @click="setPhotos([url, ...photos.filter((_, j) => j !== i)])"
                    >
                      设主图
                    </button>
                    <button
                      class="icon-btn"
                      :aria-label="'删除图片 ' + (i + 1)"
                      @click="setPhotos(photos.filter((_, j) => j !== i))"
                    >
                      <AppIcon name="X" />
                    </button>
                  </template>
                </figcaption>
              </figure>
            </div>
            <ListingSchemaField
              v-for="key in [
                'mainImageUrl',
                'productSecondaryImageURL',
                'shortDescription',
                'longDescription',
                'keyFeatures',
              ].filter(
                (k) =>
                  visibleProperties[k] ||
                  ['mainImageUrl', 'shortDescription', 'keyFeatures'].includes(k),
              )"
              :key="key"
              :name="key"
              :schema="fallbackSchema(key)"
              :labels="doc.labels"
              :model-value="current.visible[key]"
              :disabled="locked"
              :required="required.includes(key)"
              :path="'visible.' + key"
              :issues="dirty ? [] : fieldIssues(key)"
              @update:model-value="update('visible', key, $event)"
            />
          </section>
          <section
            v-if="tab === 'basic' || tab === 'optional'"
            id="listing-required"
            class="listing-form-section"
          >
            <p v-if="!currentSpec" class="notice">
              请先在“基本信息”中选择沃尔玛商品类型并获取字段。
            </p>
            <template v-else
              ><div class="listing-subheading">
                <h3>{{ tab === 'basic' ? '该类目必填属性' : '该类目选填属性' }}</h3>
                <p>单位、数值及选项均以沃尔玛当前规格为准。</p>
              </div>
              <div v-if="tab === 'optional'" class="listing-inline-controls">
                <input
                  v-model="optionalQuery"
                  class="listing-optional-search"
                  placeholder="搜索属性名称，添加需要的信息"
                  aria-label="搜索选填属性"
                />
                <label class="listing-toggle"
                  ><input v-model="showAllOptional" type="checkbox" />显示全部选填项</label
                >
              </div>
              <p v-if="tab === 'optional' && !optionalQuery && !showAllOptional" class="muted">
                当前显示已填写项；搜索名称或勾选“显示全部”可添加更多属性。
              </p>
              <ListingSchemaField
                v-for="key in tab === 'basic'
                  ? mandatoryKeys
                  : optionalKeys.filter((k) => showOptional(k, 'visible'))"
                :key="key"
                :name="key"
                :schema="visibleProperties[key]"
                :labels="doc.labels"
                :model-value="current.visible[key]"
                :disabled="locked"
                :required="required.includes(key)"
                :path="'visible.' + key"
                :issues="dirty ? [] : fieldIssues(key)"
                @update:model-value="update('visible', key, $event)" />
              <template v-if="tab === 'optional'"
                ><div class="listing-subheading"><h4>选填履约信息</h4></div>
                <ListingSchemaField
                  v-for="key in optionalOrderableKeys.filter((k) => showOptional(k, 'orderable'))"
                  :key="key"
                  :name="key"
                  :schema="currentSpec.sections.orderable.properties![key]"
                  :labels="doc.labels"
                  :model-value="current.orderable[key]"
                  :disabled="locked"
                  :path="'orderable.' + key"
                  :issues="dirty ? [] : fieldIssues(key, 'Orderable')"
                  @update:model-value="update('orderable', key, $event)"
              /></template>
              <template v-for="(schema, key) in currentSpec.sections" :key="key"
                ><ListingSchemaField
                  v-if="!['visible', 'orderable'].includes(String(key))"
                  :name="String(key)"
                  :schema="schema"
                  :labels="doc.labels"
                  :model-value="current.extra[key]"
                  :disabled="locked"
                  :path="'extra.' + key"
                  @update:model-value="current!.extra[key] = $event" /></template
            ></template>
          </section>
          <section v-if="tab === 'results'" class="listing-form-section listing-results">
            <div class="listing-subheading">
              <h4>沃尔玛处理结果</h4>
              <p>收到批次编号代表已接收；以逐项处理和发布状态为准。</p>
              <button v-if="canSubmit" class="btn small" :disabled="busy" @click="refreshResults()">
                刷新处理结果
              </button>
            </div>
            <table class="data-table">
              <thead>
                <tr>
                  <th>来源 SKU / 店铺 SKU</th>
                  <th>状态</th>
                  <th>中文原因</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in doc.rows" :key="row.platform_sku">
                  <td>
                    {{ row.source_sku }}<br /><strong>{{ row.platform_sku }}</strong>
                  </td>
                  <td
                    :class="{
                      'seller-risk-label': [
                        'failed',
                        'unpublished',
                        'rejected',
                        'stock_pending',
                      ].includes(doc.results[row.platform_sku]?.status || ''),
                    }"
                  >
                    {{ statusLabels[doc.results[row.platform_sku]?.status] || '尚未提交' }}
                  </td>
                  <td>
                    {{ doc.results[row.platform_sku]?.message || '填写资料并校验后可提交' }}
                    <p
                      v-for="(e, i) in doc.results[row.platform_sku]?.errors"
                      :key="i"
                      class="seller-risk-label"
                    >
                      {{ e.message }}
                    </p>
                    <details v-if="doc.results[row.platform_sku]?.errors.length">
                      <summary>原始原因</summary>
                      <pre>{{ doc.results[row.platform_sku].errors }}</pre>
                    </details>
                    <button
                      v-if="
                        ['failed', 'rejected', 'unpublished'].includes(
                          doc.results[row.platform_sku]?.status || '',
                        )
                      "
                      class="btn small"
                      @click="
                        activeSku = row.platform_sku;
                        tab = 'basic';
                        statusFilter = 'all';
                      "
                    >
                      修改此商品后重试
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <article v-for="a in doc.attempts" :key="a.id" class="listing-attempt">
              <div>
                <strong>{{ a.feed_id ? '沃尔玛批次 ' + a.feed_id : '待确认提交 ' + a.id }}</strong>
                <p>
                  {{ new Date(a.created_at).toLocaleString('zh-CN') }} · {{ a.skus.length }} 个 SKU
                  · 店铺 {{ doc.store.name }} · Partner {{ a.partner_id }}
                </p>
                <p>{{ a.message }}</p>
                <p v-if="a.polling">
                  后台查询：{{
                    a.polling.state === 'complete'
                      ? '已结束'
                      : a.polling.state === 'repair_pending'
                        ? '正在生成修复草稿（不会自动重提）'
                        : a.polling.state === 'paused'
                          ? '已暂停，需管理员处理'
                          : '持续查询原批次'
                  }}
                  · 已查询 {{ a.polling.tries }} 次<span v-if="a.polling.state === 'pending'">
                    · 下次
                    {{ new Date(a.polling.next_at * 1000).toLocaleTimeString('zh-CN') }}</span
                  >
                </p>
                <p v-if="a.polling?.last_error" class="seller-risk-label">
                  {{ a.polling.last_error }}
                </p>
              </div>
              <button
                v-if="a.feed_id && canSubmit"
                class="btn small"
                :disabled="busy"
                @click="refreshResults(a)"
              >
                查询并同步库存
              </button>
              <div
                v-if="!a.feed_id && ['sending', 'unknown'].includes(a.state)"
                class="listing-reconcile"
              >
                <p class="seller-risk-label">
                  结果未知，不能直接重提。请在沃尔玛卖家中心核对本次 SKU 清单，并关联原批次编号。
                </p>
                <input
                  v-model="reconcileIds[a.id]"
                  placeholder="原沃尔玛 Feed ID"
                  :aria-label="'关联批次 ' + a.id"
                /><button
                  v-if="canSubmit"
                  class="btn small"
                  :disabled="busy || !reconcileIds[a.id]"
                  @click="reconcile(a)"
                >
                  核验并关联原批次
                </button>
              </div>
            </article>
            <p v-if="!doc.attempts.length" class="muted">尚未向沃尔玛提交任何商品。</p>
          </section>
          <details class="listing-raw">
            <summary>查看本 SKU 完整资料 JSON</summary>
            <pre>{{ current }}</pre>
          </details>
        </main>
      </div>
      <footer class="listing-action-bar">
        <div class="listing-selection">
          <label
            ><input
              type="checkbox"
              :checked="validSkus.length > 0 && selectedValid.length === validSkus.length"
              :disabled="dirty || !validSkus.length"
              @change="selected = ($event.target as HTMLInputElement).checked ? [...validSkus] : []"
            />选择校验通过且可提交的 {{ validSkus.length }} 个 SKU</label
          >
          <details v-if="validSkus.length">
            <summary>已选 {{ selectedValid.length }} 个 · 调整选择</summary>
            <label
              v-for="row in eligible.filter((r) => validSkus.includes(r.platform_sku))"
              :key="row.platform_sku"
              ><input v-model="selected" type="checkbox" :value="row.platform_sku" />{{
                row.source_sku
              }}
              · {{ row.platform_sku }}</label
            >
          </details>
        </div>
        <div class="actions">
          <button
            v-if="canEdit"
            class="btn"
            :disabled="busy || saving || generating || !dirty"
            @click="run(save)"
          >
            保存资料</button
          ><button
            v-if="canEdit"
            class="btn"
            :disabled="busy || saving || generating"
            @click="validate"
          >
            1. 校验上架资料</button
          ><button
            v-if="canSubmit"
            class="btn primary"
            :disabled="
              busy || saving || generating || dirty || !selectedValid.length || !doc.validation.id
            "
            @click="openSubmit"
          >
            2. 提交沃尔玛</button
          ><button
            class="btn"
            :disabled="busy"
            @click="canSubmit ? refreshResults() : (tab = 'results')"
          >
            3. 查看处理结果
          </button>
        </div>
      </footer>
      <dialog
        ref="zoomDialog"
        class="listing-image-dialog"
        @click="$event.target === zoomDialog && zoomDialog?.close()"
      >
        <button class="icon-btn" aria-label="关闭大图" @click="zoomDialog?.close()">
          <AppIcon name="X" />
        </button>
        <ProductImage :src="zoomUrl" image-class="listing-full-image" />
      </dialog>
      <dialog
        ref="confirmDialog"
        class="listing-confirm-dialog"
        @cancel="busy ? $event.preventDefault() : undefined"
      >
        <div class="listing-confirm-header">
          <h3>确认正式提交沃尔玛</h3>
          <button
            class="icon-btn"
            aria-label="关闭"
            :disabled="busy"
            @click="confirmDialog?.close()"
          >
            <AppIcon name="X" />
          </button>
        </div>
        <p>
          以下商品将提交至 <strong>{{ doc.store.name }}</strong
          >，Partner ID <strong>{{ doc.validation.partner_id }}</strong
          >。
        </p>
        <table class="data-table">
          <thead>
            <tr>
              <th>店铺 SKU</th>
              <th>商品</th>
              <th>售价</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in doc.rows.filter((r) => selectedValid.includes(r.platform_sku))"
              :key="row.platform_sku"
            >
              <td>{{ row.platform_sku }}</td>
              <td>{{ plain(String(row.visible.productName || '')) }}</td>
              <td>{{ money(String(row.orderable.price || '')) }}</td>
            </tr>
          </tbody>
        </table>
        <label class="listing-confirm-label"
          >输入目标店铺名 <strong>{{ doc.store.name }}</strong> 以确认<input
            v-model="confirmation"
            :placeholder="doc.store.name"
            :disabled="busy"
        /></label>
        <p class="subtitle">提交后请查询逐项结果。处理中的商品不能再次提交。</p>
        <div class="actions">
          <button class="btn" :disabled="busy" @click="confirmDialog?.close()">返回检查</button
          ><button
            class="btn primary"
            :disabled="busy || confirmation !== doc.store.name"
            @click="submit"
          >
            {{ busy ? '正在提交…' : `正式提交 ${selectedValid.length} 个 SKU` }}
          </button>
        </div>
      </dialog>
    </template>
    <ListingProductDetails
      v-if="detailRow"
      :listing-id="detailRow.listing_id"
      :row="detailRow"
      @close="detailRow = undefined"
    />
    <ListingPreparationSettings
      v-if="codePoolOpen"
      @imported="importedCodes += $event"
      @close="closeCodePool"
    />
  </div>
</template>

<style scoped>
.identifier-pool-tools {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}
.identifier-pool-empty {
  color: #ab5b36;
}
.preparation-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 16px 0;
}
.preparation-summary > span {
  padding: 10px 14px;
  background: #f1f6fa;
  border: 1px solid #dde7ef;
  border-radius: 10px;
  color: #536b7e;
}
.preparation-summary strong {
  margin-left: 10px;
  font-size: 20px;
  color: #2e516c;
}
</style>
