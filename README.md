# 集物 · 独立商品库

本机使用的独立 Web 系统。列表展示 ASIN、商品图片、来源、卖家、SKU、店铺、品牌和核验状态；详情包含主商品与每个子 SKU 的价格、库存、EAN、图片、五点、描述、动态属性及来源证据。中文界面支持搜索、筛选、卡片/表格切换、CSV 导出和 JSON 导入。

## 启动

首次安装需要 Python 3.11 或更新版本、Node.js 22.12 或更新版本（推荐 Node 24 LTS）。在本目录执行：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
cd frontend
npm ci
npm run build
cd ..
python -m catalog serve
```

打开 http://127.0.0.1:8765 。Mac 也可双击 `启动商品库.command`。服务默认只绑定本机；停止服务按 Ctrl+C。端口占用时使用 `python -m catalog serve --port 8766`。

数据库在 `data/catalog.db`。运行和查看已经导入的数据不需要数字酋长项目、浏览器登录、DeepSeek 或上传程序。前端没有外部脚本/字体依赖；商品图片直接加载已记录的原图链接，离线或原图失效时显示占位。

前端已构建后，日常启动只需要 Python，无需启动 Node 服务。修改前端源码后须重新运行 `npm run build`，再刷新页面；不要直接编辑 `web/dist/` 构建产物。

## 技术结构与开发

前端使用 Vue 3、TypeScript、Vue Router 和 Vite；后端保留 Python FastAPI，数据库为 SQLite。组件库未绑定大型 UI 框架，图标使用 Lucide Vue，颜色和尺寸集中在 CSS 变量中。页面按路由拆包。具体分层、修改规则和发布步骤见 [MAINTENANCE.md](MAINTENANCE.md)。

开发时在一个终端运行 `python -m catalog serve`；另一终端执行：

```sh
cd frontend
npm ci
npm run dev
```

打开 Vite 输出地址下的 `/assets/dist/#/products`（默认 http://localhost:5173/assets/dist/#/products）。前端开发服务将 `/api` 代理到本机 8765。日常使用仍打开 8765 的生产构建。开发调试 JSON 导入也会写入后端数据库，测试写入应先用 `python -m catalog --db /path/to/test.db serve` 启动独立测试数据库。

## 导入现有记录

商品列表支持按已记录的 `fields.dc_productType`（ERP Product Type）筛选类目，类目选项显示全库模板数，并可与店铺、品牌、日期、核验状态和关键词组合。卡片、表格及详情展示该类目；CSV 导出沿用筛选并附带类目列。缺失、无效或“请选择”的记录归入“未记录类目”。不根据标题推测，也不改写历史资料。`/api/stats` 返回 `categories`，将其 `value` 原样作为 `/api/products` 或 `/api/export.csv` 的 `category` 参数；`type:` 前缀表示具体类目，`missing` 表示未记录。

```sh
python -m catalog import-digitemperor --logs /path/to/DigitEmperor/project/logs
```

适配器只读明确的批次清单、审核文件、修订与最终回读文件。不会读取浏览器配置、环境变量文件、Cookie，也不会运行上游代码、修改 ERP 或刊登。诊断结果写入 `data/import-diagnostics.json`。未实际执行填写、没有审计的待处理清单不会被当成已填写商品。

身份规则：以来源 ASIN 管理商品；以 `namespace + external_id` 管理店铺模板。数字酋长 external_id 由店铺、原记录 ID 和准确模板名组成。同一 ASIN 可以对应不同店铺或不同日期模板。子变体以模板内唯一 SKU 关联；不根据行序关联、不通过 SKU 后缀推测 ASIN。老记录的品牌 SKU 改名只使用审计中明确的已验证关联。

证据规则：优先展示准确模板独立重开后的实际值；修订需核对模板、店铺、参数和来源哈希。没有回读的记录显示“编辑快照”，不把填写计划作为已保存结果。两个未成功保存的 10 月 23 日商品保留原始回读/打开状态，不显示未提交的修订计划。只读检查但缺少最初填写审计的商品明确标记来源价格与保存证据不足。卖家字段缺失时显示“未采集”；不以品牌、制造商、店铺或创建人替代。品牌筛选按本轮指定品牌，实际填写的 `Brand Name` 在详情属性里完整保留。

