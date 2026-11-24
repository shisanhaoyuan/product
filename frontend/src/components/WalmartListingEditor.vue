<script setup lang="ts">
import { sourceInfo } from '../utils/productSources';
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
import { errorMessage, plain, money as formatMoney } from '../utils/display';
import { listingSourceImages } from '../utils/listingSourceImages';
import { listingAsins, listingIdentityLabel } from '../utils/listingIdentity';
import { claimAutomaticCollection } from '../utils/automaticCollection';
import { shouldPrepareOnEntry } from '../utils/listingPreparation';
import { completeGroupCandidates, toggleVariantSelection, reviewVariantValue, reviewedVariantAttributes } from '../utils/listingVariants';
import { collectMissingPrices, collectListingSources, preferLocalSourceBrowser } from '../utils/sourceCollection';
import { applyListingDefaults, setFamilyProductType } from '../utils/listingDefaults';
import ListingSchemaField from './ListingSchemaField.vue';
import ListingVariantGroup from './ListingVariantGroup.vue';
import ListingPriceRecovery from './ListingPriceRecovery.vue';
import ListingStatusNotices from './ListingStatusNotices.vue';
import ProductImage from './ProductImage.vue';
import ListingProductDetails from './ListingProductDetails.vue';
import ProductStoreHistory from './ProductStoreHistory.vue';
import ListingImagePicker from './ListingImagePicker.vue';
import WalmartResultReasons from './WalmartResultReasons.vue';
function fixedOrigin(key: string, schema?: JsonSchema) {
  const normalize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '');
  return [key, schema?.title || ''].some(
    (value) => normalize(value) === 'countryoforiginsubstantialtransformation',
  );
}
const detailRow = ref<ListingRow>();
import AppIcon from './AppIcon.vue';
import ListingPreparationSettings from './ListingPreparationSettings.vue';
import { preparationApi } from '../api/preparation';
const props = defineProps<{ bid: string }>();
function money(value: string | number | null | undefined) {
  return formatMoney(value, doc.value?.currency || 'USD');
}
const canadaSpecFile = ref<HTMLInputElement>();
async function importCanadaSpecs(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  await run(async () => {
    if (file.size > 15 * 1024 * 1024) throw new Error('规格文件不能超过 15 MB');
    if (dirty.value) await save();
    install(await publishingApi.importCanadaSpecs(props.bid, JSON.parse(await file.text())));
    specs.value = { ...doc.value?.specs };
    await loadMetadata();
    message.value = '加拿大官方规格已导入，可开始 AI 填充并检查结果。';
  });
  input.value = '';
}
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
const activeOptional = ref<Set<string>>(new Set());
const zoomDialog = ref<HTMLDialogElement>();
const zoomUrl = ref('');
const imagePickerOpen = ref(false);
const optionalMatches = (key: string) =>
  `${key} ${doc.value?.labels[key] || ''} ${visibleProperties.value[key]?.title || ''} ${currentSpec.value?.sections.orderable.properties?.[key]?.title || ''}`
    .toLowerCase()
    .includes(optionalQuery.value.trim().toLowerCase());
