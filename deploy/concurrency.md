# 五人团队并发与多 Key 配置

## 默认调度

- 同时运行 **10 个生成批次**；每位运营最多同时运行 **2 个**。
- 每人最多保留 20 个未完成批次（含采集中、运行中和排队中），全公司最多 100 个；达到上限后提示稍后再提交。
- 超出运行位置的任务存入数据库，提交请求立即返回任务状态，无需等待 AI 完成；页面原有进度轮询继续显示状态。
- 排队任务按提交时间调度，每轮先给不同运营各取一个，约每 2 秒补充空位；达到个人并发上限的运营暂时跳过。同一批次的子变体串行填充。
- 5 把 Key 组成共享池，**每把最多同时处理 2 个 AI 请求**；Key 不与运营固定绑定，优先轮流使用空闲通道。同一批次后续的 AI 请求也要排队，避免抢占其他人的请求。
- 只有 1 把 Key 时，最多有 2 个实际 AI 请求同时执行，其余生成工作位在等待通道。配置 5 把 Key 后最多 10 个 AI 请求同时执行；供应商的账号总限流仍然有效。
- 明确收到限流或认证错误时，冷却该 Key，并尝试其他 Key。全部通道不可用会显示可重试错误；超时或响应不完整不会自动重放请求。
- 排队状态不会因等待超过十分钟被误判为中断；后台会继续调度数据库中的等待任务。运行中任务超过十分钟没有进度会标记中断，保留已保存资料，需重新生成未完成项。

## 配置五把 Key

保留原来的 `CATALOG_LISTING_AI_KEY`，新增四把到 `CATALOG_LISTING_AI_KEYS`。在服务器 `/etc/jiwu.env` 的现有 AI 配置处编辑（以下为占位示例，不是可用密钥）：

```dotenv
CATALOG_LISTING_AI_KEY=原有的第一把密钥
CATALOG_LISTING_AI_KEYS='["第二把密钥","第三把密钥","第四把密钥","第五把密钥"]'
CATALOG_GENERATION_WORKERS=10
CATALOG_GENERATION_PER_USER=2
CATALOG_AI_KEY_PARALLEL=2
```

`KEYS` 必须为 JSON 字符串数组，外层单引号用于保护 JSON 双引号。重复 Key 会自动去重；不用在多个变量里重复填写第一把。所有 Key 共用现有接口地址及模型，不能混用不同平台的 Key。

也支持现有 `CATALOG_LISTING_AI_ENV_FILE` 指向的私有文件或本机 `.env.listing-ai` 中的 `CATALOG_LISTING_AI_*` 配置。服务器进程环境中的同名变量优先。运行并发的三个变量从进程环境读取。配置后重启服务，不要把真实 Key 提交到 Git。

```bash
chmod 600 /etc/jiwu.env
systemctl restart jiwu
systemctl is-active jiwu
```

每位运营需使用自己的系统账号。共用登录账号会共用该账号的两个运行位置。本机未启用团队登录时，任务使用同一个本地用户配额。

## 其他资源上限

| 资源 | 默认上限 | 环境变量 |
| --- | --- | --- |
| HTTP 同步处理线程 | 32 | `CATALOG_HTTP_THREADS` |
| 已接纳 API 请求 | 48 | `CATALOG_HTTP_ACTIVE` |
| 等待接纳请求 | 64，最多等 2 秒 | `CATALOG_HTTP_WAITING`、`CATALOG_HTTP_WAIT_TIMEOUT` |
| 修改及慢操作请求 | 8 | `CATALOG_HTTP_WORK` |
| 图片 HTTP 请求 | 8 | `CATALOG_HTTP_IMAGES` |
| PostgreSQL 连接 | 16 | `CATALOG_DB_POOL_SIZE` |
| 等待数据库连接 | 32，最多等 3 秒 | `CATALOG_DB_POOL_WAITING`、`CATALOG_DB_POOL_TIMEOUT` |
| 来源图片下载线程 | 4，另有 16 个等待位置 | `CATALOG_IMAGE_WORKERS` |
| 全部未完成生成批次 | 100 | `CATALOG_GENERATION_QUEUE_LIMIT` |
| 每人未完成生成批次 | 20 | `CATALOG_GENERATION_USER_QUEUE_LIMIT` |

接纳或数据库队列满时返回 503 与 `Retry-After`，生成队列满时返回 429。上架、改价等操作不会因限流被程序自动重放；结果未知时先查处理记录。

数据库只在短事务内使用连接；AI 和远端请求不占用数据库事务锁。已有批次修订、店铺身份、真实编码唯一性及提交幂等检查继续生效。

以上限制按单个服务进程计数，现有 systemd 服务保持一个 Uvicorn 进程。不要直接增加 Uvicorn workers，否则会成倍增加线程、数据库连接和 Key 并发；跨进程的统一配额需另行设计。

管理员登录后可访问 `/api/admin/runtime` 查看各队列、连接池、AI 通道使用及冷却数量，不返回 Key 或数据库连接字符串。这些上限用于控制资源消耗，实际吞吐量取决于服务器资源、商品变体数量及供应商额度。

## 员工本机浏览器

Amazon 数据接入、商品来源采集、图片补采和预上架来源核验共用“集物采集插件”，电脑上安装一次即可。插件随服务端下载包更新，版本与能力握手不匹配时前端会阻止启动并引导更新；使用本机浏览器时沿用该浏览器邮编，Wraith 为独立的可选后台备用通道。

界面仍保留主动切换到旧服务器采集方式的选项。AI 生成、图片落库和沃尔玛 API 操作仍由服务器执行。

## 更新服务器

```bash
cd /www/wwwroot/jiwu
bash deploy/update-git.sh
```

更新脚本会安装新增的连接池依赖、构建前端并重启服务。默认并发值无需额外配置；补充四把 Key 后重启即可启用对应通道。
