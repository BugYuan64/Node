# 001 双协议代理部署

2026-10-25兼容修复：生成的HY节点显式设置`dialer-proxy: DIRECT`，使用DIRECT拨号器连接HY服务器；业务流量仍经HY转发，不固定本机网卡。此前在Windows主内核中对照确认该设置可恢复HY连接，底层差异未完全定位，不据此认定运营商封锁或Mihomo升级必然失效。

本轮已同步渲染器并沿用原URL发布4736字节订阅；26项离线回归及Mihomo配置校验通过，可信HTTPS内容一致、错误token返回404。13份服务配置、状态和证书以及三服务PID保持不变，phase=final、无pending。按用户要求未再启动代理探测进程，更新后的实际访问由用户手测。

更新：2026-10-20。001（13.193.30.180，Ubuntu24.04）提供Xray VLESS/WebSocket/TLS和Hysteria2/QUIC，并通过一条HTTPS mihomo YAML订阅进行国内直连、海外代理和协议自动选择。支持Clash Verge及Clash Meta for Android；两协议共用服务器与出口，无法隔离整机或共同网络故障。

2026-10-20公网IP重新绑定至新实例后，已恢复三项服务、原订阅URL和认证参数，重新签发三个域名的证书。新源码目录为`/home/ubuntu/cohost-proxy-20261020`，服务自启及服务端`check`通过，当前为`final`、无pending。用户放行云防火墙后，公网可信HTTPS订阅返回200并与本机4711字节配置摘要一致；电脑隔离mihomo的DNS、国内直连、双协议出口、Google完整下载及两个方向自动切换均通过，出口均为13.193.30.180。新实例手机Wi-Fi/移动数据验收待用户实测；下文10月16日、18日手机结果属于原实例历史验收。

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

Lightsail当前实例的Networking → IPv4 firewall应保留以下入站规则（EC2对应安全组入站规则）：

| 用途 | 协议 | 端口 | 来源 |
| --- | --- | --- | --- |
| SSH管理 | TCP | 22 | 管理者公网IP/32；按需保留Lightsail浏览器SSH入口 |
| ACME证书签发/续期 | TCP | 80 | 0.0.0.0/0 |
| HTTPS订阅与Xray | TCP | 443 | 0.0.0.0/0 |
| Hysteria2 | UDP | 8443 | 0.0.0.0/0 |

当前节点使用IPv4，不要求IPv6规则。不要开放回环10000、18080、2019或客户端控制端口。UDP443仅用于Caddy可选HTTP/3，不是本订阅必需端口。新实例UFW未启用、nftables规则为空；本次未启用额外主机防火墙。云防火墙操作见[AWS官方说明](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-editing-firewall-rules.html)。

Xray状态为/etc/xray-mihomo-deployer/deploy.env，双协议状态为/etc/cohost-proxy/state.json。三域名分别用于Xray身份、HY身份和订阅；节点固定IPv4，保留SNI、Host和证书校验。不要用空白模板覆盖已有状态。

客户端URL保存在本机build/subscription-url.txt，完整配置为build/config.yaml。新克隆不含这两个私有文件，需从自己的受控备份恢复，或通过已验证的SSH连接执行服务器show命令，私下取得HTTPS URL并下载/导入。客户端使用规则模式，PROXY选择自动组或手选协议。

自定义含凭据的安装输入放在被忽略的private/或仓库外，用install --config <私有路径>传入。config.example.json保持空密码/token，首次安装生成独立凭据，后续复用保存值。

## 运行与维护

远程Ubuntu，当前源码目录：

~~~bash
cd /home/ubuntu/cohost-proxy-20261020
sudo python3 -B deploy.py decouple-services
sudo python3 -B deploy.py update-rules
sudo python3 -B deploy.py check
sudo python3 -B deploy.py show
~~~

decouple-services精确迁移旧HY unit，只写unit并daemon-reload，不重启HY/Caddy，重复执行无变更；已有staged部署也可先迁移再finalize。当前001已完成迁移。凭据、订阅URL、证书和ACME转发不变。迁移/规则更新失败或中断保留快照及pending，重新运行对应命令先恢复；详见[部署维护说明](cohost/README.md)。

按需执行sudo python3 -B deploy.py tune-network：TCP BBR及至少7MiB UDP收发上限。首次应用可能重启Caddy/HY，参数相同的重复调用不重启；它与依赖迁移是不同操作。

新实例本轮未执行网络调优，实查拥塞控制为cubic，UDP收发上限各4MiB。公网端口放行并通过电脑隔离客户端验收后，已执行finalize及最终check，原双协议订阅URL不变。电脑Clash Verge、手机CMFA可更新原订阅并重连，分别手选proxy-Xray和proxy-HY测试后恢复自动选择；手机还需在Wi-Fi、移动数据下各验收一次。电脑测试未修改GUI当前配置选择。

## 分流、DNS与服务独立性

当前62条规则按入口/局域网、Google服务例外、明确国内域名及GeoSite CN、已识别海外域名、GeoIP CN、最终PROXY顺序匹配。Google服务与下载域名（包括services.googleapis.cn、Google API、gstatic、gvt和Play下载CDN）在.cn/GeoSite CN之前走PROXY，DNS也优先使用经PROXY的Cloudflare/Google DoH。其他国内域名仍使用阿里/腾讯DoH；fake-ip保留域名匹配。地理数据每天尝试更新，需要按准确域名维护例外。

自动组60秒检查、4秒超时、50ms容差并要求HTTP204；PROXY没有DIRECT选项。选择变化作用于后续连接，已有长连接需重建。通用204健康不代表具体应用或长连接一直可用。

