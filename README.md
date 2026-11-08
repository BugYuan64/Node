# 双协议代理

基于 Xray VLESS/WebSocket/TLS 和 Hysteria2 QUIC 的双协议代理项目，将两个节点合并为一份 HTTPS mihomo 订阅，供 Clash Verge、Clash Meta for Android 等兼容客户端使用。

服务端工具面向 **已有 Xray/Caddy 配置的 Ubuntu 24.04 主机**，支持 systemd 部署维护及 Docker Compose 迁移。它不是从空白 VPS 开始的一键安装器。两个协议共享服务器，提供协议冗余，不提供多主机容灾。

## 项目组成

| 路径 | 职责 |
| --- | --- |
| `cohost/render.py` | 配置校验及 Hysteria2、Caddy、mihomo、systemd unit 渲染 |
| `cohost/deploy.py` | systemd 阶段部署、事务恢复、规则更新及共用验证函数 |
| `cohost/containers/` | Compose 定义、镜像封装、迁移、检查和 systemd 回退 |
| `cohost/verify.py` | 服务端 Hysteria2 TCP、UDP 及错误密码检查 |
| `cohost/verify_client.py` | 隔离客户端的 DNS、分流、协议出口、下载与故障切换验证 |
| `cohost/measure.py` | 按需测量线路，结果写入本地忽略目录 |
| `cohost/tests/` | 使用虚构凭据和示例地址的回归测试 |
| `cohost/config.example.json` | 空凭据配置模板 |
| `.github/workflows/ci.yml` | Python 回归与固定版本 mihomo 配置检查，不自动部署 |

设计与阶段安排见 [项目最终架构](项目最终架构.md)、[阶段步骤实现](阶段步骤实现.md)；配置契约见 [维护 README](cohost/README.md)，容器操作见 [容器 README](cohost/containers/README.md)。

## 环境与域名

本地测试需要 Python 3.10 或更新版本及 `requirements-test.txt` 中的依赖。服务端维护需要 Linux、root、已有的 Xray/Caddy 安装及其配置；Compose 模式另需 Docker Compose 和原服务二进制。支持的服务端架构为 x86_64、aarch64。

以下均为可替换的示例域名，不指向项目实际部署：

| 示例域名 | 用途 |
| --- | --- |
| `proxy.example.com` | Xray TLS/WebSocket，TCP 443 |
| `hy.example.com` | Hysteria2，示例 UDP 8443；HTTP-01 由 Caddy 转发 |
| `sub.example.com` | HTTPS 私有订阅，TCP 443 |
| `example.com` | 可选根域，独立 HTTPS 404 页面 |

节点默认使用域名连接，保留 TLS 校验、SNI 和 WebSocket Host。`SERVER_IP` 仅为已有渲染器支持的可选覆盖，不是必填配置；使用域名时不填写它，也不生成固定服务器 IP 的直连规则。更新服务器地址时调整 DNS。

Hysteria2 的业务 UDP 不能通过普通 HTTP 反向代理；直接连接的域名应采用能到达服务端的 DNS 记录。云防火墙按配置开放 TCP 80/443、Hysteria2 的 UDP 端口；回环监听端口不对公网开放。本项目不自动修改云防火墙。

## 本地检查

从项目根目录执行：

```sh
python -m pip install -r requirements-test.txt
python -B -m unittest discover -s cohost/tests -v
```

CI 使用 Python 3.10、3.13，并以固定版本 mihomo 验证虚构配置能通过、错误配置能被拒绝。测试不会读取生产凭据或自动部署到服务器。

外部客户端验证按自己的受控配置和内核路径执行：

```sh
python -B cohost/verify_client.py --config private/config.yaml --mihomo /path/to/mihomo --expected-ip EXPECTED_EXIT_IP
```

`EXPECTED_EXIT_IP` 是验证时填写的预期出口，不写入项目配置。分别加上 `--unavailable-node proxy-HY`、`--unavailable-node proxy-Xray`，可在临时内核内模拟单个节点不可用，不停止服务端节点。

## 服务端使用

以下 `/opt/Node` 仅是源码目录示例，应替换为自己的目录：

```sh
cd /opt/Node/cohost/containers
sudo docker compose ps
sudo python3 -B manage.py check
# 仅在受控终端查看订阅；输出含私有 URL
sudo python3 -B ../deploy.py show
```

已有 systemd 部署的初始化、finalize 及维护边界见 [维护 README](cohost/README.md)。Compose 模式使用 `manage.py`；不要绕过 Docker 状态标记运行旧版维护命令。

## 分流与客户端

订阅包含国内/局域网直连、海外代理和 Google 服务/下载例外规则，Google 例外先于国内规则。国内 DoH 直连，海外及 Google DoH 经代理；入口域名单独解析并直连，Hysteria2 显式使用 DIRECT 拨号。自动组默认每 60 秒探测、超时 4 秒、容差 50ms，期望响应码 204；代理选择组不提供 DIRECT 选项。

Clash Verge 的 DNS、端口和 IPv6 覆写可能改变订阅行为，重载使用客户端内置流程。Android 需检查系统 VPN 接管、应用访问控制、DNS 和 IPv6，分别验证 Wi-Fi、移动数据下的两个节点及自动选择。桌面下载成功不代表手机商店登录和实际安装已通过；已建立长连接也不会因组切换自动迁移。

## 发布内容与限制

仓库仅保存项目本体、README、阶段设计和最终架构。真实订阅、UUID、密码、token、证书、SSH 密钥、本机配置、部署现场清单及过程/审核/诊断记录不上传；`build/`、`private/` 等本地目录由 Git 忽略，忽略规则不提供加密。

现有功能不包含通用空白机安装、自动升级、持续告警和多 VPS 容灾。源码与离线测试的检查不能代替服务端业务、手机网络、整机重启或跨机恢复验收；具体部署及恢复材料由使用者在私有位置维护。
