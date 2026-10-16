# 部署与维护

当前001的Xray、Hysteria2与Caddy共享完整HTTPS订阅。项目结构、客户端用法和验收见[根README](../README.md)。

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

001已finalize，不重新install；修改域名、凭据或端口需要独立迁移。

## 依赖迁移与维护

将源码上传到/home/ubuntu/cohost-proxy-20261015，保留有效状态及证书。旧版本首次升级时先执行依赖迁移，再运行严格check；旧staged也可迁移后finalize。

~~~bash
cd /home/ubuntu/cohost-proxy-20261015
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

依赖迁移恢复仅还原unit并daemon-reload，不停止数据进程。001本轮源码/unit回退包为/var/backups/cohost-proxy/pre-decouple-20261016.tar.gz（0600）；逐事务unit快照也保留。需要撤回时同时还原配套管理源码及unit，再daemon-reload并用对应版本check，避免仅恢复unit造成渲染漂移。

既有完整恢复包current.tar.gz及其他有效快照继续保留；可能含二进制、证书、密码及令牌，不进入Git或公开日志。尚未演练整机恢复或重启持久性；Git源码和本机YAML都不能替代这些私有资料。

## 客户端及私有资料

本机build/subscription-url.txt和build/config.yaml全部由Git忽略。新克隆从私有备份或已验证SSH上的show恢复URL，通过可信HTTPS订阅；不从旧提交分发凭据。GUI使用规则模式，关闭独立DNS覆写；全局IPv6开关可能覆盖订阅值。

verify_client.py在隔离进程中检查DNS、规则、双协议出口；--unavailable-node只改变测试端点。measure.py以匿名curl按需测公开目标；没有新增后台监控。服务器日常运行需要deploy.py、render.py、verify.py及配置模板；客户端验证工具与测试保留在源码仓库供验收和CI使用。

历史清理不使旧凭据失效，也不撤回旧克隆或缓存。线上凭据本轮未轮换；不要把旧历史重新推回。Git忽略不等于加密，URL、YAML、私有输入与恢复包都应受控保存。
