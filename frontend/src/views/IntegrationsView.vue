<script setup lang="ts">
import AppIcon from '../components/AppIcon.vue';
import ImportUploader from '../components/ImportUploader.vue';
import StatusBadge from '../components/StatusBadge.vue';
import { useAuth } from '../composables/useAuth';
const { auth } = useAuth();
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">DATA CONNECTIONS</p>
      <h1>数据接入</h1>
    </div>
    <a class="btn" href="/docs" target="_blank" rel="noopener">API 文档 ↗</a>
  </div>
  <div class="integration-grid">
    <section class="section">
      <div class="section-head">
        <h2>商品 JSON 导入</h2>
        <StatusBadge>SCHEMA V1</StatusBadge>
      </div>
      <div class="section-body">
        <p v-if="auth.enabled && auth.user?.role === 'viewer'" class="notice">
          当前账号为只读成员，导入需由编辑员或管理员操作。
        </p>
        <ImportUploader v-else />
      </div>
    </section>
    <section class="section">
      <div class="section-head">
        <h2>数字酋长 · 本地历史资料</h2>
        <StatusBadge type="good">已支持</StatusBadge>
      </div>
      <div class="section-body">
        <ul class="steps">
          <li>
            <div><strong>本地历史导入</strong><small>模板清单、审计及最终回读</small></div>
          </li>
          <li>
            <div><strong>身份核对</strong><small>SKU、店铺、模板与来源哈希</small></div>
          </li>
          <li>
            <div><strong>证据记录</strong><small>独立回读、编辑快照与来源资料</small></div>
          </li>
        </ul>
        <details class="details">
          <summary>本机刷新导入命令</summary>
          <pre class="json-block">
python -m catalog import-digitemperor --logs /path/to/DigitEmperor/project/logs</pre>
        </details>
        <div class="notice">当前接入为手动导入历史记录；未开启 ERP 实时同步。</div>
      </div>
    </section>
    <section class="section">
      <div class="section-head"><h2>备份与迁移</h2></div>
      <div class="section-body">
        <p class="subtitle">
          JSON 备份包含当前模板及子变体。完整数据库备份额外保留历史快照与导入记录。
        </p>
        <div class="actions integration-actions">
          <a class="btn" href="/api/export.json"><AppIcon name="Download" />导出 JSON</a
          ><a class="btn" href="/api/export.csv"><AppIcon name="Download" />导出 CSV</a>
        </div>
        <details class="details">
          <summary>完整数据库备份命令</summary>
          <pre class="json-block">python -m catalog backup /path/to/catalog-backup.db</pre>
        </details>
      </div>
    </section>
    <section class="section">
      <div class="section-head"><h2>接口与格式</h2></div>
      <div class="section-body">
        <ul class="steps">
          <li>
            <div><strong>商品导入</strong><small>POST /api/imports</small></div>
          </li>
          <li>
            <div>
              <a class="detail-link" href="/api/import-schema" target="_blank" rel="noopener"
                >导入数据 Schema ↗</a
              >
            </div>
          </li>
          <li>
            <div>
              <a class="detail-link" href="/openapi.json" target="_blank" rel="noopener"
                >OpenAPI ↗</a
              >
            </div>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>
