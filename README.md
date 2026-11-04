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

前端使用 Vue 3、TypeScript、Vue Router 和 Vite；后端保留 Python FastAPI，服务器数据库为 PostgreSQL，本机兼容 SQLite。组件库未绑定大型 UI 框架，图标使用 Lucide Vue，颜色和尺寸集中在 CSS 变量中。页面按路由拆包。具体分层、修改规则和发布步骤见 [MAINTENANCE.md](MAINTENANCE.md)。

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

一次导入在同一数据库事务中完成；重复内容不新增快照；身份冲突或重复 SKU 整批拒绝。新版本保留旧快照。当前支持商品库内的本地编辑，不提供编辑 ERP 或发回 ERP 的功能。

## 备份与迁移

以下为本机 SQLite 备份方法。服务器 PostgreSQL＋图片目录必须使用 [备份与恢复说明](deploy/宝塔部署说明.md)，同时备份两者。

SQLite 完整备份（包含快照和导入记录，使用在线备份而非直接复制活跃数据库）：

```sh
python -m catalog backup /path/to/catalog-backup.db
```

另一台电脑安装依赖后，指定备份数据库启动：

```sh
python -m catalog --db /path/to/catalog-backup.db serve
```

网页可导出当前数据 JSON，用于迁移/再次导入；默认命名空间为 digitemperor，其他接入使用 `/api/export.json?namespace=your-namespace`。JSON 不包含历史快照；完整备份请用上面的数据库备份。CSV 导出遵循当前筛选，并防范表格公式注入。

## 后续部署

通过 Git 安装和后续更新见 [Git 部署](deploy/Git部署.md)。首次部署完成后，在服务器执行 `bash /www/wwwroot/jiwu/deploy/update-git.sh`，按备份、快进更新、依赖安装、前端构建和服务健康检查的顺序更新。数据库与环境配置独立于仓库，服务器有本地代码修改时停止更新。

团队服务器版已增加账号登录、管理员/编辑员/只读角色、首次改密、账号禁用、会话撤销及操作记录。通过 `CATALOG_AUTH_ENABLED=1` 显式启用；本机模式仍保持原来行为。阿里云宝塔的配置、完整迁移、systemd、Nginx 和备份步骤见 [宝塔部署说明](deploy/宝塔部署说明.md)。尚未执行实际服务器部署。

Vue 前端、FastAPI API、数据库层和来源适配器各自独立，可由后续适配器增量更新。生产前端需要 Node 构建，构建后由 Python 同源提供页面和 API；运行不调用 AI，不产生模型费用。服务器通过 `CATALOG_DATABASE_URL` 配置 PostgreSQL，`CATALOG_IMAGE_STORAGE=filesystem` 与 `CATALOG_IMAGE_ROOT` 配置独立图片目录；本机兼容 `CATALOG_DB` 指定 SQLite 文件；数据模型保留 Schema 版本、命名空间、稳定外部身份和来源证据，方便后续 ERP API、定时任务和多平台接入。

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


## 卖家黑名单

顶部「卖家黑名单」可添加来源平台、准确卖家名称及自定义原因（最多 2000 字），支持搜索、修改原因、解除和重新拉黑。商品详情的「卖家风险」列出主商品与子变体卖家，可直接进入管理。商品列表可筛选命中 / 未命中黑名单，CSV 导出跟随筛选并包含原因。

匹配键为「来源平台＋卖家名称」，统一 Unicode 全半角、大小写及连续空白，按完整名称匹配，不以品牌或子串代替。跨店铺生效；未记录卖家无法判断，同名卖家需要人工核实。目前不具备卖家 ID 数据，后续接入时可扩展匹配键。

黑名单是本商品库的人工判断，不修改 ERP、Amazon、原始导入数据或历史快照。重新导入商品不会覆盖黑名单。团队模式管理员和编辑可维护，只读成员可查看，写入需通过登录、来源和 CSRF 校验。解除保留原记录，操作前后状态通过 `seller_blacklist_events` 追溯，团队模式记录操作者 ID。

接口：`GET /api/seller-blacklist` 支持 `q`、`status=all|active|inactive`、`page`、`page_size`；`POST` 同路径接收 `source`、`seller`、`reason`，重复身份返回 409；`PATCH /api/seller-blacklist/{id}` 接收 `reason`、`active`。商品查询与 CSV 支持 `seller_status=blocked|unblocked`，查询响应中的 `seller_blacklist` 为当前命中项，不属于来源快照。

