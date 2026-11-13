<script setup lang="ts">
import { computed } from 'vue';
import { groupedAttributes } from '../utils/attributeLayout';
const props = defineProps<{ value: unknown; query?: string }>();
const groups = computed(() => groupedAttributes(props.value, props.query));
</script>
<template>
  <div class="organized-attributes">
    <template v-for="group in groups" :key="group.id">
      <component
        :is="group.id === 'internal' ? 'details' : 'section'"
        class="attribute-group"
        :open="group.id === 'internal' && !!query?.trim() ? true : undefined"
      >
        <component :is="group.id === 'internal' ? 'summary' : 'h4'" class="attribute-group-title">
          {{ group.title }} <span>{{ group.rows.length }} 项</span>
        </component>
        <dl class="organized-grid" :style="{ '--field-count': Math.min(group.rows.length, 3) }">
          <div
            v-for="(row, index) in group.rows"
            :key="index"
            class="organized-field"
            :data-field-key="row.label"
            :class="{ 'is-long': row.long, 'is-empty': row.empty }"
          >
            <dt>
              {{ row.title
              }}<small v-if="row.title !== row.label && row.label">{{ row.label }}</small>
            </dt>
            <dd>
              <slot name="value" :row="row">{{ row.value }}</slot>
            </dd>
          </div>
        </dl>
      </component>
    </template>
    <p v-if="!groups.length" class="attribute-no-match">没有匹配的字段</p>
  </div>
</template>
<style scoped>
.attribute-group + .attribute-group {
  margin-top: 24px;
}
.attribute-group-title {
  color: #405774;
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 12px;
}
summary.attribute-group-title {
  cursor: pointer;
}
.attribute-group-title span {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 400;
  color: var(--muted);
}
.organized-grid {
  display: grid;
  grid-template-columns: repeat(var(--field-count), minmax(0, 1fr));
  gap: 10px;
  margin: 0;
}
.organized-field {
  min-width: 0;
  padding: 14px 18px;
  background: #fff;
  border: 1px solid #e8edf3;
  border-radius: 10px;
}
.organized-field.is-long {
  grid-column: 1 / -1;
}
dt {
  font-size: 12px;
  font-weight: 500;
  color: #526984;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
dt small {
  display: block;
  font-size: 11px;
  font-weight: 400;
  color: #8491a1;
  margin-top: 2px;
}
dd {
  margin: 8px 0 0;
  color: #2d3b4e;
  font-size: 14px;
  line-height: 1.85;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.is-long dd {
  max-width: 100ch;
}
.is-empty dd,
.attribute-no-match {
  color: #929dac;
  font-size: 12px;
}
@media (max-width: 1000px) {
  .organized-grid {
    grid-template-columns: repeat(min(var(--field-count), 2), minmax(0, 1fr));
  }
}
@media (max-width: 600px) {
  .organized-grid {
    grid-template-columns: 1fr;
  }
  .organized-field {
    padding: 12px 14px;
  }
}
</style>
