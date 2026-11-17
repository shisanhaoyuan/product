<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import {
  preparationApi,
  type PreparationSettings,
  type RealIdentifier,
  type IdentifierInput,
} from '../api/preparation';
import type { WalmartStore } from '../api/walmart';
import type { ProductType, ShipNode } from '../api/publishing';
import { errorMessage } from '../utils/display';
import AppIcon from './AppIcon.vue';
import FilterSelect from './FilterSelect.vue';
const props = defineProps<{ store?: WalmartStore | null }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLElement>();
const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
function keyboard(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !dialog.value) return;
  const controls = Array.from(
    dialog.value.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]',
    ),
  ).filter((el) => el.getClientRects().length > 0);
  const first = controls[0],
    last = controls.at(-1);
  if (!first || !last) {
    event.preventDefault();
    dialog.value.focus();
    return;
  }
  if (
    event.shiftKey &&
    (document.activeElement === first || document.activeElement === dialog.value)
  ) {
    event.preventDefault();
    last.focus();
  } else if (
    !event.shiftKey &&
    (document.activeElement === last || document.activeElement === dialog.value)
  ) {
    event.preventDefault();
    first.focus();
  }
}
onMounted(async () => {
  await nextTick();
  dialog.value?.focus();
});
onBeforeUnmount(() => previousFocus?.focus());
const settings = ref<PreparationSettings>();
const nodes = ref<ShipNode[]>([]),
  types = ref<ProductType[]>([]),
  codes = ref<RealIdentifier[]>([]);
const busy = ref(false),
  error = ref(''),
  message = ref(''),
  input = ref(''),
  query = ref('');
