# 大建云仓加拿大站

商品库分为「大建云仓商品库 US」和「大建云仓商品库 Canada」。加拿大使用独立 Buyer API 账号，与美国共用官方接口地址及签名规则。账号、同步锁、商品身份和巡查记录按地区隔离；相同 SKU 不会覆盖美国商品。

## 服务器配置

更新代码并构建前端后，在服务器终端编辑 `/etc/jiwu.env`，保留原有美国配置，添加以下变量（占位内容替换为加拿大账号资料）：

```dotenv
CATALOG_GIGA_CA_ENVIRONMENT=production
CATALOG_GIGA_CA_CLIENT_ID=加拿大账号的ClientID
CATALOG_GIGA_CA_CLIENT_SECRET=加拿大账号的ClientSecret
```

不要把密钥写入 Git 或发送到聊天；保持环境文件原有的受限权限，确保 `jiwu` 服务账号可以读取。保存后执行：

```bash
systemctl restart jiwu
systemctl is-active jiwu
```

进入「数据接入 → 大建云仓 Canada」，确认已配置正式环境后同步。接口范围与美国站相同：加拿大账号 Buyer 收藏夹或有囤货库存的商品。商品进入 Canada 商品库，可查看图片、详情、价格、分仓库存，也可筛选和导出。价格保留 API 返回的币种，不自动换算为美元；应核对加拿大账号返回的币种。

## 每周巡查

沿用 `jiwu-giga-patrol.timer`，每周一北京时间 09:00 执行，时间不变。已有定时器会在更新代码后自动巡查所有已配置来源（US、Canada、赛盈），无需另建定时器。未安装时运行：

```bash
cd /www/wwwroot/jiwu
bash deploy/install-giga-patrol.sh
```

只手动巡查加拿大已入库商品：

```bash
cd /www/wwwroot/jiwu
runuser -u jiwu -- .venv/bin/python -m catalog.giga_patrol --env-file /etc/jiwu.env --source giga-canada
```

在「大建云仓商品库 Canada → 价格与库存巡查」查看变更前后数值和失败原因。巡查只刷新该加拿大账号已入库的商品，接口失败保留原值。

## 加拿大上架

本次完成加拿大来源商品库，不包含 Walmart Canada 刊登接口。加拿大商品暂不允许创建美国站预上架批次或提交到美国店铺；加拿大巡查也不会向美国沃尔玛调整价格、库存。后续接入加拿大站店铺与刊登规则后再开放。