导入时间依据历史文件的修改时间，不声称是实时同步时间。若把旧文件复制到新目录，可能改变修改时间；正式接入应传入真实的 `observed_at`。同一时间内容冲突拒绝导入，不任意覆盖。旧时间数据跳过，低等级编辑快照不会覆盖独立回读。修订不会重新计算或乘算价格。

## 扩展接入

接口文档：http://127.0.0.1:8765/docs ，机器可读 Schema：`/openapi.json` 和 `/api/import-schema`。

新系统只需生成统一 `ImportBundle`，通过 `POST /api/imports`、网页 JSON 导入或以下命令接入：

```sh
python -m catalog import-json /path/to/bundle.json
```

```json
{
  "schema_version": 1,
  "namespace": "another-erp",
  "label": "新系统商品导入",
  "listings": [{
    "external_id": "store-21|record-123|template-123",
    "asin": "B012345678",
    "sku": "B012345678-p",
    "source": "Amazon",
    "source_url": "https://www.amazon.com/dp/B012345678",
    "seller": "",
    "store": "MYZ-L-21",
    "brand": "Growlume",
    "template": "template-123",
    "record_id": "record-123",
    "observed_at": "2026-10-29T15:00:00+08:00",
    "evidence_kind": "source_only",
    "copy": {"title": "来源商品标题", "description": "", "features": []},
    "variants": []
  }]
}
```

金额保留原字符串与 USD 展示；空值不转换成零。`evidence_kind` 可选 `persisted_readback`、`editor_snapshot`、`source_only`。`save_succeeded`、`fill_complete`、`readback_verified` 独立保存。仅独立回读允许宣称完整；填写完整还需要保存确认。适配器扩展目录：`catalog/adapters/`，上游不必采用 Python，也不必依赖上传脚本。

一次导入在同一数据库事务中完成；重复内容不新增快照；身份冲突或重复 SKU 整批拒绝。新版本保留旧快照。当前为查看与数据接入系统，不提供编辑 ERP 或发回 ERP 的功能。

## 备份与迁移

完整备份（包含快照和导入记录，使用 SQLite 在线备份而非直接复制活跃数据库）：

```sh
python -m catalog backup /path/to/catalog-backup.db
```

另一台电脑安装依赖后，指定备份数据库启动：

```sh
python -m catalog --db /path/to/catalog-backup.db serve
```

网页可导出当前数据 JSON，用于迁移/再次导入；默认命名空间为 digitemperor，其他接入使用 `/api/export.json?namespace=your-namespace`。JSON 不包含历史快照；完整备份请用上面的数据库备份。CSV 导出遵循当前筛选，并防范表格公式注入。

## 后续部署

团队服务器版已增加账号登录、管理员/编辑员/只读角色、首次改密、账号禁用、会话撤销及操作记录。通过 `CATALOG_AUTH_ENABLED=1` 显式启用；本机模式仍保持原来行为。阿里云宝塔的配置、完整迁移、systemd、Nginx 和备份步骤见 [宝塔部署说明](deploy/宝塔部署说明.md)。尚未执行实际服务器部署。

Vue 前端、FastAPI API、SQLite 数据层和来源适配器各自独立，可由后续适配器增量更新。生产前端需要 Node 构建，构建后由 Python 同源提供页面和 API；运行不调用 AI，不产生模型费用。可通过 `CATALOG_DB` 更换数据库文件位置；数据模型保留 Schema 版本、命名空间、稳定外部身份和来源证据，方便后续 ERP API、定时任务和多平台接入。

本机模式不启用登录；服务器团队模式必须启用登录和 HTTPS 并配置定期备份。三种角色均可读取和导出全库，暂不按店铺隔离。`CATALOG_IMPORT_TOKEN` 可额外保护导入 API，但不绕过登录和角色验证。默认拒绝跨源浏览器写入，不代理远程图片或执行导入 HTML。多用户高并发写入时可替换 repository 层为 PostgreSQL。

## 验证

```sh
python -m pytest -q
cd frontend
npm test
npm run build
npm audit
```

覆盖幂等导入、版本顺序、SKU/ASIN身份保护、事务回滚、不同店铺隔离、价格库存及空值保留、安全 URL、跨站导入限制、API 搜索导出与历史快照。前端另外检查金额空值与零值、接口错误、取消请求和禁止自动重试写入。浏览器验收记录见 `VALIDATION.md`。
