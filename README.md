# 001 双协议代理与 HTTPS 订阅

更新：2026-10-16。001（13.193.30.180，Ubuntu 24.04）同时提供 Xray VLESS/WebSocket/TLS 与 Hysteria2/QUIC。客户端通过一条完整的 mihomo YAML URL，使用国内直连、海外代理和双协议自动选择。

当前已部署。两种协议共用同一 VPS 和出口，能应对协议故障；整机或共同网络路径故障仍会同时影响两者。

## 功能、技术与目录

| 路径 | 实际用途 |
| --- | --- |
| .gitattributes | 文本统一LF，Windows批处理检出为CRLF，二进制不转换 |
| .gitignore | 排除私有订阅、密钥、运行状态及临时缓存 |
| cohost/deploy.py | 安装迁移、订阅原子更新、服务检查、网络调参与事务恢复 |
| cohost/render.py | 生成 HY2、Caddy、systemd 和完整 mihomo 配置 |
| cohost/config.example.json | 不含真实凭据的配置模板 |
| cohost/verify.py | 服务端 HY2 TCP/UDP、错误密码验证 |
| cohost/verify_client.py | 客户端 DNS、分流、双节点出口、隔离故障切换验收 |
| cohost/measure.py | 使用隔离 mihomo 与匿名 curl 复测两条线路 |
| cohost/tests/ | 凭据、配置契约、网络参数和回退顺序的回归测试 |
| build/ | 仅保留最终 config.yaml 和 subscription-url.txt；含凭据，禁止公开 |
| 项目最终架构.md | 组件、接口、数据流、技术选择与边界 |
| 阶段步骤实现.md | 实施顺序、交付物、完成标准和当前状态 |

服务端使用 Python 3 标准库、systemd、Xray、Caddy、Hysteria2 v2.13.0。当前客户端验证为 mihomo v1.19.32、Python 3.10+、PyYAML、curl；没有数据库。源码沿用 cohost 职责目录，没有为规划功能建立空目录。

## 环境与配置

需要已有 Xray/Caddy 环境、root/sudo、有效域名及 TCP80/443、UDP8443。三个 sslip.io 域名分别承担 Xray TLS 身份、HY2 TLS 身份和订阅。TCP80 必须保留用于 ACME 自动续期；程序不修改 AWS 云防火墙。

Xray 有效凭据状态是 /etc/xray-mihomo-deployer/deploy.env，双协议状态是 /etc/cohost-proxy/state.json。域名、密码、端口迁移需独立处理，不可用空白模板覆盖现有状态。安装器负责已有 Xray/Caddy 上的双协议迁移，并非空白 VPS 整机安装器。

本机唯一订阅 URL 在 build/subscription-url.txt。将其中 HTTPS URL 添加到客户端，使用规则模式，让 PROXY 选择“自动选择”。URL 和 YAML 都含访问凭据，不写入公开文档。

## 当前分流与线路

1. 自有入口、订阅和局域网优先直连，避免循环。
2. 保留本地订阅中明确的中国站点域名，配合 .cn 与 GeoSite CN 直连。
3. 已识别海外域名进入 PROXY；其余域名允许解析后由 GeoIP CN 补充国内直连，最后代理兜底。
4. 国内 DNS 使用阿里/腾讯 DoH；海外 DNS 经 PROXY 使用 Cloudflare/Google DoH。节点固定 IP 连接，SNI、Host 与证书校验保留。

当前有50条规则。没有采用旧订阅的 -cn 关键词、共享 CDN 全部直连、整个 Apple 网段直连或邮件端口直连；这些规则会使部分海外流量误直连。地理数据每天尝试更新，但无法保证覆盖每个新域名，应按准确域名维护例外。

自动组每60秒检查、4秒超时、50ms容差，要求HTTP204；失败阈值2用于触发额外健康检查。PROXY 提供自动组及两个节点，不提供海外流量的 DIRECT 选项。选择变化影响后续连接，已有长连接需重连。

001 已开启 TCP BBR；UDP 收发缓冲上限由4MiB提升至7MiB并持久化。保留 HY2 默认自适应拥塞控制、流窗口和 MTU 探测，不设置未经带宽测量的固定 Brutal 速率。

## 维护与验证

远程 Ubuntu：

~~~bash
cd /home/ubuntu/cohost-proxy-20261015
sudo python3 -B deploy.py update-rules
sudo python3 -B deploy.py tune-network
sudo python3 -B deploy.py check
sudo python3 -B deploy.py show
~~~

上传修改后的源码后，update-rules 在原URL发布，不轮换凭据。tune-network 首次应用会短暂重启 Caddy/HY2，重复执行且参数未变时不重启。失败或中断通过 pending 与快照恢复。迁移及回退见 [cohost/README.md](cohost/README.md)。

本机 Windows PowerShell：

~~~powershell
python -X utf8 -B -m unittest discover -s cohost/tests -v
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180 --unavailable-node proxy-HY
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180 --unavailable-node proxy-Xray
python -X utf8 -B cohost/measure.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --rounds 5
~~~

故障测试只修改隔离测试端点，不停止生产服务。服务端检查涵盖配置、权限、服务、HY TCP/UDP/错误密码、可信HTTPS内容与错误令牌404。

## 当前验收

2026-10-16：12项回归测试通过；服务端与线上订阅检查通过；实际 Clash Verge 已加载50条规则、订阅DNS分流、TCP并发和自动选择，出口为001。GUI全局IPv6会覆盖订阅的IPv6值；当前保留此全局设置，节点仍使用IPv4固定IP。

公开HTTPS和AWS静态文件请求、双协议故障切换已验证。AWS入口与登录页的HTTP/3对照没有一致的整页收益，未复现数十秒卡顿，因此未永久屏蔽UDP443。登录后的资源页面不在已验证范围，结果不能视为长期SLA。

本地仅保留部署源码、维护文档和最终订阅资料，浏览器快照、测试缓存、临时备份及过程报告已清理。验证器运行时会按需重新生成缓存。服务器有效状态、证书和恢复备份继续保留，恢复方法见cohost/README.md。

## Git仓库

仓库保存部署源码、测试、配置模板和文档。build中的真实订阅配置与URL、SSH信息、私钥和运行状态不提交。克隆后可在已有VPS运行deploy.py show获取订阅URL，再按需准备本机私有build资料进行客户端验证。
