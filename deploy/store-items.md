# 店铺商品管理

入口：顶部独立的“店铺商品管理”。选择已绑定的 Walmart US／CA 店铺，直接调用该店铺的商品 API，不依赖本系统的入库、预上架或提交记录。因此通过卖家中心或其他工具上架的商品也可以查询管理。

- 每页查询 50 个 SKU，使用官方 nextCursor 顺序翻页；游标过期请重新查询。可输入完整 SKU 精确搜索。平台返回的未发布、已下架商品仍按真实状态展示。
- 修改售价：美国 USD、加拿大 CAD 正数，最多两位小数；人工允许涨价或降价。
- 调整库存：美国先选择当前店铺有效发货仓并读取该仓库存；加拿大自动选择并读取默认库存，再填写非负整数。可清零或补库存。
- 永久下架：调用 Retire an item，输入完整 SKU 确认。官方说明最长可能需要 48 小时处理；回执不当作最终完成。临时停售可改库存为 0。
- 每次提交前重新验证 店铺身份（US partner ID／CA OAuth 指纹）、授权版本、SKU 和远程原值。记录操作人、目标、原值（请求记录）、仓库、时间、结果；只读成员可查看，管理员和编辑成员可操作。店铺凭证管理仍限管理员。
- 使用请求编号防重复。发送后超时、响应异常或回读不一致显示“结果待确认”，在操作记录点击“回读核对”确认后才能再次修改；不会盲目重发。
- 待确认的人工请求阻止同店铺 SKU 自动巡查写入；未确认的巡查请求也会阻止人工修改。回读与实际目标不一致应在卖家中心核对，不要通过删除审计记录绕过。
- 商品资料、来源库和上架模板不改动。已有巡查仍按原计划执行，已确认人工售价调整之后，下次来源涨价仍可能按巡查规则上调。

部署使用既有更新流程，启动自动创建 walmart_offer_changes 审计表；无须同步数据库。页面列出已验证有效绑定，未配置或权限不足时明确提示。测试使用模拟接口和独立数据库，未实际修改线上店铺。

接口依据（2026-11-20）：
- https://developer.walmart.com/us-marketplace/reference/getallitems
- https://developer.walmart.com/us-marketplace/reference/getanitem
- https://developer.walmart.com/us-marketplace/reference/updateprice
- https://developer.walmart.com/us-marketplace/reference/retireanitem
- 库存沿用现有 /v3/inventory GET/PUT，限定 SKU 与 shipNode。

商品列表增加 80×80 缩略图，可点击在新标签查看原图。优先使用 API 返回主图；普通商品 API 未返回图片时，仅从同店铺、同 partner、同平台 SKU 当前提交记录读取主图，并标记“上架提交图”。没有可靠映射或加载失败时展示占位，不用其他商品的图片。

## 加拿大商品管理（2026-11-28）

已验证的加拿大店铺与美国店铺一起显示，选项带站点和币种。不需要解绑或重新输入密钥；更新服务器前后端后刷新即可。所有调用使用绑定站点解密凭证并带 `WM_MARKET: ca`、`WM_GLOBAL_VERSION: 3.1`，不调用美国专用店铺资料或发货仓接口。

- 查询与下架使用全球商品接口；加拿大库存不传内部默认仓标识作为 shipNode。
- 加拿大改价使用 `POST /v3/price`，独立的 MPItemFeedHeader（价格版本 `5.0.20250801-18_47_55-api`）和 `MPItem[].Price`；美国继续使用原 PUT 格式。不把加拿大新建商品规格版本套到改价接口。
- 仍核对权限、币种、原值、店铺身份和绑定版本，提交后按实际 SKU 回读；请求不明或拒绝信息保留在操作记录，不自动重复写入。
- 加拿大图片只使用该店铺 API 图片或同 SKU 提交图片，不调用美国商品搜索补图。
- 验证使用隔离数据库和模拟 HTTP 接口，未查询或修改线上 12 号店商品。

官方契约核对：
- https://developer.walmart.com/global-marketplace/reference/getallitems
- https://developer.walmart.com/global-marketplace/reference/getanitem
- https://developer.walmart.com/global-marketplace/reference/updateprice
- https://developer.walmart.com/global-marketplace/reference/getinventory
- https://developer.walmart.com/global-marketplace/reference/updateinventoryforanitem
- https://developer.walmart.com/global-marketplace/reference/retireanitem