HY通过Wants弱依赖启动Caddy，保留After排序。Caddy显式停止/重启不再通过强依赖连带停止HY；已有有效证书时，HY数据路径独立。首次签发/续期仍需要Caddy从TCP80转发到HY回环18080，不能删除该链路。tune-network仍会按维护操作显式重启两服务。

Clash Verge的独立DNS覆写应关闭以使用订阅DNS；全局IPv6开关可能覆盖YAML对应字段。当前不全局屏蔽UDP443。

## Android 16 / CMFA 2.11.35

更新已有proxy-001.yaml订阅，再停止并重新启动代理，使旧连接和DNS状态重新建立。网络设置按以下基线核对：

| 设置 | 建议值与原因 |
| --- | --- |
| 自动路由系统流量 | 开启，使用VpnService接管流量 |
| DNS劫持 | 开启，使用订阅的分流DNS；系统私人DNS先设为关闭进行排查 |
| 允许应用绕过 | **关闭**，避免Google组件主动绕开VPN；与绕过私有网络是独立选项 |
| 访问控制模式 | 允许所有应用，包含Play商店、Play服务和下载管理器 |
| 绕过私有网络 | 可保留开启，局域网直连；不等于允许应用绕过VPN |
| 允许IPv6 | 关闭，与本订阅IPv4基线一致 |
| 系统代理 / Stack Mode | 初次保留现值；仅在更新订阅后仍异常时逐项对照，避免同时修改多个条件 |

移动数据下若自动选择仍高延迟，将PROXY暂时手选proxy-Xray（TCP443）对照proxy-HY（UDP8443），分别重新打开商店尝试同一个小应用。桌面测得的协议耗时不能代替手机运营商路径；短小204探测也不能证明大文件下载稳定，不根据桌面测量强制更换手机默认协议。

商店仍显示旧的“等待下载”时，先取消失败任务、强行停止Play商店并清除其缓存，重新打开；确认商店的应用下载偏好允许当前移动网络。无需先清除Google账号或Play服务全部数据。订阅无法替手机关闭应用绕过、修改访问控制或系统私人DNS。

依据：[CMFA 2.11.35 VPN实现](https://github.com/MetaCubeX/ClashMetaForAndroid/blob/v2.11.35/service/src/main/java/com/github/kr328/clash/service/TunService.kt)、[mihomo DNS策略](https://wiki.metacubex.one/config/dns/)、[CMFA Google Play问题讨论](https://github.com/MetaCubeX/ClashMetaForAndroid/discussions/813)。CMFA此版本的Android VPN MTU由客户端代码固定为9000，不能承诺订阅中的tun.mtu能够修改它。本次未添加未经手机实测的MTU、固定HY带宽或全局UDP443封锁。

## 验证与CI

本机Windows PowerShell：

~~~powershell
python -m pip install -r requirements-test.txt
python -X utf8 -B -m unittest discover -s cohost/tests -v
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180 --unavailable-node proxy-HY
python -X utf8 -B cohost/verify_client.py --config build/config.yaml --mihomo 'D:\Clash Verge\verge-mihomo.exe' --expected-ip 13.193.30.180 --unavailable-node proxy-Xray
~~~

verify_client.py还逐协议核对services.googleapis.cn、Play API、gvt1下载入口的PROXY命中，以及完整Google公开仓库XML下载。API根路径的404只作为TLS/HTTP可达证据，不能冒充已登录商店的安装成功。故障注入只改变隔离测试进程，不停止生产节点。measure.py可按需测公开匿名目标，不等于浏览器整页或真实应用长连接测试。

2026-10-18 Android修复的本机25项回归通过，新增测试先复现Google规则/DNS例外缺失，再由修复通过；隔离客户端的国内分流、双协议出口、Google规则命中及完整公开下载通过。001发布后，用户按更新订阅、关闭应用绕过并重连的步骤进行手机移动数据复测，确认Google Play已可以正常下载/更新。此结果为手机端反馈；尚无修复后的手机延迟数值，不据此宣称延迟已经降低。

2026-10-16历史验收：Windows与Ubuntu共23项回归通过；Linux固定版本mihomo接受虚构配置并拒绝故意损坏配置。001实际停止/重启Caddy时HY PID不变，停机期间HY TCP/UDP通过；恢复后服务、可信订阅、错误token404及外部双协议出口、DNS和分流通过。迁移未改10份私有配置及证书内容。没有执行生产证书重签或整机重启演练。

CI在Ubuntu24.04上运行Python3.10/3.13单元测试，以及固定版本/下载摘要的mihomo虚构配置校验；无生产凭据、制品上传或自动部署。真实运行结论以[GitHub Actions](https://github.com/BugYuan64/Node/actions)中对应提交为准。

## 凭据与历史

源码只分发程序、虚构测试凭据、空输入模板、CI和必要文档。build/、private/、密钥及运行状态不提交；git add -f或复制到其他目录仍可绕过忽略。Git忽略不提供加密或磁盘访问控制，完整目录备份也包含私有文件。

10月16日按授权从Git历史移除曾提交的build/config.yaml和build/subscription-url.txt，并清理独立分析报告及临时资料。已分发的旧克隆、GitHub缓存或第三方副本不由分支历史重写自动撤回；旧克隆应重新克隆，避免把旧历史推回。线上凭据未轮换，历史清理不使旧凭据失效。服务器有效证书和私有状态保留；10月16日整套基线及10月18日更新前回退包分别保留，位置及历史清理清单见[001部署文件目录](001部署文件目录.md)。
