# Docker Compose 部署

本目录将**已有且已 finalize 的 systemd 双协议部署**迁移为 Compose 项目 `cohost-proxy`，提供 `migrate`、`check`、`rollback`。需要 Linux root、Docker Compose、现有配置、服务账号及 Xray/Caddy/Hysteria2 二进制；不负责空白机器初始化或 Docker 安装。

## 镜像与运行结构

`Dockerfile` 使用 `FROM scratch`，由 `manage.py` 将宿主机现有静态二进制及系统 CA 打包为三个本地镜像，不下载或升级程序，不将认证、订阅和证书复制进镜像。迁移生成私有 `.env`，记录完整镜像 ID 和服务账号的数字 UID/GID；Compose 使用 `pull_policy: never`，重建需要自行保留原二进制和 CA。

三个服务采用 host 网络、`restart: unless-stopped`、只读根文件系统、`no-new-privileges`、受限能力和日志限额。Xray/Hysteria2 使用原服务账号的数字 UID/GID；Caddy 使用 root，仅增加绑定低端口和读写原证书所需的 `NET_BIND_SERVICE`、`DAC_OVERRIDE`。

配置目录只读挂载，支持宿主机原子替换；证书和 ACME 数据目录可写持久化。容器间没有强启动依赖：Hysteria2 持有有效证书时的数据通路独立，但首次签发/续期仍需要 Caddy 将 HTTP-01 请求转发到 Hysteria2 的回环挑战端口。其他既有 Caddy 路由继续由原配置管理。

## 日常操作

以下目录为示例，应替换为自己的源码位置：

```sh
cd /opt/Node/cohost/containers
sudo docker compose ps
sudo python3 -B manage.py check
# 按需单独重启，不作为例行操作
sudo docker compose restart xray
sudo docker compose restart caddy
sudo docker compose restart hysteria
```

`check` 校验私有输入摘要、渲染结果、Caddy token 片段权限、容器运行及重启次数、完整镜像 ID、重启策略、配置有效性、Hysteria2 TCP/UDP/错误认证、可信 HTTPS 订阅及旧路由失效。检查会临时启动测试 mihomo，结束后退出，不修改桌面客户端的选择。

容器未定义 Docker HEALTHCHECK；`ps` 的 Up 仅表示进程存活。检测到自动重启会报错，应核对私有日志与原因。运行错误和超时诊断保存到 root 私有文件，不应公开上传。

## 迁移与回退

```sh
sudo python3 -B manage.py migrate
sudo python3 -B manage.py rollback
```

迁移在共用部署锁内先执行原服务完整检查，构建固定镜像并校验候选容器，再备份配置、状态、源码和证书。逐文件核验归档后才停止原服务并启动容器；切换后检查失败则回退。已有 active 标记时，`migrate` 只检查，不重复切换。

Docker 管理状态保存在 `/etc/cohost-proxy/docker.json`，包括阶段、镜像摘要、原 systemd 自启状态、受保护文件摘要和备份位置。断电或进程被杀死后可能保留 `switching` 标记，应修复环境并执行 `rollback`，不要删除标记。

回退停止本 Compose 项目，恢复原 systemd 自启状态和服务。启动原 Caddy 前，将其持久数据内 root 所有的普通文件/目录交还原 caddy 账号，保留内容、模式并排除符号链接，使容器续期的新证书继续可读。属主恢复或业务检查失败时保留标记以便重试。

普通回退使用现用配置和证书，不解压旧备份覆盖续期结果。迁移备份含秘密，必须私有保存；它不包含 Docker 引擎、镜像和全部系统二进制，不能单独当作整机恢复包。

## 维护边界

容器接管后，原 `xray`、`caddy`、`hy2-mihomo` systemd 服务应停止并禁用，避免抢占端口。`deploy.py` 除 `show` 外拒绝旧维护命令；需要修改规则、调参或升级二进制时，先回退并验证原服务，再执行对应维护及重新迁移。

本项目不自动改变云防火墙、Docker 守护进程或其他容器。离线测试不能代替服务器迁移、真实客户端、重启和跨机恢复验证；本 README 不保存特定服务器的 IP、指纹、备份时间或审核流水。