升级后启动服务会自动创建黑名单表（迁移 3）。完整 SQLite 备份包含黑名单和操作记录；现有商品 JSON 导出只备份商品来源数据，不包含黑名单。迁移服务器请使用 `python -m catalog backup <备份文件.db>` 的完整数据库备份。


## 批量预上架

商品库支持逐件勾选、选择本页及跨页选择当前筛选的全部结果。筛选条件改变会清空旧选择，翻页保留手动勾选；每批 1–500 件，超出时需缩小范围。点击「预上架」，选择目标店铺、输入本批品牌和价格倍率，生成预览后创建待确认批次。来源店铺筛选与目标店铺设置是两个独立概念。

预览逐 SKU 展示当前售价、历史来源含运费价格、首次 ERP 原价、取较高值后的基准、新售价、历史库存与 MSRP。只使用同 SKU 已核验的价格审计；缺少依据不会拿当前售价再次加倍率，也不会反除历史倍率推测原价。多变体沿用 floor(基准 × 倍率) + 0.99；单商品四舍五入保留两位小数；MSRP 为最高目标售价 × 2。这是历史资料预览，正式执行前仍需重新采集来源价格与库存。

预上架只设置目标品牌，不直接替换来源快照中的文案、SKU、ASIN、库存、图片。批次冻结来源内容、来源摘要、目标参数和价格计划，供后续执行适配器使用。品牌文案替换和店铺账号映射需由后续适配器处理并回读核验。

命中卖家黑名单、填写不完整、缺少价格依据、ASIN/SKU/审计不符或库存不符合 0/10 规则，会列入待复核。可以保存待复核批次，但不能确认。缺失卖家会提示无法完整检查，不视为已验证安全。

状态：待确认 → 已确认（待接入执行），任一未取消批次可取消。确认时重新检查冻结商品、店铺和黑名单；内容变化需取消后重新预览。当前没有平台刊登接口，确认操作不会向 ERP 或 Walmart 发送数据，也不会标记成已上架。同来源 ASIN 在同一目标店铺的未取消批次中只允许出现一次，取消后释放占用；跨店铺可独立建立批次。创建使用幂等请求键，超时重试不会重复建立批次。

「预上架 → 店铺管理」可补充或停用目标店铺。初始名单仅含用户截图可见店铺与迁移时库内已有店铺，保留 HomeYao / MY-L-16、YQ1 / MYZ-L-14 的名称对应；不是完整 30 店清单，也不表示账号已连接。没有复制截图中的任何凭据。

接口：`GET/POST /api/listing-stores`，`PATCH /api/listing-stores/{id}`；`POST /api/prelisting/preview` 接收 `listing_ids` 或 `filters`（二选一）、`store_id`、`brand`、`multiplier`；`POST /api/prelisting/batches` 额外接收预览的 `preview_hash` 与 UUID `request_key`；`GET /api/prelisting/batches` 分页列出批次，`GET /api/prelisting/batches/{id}` 查看详情，`POST /api/prelisting/batches/{id}/action` 接收 `action=confirm|cancel`。错误预览、越界参数及过时数据均由后端拒绝，所有写入复用团队角色、Origin、CSRF 校验。

迁移 4 自动创建店铺、批次、商品快照、占用及操作记录表。完整 SQLite 备份包含这些数据，商品 JSON 导出仍仅包含来源商品。

## 商品双击编辑

子商品图片支持点击缩略图放大、上一张/下一张、打开原图，以及「管理图片」中的单张删除和链接添加（每行一条，最多 100 张，自动去重）。图片增删即时保存到当前唯一 SKU，失败会保留原图片并显示错误。只读成员可以预览，不能增删。原始来源快照仍保留，修改未同步 ERP。

「本地导入图片」支持多选 JPG、PNG、WebP，单张最多 8 MB、2500 万像素。服务端解码校验、校正方向并清除元数据后保存，重复图片去重。旧版/本机默认 SQLite 模式将图片存入 `image_assets` 表（迁移 6）；服务器 filesystem 模式将图片存入独立目录，PostgreSQL 只保存图片元数据，完整备份必须同时包含数据库和图片目录；商品 JSON 导出不包含二进制，迁移应使用完整数据库备份。上传成功但尚未关联商品的文件可能暂留数据库。新增运行依赖为 Pillow 和 httpx，升级时重新安装项目依赖。

