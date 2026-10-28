# Docker Compose 部署

2026-10-25 已在 001 将 Xray、Caddy、Hysteria2 从 systemd 迁移至独立 Compose 项目 `cohost-proxy`。Docker 原已安装，本次没有重装或重启 Docker 守护进程；其他容器不属于本项目。

本目录是现用维护入口，服务器路径为 `/home/ubuntu/cohost-proxy-20261020/containers/`。需要 Linux、root、现有完整代理安装及 Docker Compose；这是已有部署的迁移工具，不是空白机器安装器。

当前公网 IP `35.73.46.1`，三个直连子域名为 `proxy.wsnbb.shop`、`hy.wsnbb.shop`、`sub.wsnbb.shop`（均仅 DNS）。主域 `wsnbb.shop` 提供有效 HTTPS404。域名迁移保留原认证值和其他 Caddy 路由，2026-10-28 已移除 SERVER_IP 覆盖，节点改用域名连接，TLS 身份校验保持；本次仅更新订阅与输入摘要，无服务端重启。迁移前归档与事务快照见 [部署目录](../../001部署文件目录.md)。

## 运行方式

三个镜像用 `FROM scratch` 包装服务器已有静态二进制和系统 CA 包，不下载或升级代理程序，不在镜像中复制认证、订阅或证书。`manage.py migrate` 自动构建镜像，以完整镜像 ID 写入本目录私有 `.env`，Compose 禁止隐式拉取镜像。当前版本为 Xray 26.3.27、Caddy 2.11.7、Hysteria2 2.13.0；完整二进制摘要保存在 `/etc/cohost-proxy/docker.json`。重建需保留这些二进制和 CA 文件，镜像不是公开镜像仓库上的发行物。

三个容器使用 host 网络，维持 TCP80/443、UDP8443 及回环10000/18080/2019；现有 `/aichat/` 转发及 HY HTTP-01 续期链路继续生效。没有新增端口映射或修改云防火墙。容器没有启动依赖，Caddy 重启不连带停止 HY；证书续期仍需要 Caddy。

沿用 `/usr/local/etc/xray`、`/usr/local/share/xray`、`/etc/caddy`、`/etc/hy2-mihomo`、`/srv/mihomo-sub` 的只读目录挂载，避免宿主机原子替换文件后容器继续读取旧 inode。Caddy 与 HY 的原 `/var/lib/` 目录可写，证书和 ACME 账户继续持久保存。迁移没有更改原配置、订阅、认证、证书内容或属主权限。

Xray/HY 使用原服务用户的数字 UID/GID。Caddy 使用 root，只保留 `NET_BIND_SERVICE`（低端口）和 `DAC_OVERRIDE`（读取、续写原 caddy 用户拥有的证书），不修改原证书目录权限。所有容器根文件系统只读、启用 no-new-privileges，日志每容器最多约 3×10MiB。Caddy 日志可能含私有路由，不公开上传。

## 日常命令

```bash
cd /home/ubuntu/cohost-proxy-20261020/containers
sudo docker compose ps
sudo python3 -B manage.py check
sudo docker compose restart xray       # 按需重启单个服务
sudo docker compose restart caddy
sudo docker compose restart hysteria
```

`check` 同时核对 Xray 私有状态摘要和 Caddy 片段 root:caddy0640，防止配置漂移；命令失败/超时诊断保存在 root 私有文件。它还核对三个容器运行、无重启、完整镜像 ID、重启策略、配置校验，以及 HY TCP/UDP/错误密码、可信 HTTPS 订阅和旧订阅失效。它会临时启动测试用 mihomo，结束后退出，不修改桌面代理选择。容器没有内置 Docker HEALTHCHECK；`ps` 的 Up 只表示进程存活，业务检查使用本命令。发生过容器自动重启时检查会报错，应先定位日志和原因。

Docker 开机自启、容器使用 `restart: unless-stopped`。原 `xray`、`caddy`、`hy2-mihomo` systemd 单元保留但 inactive/disabled。不要手动启动它们，以免抢占端口。原 `deploy.py` 在 Docker 状态标记存在时拒绝旧维护命令（`show` 除外），避免 systemd 操作与容器冲突。目前修改规则、网络调参或升级原二进制，先回退 systemd，再用原工具完成变更，之后重新迁移；不要直接套用旧文档里的 update-rules/check。

## 迁移和回退

```bash
# 现有 final 部署迁移；已 active 时只检查，不重复切换
sudo python3 -B manage.py migrate
# 回退：停止本 Compose 项目，恢复原 unit 自启状态并启动、检查原服务
sudo python3 -B manage.py rollback
```

迁移共享原部署锁，先运行旧版完整 check、构建并校验候选容器，然后备份配置、状态、源码和证书，逐文件核验备份后才停止旧服务。切换后检查失败会停止本项目容器并恢复原服务。进程被强制杀死或主机断电时可能留下 `switching` 标记；修复环境后执行 `rollback`，不要删除标记。回退检查失败保留标记供重试。

成功迁移前备份为 `/var/backups/cohost-proxy/docker-before-20261025T050420Z.tar.gz` 及 `.sha256`，root-only；首次失败尝试的备份也保留。`docker.json` 记录备份路径、原自启状态和受保护文件摘要。此增量包含私密资料，不进 Git；它不包含 Docker 镜像、Docker 引擎或所有系统二进制，不能单独称为整机恢复包。原 10 月 20 日整套备份仍保留。

普通回退复用持续挂载的现用文件和证书，不解压旧备份覆盖续期结果。启动 systemd Caddy 前，将其数据目录内 root 所有的文件/目录交还给原 caddy 账号，保留内容和模式，排除符号链接；这保证容器续期新建的0600证书仍可被原服务读取。若属主恢复失败，保留状态标记，不启动旧服务。若文件已损坏，应先核验归档摘要、内容和路径再实施单独恢复。

## 当前验证

本轮37项本机回归通过。服务端完整检查通过：三容器运行、固定镜像、无自动重启，HY TCP/UDP/错误密码验证和可信订阅正常。原 systemd 三项仍 inactive/disabled，Docker enabled。

新域名外网可信 HTTPS 订阅200、4619字节与服务器摘要一致，错误 token404；主域原525修复为可信 HTTPS404。本机隔离 mihomo 的 DNS、国内直连、双协议出口（35.73.46.1）、Google 规则和完整 XML 下载、两个方向的自动故障切换均通过。手机 Wi-Fi/移动数据仍由用户实测；未重启整机或 Docker 守护进程。

首次容器迁移的32项回归及权限失败自动回退属于迁移当时验证；本轮域名切换前另做完整检查和逐文件私有归档，不把旧归档称为新域名最终状态。
