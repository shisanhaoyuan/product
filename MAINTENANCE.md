# 商品库维护约定

## 系统边界

这是独立商品库。数字酋长上传项目只作为可选历史数据来源；服务无需导入或运行上传脚本。历史商品展示与编辑独立运行，不向 ERP 回写。预上架由后端按批次参数计算价格；绑定店铺的 Walmart 正式上架必须由用户明确确认。AI 生成及后台结果查询不刊登、不写库存；显式库存同步沿用 SKU、店铺及来源保护。

当前支持本机使用和服务器团队使用。Vue 3 + TypeScript 管理页面和交互；FastAPI 管理 API；生产 PostgreSQL 保存商品、模板、子变体、快照、账号及审计，服务器独立目录保存图片；SQLite 保留为本机和旧数据迁移模式。生产页面与 API 同源，开发时由 Vite 代理 API。无需同时引入 React 或另一套路由/状态框架。

## 代码位置

| 目录或文件 | 责任 |
| --- | --- |
| `frontend/src/views/` | 列表、详情、导入与接入页面 |
| `frontend/src/components/` | 商品卡片、图片、文案、子 SKU 面板、状态及图标 |
| `frontend/src/api/types.ts` | 与后端响应对应的 TypeScript 类型 |
| `frontend/src/api/client.ts` | 集中请求、HTTP 错误与取消信号 |
| `frontend/src/composables/useCatalog.ts` | 共享统计与请求去重 |
| `frontend/src/router.ts` | Hash 路由与懒加载 |
| `frontend/src/styles/theme.css` | 统一设计变量、组件样式及断点 |
| `frontend/src/utils/display.ts` | 金额展示、安全链接和文案转纯文本 |
| `catalog/app.py` | API 与静态资源入口 |
| `catalog/models.py` | 导入契约、验证与 Schema |
| `catalog/db.py` | 数据事务、检索、版本与快照 |
| `catalog/adapters/` | 各来源到统一导入契约的转换 |
| `web/dist/` | Vite 生成的可替换构建产物 |
| `web/api.*`、`web/docs.css` | 独立 API 文档页 |
| `data/catalog.db` | 业务数据，升级代码时不得覆盖 |

前端依赖由 `package-lock.json` 锁定，安装使用 `npm ci`。格式使用 Prettier；类型检查使用 vue-tsc；单元测试使用 Vitest。修改功能前先阅读对应页面与 API 类型，勿把抓取、数据转换、价格规则加入展示组件。

## 不变约束

1. 模板按 `namespace + external_id` 隔离；同一 ASIN 的不同店铺/模板不能混为一条记录。
2. 子变体使用模板内唯一 SKU 关联。组件选择值与列表 key 都使用 SKU，切换商品重置选择；不可按行号拼接价格、库存或文案。
3. 价格为来源字符串。缺失值显示为空缺标记，真实 `0` 保留，不在前端推导 MSRP、倍率或库存。
4. 保存成功、独立回读、填写完整分别展示。编辑快照不能升级成持久化证据。
5. HTML 文案只按安全纯文本展示。外部链接仅允许无内嵌凭据的 HTTP(S)，不使用来源 HTML 的 `v-html`。
6. 列表筛选通过路由保存；视图偏好在 localStorage。异步读取取消过期请求，旧结果不可覆盖当前商品。
7. 写请求不自动重试。导入只有服务端确认成功才报告成功；统计刷新失败不能误报导入失败。

## 修改与验收

小改动保持在所属层：样式改 CSS/组件，交互改页面，接口改 API 层和类型，来源改适配器。新增来源仍生成 `ImportBundle`，通过既有 API 或 CLI 导入。

每次发布至少运行：

```sh
python -m pytest -q
cd frontend
npm ci
npm test
npm run format:check
npm run build
npm audit
```

前端构建成功后启动后端，在项目根目录运行：

```sh
python validation/browser_check.py
python validation/frontend_state_check.py
```

浏览器验收需要额外安装 Playwright Python 包及本机 Chrome。脚本针对当前验收数据集，检查列表、筛选、分页、路由、详情与子 SKU、API 文档以及移动端布局；换数据集后需同步调整预期数量，不以硬编码数量作为业务规则。界面变化还需查看 `validation/` 截图。

接口类型当前为手动维护。修改响应字段必须同时更新 Pydantic/后端响应、TypeScript 类型及契约相关测试，不能仅用类型断言绕过检查。接口规模扩大时可从 OpenAPI 自动生成类型。

