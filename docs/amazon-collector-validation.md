# Amazon 采集模块验收记录（2026-11-25）

## 已通过

- 后端全量测试：994 passed，8 skipped（增加最后一个图片保留用例前）。最后的同 ASIN 部分图片缓存修正后，采集专项 14 passed。
- 前端：163 passed；vue-tsc 与生产构建通过；npm audit 0 vulnerabilities。
- 插件 worker/location 单元测试：10 passed。
- 临时 PostgreSQL 实库：采集任务、权限、原子入库、回滚、去重、旧数据保护和表格解析相关测试 27 passed；测试后关闭并删除临时数据库。
- `validation/amazon_collector_check.py`：加载真实扩展，在隔离数据库、模拟 Amazon 页面下通过 Excel 拖入 → 后台采集 → 子 ASIN 发现 → 独立价格/属性 → 入库；检查移动布局、下拉选项、懒加载多报价及无关商品映射排除。
- `validation/amazon_regular_price_check.py`：插件和 Wraith 共用提取规则均保留当前价85.49／原价89.99，排除会员价和隐藏0价。
- 大表格：21MB有效XLSX实际通过HTTP上传并解析；超过150MB拒绝，其他接口20MB限制仍生效。上传临时文件与CSV流式处理避免整体多次复制。
- `git diff --check` 无错误。

## 尚未通过或未验证

- 全项目 Prettier 检查仍报告四个本次未编辑文件：`src/api/publishing.ts`、`src/components/WalmartListingEditor.vue`、`src/styles/publishing.css`、`src/views/ProductsView.vue`。本次新增/修改前端文件均已格式化。
- 旧综合脚本 `browser_check.py` 和 `frontend_state_check.py` 使用临时商品数据运行，已将24条/页的旧期望在临时运行副本中调整到当前50条/页，仍分别遇到子SKU选择断言和变体字段断言失败；没有将这两次运行算作通过，也没有借此修改未涉及的商品详情页。
- 未连接生产数据库，未调用真实Walmart上架，未使用真实Amazon商品验证此次完整采集；真实验证码能否由Wraith处理仍需现场联调。
- 本文记录推送前的本地验收，未部署服务器。生产Nginx原21m限制需要改为150m并检查重载；更新Git不会改动已经启用的宝塔站点配置。
