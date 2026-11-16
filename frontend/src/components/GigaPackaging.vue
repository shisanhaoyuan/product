<script setup lang="ts">
import { computed } from 'vue';
import { packaging, packageDimensions, packageWeight } from '../utils/giga';
import GigaFields from './GigaFields.vue';

const props = defineProps<{ detail: Record<string, unknown> }>();
const packages = computed(() => packaging(props.detail));
</script>

<template>
  <section class="giga-panel giga-packaging">
    <h3>包装信息 <small v-if="packages.isCombo">按组合子产品展示</small></h3>
    <p v-if="packages.isCombo" class="giga-help">
      组合商品的包装数据分别属于各个子产品；数量为每组商品包含的子产品数量，不合并尺寸或重量。
    </p>
    <div v-if="packages.hasParent || !packages.isCombo" class="giga-package-parent">
      <h4>{{ packages.isCombo ? '父商品包装（接口单独返回）' : '商品包装' }}</h4>
      <div class="giga-package-summary">
        <div>
          <span>包装尺寸 · 长 × 宽 × 高</span
          ><strong>{{ packageDimensions(packages.parent) }}</strong>
        </div>
        <div>
          <span>包装重量</span><strong>{{ packageWeight(packages.parent) }}</strong>
        </div>
      </div>
      <details>
        <summary>查看完整包装字段</summary>
        <GigaFields :value="packages.parent" />
      </details>
    </div>
    <p v-else class="giga-help">父商品未单独提供包装尺寸，请查看下方各子产品包装。</p>
    <article v-for="(row, index) in packages.combo" :key="index" class="giga-package-card">
      <header>
        <h4>
          子产品 <span>{{ row.sku || 'SKU 未提供' }}</span>
        </h4>
        <span class="giga-chip">数量 {{ row.quantity ?? '未提供' }}</span>
      </header>
      <div class="giga-package-summary">
        <div>
          <span>包装尺寸 · 长 × 宽 × 高</span><strong>{{ packageDimensions(row.fields) }}</strong>
        </div>
        <div>
          <span>包装重量</span><strong>{{ packageWeight(row.fields) }}</strong>
        </div>
      </div>
      <details>
        <summary>查看完整包装字段</summary>
        <GigaFields :value="row.fields" /><GigaFields :value="row.extra" />
      </details>
    </article>
    <p v-if="packages.isCombo && !packages.combo.length" class="giga-muted">
      接口未返回组合子产品包装（comboInfo），请重新同步商品；若仍为空，需要向大建云仓核实接口数据。
    </p>
  </section>
</template>
