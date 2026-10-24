# 部署与维护

2026-10-25已同步HY拨号兼容修复：`render.py`为HY节点生成`dialer-proxy: DIRECT`，只控制连接HY服务器的拨号路径，业务流量仍通过HY；不绑定固定网卡。原URL、认证、服务配置和证书保持。

本轮为遵守用户手测要求，采用一次性受控发布：核对旧源码摘要及配置漂移，复用部署器快照、原子写入、配置校验和可信订阅检查，不调用会启动代理探测的完整`check`/`update-rules`。26项离线回归通过，服务PID保持、无pending；此次发布后的业务验收由用户完成。日常完整检查仍会运行短时HY探测。

Clash Verge托管内核应通过客户端内置流程更新/重启。此次直接调用内核`/restart`曾留下服务管理器无法接管的进程并造成端口冲突；已清理孤立进程并重启系统服务恢复，不把该问题归因于版本升级。

当前001的Xray、Hysteria2与Caddy共享完整HTTPS订阅。2026-10-20公网IP 13.193.30.180重新绑定新实例后，已恢复三服务、原URL/认证并重签三个域名证书；源码为`/home/ubuntu/cohost-proxy-20261020`。云防火墙放行后，可信公网订阅、电脑隔离mihomo DNS/分流、双协议出口、Google完整下载和双向自动切换通过；已finalize，最终check通过，无pending，三服务active/enabled。手机Wi-Fi/移动数据仍待用户实测。项目结构、客户端用法和验收见[根README](https://github.com/BugYuan64/Node/blob/main/README.md)。

## 条件与安装

需要Ubuntu24.04 x86_64/aarch64、root/sudo、Python3、systemd、curl、useradd及已有Xray/Caddy；不是空白VPS安装器。Xray配置为/usr/local/etc/xray/config.json，状态为/etc/xray-mihomo-deployer/deploy.env；Caddy主配置导入/etc/caddy/Caddyfile.d/*。

config.example.json只保留空密码/token模板。自定义输入用仓库外文件或根目录被忽略的private/cohost.json，通过--config传入。首次空值生成独立凭据，重试复用保存值；不能覆盖现有私有状态。

~~~bash
sudo python3 -B deploy.py install --config <私有输入路径>
sudo python3 -B deploy.py check
sudo python3 -B deploy.py show
# 新URL在外部客户端验收成功后
sudo python3 -B deploy.py finalize
~~~

新实例已在外部电脑验收后finalize，不重新install，日常使用check/show。修改域名、凭据或端口需要独立迁移。

## 依赖迁移与维护

将源码上传到/home/ubuntu/cohost-proxy-20261020，保留有效状态及证书。旧版本首次升级时先执行依赖迁移，再运行严格check；旧staged也可迁移后finalize。

~~~bash
cd /home/ubuntu/cohost-proxy-20261020
sudo python3 -B deploy.py decouple-services
sudo python3 -B deploy.py update-rules
sudo python3 -B deploy.py check
sudo python3 -B deploy.py show
~~~

decouple-services只接受精确已知旧unit/新unit，保留当前phase、订阅、凭据及证书。保存root私有快照后原子写unit并daemon-reload，不重启HY或Caddy；实际依赖中仍有Caddy强关联会失败并回退。重复运行无变更。

HY使用Wants弱依赖及After顺序；有效证书下，Caddy显式停/重启不再连带停止HY。首次签发/续期仍需TCP80的HTTP-01转发到127.0.0.1:18080，不能停用该链路。全套check仍要求Caddy、Xray和订阅健康。

update-rules先校验非订阅配置和候选YAML，再快照/原子发布原URL，维持Caddy片段root:caddy0640；失败恢复文件与权限。

tune-network将UDP收发上限提升到至少7340032字节并启用TCP BBR，保留较大现值、现有网卡队列及HY自适应参数。保存持久文件和运行值，首次变更显式重启Caddy/HY；相同参数重复调用不重启。它不是依赖迁移的必要步骤。

## 恢复

状态和pending在/etc/cohost-proxy；事务快照在/var/backups/cohost-proxy。重新执行对应install、finalize、update-rules、tune-network或decouple-services会先恢复未完成事务。不要手工删除pending及其备份；恢复失败会保留pending。

新实例最终恢复快照为`/var/backups/cohost-proxy/current-20261020-final.tar.gz`及`.sha256`，包含本轮final部署源码、程序、配置、认证状态和新证书。`current-20261020-staged.tar.gz`及`.sha256`保留为finalize前回退点，不能将staged归档称为最终状态。下面10月16日、18日路径为原实例历史记录，不表示这些旧备份已迁移到新实例。

依赖迁移中断恢复仅还原unit并daemon-reload，不停止数据进程。001在10月16日清理旧版资料后保留/var/backups/cohost-proxy/current.tar.gz及current.tar.gz.sha256（均0600）作为当时的整套部署基线；快照含源码、二进制、服务配置、私有状态、证书与内部校验清单。需要恢复时先校验摘要并检查内部清单，协调服务窗口后恢复配套文件、属主权限和服务依赖，避免仅恢复unit造成渲染漂移。10月18日新增的变更前备份及发布记录见下文，不删除本次事务快照。

本次在无pending且当前快照逐文件内容校验成功后，删除已完成事务的历史快照及旧版回退包。以后产生的新pending及其引用备份仍必须保留到对应事务恢复/完成；不要套用本次清单盲目清理。当前快照含证书、密码及令牌，不进入Git或公开日志。尚未演练独立机器恢复或重启持久性；Git源码和本机YAML不能替代私有快照及系统环境准备。完整目录见[001部署文件目录](https://github.com/BugYuan64/Node/blob/main/001部署文件目录.md)。

## 客户端及私有资料

本机build/subscription-url.txt和build/config.yaml全部由Git忽略。新克隆从私有备份或已验证SSH上的show恢复URL，通过可信HTTPS订阅；不从旧提交分发凭据。GUI使用规则模式，关闭独立DNS覆写；全局IPv6开关可能覆盖订阅值。

2026-10-18订阅增加Google服务/下载域名的代理和DNS例外，优先于.cn及GeoSite CN；原URL、两协议和国内直连保留。CMFA手机需更新订阅、关闭“允许应用绕过”并重连，其他设置及移动网络对照步骤见根README的Android章节。手机本地VpnService设置不能由服务器订阅自动修改。

本次变更前回退包为/var/backups/cohost-proxy/android-before-20261018T052314Z.tar.gz及.sha256，含旧渲染器、验收工具、部署README、订阅和Caddy片段，已逐文件核验且仅root可读；android-update-20261018T052314Z.json记录更新摘要与不变性检查。原current.tar.gz仍是10月16日的整套恢复基线，未包含本次Google规则，恢复后需重新同步当前源码并update-rules。

verify_client.py在隔离进程中检查DNS、规则、双协议出口，以及Google服务代理命中和完整公开XML下载；--unavailable-node只改变测试端点。公开下载测试不等同于手机登录后的Play安装验收。measure.py以匿名curl按需测公开目标；没有新增后台监控。服务器日常运行需要deploy.py、render.py、verify.py及配置模板；客户端验证工具与测试保留在源码仓库供验收和CI使用。

历史清理不使旧凭据失效，也不撤回旧克隆或缓存。线上凭据本轮未轮换；不要把旧历史重新推回。Git忽略不等于加密，URL、YAML、私有输入与恢复包都应受控保存。
