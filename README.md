# 001 双协议代理部署

更新：2026-10-16。001（13.193.30.180，Ubuntu24.04）提供Xray VLESS/WebSocket/TLS和Hysteria2/QUIC，并通过一条HTTPS mihomo YAML订阅进行国内直连、海外代理和协议自动选择。两协议共用服务器与出口，无法隔离整机或共同网络故障。

## 技术与目录

服务端采用Python3标准库、systemd、Xray、Caddy、Hysteria2 v2.13.0；客户端验证使用mihomo v1.19.32、Python3.10+、PyYAML和curl。无数据库或独立订阅进程。

| 路径 | 职责 |
| --- | --- |
| cohost/deploy.py | 安装迁移、事务恢复、服务检查、规则更新、网络调参和依赖解耦 |
| cohost/render.py | 生成HY、Caddy、systemd和完整mihomo配置 |
| cohost/config.example.json | 空密码/token的非秘密输入模板 |
| cohost/verify.py | 服务端HY TCP/UDP与错误密码检查，部署器运行依赖 |
| cohost/verify_client.py | 隔离客户端的DNS、分流、双协议出口和故障切换验收 |
| cohost/measure.py | 隔离mihomo与匿名curl的按需线路测量 |
| cohost/tests/ | 使用虚构凭据的部署、渲染及迁移回归测试 |
| .github/workflows/ci.yml | push、PR、手动触发的自动测试及mihomo配置校验 |
| requirements-test.txt | 测试及客户端工具的PyYAML依赖 |
| build/ | 本机私有订阅及按需缓存，全部由Git忽略 |
| 项目最终架构.md | 当前组件、接口、数据流及设计边界 |
| 阶段步骤实现.md | 实施阶段、验收标准和未实现事项 |
| 001部署文件目录.md | 001现用源码、系统配置、证书、缓存、备份及旧版文件清理清单 |

## 环境与配置

当前安装器用于已有Xray/Caddy的Ubuntu24.04 x86_64/aarch64，要求root/sudo、Python3、systemd、curl和有效域名。不是空白VPS安装器。开放TCP80/443和UDP8443；TCP80用于HTTP-01，程序不修改云防火墙或SSH。

Xray状态为/etc/xray-mihomo-deployer/deploy.env，双协议状态为/etc/cohost-proxy/state.json。三域名分别用于Xray身份、HY身份和订阅；节点固定IPv4，保留SNI、Host和证书校验。不要用空白模板覆盖已有状态。

客户端URL保存在本机build/subscription-url.txt，完整配置为build/config.yaml。新克隆不含这两个私有文件，需从自己的受控备份恢复，或通过已验证的SSH连接执行服务器show命令，私下取得HTTPS URL并下载/导入。客户端使用规则模式，PROXY选择自动组或手选协议。

自定义含凭据的安装输入放在被忽略的private/或仓库外，用install --config <私有路径>传入。config.example.json保持空密码/token，首次安装生成独立凭据，后续复用保存值。

## 运行与维护

远程Ubuntu，当前源码目录：

~~~bash
cd /home/ubuntu/cohost-proxy-20261015
sudo python3 -B deploy.py decouple-services
sudo python3 -B deploy.py update-rules
sudo python3 -B deploy.py check
sudo python3 -B deploy.py show
~~~

decouple-services精确迁移旧HY unit，只写unit并daemon-reload，不重启HY/Caddy，重复执行无变更；已有staged部署也可先迁移再finalize。当前001已完成迁移。凭据、订阅URL、证书和ACME转发不变。迁移/规则更新失败或中断保留快照及pending，重新运行对应命令先恢复；详见[部署维护说明](cohost/README.md)。

按需执行sudo python3 -B deploy.py tune-network：TCP BBR及至少7MiB UDP收发上限。首次应用可能重启Caddy/HY，参数相同的重复调用不重启；它与依赖迁移是不同操作。

## 分流、DNS与服务独立性

当前50条规则按入口/局域网、明确国内域名及GeoSite CN、已识别海外域名、GeoIP CN、最终PROXY顺序匹配。国内使用阿里/腾讯DoH，海外经PROXY使用Cloudflare/Google DoH；fake-ip保留域名匹配。地理数据每天尝试更新，需要按准确域名维护例外。

自动组60秒检查、4秒超时、50ms容差并要求HTTP204；PROXY没有DIRECT选项。选择变化作用于后续连接，已有长连接需重建。通用204健康不代表具体应用或长连接一直可用。

HY通过Wants弱依赖启动Caddy，保留After排序。Caddy显式停止/重启不再通过强依赖连带停止HY；已有有效证书时，HY数据路径独立。首次签发/续期仍需要Caddy从TCP80转发到HY回环18080，不能删除该链路。tune-network仍会按维护操作显式重启两服务。

Clash Verge的独立DNS覆写应关闭以使用订阅DNS；全局IPv6开关可能覆盖YAML对应字段。当前不全局屏蔽UDP443。

## 验证与CI

本机Windows PowerShell：

~~~powershell
python -m pip install -r requirements-test.txt
python -X utf8 -B -m unittest discover -s cohost/tests -v
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180 --unavailable-node proxy-HY
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180 --unavailable-node proxy-Xray
~~~

故障注入只改变隔离测试进程，不停止生产节点。measure.py可按需测公开匿名目标，不等于浏览器整页或真实应用长连接测试。

本轮Windows与Ubuntu共23项回归通过；Linux固定版本mihomo接受虚构配置并拒绝故意损坏配置。001实际停止/重启Caddy时HY PID不变，停机期间HY TCP/UDP通过；恢复后服务、可信订阅、错误token404及外部双协议出口、DNS和分流通过。迁移未改10份私有配置及证书内容。没有执行生产证书重签或整机重启演练。

CI在Ubuntu24.04上运行Python3.10/3.13单元测试，以及固定版本/下载摘要的mihomo虚构配置校验；无生产凭据、制品上传或自动部署。真实运行结论以[GitHub Actions](https://github.com/BugYuan64/Node/actions)中对应提交为准。

## 凭据与历史

源码只分发程序、虚构测试凭据、空输入模板、CI和必要文档。build/、private/、密钥及运行状态不提交；git add -f或复制到其他目录仍可绕过忽略。Git忽略不提供加密或磁盘访问控制，完整目录备份也包含私有文件。

本轮按授权从Git历史移除曾提交的build/config.yaml和build/subscription-url.txt，并清理独立分析报告及临时资料。已分发的旧克隆、GitHub缓存或第三方副本不由分支历史重写自动撤回；旧克隆应重新克隆，避免把旧历史推回。线上凭据未轮换，历史清理不使旧凭据失效。服务器有效证书和私有状态保留；旧版备份与已完成事务快照由经逐文件校验的当前部署快照替代，位置及清理清单见[001部署文件目录](001部署文件目录.md)。