「按 ASIN 重新抓取」使用当前子 SKU 已记录的 Amazon ASIN，先核验网页当前 ASIN，再读取该商品图库；无法读取图库时尝试主图。抓取结果先预览勾选，点击「添加所选图片」才合并保存，不替换现有图片。暂仅支持 Amazon 美国站，遇到验证码、网络失败、缺少 ASIN 或页面身份不一致时显示原因并保留原图片；不保证每次能取得完整图库。

主商品顶部图片区也支持相同的放大预览、删除、链接添加、本地导入和 ASIN 抓图。主商品操作只修改主商品图片，抓图仅使用主商品 ASIN；子商品操作仍按唯一 SKU 对应。主商品图片为空时显示空状态，不混入子商品图片。API 省略 `variant_sku` 表示主商品，提供该字段时必须匹配存在的子 SKU。

本地图片使用受商品库访问权限保护的内部地址，可在本系统和待上架批次中查看。后续接入正式刊登时，适配器需将本地图片上传到平台或公开图片托管服务，不能直接把内部地址提交给外部平台。

子商品表格展示完整图片组、组合属性、售价、库存、商品编码类型与编码、Swatch Variant Attribute、Swatch Image URL、标题、描述字符数和 Key Features 数量。描述及卖点的「查看 / 编辑」进入对应 SKU 的详细内容。旧导入记录中的编码类型、色卡和运输重量从该行原始单元格读取，缺失时留空，原始证据不变。新增 API 导入支持 `product_id_type`、`swatch_attribute`、`swatch_image`、`shipping_weight`（lb）；这些字段可以独立编辑。

可以在子商品表中勾选一个或多个 SKU 预上架，或点击某行的「预上架」。预览和保存批次保留所选 SKU 的完整图片、属性、色卡、编码、标题、描述、卖点及运输重量，并可在批次详情展开核对。`variant_skus` 参数按商品 ID 映射 SKU 数组，必须与 `listing_ids` 对应；省略时仍包含全部子变体。确认时核对相同选择范围，后续修改不会覆盖旧批次内容。预上架价格继续使用已核验来源基准；手工改价不会被再次乘倍率，也不能绕过待复核。当前占用仍按父 ASIN＋目标店铺，同组其他子 SKU 若需加入，应取消旧批次后合并重建。仅创建待上架批次，尚无正式刊登接口。

标题、普通文字和描述采用原位文字编辑，保持原有字号、字重、宽度与自然换行，不切换为单行输入框。只接收纯文本粘贴；未输入任何修改时保留原始内容（包括来源 HTML）。描述实际修改后按显示的纯文本保存。金额、库存、图片链接及结构化属性保留对应校验控件。

商品详情中双击标题、品牌、卖家、价格、库存、EAN、卖点、描述、图片链接或可编辑属性，进入行内编辑。编辑时只显示输入控件，点击编辑框外空白处保存，Esc 取消；单行输入可按 Enter 保存，多行可按 Ctrl/Command+Enter 保存。保存失败会保留输入并显示错误。编辑框内的点击不会触发自动保存，导航和其他按钮保持原有行为。子变体的内容按唯一 SKU 保存。ASIN、SKU、店铺、模板和来源证据保持只读。库存保留 0/10 规则。管理员与编辑员可修改，只读成员仅可查看。

修改即时保存在本商品库，标记「手工修改 · 未同步 ERP」。列表、搜索、类目筛选及 CSV 使用修改后的内容；来源 JSON 导出和历史快照保持原始记录。完整 SQLite 备份包含本地修改及操作历史。「恢复来源记录」清除当前商品的本地覆盖，保留审计记录。预上架将本地修改列为待复核，防止把修改后的内容冒充 ERP 回读证据。

保存使用来源摘要及编辑版本检查并发冲突，过时页面不能覆盖新修改。重新导入更新未编辑字段，保留本地覆盖并提示核对；已不存在的子 SKU 修改不会应用到其他变体。接口：`PATCH /api/products/{id}/edits` 接收 `expected_revision`、`field`、`value`，可附 `variant_sku`、`key`；`POST /api/products/{id}/edits/reset` 接收 `expected_revision`。迁移 5 自动创建覆盖及审计表，写入沿用角色、Origin、CSRF 校验。
