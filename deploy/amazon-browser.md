# Amazon 后台浏览器采集

普通 HTTP 请求被拦截、缺少原价或需要展开报价时，可启用独立 Wraith 后台浏览器。使用固定版本，不安装同名但不同项目的 PyPI `wraith`。

执行顺序：普通请求 → 一次 Wraith headless 尝试 → 核对 ASIN、配送地区 10001 和同报价原价 → 原价乘倍率 → 当前 SKU 图片下载与保存 → AI 填充。Wraith 可处理部分 JavaScript／HUMAN 长按验证，但没有保证 Amazon 图形验证码能通过；失败会在当前 SKU 显示原因，其他商品继续处理。用户可在页面核对后填写来源原价并确认售价。不会自动提交商品。

## 阿里云 Linux / 宝塔服务器安装

以下命令在服务器 root 终端执行。浏览器使用独立 Python 环境和缓存，代码更新不会删除运行环境。先检查 `/www/server/jiwu-data/amazon-browser/venv/bin/python` 是否存在；已有环境时跳过创建，直接安装固定版本依赖。

```bash
install -d -m 700 -o jiwu -g jiwu /www/server/jiwu-data/amazon-browser
runuser -u jiwu -- /www/wwwroot/jiwu/.venv/bin/python -m venv /www/server/jiwu-data/amazon-browser/venv
runuser -u jiwu -- /www/server/jiwu-data/amazon-browser/venv/bin/python -m pip install --no-cache-dir -r /www/wwwroot/jiwu/deploy/amazon-browser-requirements.txt
runuser -u jiwu -- env XDG_CACHE_HOME=/www/server/jiwu-data/amazon-browser/cache /www/server/jiwu-data/amazon-browser/venv/bin/python -m camoufox fetch official/158.0-beta.40
```

若启动报告缺少 GTK、X11、NSS、字体或 GLIBC 版本不兼容，先处理该系统的浏览器依赖；不要替换服务器系统 GLIBC。Wraith 版本与浏览器版本必须配套。该安装流程需要访问 GitHub 及浏览器下载源，尚未在用户服务器实际执行。

先执行离线启动检查（只加载空白页，不抓取商品）：

```bash
runuser -u jiwu -- env XDG_CACHE_HOME=/www/server/jiwu-data/amazon-browser/cache /www/server/jiwu-data/amazon-browser/venv/bin/python - <<'PY'
from wraith.engine import launch
s = launch(engine='camoufox', headless=True, geoip=False)
try:
    s.page.goto('about:blank')
    print('后台浏览器启动成功')
finally:
    s.close()
PY
```

启动成功后，在服务器私有 `/etc/jiwu.env` 中添加以下三项（已存在则修改，不要重复追加）。无需 API Key、Chrome 账号或 Cookie：

```ini
CATALOG_AMAZON_BROWSER_PYTHON=/www/server/jiwu-data/amazon-browser/venv/bin/python
CATALOG_AMAZON_BROWSER_ENGINE=camoufox
CATALOG_AMAZON_BROWSER_CACHE=/www/server/jiwu-data/amazon-browser/cache
```

```bash
systemctl restart jiwu
systemctl is-active jiwu
```

删除或留空 `CATALOG_AMAZON_BROWSER_PYTHON` 即可关闭备用通道。默认不开启，不影响未安装浏览器的服务器。每个 Web 进程最多一个浏览器任务，单次最长 110 秒；超时关闭任务进程组。当前部署只有一个 Web 进程。若将来扩展多进程，需要将浏览器独立为队列服务，共享并发和限流。

## 数据与确认边界

使用临时独立浏览器会话，不借用用户浏览器配置、登录态或凭据，不自动购买代理或调用外部验证码付费服务。仅把目标商品和报价的 HTML 片段传回应用，沿用同 SKU 解析与身份校验。返回成功、HTTP 200 或 Cookie 存在都不等于报价已核验。

人工补价只保存到当前批次、当前店铺及当前子 SKU。服务端按来源原价乘本批次倍率、ROUND_HALF_UP 两位计算；记录人工原价、倍率、最终售价及操作者。缺少建议零售价时提供两倍售价的建议值（可修改、非强制）。不会把人工数值写成自动抓取证据；售价或来源变化会使人工确认失效。库存、EAN、类目、店铺与沃尔玛字段校验继续生效。

## 图片采集

来源报价采集同时解析已核验当前 ASIN 的图片画廊，不采集推荐商品和其他变体。
AI 生成前会对当前 SKU 的主图、附图及来源图片进行下载校验，保存到既有图片存储
（服务器部署沿用 `CATALOG_IMAGE_STORAGE=filesystem` 和 `CATALOG_IMAGE_ROOT`）。
相同文件按内容去重，已缓存的同 SKU 图片可复用；每轮最多采集 32 张，两张并发，单张限 8 MB。
采集结果显示保存数量和失败原因，图片失败不会丢失已采集报价。旧图片链接仍可从原始快照追溯。

本地图片用于 AI 读取；正式提交时沿用 `/api/listing-media/` 的公开刊登图片链接。
`CATALOG_PUBLIC_ORIGIN` 必须是沃尔玛能访问的 HTTPS 站点，反向代理需要保留该路径。
保存成功不代表外部平台一定可访问，平台反馈主图缺失时仍需核对公开链接。
新增供应商 CDN 须明确配置可信 `CATALOG_LISTING_IMAGE_HOSTS`（逗号分隔域名），不能开放任意地址。
浏览器验证助手的来源模式也会附带当前画廊；已安装旧助手时需重新下载并加载新版本。