type OptionalAttribute = {
  key: string;
  section: 'visible' | 'orderable';
  schema: JsonSchema;
  recommended: boolean;
};
const optionalAttributes = computed<OptionalAttribute[]>(() => {
  const visible = optionalKeys.value.map((key) => ({
    key,
    section: 'visible' as const,
    schema: visibleProperties.value[key],
    recommended: /@group=Recommended/i.test(visibleProperties.value[key]?.comments || ''),
  }));
  const orderable = optionalOrderableKeys.value.map((key) => ({
    key,
    section: 'orderable' as const,
    schema: currentSpec.value?.sections.orderable.properties?.[key] || {},
    recommended: /@group=Recommended/i.test(
      currentSpec.value?.sections.orderable.properties?.[key]?.comments || '',
    ),
  }));
  return [...visible, ...orderable];
});
const recommendedAttributes = computed(() =>
  optionalAttributes.value.filter(
    (item) =>
      item.recommended &&
      (optionalMatches(item.key) || optionalIsSelected(item)),
  ),
);
const otherOptionalAttributes = computed(() =>
  optionalAttributes.value.filter(
    (item) =>
      !item.recommended &&
      (optionalMatches(item.key) || optionalIsSelected(item)),
  ),
);
function optionalId(item: OptionalAttribute) {
  return `${item.section}.${item.key}`;
}
function optionalIsSelected(item: OptionalAttribute) {
  return (
    current.value?.[item.section][item.key] !== undefined ||
    activeOptional.value.has(optionalId(item))
  );
}
function toggleOptional(item: OptionalAttribute) {
  if (locked.value || fixedOrigin(item.key, item.schema)) return;
  const next = new Set(activeOptional.value);
  const id = optionalId(item);
  if (optionalIsSelected(item)) {
    next.delete(id);
    update(item.section, item.key, undefined);
  } else next.add(id);
  activeOptional.value = next;
}
function addOptionalGroup(items: OptionalAttribute[]) {
  activeOptional.value = new Set([
    ...activeOptional.value,
    ...items.map(optionalId),
  ]);
}
watch(activeSku, () => (activeOptional.value = new Set()));
const photos = computed(() =>
  [
    current.value?.visible.mainImageUrl,
    ...((current.value?.visible.productSecondaryImageURL as string[]) || []),
  ].filter((url): url is string => typeof url === 'string' && !!url),
);
const draggingPhotoIndex = ref<number | null>(null);
function setPhotos(urls: string[]) {
  update('visible', 'mainImageUrl', urls[0]);
  update('visible', 'productSecondaryImageURL', [...new Set(urls)].slice(1));
}
function reorderPhoto(from: number, to: number) {
  if (from === to || from < 0 || to < 0 || from >= photos.value.length || to >= photos.value.length)
    return;
  const reordered = [...photos.value];
  const [photo] = reordered.splice(from, 1);
  reordered.splice(to, 0, photo);
  setPhotos(reordered);
}
function startPhotoDrag(event: DragEvent, index: number) {
  if (locked.value) return;
  draggingPhotoIndex.value = index;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(index));
  }
}
function finishPhotoDrag() {
  draggingPhotoIndex.value = null;
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
let sourceCollection: AbortController | undefined;
const serialized = () =>
  doc.value ? JSON.stringify({ version: doc.value.version, rows: doc.value.rows }) : '';
const dirty = computed(() => !!doc.value && serialized() !== saved.value);
const current = computed(() => doc.value?.rows.find((r) => r.platform_sku === activeSku.value));
const asins = computed(() => doc.value ? listingAsins(doc.value.rows, doc.value.basis) : {});
const identityLabel = (row: ListingRow) => listingIdentityLabel(row.platform_sku, asins.value);
watch(() => current.value?.product_type, () => (activeOptional.value = new Set()));
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
const collectingSources = ref(false);
const collectionMessage = ref('');
const awaitingSources = computed(() => generation.value?.state === 'awaiting_sources');
const generating = computed(() => ['awaiting_sources', 'queued', 'running'].includes(generation.value?.state || ''));
const generationProcessed = computed(() =>
  Object.values(generation.value?.report.rows || {}).filter(row =>
    ['complete', 'review', 'failed', 'skipped'].includes(row.state),
  ).length,
);
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
async function generate(foregroundFirst = false) {
  await run(async () => {
    if (!awaitingSources.value) {
      await save();
      generation.value = await publishingApi.generate(props.bid);
    }
    if (generation.value?.state === 'awaiting_sources') {
      claimAutomaticCollection('listing', [props.bid, generation.value.id]);
      collectingSources.value = true;
      sourceCollection = new AbortController();
      try {
        const receipts = await collectListingSources(generation.value.report.source_targets || [], sourceCollection.signal,
          text => { collectionMessage.value = text; }, foregroundFirst);
        if (!live) return;
        generation.value = await publishingApi.resumeSources(props.bid, generation.value.id, receipts);
        collectionMessage.value = '';
      } finally { collectingSources.value = false; }
    }
    await pollGeneration();
  });
}
async function cancelSourceCollection() {
  const job = generation.value;
  if (job?.state !== 'awaiting_sources') return;
  sourceCollection?.abort();
  try {
    generation.value = await publishingApi.cancelSources(props.bid, job.id);
    collectionMessage.value = '';
    await pollGeneration();
  } catch (e) { error.value = errorMessage(e); }
}
async function setSourceLocation() {
  preferLocalSourceBrowser(true);
  await generate(true);
}
const currentSpec = computed(() =>
  current.value ? specs.value[current.value.product_type] : undefined,
);
const source = computed(() =>
  doc.value?.basis.items.find((i) => i.listing_id === current.value?.listing_id),
);
const variantGroup = computed(() => doc.value?.variant_groups?.find((g) => g.listing_id === current.value?.listing_id));
function setVariantAxes(axes: string[]) {
  if (!doc.value || !variantGroup.value || locked.value || variantGroup.value.locked) return;
  for (const row of doc.value.rows.filter((r) => variantGroup.value!.skus.includes(r.platform_sku))) {
    row.variant_manual = true;
    row.visible.variantAttributeNames = [...axes];
  }
}
function setPrimaryVariant(sku: string) {
  if (!doc.value || !variantGroup.value || locked.value || variantGroup.value.locked) return;
  for (const row of doc.value.rows.filter((r) => variantGroup.value!.skus.includes(r.platform_sku))) {
    row.variant_manual = true;
    row.visible.isPrimaryVariant = row.platform_sku === sku ? 'Yes' : 'No';
  }
}
function setVariantValue(sku: string, key: string, value: unknown) {
  if (!doc.value || !variantGroup.value || busy.value || generating.value || !canEdit.value) return;
  reviewVariantValue(doc.value.rows, variantGroup.value.skus, eligible.value.map((r) => r.platform_sku), sku, key, value);
}
async function refreshVariantFields() {
  await run(async () => {
    await save();
    await getSpec();
    await load();
    message.value = '类目属性已刷新，可以在规格表中逐项复验修改';
  });
}
function setTargetStock(event: Event) {
  if (!current.value || locked.value) return;
  const value = (event.target as HTMLInputElement).value;
  current.value.target_stock = value === '' ? null : Number(value);
}
const sourceVariant = computed(() =>
  source.value?.variants.find((v) => v.platform_sku === activeSku.value),
);
const latestSourceImages = ref<string[]>([]);
const sourceImagesLoading = ref(false);
const syncingImages = ref(false);
let sourceImageSequence = 0;
watch(
  () => [current.value?.listing_id, current.value?.source_sku, sourceVariant.value?.asin],
  async () => {
    const seq = ++sourceImageSequence;
    latestSourceImages.value = [];
    const row = current.value;
    if (!row) {
      sourceImagesLoading.value = false;
      return;
    }
    sourceImagesLoading.value = true;
    try {
      const product = await api.product(row.listing_id);
      if (!live || seq !== sourceImageSequence) return;
      latestSourceImages.value = listingSourceImages(
        product,
        row.source_sku,
        sourceVariant.value?.asin || '',
      );
    } catch {
      // Keep the historical selection visible, but never pass stale images off as current.
    } finally {
      if (seq === sourceImageSequence) sourceImagesLoading.value = false;
    }
  },
  { immediate: true },
);
async function syncCurrentImages() {
  if (locked.value || !current.value || syncingImages.value) return;
  const row = current.value;
  const before = JSON.stringify(photos.value);
  syncingImages.value = true;
  try {
    const product = await api.product(row.listing_id);
    if (row !== current.value || locked.value) return;
    if (JSON.stringify(photos.value) !== before)
      throw new Error('读取期间图片选择已改变，请重新点击同步');
    const images = listingSourceImages(product, row.source_sku, sourceVariant.value?.asin || '');
    if (!images.length) throw new Error('当前 SKU／ASIN 没有可用图片，请先在对应商品档案补充图片');
    latestSourceImages.value = images;
    setPhotos(images.slice(0, 8));
    await save();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    syncingImages.value = false;
  }
}
const result = computed(() => doc.value?.results[activeSku.value]);
const locked = computed(
  () =>
    generating.value ||
    allocatingCodes.value ||
    !canEdit.value ||
    (!!result.value && !['failed', 'rejected', 'unpublished'].includes(result.value.status)),
);
const issues = computed(() => doc.value?.validation.rows?.[activeSku.value]?.errors || []);
const priceReview = computed(() => doc.value?.source_price_reviews?.[activeSku.value]);
const priceRecovery = ref<InstanceType<typeof ListingPriceRecovery>>();
const hasSellingPrice = computed(() => Number.isFinite(Number(current.value?.orderable.price)) && Number(current.value?.orderable.price) > 0);
const needsSourcePrice = computed(() => source.value?.source === 'Amazon' &&
  sourceVariant.value?.error === '缺少来源采集原价，请采集卖家与报价后重新计算' && !priceReview.value);
async function openPriceRecovery() {
  tab.value = 'basic';
  await nextTick();
  priceRecovery.value?.focus();
}

function canVerifySourceIssue(issue: Issue) {
  const missing = '缺少来源采集原价，请采集卖家与报价后重新计算';
  return issue.code === 'source_price_review' || (source.value?.source === 'Amazon' &&
    sourceVariant.value?.error === missing && [
      `${sourceVariant.value.sku}：${missing}`, '缺少已核验的来源售价，不能提交',
    ].includes(issue.message));
}
async function verifySourcePrice(originalPrice?: string) {
  if (locked.value || !current.value) return;
  if (!originalPrice && !hasSellingPrice.value) {
    await openPriceRecovery();
    return;
  }
  const sku = current.value.platform_sku;
  await run(async () => {
    await save();
    if (!doc.value) return;
    install(await publishingApi.verifySourcePrice(props.bid, doc.value.revision, sku, originalPrice));
    install(await publishingApi.validate(props.bid, doc.value.revision));
    message.value = '当前售价已人工验证，已解除来源原价缺失限制；其他要求仍按校验结果显示';
  });
}
const basic = ['productName', 'brand', 'condition', 'condition_notes'];
const copyKeys = [
  'shortDescription',
  'longDescription',
  'keyFeatures',
  'mainImageUrl',
  'productSecondaryImageURL',
];
const groupFields = ['variantGroupId', 'variantAttributeNames', 'isPrimaryVariant'];
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
    (k) => required.value.includes(k) && !basic.includes(k) && !copyKeys.includes(k) && !groupFields.includes(k),
  ),
);
const optionalKeys = computed(() =>
  Object.keys(visibleProperties.value).filter(
    (k) => !required.value.includes(k) && !basic.includes(k) && !copyKeys.includes(k) && !groupFields.includes(k),
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
const validSkus = computed(() => completeGroupCandidates(
  (validationCurrent.value ? eligible.value : [])
    .filter((r) => doc.value?.validation.rows?.[r.platform_sku]?.valid)
    .map((r) => r.platform_sku),
  eligible.value.map((r) => r.platform_sku),
  doc.value?.variant_groups || [],
));
function toggleSubmission(sku: string, event: Event) {
  selected.value = toggleVariantSelection(selected.value, sku, (event.target as HTMLInputElement).checked,
    validSkus.value, doc.value?.variant_groups || []);
}
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
  if (canEdit.value && aiReady.value && doc.value?.canada_specs_ready !== false && shouldPrepareOnEntry(doc.value, generation.value)) await generate();
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
  if (section === 'visible' && variantGroup.value && (reviewedVariantAttributes.includes(key) || variantGroup.value.axes.includes(key) || (current.value.visible.variantAttributeNames as string[] || []).includes(key))) {
    setVariantValue(current.value.platform_sku,key,value);
    return;
  }
  if (section === 'visible' && key === 'productSecondaryImageURL' && Array.isArray(value)) {
    value = [...new Set(value)].filter((u) => u !== current.value?.visible.mainImageUrl);
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
async function changeType(event: Event) {
  const type = (event.target as HTMLSelectElement).value;
  await run(async () => {
    if (!doc.value || !current.value) return;
    setFamilyProductType(doc.value, current.value.platform_sku, type);
    await save();
    await getSpec();
    if (!dirty.value) await load();
  });
}
watch(activeSku, async () => {
  imagePickerOpen.value = false;
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
    if (doc.value) {
      install(await publishingApi.refreshSource(props.bid, doc.value.revision));
      sourceCollection = new AbortController();
      const result = await collectMissingPrices(doc.value.basis, sourceCollection.signal,
        text => { message.value = text; });
      if (!live) return;
      if (result.attempted) install(await publishingApi.refreshSource(props.bid, doc.value.revision));
      if (result.errors.length) error.value = result.errors.join('；');
    }
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
  if (needsSourcePrice.value && !hasSellingPrice.value && (canVerifySourceIssue(issue) || issue.path === "orderable.price")) {
    await openPriceRecovery();
    return;
  }
  const segments = issue.path.split('.');
  if (
    segments.some((k) => optionalOrderableKeys.value.includes(k) || optionalKeys.value.includes(k))
  ) {
    tab.value = 'optional';
    optionalQuery.value = '';
    addOptionalGroup(optionalAttributes.value.filter((item) => segments.includes(item.key)));
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
    doc.value.canada_specs_ready !== false &&
    (awaitingSources.value || shouldPrepareOnEntry(doc.value, generation.value)) &&
    claimAutomaticCollection('listing', [props.bid, generation.value?.id || 'first'])
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
  sourceCollection?.abort();
  clearTimeout(saveTimer);
  clearTimeout(generationTimer);
  clearTimeout(resultTimer);
});
</script>

<template>
  <div ref="root" class="walmart-listing" @focusout="queueSave">
    <header class="listing-module-header">
      <div>
        <p class="eyebrow">WALMART {{ doc?.marketplace || 'US' }} · {{ doc?.currency || 'USD' }}</p>
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
    <div v-if="doc?.marketplace === 'CA'" class="notice notice-space">
      <strong>加拿大站刊登 · CAD</strong>
      <p>
        系统自动加载加拿大 MP_ITEM_INTL 官方规格，按加拿大类目自动填充并校验。目前支持英文资料与默认库存。
        原产国沿用默认规则；商品税码须按实际税务分类填写，系统不会猜测。
      </p>
      <p v-if="doc.canada_specification">
        {{ doc.canada_specification.source.kind === 'bundled_official' ? '已自动加载官方内置规格' : '使用店铺导入规格' }}：
        {{ doc.canada_specification.version }} · {{ doc.canada_specification.product_types }} 个商品类型
      </p>
      <p v-else-if="!doc.canada_specs_ready">加拿大官方规格读取失败，请重新部署或导入完整官方规格。</p>
      <template v-if="canSubmit">
        <input
          ref="canadaSpecFile"
          type="file"
          accept=".json,application/json"
          hidden
          @change="importCanadaSpecs"
        />
        <button class="btn" :disabled="busy || generating" @click="canadaSpecFile?.click()">
          替换加拿大官方规格
        </button>
      </template>
    </div>
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
              : !validationCurrent
                ? '需重新校验'
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
                ? '正在采集并生成刊登资料'
                : generation
                  ? '刊登资料生成结果'
                  : '自动采集并生成刊登资料'
            }}</strong>
            <p>
              {{
                collectionMessage || generation?.message ||
                (aiReady
                  ? '首次进入填写页面自动采集并生成；再次进入保留结果，需要更新时点击重新采集'
                  : 'AI 未配置，来源资料仍可正常查看和编辑')
              }}
            </p>
            <p class="muted">商品库同一会话采集 → 计算售价 → AI 填充 → 校验；邮编失效时打开网页设置 10001，完成后自动继续。</p>
            <small v-if="generation"
              >已处理 {{ generationProcessed }} /
              {{ generation.report.total ?? doc.rows.length }} 个 SKU ·
              {{ generation.report.model }}</small
            >
          </div>
          <button
            v-if="canEdit"
            class="btn"
            :disabled="busy || saving || (generating && !awaitingSources)"
            @click="generate()"
          >
            {{ awaitingSources ? '继续采集并生成' : generation ? '重新采集并生成' : '采集并生成' }}
          </button>
        </div>
        <div v-if="awaitingSources" class="notice">
          <p>正在沿用商品库的采集方式。若邮编失效，可打开同一浏览器设置；浏览器会保留 Cookie，后续子商品继续使用。</p>
          <button class="btn" :disabled="busy || collectingSources" @click="setSourceLocation">打开网页设置 10001 并继续</button>
          <button class="btn" @click="cancelSourceCollection">停止本轮采集</button>
          <a class="btn" href="/api/products/images/helper.zip" download>下载浏览器验证助手</a>
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
              sourceInfo(source?.source).detail +
              generationRow.collection.listing_id
            "
            >前往商品档案处理来源</RouterLink
          >
        </p>
        <p v-if="generationRow?.image_collection" class="notice" role="status">
          图片采集：{{ generationRow.image_collection.message }}
        </p>
        <details v-if="generationRow?.image_collection?.failures.length">
          <summary>查看图片采集失败原因</summary>
          <p
            v-for="failure in generationRow.image_collection.failures"
            :key="failure.index"
            class="muted"
          >
            第 {{ failure.index }} 张：{{ failure.reason }}
          </p>
        </details>
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
                            : field.kind === 'ai_composed'
                              ? 'AI 根据商品基础信息编写'
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
          <div
            v-for="row in rowsToShow"
            :key="row.platform_sku"
            class="listing-sku-card"
            :class="{ active: row.platform_sku === activeSku }"
            @click="!busy && (activeSku = row.platform_sku)"
          >
            <button
              type="button"
              class="listing-sku-select"
              :disabled="busy"
              :aria-label="'填写变体 ' + identityLabel(row)"
              :aria-current="row.platform_sku === activeSku ? 'true' : undefined"
              @click.stop="activeSku = row.platform_sku"
            >
              <ProductImage
                :src="String(row.visible.mainImageUrl || '')"
                image-class="listing-sku-image"
              />
              <span
                ><strong>{{ identityLabel(row) }}</strong
                ><small v-if="asins[row.platform_sku]">{{ row.platform_sku }}</small
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
            <button
              type="button"
              class="listing-sku-detail-link"
              :disabled="busy"
              aria-haspopup="dialog"
              :aria-label="'查看商品档案 ' + identityLabel(row)"
              @click.stop="detailRow = row"
            >
              查看商品档案
            </button>
          </div>
          <p v-if="!rowsToShow.length" class="muted">没有符合条件的商品</p>
        </aside>
        <main v-if="current" class="listing-form-main">
          <ProductStoreHistory :entries="source?.store_history" />
          <div class="listing-product-heading">
            <div>
              <span class="muted"
                >{{ sourceInfo(source?.source || 'Amazon').label }} ·
                {{ identityLabel(current) }}</span
              >
              <h3>{{ plain(String(current.visible.productName || '请填写商品标题')) }}</h3>
              <p>
                店铺 SKU <strong>{{ current.platform_sku }}</strong>
              </p>
            </div>
            <div class="listing-price-overview">
              <strong>{{ money(String(current.orderable.price ?? '')) }}</strong
              >
              <label class="listing-stock-input">目标库存
                <input type="number" min="0" max="999999999" step="1" aria-label="目标库存"
                  :value="current.target_stock ?? sourceVariant?.stock ?? ''"
                  :disabled="locked || busy" @input="setTargetStock" />
              </label>
            </div>
          </div>
          <div v-if="issues.length && !dirty" class="listing-issue-list">
            <strong>此 SKU 有 {{ issues.length }} 项需要处理</strong
            ><div v-for="(issue, index) in issues.slice(0, 3)" :key="index" class="listing-issue-row">
              <button @click="jump(issue)">{{ issue.message }} <span>定位 →</span></button>
              <button v-if="canVerifySourceIssue(issue) && !locked" class="source-price-verify" :disabled="busy || saving" @click="verifySourcePrice()">{{ hasSellingPrice ? '已验证' : '填写价格' }}</button>
            </div>
            <details v-if="issues.length > 3">
              <summary>展开其余 {{ issues.length - 3 }} 项</summary>
              <div v-for="(issue, index) in issues.slice(3)" :key="index" class="listing-issue-row">
                <button @click="jump(issue)">{{ issue.message }} <span>定位 →</span></button>
                <button v-if="canVerifySourceIssue(issue) && !locked" class="source-price-verify" :disabled="busy || saving" @click="verifySourcePrice()">{{ hasSellingPrice ? '已验证' : '填写价格' }}</button>
              </div>
            </details>
          </div>
          <ListingStatusNotices :review="priceReview" :result="locked ? result : undefined" />
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
                ['optional', '选择属性'],
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
                    :value="current.product_type"
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
                  >同一商品的所有子变体共用此类目，选择一次即可同步整组。</small
                >
              </div>
            </div>
            <ListingVariantGroup
              v-if="variantGroup"
              :group="variantGroup"
              :rows="doc.rows"
              :active-sku="activeSku"
              :specs="specs"
              :asins="asins"
              :labels="doc.labels"
              :disabled="busy || generating || allocatingCodes || !canEdit"
              :editable-skus="eligible.map((row) => row.platform_sku)"
              @refresh="refreshVariantFields"
              @select="activeSku = $event"
              @axes="setVariantAxes"
              @primary="setPrimaryVariant"
              @value="setVariantValue"
            />
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
              <p>原产国（实质性转变）固定 China（中国），按用户规则填写，非来源核验结论。</p>
              <template v-if="['GigaB2B', 'GigaB2B_CA'].includes(source?.source || '')">
                <p v-if="sourceVariant?.shipping_fee != null">
                  来源单价 {{ money(sourceVariant?.unit_price) }} ＋ 运费（含打包费）
                  {{ money(sourceVariant?.shipping_fee) }} ＝ 含运费基准
                  {{ money(sourceVariant?.base) }}
                </p>
                <p v-else>
                  来源单价 {{ money(sourceVariant?.unit_price) }} ·
                  运费未单列，请展开“来源价格及库存依据”核对。
                </p>
                <p>
                  含运费基准 {{ money(sourceVariant?.base) }} × 倍率 {{ doc.basis.multiplier }} ＝
                  <strong>计算售价 {{ money(sourceVariant?.price) }}</strong>
                </p>
              </template>
              <p v-else-if="priceReview?.original_price">
                人工核对原价 {{ money(priceReview.original_price) }} × 倍率 {{ priceReview.multiplier }} ＝
                <strong>确认售价 {{ money(priceReview.price) }}</strong>
              </p>
              <p v-else>
                倍率 ×{{ doc.basis.multiplier }} · 已核验计价基准 {{ money(sourceVariant?.base) }} ·
                计算售价 {{ money(sourceVariant?.price) }}
              </p>
              <p>
                <strong
                  >当前填写售价 {{ money(current.orderable.price as number | undefined) }}</strong
                >
                · 目标库存 {{ current.target_stock ?? sourceVariant?.stock ?? '未核验' }}
                <span
                  v-if="
                    sourceVariant?.price != null &&
                    current.orderable.price != null &&
                    Number(sourceVariant.price) !== Number(current.orderable.price)
                  "
                  class="seller-risk-label"
                >
                  · 当前填写售价与计算售价不同，可点击“刷新来源价格库存”重新计算
                </span>
              </p>
            </div>
            <label class="field" data-field="target_stock">
              <span>目标库存（可手动修改）</span>
              <input type="number" min="0" max="999999999" step="1"
                :value="current.target_stock ?? sourceVariant?.stock ?? ''"
                :disabled="locked || busy" @input="setTargetStock" />
              <small class="muted">填写非负整数，0 表示暂无库存。保存后按此数量提交；来源库存记录不变。</small>
            </label>
            <ListingPriceRecovery
              v-if="needsSourcePrice"
              :key="activeSku"
              ref="priceRecovery"
              :asin="sourceVariant?.asin"
              :multiplier="doc.basis.multiplier"
              :collection="generationRow?.source_collection || generationRow?.collection"
              :disabled="locked || busy || saving"
              @confirm="verifySourcePrice"
            />
            <template v-if="currentSpec"
              ><ListingSchemaField
                v-for="key in orderableKeys"
                :key="key"
                :name="key"
                :schema="currentSpec.sections.orderable.properties![key]"
                :labels="doc.labels"
                :model-value="current.orderable[key]"
                :disabled="
                  locked || fixedOrigin(key, currentSpec.sections.orderable.properties![key])
                "
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
                已选 {{ photos.length }} 张 · 默认优选 8 张，可自由增减。拖动图片可调整顺序，第一张为主图。
              </p>
              <div v-if="!locked" class="listing-image-actions">
                <label class="btn listing-image-action upload-action" :class="{ disabled: busy }">
                  <AppIcon name="Upload" />本地添加图片
                  <input
                    type="file"
                    accept="image/*"
                    multiple
                    hidden
                    :disabled="busy"
                    @change="upload"
                  />
                </label>
                <button
                  class="btn listing-image-action edit-action"
                  :disabled="busy"
                  @click="imagePickerOpen = true"
                >
                  <AppIcon name="LayoutGrid" />编辑图片
                </button>
              </div>
            </div>
            <div v-if="!locked" class="actions">
              <button
                class="btn"
                :disabled="busy || sourceImagesLoading || syncingImages"
                @click="syncCurrentImages"
              >
                {{ syncingImages ? '正在同步图片…' : '从商品库同步图片' }}
              </button>
              <small class="muted"
                >按当前 SKU／ASIN 读取最新图库，替换当前选择并默认保留前 8
                张；编辑图片可自由增减。</small
              >
            </div>
            <div class="listing-photo-gallery" data-listing-path="visible.mainImageUrl">
              <figure
                v-for="(url, i) in photos"
                :key="url + i"
                class="listing-photo-card"
                :class="{ 'is-dragging': draggingPhotoIndex === i, 'is-drop-target': draggingPhotoIndex !== null && draggingPhotoIndex !== i }"
                :draggable="!locked"
                @dragstart="startPhotoDrag($event, i)"
                @dragover.prevent
                @drop.prevent="draggingPhotoIndex !== null && reorderPhoto(draggingPhotoIndex, i); finishPhotoDrag()"
                @dragend="finishPhotoDrag"
              >
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
                      class="btn small listing-photo-make-main"
                      @click="setPhotos([url, ...photos.filter((_, j) => j !== i)])"
                    >
                      设主图
                    </button>
                    <button
                      class="icon-btn listing-photo-remove"
                      :aria-label="'删除图片 ' + (i + 1)"
                      @click="setPhotos(photos.filter((_, j) => j !== i))"
                    >
                      <AppIcon name="X" />
                    </button>
                  </template>
                </figcaption>
              </figure>
            </div>
            <div data-listing-path="visible.productSecondaryImageURL">
              <p
                v-for="issue in [
                  ...fieldIssues('mainImageUrl'),
                  ...fieldIssues('productSecondaryImageURL'),
                ]"
                :key="issue"
                class="seller-risk-label"
              >
                {{ issue }}
              </p>
            </div>
            <ListingSchemaField
              v-for="key in ['shortDescription', 'longDescription', 'keyFeatures'].filter(
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
                <h3>{{ tab === 'basic' ? '该类目必填属性' : '选择商品属性' }}</h3>
                <p>
                  单位、数值及选项均以沃尔玛当前规格为准。原产国（实质性转变）按用户规则固定为
                  China（中国），保存时自动应用，不由 AI 改写。
                </p>
              </div>
              <div v-if="tab === 'optional'" class="listing-inline-controls">
                <input
                  v-model="optionalQuery"
                  class="listing-optional-search"
                  placeholder="搜索属性名称 / 中文含义，选择要补充的商品信息"
                  aria-label="搜索选填属性"
                />
              </div>
              <template v-if="tab === 'optional'">
                <div class="optional-attribute-group">
                  <div class="optional-attribute-heading">
                    <div>
                      <h4>推荐属性</h4>
                      <p>沃尔玛建议补充的信息，有助于商品搜索和浏览。点击加号添加填写框。</p>
                    </div>
                    <button
                      v-if="recommendedAttributes.length"
                      class="optional-add-all"
                      type="button"
                      :disabled="locked || recommendedAttributes.every(optionalIsSelected)"
                      @click="addOptionalGroup(recommendedAttributes)"
                    >{{ recommendedAttributes.every(optionalIsSelected) ? '已全部添加' : '添加全部' }}</button>
                  </div>
                  <div v-if="recommendedAttributes.length" class="optional-attribute-chips">
                    <button
                      v-for="item in recommendedAttributes"
                      :key="optionalId(item)"
                      type="button"
                      class="optional-attribute-chip"
                      :class="{ selected: optionalIsSelected(item) }"
                      :disabled="locked || fixedOrigin(item.key, item.schema)"
                      :aria-pressed="optionalIsSelected(item)"
                      @click="toggleOptional(item)"
                    ><span class="optional-chip-icon">{{ optionalIsSelected(item) ? '✓' : '+' }}</span>
                      <span>{{ doc.labels[item.key] || item.schema.title || item.key }}</span>
                      <small v-if="item.schema.title && item.schema.title !== doc.labels[item.key]">{{ item.schema.title }}</small>
                    </button>
                  </div>
                  <p v-else class="muted">当前类目没有单独标记的推荐属性。</p>
                </div>
                <div class="optional-attribute-group">
                  <div class="optional-attribute-heading">
                    <div>
                      <h4>其他选填属性</h4>
                      <p>按商品实际情况补充；中文名称帮助理解字段，提交值仍遵循沃尔玛要求。</p>
                    </div>
                    <button
                      v-if="otherOptionalAttributes.length"
                      class="optional-add-all"
                      type="button"
                      :disabled="locked || otherOptionalAttributes.every(optionalIsSelected)"
                      @click="addOptionalGroup(otherOptionalAttributes)"
                    >{{ otherOptionalAttributes.every(optionalIsSelected) ? '已全部添加' : '添加全部' }}</button>
                  </div>
                  <div v-if="otherOptionalAttributes.length" class="optional-attribute-chips">
                    <button
                      v-for="item in otherOptionalAttributes"
                      :key="optionalId(item)"
                      type="button"
                      class="optional-attribute-chip"
                      :class="{ selected: optionalIsSelected(item) }"
                      :disabled="locked || fixedOrigin(item.key, item.schema)"
                      :aria-pressed="optionalIsSelected(item)"
                      @click="toggleOptional(item)"
                    ><span class="optional-chip-icon">{{ optionalIsSelected(item) ? '✓' : '+' }}</span>
                      <span>{{ doc.labels[item.key] || item.schema.title || item.key }}</span>
                      <small v-if="item.schema.title && item.schema.title !== doc.labels[item.key]">{{ item.schema.title }}</small>
                    </button>
                  </div>
                  <p v-else class="muted">没有匹配的其他选填属性。</p>
                </div>
                <div class="optional-selected-fields">
                  <h4>已选择的属性 <span>{{ optionalAttributes.filter(optionalIsSelected).length }}</span></h4>
                  <p class="muted">再次点击已选标签可移除属性及已填内容，修改会自动保存。未填写的空白框不会提交。</p>
                  <p v-if="!optionalAttributes.some(optionalIsSelected)" class="notice">从上方选择需要的属性，填写框会显示在这里。</p>
                </div>
              </template>
              <ListingSchemaField
                v-for="key in tab === 'basic'
                  ? mandatoryKeys
                  : optionalKeys.filter((k) => optionalAttributes.some((item) => item.section === 'visible' && item.key === k && optionalIsSelected(item)))"
                :key="key"
                :name="key"
                :schema="visibleProperties[key]"
                :labels="doc.labels"
                :model-value="current.visible[key]"
                :disabled="busy || locked || fixedOrigin(key, visibleProperties[key])"
                :required="required.includes(key)"
                :path="'visible.' + key"
                :issues="dirty ? [] : fieldIssues(key)"
                @update:model-value="update('visible', key, $event)" />
              <template v-if="tab === 'optional'"
                ><div class="listing-subheading"><h4>选填履约信息</h4></div>
                <ListingSchemaField
                  v-for="key in optionalOrderableKeys.filter((k) => optionalAttributes.some((item) => item.section === 'orderable' && item.key === k && optionalIsSelected(item)))"
                  :key="key"
                  :name="key"
                  :schema="currentSpec.sections.orderable.properties![key]"
                  :labels="doc.labels"
                  :model-value="current.orderable[key]"
                  :disabled="
                    locked || fixedOrigin(key, currentSpec.sections.orderable.properties![key])
                  "
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
                  <th>ASIN / 店铺 SKU</th>
                  <th>状态</th>
                  <th>中文原因</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in doc.rows" :key="row.platform_sku">
                  <td>
                    <template v-if="asins[row.platform_sku]">ASIN {{ asins[row.platform_sku] }}<br /></template><strong>{{ row.platform_sku }}</strong>
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
                    <WalmartResultReasons
                      v-if="doc.results[row.platform_sku]?.errors.length"
                      :errors="doc.results[row.platform_sku].errors"
                    />
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
                  · 店铺 {{ doc.store.name }} · {{ doc.marketplace === 'CA' ? '加拿大站 · OAuth 授权' : 'Partner ' + a.partner_id }}
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
              ><input type="checkbox" :checked="selected.includes(row.platform_sku)" @change="toggleSubmission(row.platform_sku, $event)" />{{
                identityLabel(row)
              }}
              <template v-if="asins[row.platform_sku]"> · {{ row.platform_sku }}</template></label
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
      <ListingImagePicker
        v-if="imagePickerOpen && current && !locked"
        :key="current.platform_sku"
        :listing-id="current.listing_id"
        :sku="current.source_sku"
        :label="identityLabel(current)"
        :asin="sourceVariant?.asin || ''"
        :existing="photos"
        :source-images="latestSourceImages"
        @close="imagePickerOpen = false"
        @apply="
          (urls) => {
            setPhotos(urls);
            imagePickerOpen = false;
          }
        "
      />
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
          >，<strong>{{ doc.marketplace === 'CA' ? '加拿大站 · CAD' : 'Partner ID ' + doc.validation.partner_id }}</strong>。
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
      :row="doc?.rows.find((row) => row.platform_sku === detailRow?.platform_sku) || detailRow"
      :asin="asins[detailRow.platform_sku] || ''"
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
