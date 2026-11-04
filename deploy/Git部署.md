# 通过 Git 部署与更新

仓库：`git@github.com:shisanhaoyuan/product.git`，分支 `main`。
仓库根目录就是商品库项目。此方式替代压缩包上传代码，数据库仍须单独迁移。
服务器实际系统、Python 路径、域名及已有服务确认后，再执行首次安装。

## 首次安装

服务器需要 Git、Python 3.11/3.12、venv、Node 22.12+ 或 Node 24 LTS、npm、Nginx 和 systemd。
宝塔自身的 Python 不要替换。Node 是 Git 源码部署新增的要求：`web/dist` 不提交到 Git，需要在服务器构建。

私有仓库需要服务器自己的只读 Deploy Key，不能复制本机私钥。先检查服务器现有连接是否已能读取本仓库；尚未配置时再生成专用密钥，将公钥添加到该仓库的 Deploy keys，**不勾选写权限**。GitHub 主机指纹需按官方公布值核对，不关闭 SSH 主机校验。不要把密码或私钥发到聊天或提交仓库。

目标目录必须不存在；已有目录不要直接覆盖：

```bash
git clone --branch main --single-branch git@github.com:shisanhaoyuan/product.git /www/wwwroot/jiwu
cd /www/wwwroot/jiwu
python3.12 -m venv .venv
.venv/bin/python -m pip install -r deploy/requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip check
cd frontend
npm ci --include=dev
npm run build
cd ..
```

`python3.12` 按已确认的服务器 Python 路径替换。随后按[宝塔部署说明](宝塔部署说明.md)创建 `jiwu` 服务账号、迁移数据库、配置 `/etc/jiwu.env`、创建管理员、安装 systemd 服务和配置 HTTPS 反向代理。代码目录保持 root 所有，服务账号只读；数据目录由服务账号写入。不要把网站静态根目录直接指向 Git 项目。

数据放在 `/www/server/jiwu-data/catalog.db`，环境配置放在 `/etc/jiwu.env`，均在 Git 仓库外。首次迁移使用 SQLite 在线备份，包含商品、修改记录、账号及本地图片二进制。服务器投入使用后，不再用本机数据库覆盖服务器数据库。

## 后续一条命令更新

本机修改、验证后提交并推送到 `main`，服务器执行：

```bash
bash /www/wwwroot/jiwu/deploy/update-git.sh
```

脚本会检查环境和代码是否干净，拉取远端提交；遇到本地修改或历史分叉则停止，不强制重置。随后备份旧代码及前端构建，停止服务并备份完整数据库，再快进到已获取的提交、安装锁定依赖、重新构建前端、启动服务并检查本机健康接口。更新期间有短暂停机。即使 Git 代码已是最新，也会执行安装与构建，允许修复上次未完成的更新。

脚本不修改环境文件、Nginx、服务定义或公网端口；这些部署配置如需变更，应单独核对后应用。它不会覆盖商品库，也不会重复计算价格。数据库结构迁移仍由新版本启动时执行。只通过 `git pull` 而不构建前端、安装依赖和重启服务，不能保证更新生效。

更新失败会保留备份并停止服务，防止把半完成版本当成成功。查看终端错误和 `journalctl -u jiwu -n 80 --no-pager` 后处理。备份目录内记录原提交及依赖，恢复必须先确认是否发生数据库迁移；不能仅切换旧代码后假定旧版与新版数据库兼容。确认回滚方案后，停服恢复代码、构建和匹配的数据备份。脚本不会自动删除历史备份。

健康检查只验证应用启动。每次更新后仍需用正式 HTTPS 地址登录，核对主子商品、图片上传、权限和保存回读。
