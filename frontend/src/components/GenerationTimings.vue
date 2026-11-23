<script setup lang="ts">
import type { GenerationJob } from '../api/publishing';
defineProps<{ report: GenerationJob['report'] }>();
const labels: Record<string, string> = {
  queue: '批次排队',
  collection: '来源采集',
  image_processing: '图片处理',
  ai_wait: 'AI 通道等待',
  ai_response: 'AI 响应',
  repair: '修复处理',
  validation: '逐项校验',
  final_validation: '最终校验',
};
function duration(ms = 0) {
  return ms < 1000
    ? `${Math.round(ms)} 毫秒`
    : ms < 60000
      ? `${(ms / 1000).toFixed(1)} 秒`
      : `${Math.floor(Math.round(ms / 1000) / 60)} 分 ${Math.round(ms / 1000) % 60} 秒`;
}
</script>

<template>
  <details v-if="report.timings" class="generation-timings">
    <summary>
      耗时明细 · 总用时 {{ duration(report.timings.elapsed_ms) }}
      <span v-if="report.sku_parallel"> · 同批次 {{ report.sku_parallel }} 个 SKU 并行</span>
      <strong v-if="report.phase === 'final_validation'"> · 正在最终校验</strong>
    </summary>
    <dl>
      <div v-for="(label, key) in labels" :key="key">
        <dt>{{ label }}</dt>
        <dd>{{ duration(report.timings.stages[key]?.ms) }}</dd>
      </div>
    </dl>
    <p class="muted">
      阶段耗时按并行任务累计，AI 等待与响应单独计时，因此阶段相加不等于总用时。修复时的 AI
      请求计入“AI 响应”。
    </p>
    <p v-if="report.timings.source_wait_includes_human" class="muted">
      来源采集包含本地浏览器处理和人工验证等待。
    </p>
    <p v-if="report.timings.image_cache" class="muted">
      图片处理复用 {{ report.timings.image_cache.hits }} 次，新处理
      {{ report.timings.image_cache.prepared }} 张。
    </p>
  </details>
</template>

<style scoped>
.generation-timings {
  margin-top: 12px;
  padding: 10px 14px;
  border: 1px solid #cbdbe9;
  border-radius: 10px;
  background: #edf4fb80;
}
summary {
  cursor: pointer;
  color: #365675;
  font-size: 14px;
}
dl {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 12px;
  margin: 14px 0;
}
dt {
  color: #647b91;
  font-size: 12px;
}
dd {
  margin: 4px 0 0;
  font-weight: 600;
}
p {
  font-size: 12px;
  margin: 6px 0;
}
</style>
