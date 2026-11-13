<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { Variant } from '../api/types';
import { money, plain, safeUrl, text } from '../utils/display';
import CopyContent from './CopyContent.vue';
import ProductImage from './ProductImage.vue';
import InlineEdit from './InlineEdit.vue';
import AttributeValue from './AttributeValue.vue';
import VariantImages from './VariantImages.vue';
import SourceCapturePanel from './SourceCapturePanel.vue';
import { useProductEditing } from '../composables/useProductEditing';
const props = defineProps<{ variants: Variant[] }>();
const emit = defineEmits<{ prelist: [skus: string[]] }>();
const selectedSku = ref('');
const editing = useProductEditing();
const checkedSkus = ref<string[]>([]);
const locked = computed(() => !!editing?.active.value || !!editing?.busy.value);
const allChecked = computed(
  () => props.variants.length > 0 && checkedSkus.value.length === props.variants.length,
);
function toggleAll() {
  checkedSkus.value = allChecked.value ? [] : props.variants.map((v) => v.sku);
}
function showCopy(sku: string) {
  selectVariant(sku);
  if (selectedSku.value === sku)
    document
      .getElementById('variant-detail')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function selectVariant(sku: string) {
  if (sku === selectedSku.value || editing?.busy.value) return;
  if (editing?.active.value) {
    if (!window.confirm('当前内容尚未保存，切换变体将放弃这次输入。继续切换？')) return;
    editing.active.value = '';
  }
  selectedSku.value = sku;
}
watch(
  () => props.variants,
  (variants) => {
    checkedSkus.value = checkedSkus.value.filter((sku) => variants.some((v) => v.sku === sku));
    if (!variants.some((v) => v.sku === selectedSku.value))
      selectedSku.value = variants[0]?.sku || '';
  },
  { immediate: true },
);
const selected = computed(() =>
  props.variants.find((variant) => variant.sku === selectedSku.value),
);
defineExpose({ selectVariant });
</script>
<template>
  <div v-if="variants.length" class="variant-toolbar">
    <span>每行对应一个子 SKU · 双击内容编辑 · 点击空白保存</span>
    <div v-if="editing?.enabled.value" class="actions">
      <span>已选 {{ checkedSkus.length }} / {{ variants.length }}</span>
      <button
        class="btn small primary"
        :disabled="!checkedSkus.length || locked"
        @click="emit('prelist', [...checkedSkus])"
      >
        所选子商品预上架
      </button>
    </div>
  </div>
  <div v-if="variants.length" class="table-scroll">
    <table class="data-table variant-catalog-table">
      <thead>
        <tr>
          <th v-if="editing?.enabled.value">
            <input
              type="checkbox"
              aria-label="选择全部子商品"
              :checked="allChecked"
              :disabled="locked"
              @change="toggleAll"
            />
          </th>
          <th>图片</th>
          <th>平台 SKU / ASIN</th>
          <th>属性</th>
          <th>售价 USD</th>
          <th>库存</th>
          <th>商品编码类型 / 编码</th>
          <th>Swatch Variant Attribute</th>
          <th>Swatch Image URL</th>
          <th>标题</th>
          <th>详细描述</th>
          <th>Key Features</th>
          <th v-if="editing?.enabled.value">预上架</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="variant in variants"
          :key="variant.sku"
          class="variant-row"
          :class="{ selected: selectedSku === variant.sku }"
          :data-variant="variant.sku"
          tabindex="0"
          :aria-selected="selectedSku === variant.sku"
          @click="selectVariant(variant.sku)"
          @keydown.enter="selectVariant(variant.sku)"
          @keydown.space.prevent="selectVariant(variant.sku)"
        >
          <td v-if="editing?.enabled.value" @click.stop>
            <input
              v-model="checkedSkus"
              type="checkbox"
              :value="variant.sku"
              :aria-label="'选择子商品 ' + variant.sku"
              :disabled="locked"
            />
          </td>
          <td>
            <VariantImages :images="variant.images" :sku="variant.sku" :asin="variant.asin" />
          </td>
          <td class="mono">
            {{ variant.sku }}<br /><span class="muted">{{
              variant.asin || 'ASIN 关联未记录'
            }}</span>
          </td>
          <td>
            <div v-for="(value, key) in variant.attributes" :key="key">
              <span class="muted">{{ key }}:</span
              ><AttributeValue
                :value="value"
                :target="{ field: 'attributes', key: String(key), variant_sku: variant.sku }"
                :label="String(key)"
              />
            </div>
          </td>
          <td>
            <InlineEdit
              :value="variant.price"
              :target="{ field: 'price', variant_sku: variant.sku }"
              :label="variant.sku + '售价'"
              kind="money"
              ><span class="price">{{ money(variant.price) }}</span></InlineEdit
            ><span
              v-if="variant.price_check.status"
              class="check-state"
              :class="{ bad: variant.price_check.status !== 'verified' }"
              >{{
                variant.price_check.status === 'verified' ? '✓ 历史核验通过' : '△ 核验异常'
              }}</span
            >
          </td>
          <td>
            <InlineEdit
              :value="variant.stock"
              :target="{ field: 'stock', variant_sku: variant.sku }"
              :label="variant.sku + '库存'"
              kind="stock"
              >{{ variant.stock ?? '—' }}</InlineEdit
            ><span
              v-if="variant.stock_check.status"
              class="check-state"
              :class="{ bad: variant.stock_check.status !== 'verified' }"
              >{{
                variant.stock_check.status === 'verified' ? '✓ 历史核验通过' : '△ 核验异常'
              }}</span
            >
          </td>
          <td class="mono">
            <InlineEdit
              :value="variant.product_id_type"
              :target="{ field: 'product_id_type', variant_sku: variant.sku }"
              :label="variant.sku + '商品编码类型'"
            />
            <InlineEdit
              :value="variant.ean"
              :target="{ field: 'ean', variant_sku: variant.sku }"
              :label="variant.sku + '商品编码'"
              >{{ variant.ean || '空' }}</InlineEdit
            >
          </td>
          <td>
            <InlineEdit
              :value="variant.swatch_attribute"
              :target="{ field: 'swatch_attribute', variant_sku: variant.sku }"
              :label="variant.sku + '色卡属性'"
            />
          </td>
          <td>
            <InlineEdit
              :value="variant.swatch_image"
              :target="{ field: 'swatch_image', variant_sku: variant.sku }"
              :label="variant.sku + '色卡图片链接'"
              kind="images-url"
              ><ProductImage
                v-if="variant.swatch_image"
                :src="variant.swatch_image"
                image-class="table-thumb"
              /><span v-else class="muted">未记录色卡图片</span></InlineEdit
            >
          </td>
          <td class="variant-title-cell">
            <InlineEdit
              :value="variant.copy.title"
              :target="{ field: 'copy.title', variant_sku: variant.sku }"
              :label="variant.sku + '标题'"
              ><div class="variant-title-text">
                {{ plain(variant.copy.title) || '未记录标题' }}
              </div></InlineEdit
            ><span class="muted">{{ plain(variant.copy.title).length }} 个字符</span>
          </td>
          <td>
            <button
              class="detail-link variant-copy-link"
              :disabled="locked"
              @click.stop="showCopy(variant.sku)"
            >
              {{ plain(variant.copy.description).length }} 个字符 · 查看 / 编辑
            </button>
          </td>
          <td>
            <button
              class="detail-link variant-copy-link"
              :disabled="locked"
              @click.stop="showCopy(variant.sku)"
            >
              {{ variant.copy.features.filter((value) => plain(value)).length }} 条卖点 · 查看 /
              编辑
            </button>
          </td>
          <td v-if="editing?.enabled.value">
            <button
              class="btn small"
              :disabled="locked"
              @click.stop="emit('prelist', [variant.sku])"
            >
              预上架
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div v-else class="empty">当前快照未记录子变体行；可用价格与库存在商品档案及填写属性中查看。</div>
  <div id="variant-detail">
    <div v-if="selected" :key="selected.sku" class="variant-detail">
      <div class="variant-detail-head">
        <ProductImage :src="selected.images[0]" image-class="table-thumb" />
        <div>
          <p class="eyebrow">SELECTED VARIANT / {{ selected.sku }}</p>
          <h3>{{ plain(selected.copy.title) || '本行未记录标题' }}</h3>
          <span class="subtitle"
            >ASIN：{{ selected.asin || '未记录' }}　卖家：{{ selected.seller || '未采集' }}　<a
              v-if="safeUrl(selected.source_url)"
              class="detail-link"
              :href="safeUrl(selected.source_url)"
              target="_blank"
              rel="noopener noreferrer"
              >查看本变体来源 ↗</a
            ></span
          >
        </div>
      </div>
      <div v-if="selected.identity_note" class="notice warning notice-space">
        {{ selected.identity_note }}
      </div>
      <div class="variant-edit-meta">
        <label>卖家</label
        ><InlineEdit
          :value="selected.seller"
          :target="{ field: 'seller', variant_sku: selected.sku }"
          label="子变体卖家"
        />
        <label>图片</label
        ><VariantImages :images="selected.images" :sku="selected.sku" :asin="selected.asin" />
        <label>商品编码类型</label
        ><InlineEdit
          :value="selected.product_id_type"
          :target="{ field: 'product_id_type', variant_sku: selected.sku }"
          label="子变体商品编码类型"
        />
        <label>色卡属性</label
        ><InlineEdit
          :value="selected.swatch_attribute"
          :target="{ field: 'swatch_attribute', variant_sku: selected.sku }"
          label="子变体色卡属性"
        />
        <label>色卡图片</label
        ><InlineEdit
          :value="selected.swatch_image"
          :target="{ field: 'swatch_image', variant_sku: selected.sku }"
          label="子变体色卡图片链接"
          kind="images-url"
          ><ProductImage
            v-if="selected.swatch_image"
            :src="selected.swatch_image"
            image-class="table-thumb"
          /><span v-else>未记录</span></InlineEdit
        >
        <label>运输重量 lb</label
        ><InlineEdit
          :value="selected.shipping_weight"
          :target="{ field: 'shipping_weight', variant_sku: selected.sku }"
          label="子变体运输重量"
        />
      </div>
      <CopyContent :copy="selected.copy" :variant-sku="selected.sku" />
      <SourceCapturePanel
        v-if="editing?.listingId.value"
        :listing-id="editing.listingId.value"
        :asin="selected.asin"
        :sku="selected.sku"
      />
      <details class="details">
        <summary>本子 SKU 来源、价格与库存核验</summary>
        <pre class="json-block">{{
          text({
            source_facts: selected.source_facts,
            price_check: selected.price_check,
            stock_check: selected.stock_check,
          })
        }}</pre>
      </details>
      <details class="details">
        <summary>原始表格单元格（按 SKU 保存）</summary>
        <pre class="json-block">{{ text(selected.raw_cells) }}</pre>
      </details>
    </div>
  </div>
</template>