const filtered = computed(() =>
  codes.value.filter((c) =>
    [c.value, c.brand, c.source_sku, c.proof]
      .join(' ')
      .toLowerCase()
      .includes(query.value.toLowerCase()),
  ),
);
async function run(work: () => Promise<void>) {
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
onMounted(() =>
  run(async () => {
    if (props.store) {
      settings.value = await preparationApi.read(props.store.store_id);
      const actual = await preparationApi.options(props.store.store_id);
      nodes.value = actual.nodes;
      types.value = actual.types;
    } else codes.value = (await preparationApi.codes()).items;
  }),
);
async function save() {
  await run(async () => {
    if (!props.store || !settings.value) return;
    settings.value = await preparationApi.save(props.store.store_id, settings.value);
    message.value = '已在线核验并保存，现有批次需重新生成或校验。';
  });
}
function selectType(index: number, value: string) {
  const row = settings.value?.exemptions[index],
    actual = types.value.find((t) => t.name === value);
  if (row && actual) {
    row.product_type = actual.name;
    row.category = actual.category;
  }
}
async function importCodes() {
  await run(async () => {
    const text = input.value.trim();
    let items: IdentifierInput[];
    if (text.startsWith('[')) items = JSON.parse(text);
    else
      items = text
        .split(/\r?\n/)
        .filter(Boolean)
        .map((line) => {
          const [
            type = '',
            value = '',
            brand = '',
            proof = '',
            source_system = '',
            source_sku = '',
          ] = line.split('\t').map((s) => s.trim());
          if (!['EAN', 'UPC', 'GTIN'].includes(type))
            throw new Error('每行用制表符分隔，首列填写 EAN、UPC 或 GTIN；编码需保留前导零。');
          return {
            type: type as IdentifierInput['type'],
            value,
            brand,
            proof,
            source_system,
            source_sku,
          };
        });
    const result = await preparationApi.import(items);
    codes.value = (await preparationApi.codes()).items;
    input.value = '';
    message.value = `已处理 ${result.imported} 条真实编码；重复导入保留原归属。`;
  });
}
</script>
<template>
  <div
    class="prep-overlay"
    @click.self="!busy && emit('close')"
    @keydown.esc="!busy && emit('close')"
    @keydown="keyboard"
  >
    <section
      ref="dialog"
      tabindex="-1"
      class="prep-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="prep-title"
    >
      <header>
        <div>
          <p class="eyebrow">LISTING PREPARATION</p>
          <h2 id="prep-title">{{ store ? store.name + ' · 生成前提配置' : '真实商品编码池' }}</h2>
        </div>
        <button class="btn icon" aria-label="关闭配置" :disabled="busy" @click="emit('close')">
          <AppIcon name="X" />
        </button>
      </header>
      <p v-if="busy" role="status">正在读取或核验实际店铺资料…</p>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <p v-if="message" class="notice" role="status">{{ message }}</p>
      <template v-if="store && settings">
        <p class="muted">
          按实际 Partner 绑定。生成时自动使用默认仓库与准确匹配的已批准豁免；保存配置不会刊登。
        </p>
        <label
          >默认发货仓<FilterSelect
            label="默认发货仓"
            v-model="settings.fulfillment_center_id"
            :disabled="busy"
            :options="[
              { value: '', label: '未指定（多个仓库时不会任取第一项）' },
              ...nodes.map((n) => ({ value: n.id, label: n.name + ' · ' + n.id })),
            ]"
        /></label>
        <div class="prep-heading">
          <h3>已批准的 GTIN 豁免</h3>
          <button
            class="btn small"
            :disabled="busy"
            @click="
              settings.exemptions.push({
                brand: '',
                product_type: '',
                category: '',
                submission_id: '',
              })
            "
          >
            添加豁免
          </button>
        </div>
        <p class="muted">
          按品牌＋官方商品类型唯一匹配，保存和生成时均在线核验 APPROVED。未批准记录不能代替条码。
        </p>
        <article v-for="(ex, index) in settings.exemptions" :key="index" class="prep-exemption">
          <label
            >品牌<input
              v-model="ex.brand"
              :disabled="busy"
              maxlength="200"
              placeholder="与批次品牌完全一致"
          /></label>
          <label
            >官方商品类型<FilterSelect
              label="豁免商品类型"
              :model-value="ex.product_type"
              :disabled="busy"
              :options="[
                { value: '', label: '请选择实际类型' },
                ...types.map((t) => ({ value: t.name, label: t.name + ' · ' + t.category })),
              ]"
              @update:model-value="selectType(index, $event)"
          /></label>
          <label
            >批准申请编号<input
              v-model="ex.submission_id"
              :disabled="busy"
              maxlength="200"
              placeholder="真实 submissionId"
          /></label>
          <button class="btn small" :disabled="busy" @click="settings.exemptions.splice(index, 1)">
            移除
          </button>
        </article>
        <footer>
          <button class="btn primary" :disabled="busy" @click="save">在线核验并保存配置</button>
        </footer>
      </template>
      <template v-if="!store">
        <p class="notice">
          仅导入有真实归属依据的编码，不生成随机条码。相同商品、品牌跨店复用；不同变体不会共用。已分配的归属不可覆盖。
        </p>
        <label
          >批量导入（从表格复制，使用制表符分隔）<textarea
            v-model="input"
            rows="5"
            :disabled="busy"
            placeholder="类型[TAB]编码[TAB]品牌[TAB]归属证明[TAB]来源系统（可选）[TAB]来源SKU（可选）"
          />
        </label>
        <small
          >也可粘贴 JSON 数组：type、value、brand、proof、source_system、source_sku。未指定来源 SKU
          的编码会按品牌依次分配；请保证归属证明允许该用途。</small
        >
        <button class="btn primary" :disabled="busy || !input.trim()" @click="importCodes">
          校验并导入真实编码
        </button>
        <input
          v-model="query"
          type="search"
          aria-label="搜索编码池"
          placeholder="搜索编码、品牌、归属 SKU 或证明"
        />
        <div class="prep-table">
          <table>
            <thead>
              <tr>
                <th>编码</th>
                <th>品牌</th>
                <th>分配归属</th>
                <th>依据</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in filtered" :key="c.value">
                <td>{{ c.kind }} {{ c.value }}</td>
                <td>{{ c.brand }}</td>
                <td>{{ c.source_sku ? c.source_system + ' / ' + c.source_sku : '未分配' }}</td>
                <td>{{ c.proof }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <small>显示最近 1000 条记录，已分配编码重试时复用。</small>
      </template>
    </section>
  </div>
</template>
<style scoped>
.prep-overlay {
  position: fixed;
  inset: 0;
  z-index: 210;
  background: #20384c55;
  backdrop-filter: blur(4px);
  display: grid;
  place-items: center;
  padding: 24px;
}
.prep-dialog {
  width: min(960px, 100%);
  max-height: 90vh;
  overflow: auto;
  background: #fff;
  padding: 28px;
  border-radius: 18px;
  display: grid;
  gap: 18px;
  box-shadow: 0 24px 80px #172d4924;
}
header,
.prep-heading,
footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
h2,
h3,
p {
  margin: 0;
}
h2 {
  font-size: 22px;
}
h3 {
  font-size: 17px;
}
label {
  display: grid;
  gap: 8px;
  min-width: 0;
  font-size: 14px;
}
input,
textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d5e0e7;
  border-radius: 8px;
  background: #f8fafc;
  font: inherit;
  color: #263d4f;
}
.prep-exemption {
  display: grid;
  grid-template-columns: 1fr 1.5fr 1fr auto;
  align-items: end;
  gap: 12px;
  padding: 16px;
  background: #f4f7fa;
  border-radius: 12px;
}
.prep-dialog :deep(.filter-select) {
  width: 100%;
}
small,
.muted {
  color: #627888;
  line-height: 1.7;
}
.prep-table {
  overflow: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
td,
th {
  padding: 12px;
  border-bottom: 1px solid #e3eaf0;
  text-align: left;
  overflow-wrap: anywhere;
}
.icon svg {
  width: 20px;
  height: 20px;
}
@media (max-width: 760px) {
  .prep-overlay {
    padding: 12px;
  }
  .prep-dialog {
    padding: 18px;
  }
  .prep-exemption {
    grid-template-columns: 1fr;
  }
  header {
    align-items: flex-start;
  }
}
</style>
