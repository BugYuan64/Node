# 当前双协议部署程序

001的Xray + Hysteria2 + Caddy共享一条完整HTTPS订阅。功能、验证方法和当前验收边界见[项目README](../README.md)。

## 条件与首次迁移

Ubuntu24.04 x86_64/aarch64，root/sudo、Python3、systemd、curl、useradd，且已有Xray/Caddy。
Xray配置为 /usr/local/etc/xray/config.json，有效状态为 /etc/xray-mihomo-deployer/deploy.env；
Caddy主配置导入 /etc/caddy/Caddyfile.d/*。不是空白VPS安装器。

模板是config.example.json。首次空密码/token会生成独立凭据，后续复用已保存值。
代理、HY、订阅域名需指向VPS，公网TCP80/443、UDP8443必须可用。

~~~bash
sudo python3 -B deploy.py install
sudo python3 -B deploy.py check
sudo python3 -B deploy.py show
# 新URL在外部客户端的双节点验证成功后
sudo python3 -B deploy.py finalize
~~~

001已finalize，不能重新install；修改域名、凭据或端口需要独立迁移，不能覆盖私有状态。

## 维护发布与调参

先将本目录源码上传至 /home/ubuntu/cohost-proxy-20261015，保留其私有运行状态。

~~~bash
cd /home/ubuntu/cohost-proxy-20261015
sudo python3 -B deploy.py update-rules
sudo python3 -B deploy.py tune-network
sudo python3 -B deploy.py check
sudo python3 -B deploy.py show
~~~

update-rules 校验非订阅内容未变化、mihomo解析成功，快照订阅和Caddy snippet后原子发布；同时将旧snippet权限迁移为root:caddy0640。原URL和节点密码不变，失败恢复文件及权限，不需要更换订阅。

tune-network 先检查已有部署，尝试加载内核tcp_bbr，确保可用后将UDP收发上限提升到至少7340032字节并设TCP BBR，保留已有更大的上限。保存文件快照和.runtime.json运行值，写入sysctl/modules-load，重启Caddy/HY2并完整验收。首次有短暂重连；相同参数重复运行不重启。保留现有网卡qdisc及HY默认拥塞控制/窗口/MTU探测。

客户端订阅使用60秒健康检查、4秒超时、50ms容差和HTTP204，不对生产服务做故障注入。

## 恢复

状态为 /etc/cohost-proxy/state.json，pending为同目录pending.json，事务快照在 /var/backups/cohost-proxy。
执行中断后，重新运行对应install、finalize、update-rules或tune-network，会先恢复未完成事务。不要手动删除pending或其备份；恢复出错会保留它。首次安装回退先停止并禁用新HY服务，再移除unit，确认停止后才清pending。

2026-10-16优化前完整配置/源码包为pre-optimize-20261016.tar.gz；它不含二进制，原current.tar.gz仍保留二进制/证书等完整恢复资料。尚未做整机恢复演练。

如需人工撤回本次已完成优化，先安排维护窗口并核对备份存在。恢复顺序是：停止HY → 从优化前包恢复原源码/配置 → 恢复网络文件及保存的运行值 → daemon-reload → 重启Caddy/HY → 服务器check及外部客户端验收。不要只恢复YAML而保留不一致的渲染器。

本次优化前内核值为cubic、rmem_max/wmem_max各4194304；调参快照.runtime.json也保存这些值。sysctl新增文件为 /etc/sysctl.d/80-cohost-proxy.conf，模块加载文件为 /etc/modules-load.d/cohost-proxy.conf。人工恢复持久配置后还必须恢复运行值，否则等到重启才改变。

check要求token配置root:caddy0640。恢复旧包后仍应限制该权限；源码和恢复包都可能包含凭据，仅root可读。mihomo地理缓存位于 /var/cache/cohost-proxy/mihomo。

## 客户端与复测

URL见本机build/subscription-url.txt，或服务器show输出。使用规则模式，PROXY选择自动组或单协议；当前不包含DIRECT。Clash Verge的独立DNS覆写需关闭，使用订阅DNS；全局IPv6开关可能覆盖订阅对应字段。

verify_client.py在临时进程中验证DNS、DIRECT命中、双协议出口；--unavailable-node模拟单端点失败，强制健康检查并验证存活节点出口。measure.py用匿名curl测公开HTTPS，不使用登录Cookie，也不等于浏览器HTTP/3测试。
