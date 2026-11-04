# 通过 Git 部署与更新

仓库：`git@github.com:shisanhaoyuan/product.git`，分支 `main`。
仓库根目录就是商品库项目。此方式替代压缩包上传代码，数据库仍须单独迁移。
目标服务器为 Alibaba Cloud Linux 3，使用已安装的 Python 3.11、Node 22；PostgreSQL 与 HTTPS 需按部署说明配置。

## 首次安装

服务器需要 Git、Python 3.11/3.12、venv、Node 22.12+ 或 Node 24 LTS、npm、Nginx 和 systemd。
宝塔自身的 Python 不要替换。Node 是 Git 源码部署新增的要求：`web/dist` 不提交到 Git，需要在服务器构建。

私有仓库需要服务器自己的只读 Deploy Key，不能复制本机私钥。先检查服务器现有连接是否已能读取本仓库；尚未配置时再生成专用密钥，将公钥添加到该仓库的 Deploy keys，**不勾选写权限**。GitHub 主机指纹需按官方公布值核对，不关闭 SSH 主机校验。不要把密码或私钥发到聊天或提交仓库。

目标目录必须不存在；已有目录不要直接覆盖：

```bash
git clone --branch main --single-branch https://github.com/shisanhaoyuan/product.git /www/wwwroot/jiwu
cd /www/wwwroot/jiwu
python3.11 -m venv .venv
.venv/bin/python -m pip install -r deploy/requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip check
cd frontend
npm ci --include=dev
npm run build
cd ..
```

`python3.11` 按已确认的服务器 Python 路径替换。随后按[宝塔部署说明](宝塔部署说明.md)创建 `jiwu` 服务账号、迁移数据库、配置 `/etc/jiwu.env`、创建管理员、安装 systemd 服务和配置 HTTPS 反向代理。代码目录保持 root 所有，服务账号只读；数据目录由服务账号写入。不要把网站静态根目录直接指向 Git 项目。

生产使用 PostgreSQL，图片放在 `/www/server/jiwu-data/images`，配置放在 `/etc/jiwu.env`，均独立于 Git。首次将 SQLite 在线备份通过 `migrate-sqlite` 迁入空 PostgreSQL；不能直接复制 .db 当作生产库。完整步骤见宝塔部署说明。投入使用后不再用本机库覆盖。

## 后续一条命令更新

仅使用服务器公网 IP 时也可部署，访问地址使用 `https://实际公网IP`，`CATALOG_PUBLIC_ORIGIN` 填写相同地址，保留团队登录和 Secure Cookie。Nginx 使用该 IP 的独立站点，配置时先检查现有默认站点，避免覆盖其他应用。IP 无需 DNS 解析，但 HTTPS 证书必须包含该 IP 的 Subject Alternative Name。

Let's Encrypt 的 `shortlived` profile 支持 IP 证书，有效期为 160 小时，需要自动续期。Certbot 需支持 `--ip-address` 和 `--required-profile shortlived`；先确认安装版本与服务器端口、站点配置，再通过 webroot HTTP-01 验证申请，不能为申请证书随意停掉现有 Nginx。配置定时续期、成功后的 Nginx 校验和重载，并检查续期失败告警。首次申请的服务条款由服务器管理员确认。

参考：[Let's Encrypt profiles](https://letsencrypt.org/docs/profiles/#shortlived)、[Certbot 参数说明](https://eff-certbot.readthedocs.io/en/stable/using.html)。IP 证书尚未在目标服务器申请或验证。无需改动应用的 HTTPS 限制，也不要将公网部署改成无登录或明文 Cookie 模式。

已有 `/etc/letsencrypt/live/jiwu-ip/` 证书、独立 Certbot 位于 `/opt/jiwu-certbot`、80 端口已能验证 `/www/server/jiwu-acme` 时，可用 root 执行：

```bash
python3.11 deploy/configure_ip_https.py 8.153.77.160
```

脚本新增 `jiwu_https.conf` 和 `jiwu-cert-renew.timer`，不覆盖其他站点，也不更改现有 80 端口用途。每六小时检查续期，成功后检查并重载 Nginx；安装后验证本机可信 HTTPS 和模拟续期。目标配置已存在且内容不同时拒绝覆盖。配置测试失败清理本次新增文件；重载或续期验证失败则保留配置和日志供排查。请确认公网 TCP 443 已放行，并用浏览器验证登录。

续期状态：`systemctl list-timers jiwu-cert-renew.timer`；失败日志：`journalctl -u jiwu-cert-renew.service --no-pager`。保持 80 端口验证路径可从公网访问。短期 IP 证书必须持续监控续期结果。

## 执行更新

本机修改、验证后提交并推送到 `main`，服务器执行：

```bash
bash /www/wwwroot/jiwu/deploy/update-git.sh
```

脚本会检查环境和代码是否干净，拉取远端提交；遇到本地修改或历史分叉则停止，不强制重置。随后备份旧代码及前端构建，停止服务并备份 PostgreSQL 和图片目录，再快进到已获取的提交、安装锁定依赖、重新构建前端、启动服务并检查本机健康接口。更新期间有短暂停机。即使 Git 代码已是最新，也会执行安装与构建，允许修复上次未完成的更新。

脚本不修改环境文件、Nginx、服务定义或公网端口；这些部署配置如需变更，应单独核对后应用。它不会覆盖商品库，也不会重复计算价格。数据库结构迁移仍由新版本启动时执行。只通过 `git pull` 而不构建前端、安装依赖和重启服务，不能保证更新生效。

更新失败会保留备份并停止服务，防止把半完成版本当成成功。查看终端错误和 `journalctl -u jiwu -n 80 --no-pager` 后处理。备份目录内记录原提交及依赖，恢复必须先确认是否发生数据库迁移；不能仅切换旧代码后假定旧版与新版数据库兼容。确认回滚方案后，停服恢复代码、构建和匹配的数据备份。脚本不会自动删除历史备份。

健康检查只验证应用启动。每次更新后仍需用正式 HTTPS 地址登录，核对主子商品、图片上传、权限和保存回读。
