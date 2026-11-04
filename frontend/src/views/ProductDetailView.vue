<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { catalogApi } from '../api/client';
import type { ProductDetail } from '../api/types';
import { useCatalog } from '../composables/useCatalog';
import {
  errorMessage,
  evidenceLabels,
  money,
  priceRange,
  productStatus,
  safeUrl,
  text,
} from '../utils/display';
import AppIcon from '../components/AppIcon.vue';
import CopyContent from '../components/CopyContent.vue';
import ProductImage from '../components/ProductImage.vue';
import StatusBadge from '../components/StatusBadge.vue';
import VariantPanel from '../components/VariantPanel.vue';
const route = useRoute();
const { stats, refreshStats } = useCatalog();
const product = ref<ProductDetail>(),
  loading = ref(true),
  error = ref('');
const photo = ref(0),
  attributeSearch = ref(''),
  activeSection = ref('copy'),
  message = ref('');
let controller: AbortController | undefined;
const images = computed(() =>
  product.value?.images.length
    ? product.value.images
    : product.value?.variants.flatMap((variant) => variant.images).slice(0, 12) || [],
);
const attributes = computed(() =>
  Object.entries(product.value?.fields || {}).filter(([key, value]) =>
    (key + ' ' + text(value)).toLowerCase().includes(attributeSearch.value.toLowerCase()),
  ),
);
const sections = computed(() => [
  { id: 'copy', label: '主商品文案' },
  { id: 'variants', label: `子变体 ${product.value?.variants.length || 0}` },
  { id: 'attributes', label: '填写属性' },
  { id: 'checks', label: '核验与待复核' },
  { id: 'evidence', label: '来源证据' },
]);
const identity = computed(() => {
  const p = product.value;
  if (!p) return [];
  return [
    ['商品类目（已记录 Product Type）', p.category || '未记录类目'],
    ['商品 SKU', p.sku],
    ['平台 SKU', p.platform_sku || (p.variants.length ? '见子变体表' : '未记录')],
    ['商品卖家', p.seller || '未采集 · 不以品牌替代'],
    ['上传账号 / 创建人', p.creator],
    ['上传时间', p.uploaded_at],
    ['快照时间', new Date(p.observed_at).toLocaleString('zh-CN', { hour12: false })],
  ];
});
const provenance = computed(() => {
  const p = product.value;
  if (!p) return [];
  return [
    ['数据类型', evidenceLabels[p.evidence_kind]],
    ['来源文件', p.evidence.relative_file || p.evidence.file || 'API 导入'],
    ['文件 SHA-256', p.evidence.sha256 || '未提供'],
    ['准确模板', p.template],
    ['原商品记录 ID', p.record_id],
    ['时间依据', p.evidence.timestamp_basis || '上游 observed_at'],
    ['导入说明', p.evidence.note || ''],
  ];
});
async function load() {
  controller?.abort();
  const request = new AbortController();
  controller = request;
  loading.value = true;
  product.value = undefined;
  error.value = '';
  photo.value = 0;
  attributeSearch.value = '';
  activeSection.value = 'copy';
  message.value = '';
  try {
    const result = await catalogApi.product(String(route.params.id), request.signal);
    if (!request.signal.aborted) product.value = result;
    if (!stats.value) await refreshStats();
  } catch (reason) {
    if (!request.signal.aborted) error.value = errorMessage(reason);
  } finally {
    if (!request.signal.aborted) loading.value = false;
  }
}
function goSection(id: string) {
  activeSection.value = id;
  document.getElementById('section-' + id)?.scrollIntoView({ behavior: 'smooth' });
}
async function copyAsin() {
  try {
    await navigator.clipboard.writeText(product.value?.asin || '');
    message.value = '已复制 ASIN';
  } catch {
    message.value = '无法访问剪贴板，请手动复制';
  }
}
watch(() => route.params.id, load, { immediate: true });
onBeforeUnmount(() => controller?.abort());
</script>
<template>
  <RouterLink class="back-link" to="/products"><AppIcon name="ArrowLeft" />返回商品库</RouterLink>
  <div v-if="loading" class="loading">正在读取商品详情…</div>
  <div v-else-if="error" class="error-box">
    {{ error }} <button class="btn small" @click="load">重试</button>
  </div>
  <template v-else-if="product">
    <div class="page-heading detail-heading">
      <div>
        <p class="eyebrow">PRODUCT / {{ product.asin }}</p>
        <h1>{{ product.copy.title || product.sku }}</h1>
        <p class="subtitle">
          {{ product.store }} <span class="dot-separator">/</span> {{ product.brand }}
          <span class="dot-separator">/</span> {{ product.template }}
        </p>
        <div class="detail-status">
          <StatusBadge :type="productStatus(product).type">{{
            productStatus(product).label
          }}</StatusBadge
          ><StatusBadge :type="product.evidence_kind === 'persisted_readback' ? 'blue' : 'warn'">{{
            evidenceLabels[product.evidence_kind]
          }}</StatusBadge
          ><StatusBadge :type="product.save_succeeded ? 'good' : 'warn'">{{
            product.save_succeeded ? '双保存已确认' : '保存未确认'
          }}</StatusBadge>
        </div>
      </div>
      <a class="btn small" :href="'/api/products/' + product.id" target="_blank" rel="noopener"
        >查看 JSON ↗</a
      >
    </div>
    <div v-if="product.evidence_kind !== 'persisted_readback'" class="notice warning notice-space">
      本商品没有可用的独立产品库回读。下方为历史编辑快照，不能视为已保存的 ERP 内容。
    </div>
    <div class="detail-top">
      <section class="section">
        <div class="gallery-main" id="gallery-main">
          <ProductImage :src="images[photo]" alt="商品主图" :lazy="false" />
        </div>
        <div class="gallery-thumbs">
          <button
            v-for="(image, index) in images"
            :key="image + index"
            :class="{ selected: photo === index }"
            :data-photo="index"
            :aria-label="'查看第 ' + (index + 1) + ' 张图片'"
            @click="photo = index"
          >
            <ProductImage :src="image" />
          </button>
        </div>
      </section>
      <section class="section">
        <div class="section-head">
          <h2>商品档案</h2>
          <StatusBadge>{{
            product.variants.length ? product.variants.length + ' 个子变体' : '未记录子变体'
          }}</StatusBadge>
        </div>
        <div class="section-body">
          <div class="identity-grid">
            <div class="identity-item">
              <label>ASIN</label>
              <div class="value mono">
                {{ product.asin
                }}<button
                  class="copy-button"
                  title="复制 ASIN"
                  aria-label="复制 ASIN"
                  @click="copyAsin"
                >
                  <AppIcon name="Copy" /></button
                ><span role="status">{{ message }}</span>
              </div>
            </div>
            <div v-for="[label, value] in identity" :key="label" class="identity-item">
              <label>{{ label }}</label>
              <div class="value">{{ value || '未记录' }}</div>
            </div>
            <div class="identity-item">
              <label>商品来源</label>
              <div class="value">
                <a
                  v-if="safeUrl(product.source_url)"
                  :href="safeUrl(product.source_url)"
                  target="_blank"
                  rel="noopener noreferrer"
                  >{{ product.source }} · 打开来源页面 ↗</a
                ><template v-else>{{ product.source }}</template>
              </div>
            </div>
          </div>
          <div class="detail-facts">
            <div class="detail-fact">
              <label>{{ product.variants.length ? '子变体售价范围' : '售价' }} · USD</label
              ><strong>{{
                priceRange(
                  product.variants.length
                    ? product.variants.map((variant) => variant.price)
                    : [product.price],
                )
              }}</strong>
            </div>
            <div class="detail-fact">
              <label>MSRP · USD</label><strong>{{ money(product.msrp) }}</strong>
            </div>
            <div class="detail-fact">
              <label>本轮价格倍率</label
              ><strong class="text-value">× {{ product.multiplier || '—' }}</strong>
            </div>
            <div v-if="!product.variants.length" class="detail-fact">
              <label>在线库存</label><strong>{{ product.stock ?? '—' }}</strong>
            </div>
          </div>
        </div>
      </section>
    </div>
    <div class="detail-tabs">
      <a
        v-for="section in sections"
        :key="section.id"
        :href="'#section-' + section.id"
        :data-section="section.id"
        :class="{ active: activeSection === section.id }"
        @click.prevent="goSection(section.id)"
        >{{ section.label }}</a
      >
    </div>
    <div class="detail-content">
      <section class="section" id="section-copy">
        <div class="section-head">
          <div>
            <h2>主商品文案</h2>
            <p>
              {{
                product.evidence_kind === 'persisted_readback'
                  ? '产品库重新打开后读取的原始内容'
                  : '历史编辑框快照'
              }}
              · 仅以安全文本呈现 HTML
            </p>
          </div>
        </div>
        <div class="section-body"><CopyContent :copy="product.copy" /></div>
      </section>
      <section class="section" id="section-variants">
        <div class="section-head">
          <div>
            <h2>子变体</h2>
            <p>按平台 SKU 独立对应。点击一行查看该子 SKU 的文案及来源证据。</p>
          </div>
          <StatusBadge>{{ product.variants.length }} 个 SKU</StatusBadge>
        </div>
        <VariantPanel :key="product.id" :variants="product.variants" />
      </section>
      <section class="section" id="section-attributes">
        <div class="section-head">
          <div>
            <h2>填写属性</h2>
            <p>实际字段名称和值，包括空值。</p>
          </div>
          <input
            class="attributes-search"
            id="attribute-search"
            v-model="attributeSearch"
            placeholder="搜索字段名或值"
            aria-label="搜索属性"
          />
        </div>
        <div class="section-body">
          <div class="attribute-grid">
            <div
              v-for="[key, value] in attributes"
              :key="key"
              class="attribute"
              :data-attribute="key"
            >
              <label>{{ key }}</label>
              <div class="value">{{ text(value) || '空' }}</div>
            </div>
          </div>
          <details v-if="product.management.length" class="details">
            <summary>管理多属性组合</summary>
            <pre class="json-block">{{ text(product.management) }}</pre>
          </details>
        </div>
      </section>
      <section class="section" id="section-checks">
        <div class="section-head">
          <div>
            <h2>核验与待复核</h2>
            <p>保存状态、独立回读与填写完整性分别记录。</p>
          </div>
        </div>
        <div class="section-body">
          <div class="summary-box">
            <div class="check-box">
              <label>保存状态</label
              ><StatusBadge :type="product.save_succeeded ? 'good' : 'warn'">{{
                product.save_succeeded ? '双保存已确认' : '未确认'
              }}</StatusBadge>
            </div>
            <div class="check-box">
              <label>独立回读</label
              ><StatusBadge :type="product.readback_verified ? 'good' : 'warn'">{{
                product.readback_verified
                  ? '检查通过'
                  : product.evidence_kind === 'persisted_readback'
                    ? '已重开 · 仍有异常'
                    : '缺少回读'
              }}</StatusBadge>
            </div>
            <div class="check-box">
              <label>填写完整性</label
              ><StatusBadge :type="product.fill_complete ? 'good' : 'warn'">{{
                product.fill_complete ? '已核验完整' : '仍需复核'
              }}</StatusBadge>
            </div>
          </div>
          <div v-for="(issue, index) in product.issues" :key="index" class="issue">
            <span class="section-name">{{ issue.section || '检查项' }}</span
            >{{ issue.label || issue.field_id || issue.reason || '历史审计异常' }}
            {{ issue.reason && (issue.label || issue.field_id) ? '· ' + issue.reason : ''
            }}<template v-if="issue.current_value !== undefined">
              · 当前值：{{ text(issue.current_value) }}</template
            ><template v-if="issue.errors"> · {{ text(issue.errors) }}</template>
          </div>
          <p v-if="!product.issues.length" class="subtitle">
            {{
              product.fill_complete
                ? '历史核验未记录未完成项。'
                : '整体状态仍需复核，请展开原始检查结果。'
            }}
          </p>
          <details class="details">
            <summary>展开历史逐项检查</summary>
            <pre class="json-block">{{ text(product.checks) }}</pre>
          </details>
        </div>
      </section>
      <section class="section" id="section-evidence">
        <div class="section-head">
          <div>
            <h2>来源证据</h2>
            <p>本地历史记录</p>
          </div>
        </div>
        <div class="section-body">
          <div v-for="[label, value] in provenance" :key="text(label)" class="provenance-row">
            <span>{{ label }}</span
            ><span class="mono">{{ text(value) }}</span>
          </div>
          <details class="details">
            <summary>Amazon 来源资料</summary>
            <pre class="json-block">{{ text(product.source_facts) }}</pre>
          </details>
          <details class="details">
            <summary>本系统快照历史 · {{ product.history.length }} 条</summary>
            <div class="table-scroll">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>类型</th>
                    <th>快照</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="snapshot in product.history" :key="snapshot.id">
                    <td>{{ snapshot.observed_at }}</td>
                    <td>{{ evidenceLabels[snapshot.evidence_kind] }}</td>
                    <td>
                      <a
                        class="detail-link"
                        :href="'/api/products/' + product.id + '/snapshots/' + snapshot.id"
                        target="_blank"
                        rel="noopener"
                        >查看原始快照 ↗</a
                      >
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </details>
        </div>
      </section>
    </div>
  </template>
</template>
