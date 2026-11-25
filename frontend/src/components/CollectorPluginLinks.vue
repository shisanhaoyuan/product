<script setup lang="ts">
import {
  collectorPluginVersion,
  collectorPluginDownload,
  collectorPluginGuide,
} from '../utils/collectorPlugin';
</script>

<template>
  <span class="collector-plugin-links">
    <a class="btn small" :href="collectorPluginDownload" download>下载集物采集插件</a>
    <a class="btn small" :href="collectorPluginGuide" target="_blank" rel="noopener"
      >安装／更新说明</a
    >
    <small class="muted">当前版本 {{ collectorPluginVersion }}</small>
  </span>
</template>

<style scoped>
.collector-plugin-links {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