## 升级与恢复

1. 生产用 `bash deploy/backup.sh` 停服备份 PostgreSQL 和图片目录；本机 SQLite 用 `python -m catalog backup /path/to/backup.db`。保留当前代码与依赖锁。
2. 在测试数据库验证导入和新功能，不用生产库试写。升级依赖时提交锁文件并重新验收。
3. 构建前端并部署整套 `web/dist`。运行服务期间应原子替换完成的目录，避免提供构建中的半成品；本机升级可先停服务再构建。
4. 后端代码更新需要重启；仅静态前端更新通常刷新页面即可。验收品牌、SKU、价格、库存与文案等关键数据。
5. 若需回退，恢复兼容的代码和构建产物。数据库格式有变化时，用备份或显式反向迁移恢复，不覆盖线上新数据。

当前使用 migrations 版本表：版本 1 为商品表；团队模式的版本 2 幂等新增 users、sessions、auth_attempts、audit_events，不更改已有商品数据。本机模式不自动创建账号表。后续改变已有表结构仍需显式版本迁移和恢复演练，不能隐式重建或清空表。

## 后续部署顺序

已实现可选团队登录及全库级角色权限，实施见 `deploy/宝塔部署说明.md`。服务器必须显式启用登录，完成 HTTPS、定时备份和恢复演练后再开放团队使用。数据库不进入静态目录或版本库；服务默认只监听本机。账号密码恢复用服务器 CLI；后台不可直接读取密码或会话 token。

需要自动同步时增加独立适配器与任务记录，保留来源身份及幂等契约。生产团队使用 PostgreSQL。尚未需要的任务队列、微服务和大型状态库不提前加入。

## PostgreSQL 与图片存储维护

`catalog/database.py` 处理参数绑定、行格式和事务锁，`postgres.sql` 为原生 PostgreSQL 结构。原始 JSON 使用 TEXT 原样保留；`projection.py` 与 SQLite 共用编辑合并逻辑，在导入/编辑事务内更新查询表。不要直接改业务表绕过查询投影更新。商品写入共用事务 advisory lock，编辑版本和预上架摘要冲突仍返回 409。

`object_storage.py` 提供 put/get 接口，filesystem 为当前生产方案；以后可添加其他后端，已有图片需单独迁移，不能只改环境变量。OSS 是可选依赖且本次未做云端验证。外链图片仍为外链。

验证：`python -m pytest -q`；真实 PostgreSQL：`python -m pytest -q --postgres-dsn postgresql://127.0.0.1:55432/jiwu_test`。后者只能使用可销毁测试库，有建 schema、建数据库权限（恢复演练需要），pg_dump/pg_restore 在 PATH 中；测试创建临时 schema/数据库并清理。禁止用生产 DSN 跑测试。

## AI 刊登资料闭环维护

规则版本 v7，当前数据库迁移20。核心模块为 `listing_generation_jobs.py`（任务）、`listing_repair.py`（3轮受限修复）、`listing_prerequisites.py`（真实编码及店铺前提）、`listing_source_evidence.py`（同 SKU 补采）、`listing_vision.py`（受控图片与证据）和 `listing_result_worker.py`（只读轮询与修复草稿）。资料校验复用 `walmart_publishing.validate_document`，禁止另设“AI 完成就视为通过”的路径。

校验不设固定10分钟过期，来源/资料/店铺/Schema/前提/平台反馈改变后失效。保留人工改动及改后恢复的锁定记录；AI 不改价格、库存、SKU、ASIN、编码、品牌、图片及法律证明字段。真实编码归属与唯一分配必须在事务锁内操作。后台任务不得调用提交接口或库存 PUT；原管理员失权后暂停。后台修复只排草稿，仍需用户确认提交。

模型密钥只在私有配置保存。测试必须禁用真实 AI，并使用隔离数据库及模拟模型/来源/平台，不能使用生产凭证或 DSN。部署需备份数据库、图片和原加密主密钥，重启应用执行迁移，不覆盖生产数据。新开关与首版限制见 `docs/AI自动刊登闭环设计.md`、`docs/沃尔玛正式上架.md` 和 `deploy/jiwu.env.example`。

## 编码表格与分配维护

