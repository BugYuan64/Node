# 001 双协议代理

当前部署：2026-10-25，Ubuntu 24.04，公网 IP `35.73.46.1`，Docker Compose 项目 `cohost-proxy`。Xray 提供 VLESS/WebSocket/TLS，Hysteria2 提供 QUIC，两节点合并为一条 HTTPS mihomo 订阅，支持 Clash Verge 与 Clash Meta for Android。

| 域名 | 用途 | DNS |
| --- | --- | --- |
| `proxy.wsnbb.shop` | Xray，TCP443 | A → `35.73.46.1`，仅 DNS |
| `hy.wsnbb.shop` | Hysteria2，UDP8443；HTTP-01 | A → `35.73.46.1`，仅 DNS |
| `sub.wsnbb.shop` | HTTPS 私有订阅 | A → `35.73.46.1`，仅 DNS |
| `wsnbb.shop` | 主域名 HTTPS，默认404 | 当前保留 Cloudflare 代理 |

2026-10-28 起节点直接使用 `proxy.wsnbb.shop`、`hy.wsnbb.shop` 连接，保留对应 SNI、WS Host 和证书校验；当前部署已移除 `SERVER_IP` 覆盖与服务器固定 IP 的 DIRECT 规则，地址随 DNS 更新。HY 的 `dialer-proxy: DIRECT` 仅控制到服务器的拨号路径。HY UDP 不能经过普通 Cloudflare HTTP 代理，不能把其 DNS 记录改成橙云。主域名不公开订阅地址或节点信息。

## 文件与入口

| 路径 | 职责 |
| --- | --- |
| `cohost/render.py` | 生成 HY、Caddy、mihomo 和回退用 systemd unit |
| `cohost/containers/` | 现用 Compose、镜像封装、迁移、检查和 systemd 回退 |
| `cohost/deploy.py` | 原 systemd 部署事务及共用验证函数；Docker 模式仅允许 show |
| `cohost/verify.py` | 服务端 HY TCP/UDP 与错误密码验证 |
| `cohost/verify_client.py` | 隔离客户端 DNS、分流、双协议出口及故障切换 |
| `cohost/measure.py` | 按需线路测量 |
| `cohost/tests/` | 使用虚构凭据和保留示例地址的回归测试 |
| `cohost/config.example.json` | 无秘密的输入模板，需要填写自己的 HY 域名 |
| `.github/workflows/ci.yml` | 单元测试和固定版本 mihomo 配置校验；无自动部署 |
| `build/`、`private/` | 本机私有资料，Git 忽略 |

服务器正式源码：`/home/ubuntu/cohost-proxy-20261020/`。完整路径和回退资料见 [部署文件目录](001部署文件目录.md)；组件与数据流见 [项目最终架构](项目最终架构.md)；实施状态见 [阶段步骤实现](阶段步骤实现.md)。

## 日常使用

```bash
cd /home/ubuntu/cohost-proxy-20261020/containers
sudo docker compose ps
sudo python3 -B manage.py check
# 私下查看完整订阅地址
sudo python3 -B ../deploy.py show
```

本机完整订阅为 `build/config.yaml`，含 token 的 URL 为 `build/subscription-url.txt`。这两份文件不进入 Git。新克隆从自己的受控备份或已验证 SSH 上的 show 恢复地址，通过可信 HTTPS 下载；不要用空白模板覆盖现有服务器状态。

原 systemd 三服务保持 inactive/disabled，Docker enabled，容器 `restart: unless-stopped`。修改规则、域名、凭据或升级程序需要独立的有备份迁移；不要在容器运行时执行旧版 install/update-rules/tune-network。操作边界见 [Docker 部署说明](cohost/containers/README.md) 和 [维护说明](cohost/README.md)。

## 分流与客户端

规则模式下，入口 IP/域名和局域网直连，Google 服务及下载域名优先代理，再匹配国内域名、GeoSite CN、海外域名、GeoIP CN，最终 PROXY。国内使用阿里/腾讯 DoH，海外及 Google 使用经 PROXY 的 Cloudflare/Google DoH。自动组每60秒探测204，超时4秒，容差50ms；PROXY 无 DIRECT 选项。两协议共享主机和出口，仅提供协议冗余。

Clash Verge 关闭独立 DNS 覆写以使用订阅 DNS；GUI 的端口和 IPv6 设置可能覆盖订阅。不要直接调用托管内核的 `/restart`，应使用客户端内置流程。

Android/CMFA：开启系统流量接管和 DNS 劫持，关闭“允许应用绕过”，允许所有应用并包含 Play 商店、Play 服务和下载管理器，IPv6 关闭。绕过私有网络可保持开启。更新新订阅后重启代理，并分别手选 Xray/HY，在 Wi-Fi 和移动数据下测试后恢复自动选择。系统私人 DNS 可能改变 DNS 行为。此版本 VPN MTU 由客户端决定，不靠订阅中的 MTU 假定已修复。

## 验证与网络条件

```powershell
python -m pip install -r requirements-test.txt
python -X utf8 -B -m unittest discover -s cohost/tests -v
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 35.73.46.1
```

故障切换验收再分别添加 `--unavailable-node proxy-HY` 和 `--unavailable-node proxy-Xray`。它们只修改临时内核，不停生产服务。Google API 的404只证明 TLS/HTTP 可达，公开 XML 完整下载不等于已登录手机商店安装成功。

云防火墙需要 TCP80/443、UDP8443；SSH22 按管理需求限制来源。回环10000、18080、2019不应公网开放。本项目不自动修改云防火墙。当前节点采用 IPv4，三个直连子域名不配置无效 AAAA。当前内核为 cubic、UDP 收发上限各4MiB，未凭猜测修改 BBR、带宽、MTU 或全局封锁 UDP443。

本轮新增回归验证自定义域名、显式 IP、拒绝 fake-IP、根域 HTTPS、容器模式状态摘要漂移和超时诊断保存。实际线上与客户端验收状态见阶段文档。手机、整机重启与跨机还原需要另行实测。

## 私密资料与恢复

源码不含真实 UUID、密码、token、完整订阅 URL、证书、SSH 密钥、运行日志或镜像内秘密。`build/`、`private/` 与 `.env` 只靠 Git 忽略，不提供加密；不要强制加入版本控制。历史凭据清理不使旧凭据失效；此次域名迁移保留认证值。

本轮迁移前整套增量回退包及配置事务保留在服务器 root 私有备份目录，不删除旧证书或历史恢复包，不重写 Git 历史。当前源码与有效配置已移除原域名依赖。备份核验只证明归档内容完整，不等于独立机器恢复或重启演练。