`identifier_spreadsheets.py` 只读取 XLSX/CSV/TSV，禁止执行公式。`listing_prerequisites.import_identifier_rows` 共用事务导入、等价编码去重与归属保护。`prelisting.create` 在原创建事务中调用随机分配，不依赖 AI 或店铺已绑定；`assigned_identifier` 与来源 content.ean 分开保存，预览摘要忽略服务器分配值，正式刊登默认值优先采用已分配编码。pool_assignment 必须在 catalog 写锁内调用；取消、重试不释放或重新随机编码。

迁移19包含导入记录和可用池索引。数据库备份包含编码池及分配审计，禁止只恢复商品表而丢失已用编码。新增导入不改历史商品来源；业务授权仅为用户提供编码，校验位通过不等于官方归属认证。

普通缺项采用用户授权的最高置信度候选策略：Python 对候选逐个校验后取置信度最大值，不设普通估算置信度门槛；来源事实仍执行证据门槛。bedSize 识图携带实际官方选项与同 SKU 文案，识图缓存包含任务版本、类型和选项；来源/默认优先，其次直接采用最高置信度有效床型观察，已有值保留。不得扩大到价格、库存、编码和合规证明等受保护字段。

编码补齐统一使用 `listing_prerequisites.allocate_document_identifiers`，调用方持写锁。准备资料、生成开始前及 `/api/prelisting/batches/{bid}/walmart/identifiers/allocate` 共用，不依赖模型和店铺在线接口；检查来源/品牌/版本，保护非空编码、豁免和非可编辑行。编辑器导入完成后触发补齐，返回实时编码池数量并失效旧校验。编码池入口同时位于店铺管理导航和编辑器编码字段下方。

## 批次回收站与默认品牌维护

迁移20新增 `prelisting_batch_trash`，采用独立表保存软删除时间与操作者，不改批次原状态；SQLite/PostgreSQL 迁移清单包含该表。批量操作持写锁，先验证整组批次均已取消、无未拒绝的平台提交、无排队或运行的生成任务，再统一写入；任一项不符合则整组不改。恢复仅恢复列表可见性，不重新生成、提交或释放 SKU/EAN。备份恢复需保留回收站和原审计表。

`listing_store_preparation.data_json.default_brand` 保存店铺默认品牌，按店铺和当前实际 Partner ID 匹配。旧客户端省略字段保留原值，明确空字符串清除默认值；修订号冲突拒绝保存。前端选店铺后带入默认品牌，临时覆盖仅影响新批次，已创建批次仍使用其快照品牌。

## 沃尔玛审核进度维护

`walmart_review.py` 按提交 rows_json 展开 SKU，窗口排序选每店铺／Partner／SKU 的最新结果，筛选和分页在数据库完成；SQLite 与 PostgreSQL 分别使用原生 JSON 函数。只投影公开结果，不暴露提交 payload 或凭证。预上架列表复用该投影：已提交隐藏、失败返回、部分提交保留未提交工作，不更改批次状态或 SKU/EAN。无新表，无迁移版本变化。

审核页 GET 只读本地库；管理员 POST 查询复用原 Feed/Partner/SKU 校验及轮询租约，强制 `sync_inventory=False`。批量查询同一 attempt 只执行一次，不调用自动修复、Feed POST 或库存 PUT。平台查询失败原结果保持，前端15秒只读取本地记录。测试在独立库模拟接口，涵盖失败返回／重试移出、部分提交、多店隔离、历史记录及只读查询。

## 标题品牌与校验限制调整（2026-11-17）

`listing_titles.py` 统一目标品牌前缀、已核验来源品牌移除及幂等处理。准备／生成／编辑保存／显式校验共用，原标题非品牌文字保留；旧草稿校验会先保存标题、增加修订号并记录 `title_brand_rule`，再按新修订号校验。已处理、结果未知及已发布行不改。`brand_prefix_v1` 标记使旧规则校验首次升级后需重新校验，后续不按10分钟失效。生成规则版本v7使历史失败缓存重算。

移除 MSRP 两倍强制校验和刷新来源时的 MSRP 覆盖；初始建议仍可取现有价格规则的两倍，人工可修改或按 Schema 省略。来源超过24小时不再阻止生成、校验或提交，但真实售价、库存可用、有效核验时间及来源摘要变化检查保留。实际库存 PUT 仍使用独立的24小时时效及SKU归属回读保护，不能与资料校验混为同一状态。无新数据库迁移，不改来源商品或已提交内容。
