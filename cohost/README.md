# 部署与维护

2026-10-25 当前代理位于 `35.73.46.1`，使用 `proxy.wsnbb.shop`、`hy.wsnbb.shop`、`sub.wsnbb.shop`，由 Docker Compose 运行。维护入口为 `/home/ubuntu/cohost-proxy-20261020/containers/`，具体容器和回退命令见 [Docker 说明](containers/README.md)。

```bash
cd /home/ubuntu/cohost-proxy-20261020/containers
sudo docker compose ps
sudo python3 -B manage.py check
sudo python3 -B ../deploy.py show   # 输出包含私有 URL，不公开粘贴
```

## 配置契约

`/etc/xray-mihomo-deployer/deploy.env` 是现用输入，名称沿用原部署器。`SERVER_IP` 是可选连接覆盖字段；2026-10-28 当前部署已移除此字段，节点连接地址采用域名；`PROXY_DOMAIN`、`SUB_DOMAIN` 与双协议状态中的 `hy_domain` 各自负责 TLS 身份和路由；可选 `ROOT_DOMAIN` 仅提供有效 HTTPS 下的404。三协议/订阅域名必须不同，根域也必须独立。

`/etc/cohost-proxy/state.json` 保存 final 阶段、HY 密码、订阅 token 和 Xray 状态摘要。state/deploy.env 均0600；Caddy 片段和订阅保持 root:caddy0640。不要把模板覆盖到这些有效文件。示例域名采用 example.com，测试采用文档专用 IPv4，不硬编码实际服务器或原域名。

渲染器保留 SNI、WS Host 和 `skip-cert-verify: false`。当前采用渲染器已有的无 SERVER_IP 模式，代理端点通过 proxy-server-nameserver 解析，三个基础设施域名走 DIRECT；HY 域名仍须仅 DNS，不能使用普通 HTTP CDN。更换 IP 后更新权威 DNS 并清理相关缓存，不必再修改客户端节点 IP。

## 域名迁移

本轮按以下顺序完成指定服务器迁移，属于一次性运维，不是通用空白安装入口：

1. 使用已验证 SSH 身份检查运行状态，三个子域名 DNS-only 指向当前公网 IP，排除无效 AAAA。
2. 原版本完整 check 通过，归档源码、配置、私有状态和证书并逐文件核验，保存0600回退包。
3. 持有部署锁，核对源码摘要与私有状态摘要，先验证候选 mihomo 配置，再保存带原权限/属主的配置和源码事务快照。
4. 原子更新域名、SERVER_IP、状态摘要、HY/Caddy 配置和订阅；验证 Caddy，再重启 Caddy/HY 签发新证书。Xray 配置、认证值、Compose 镜像和其他 Caddy 路由保留。
5. 服务端检查通过后结束事务；失败时恢复原配置和源码，重启对应容器并检查。未完成标记和其引用备份必须保留。
6. 公网可信订阅、错误 token 和外部隔离双协议/故障切换验证结果记录在根阶段文档。

正式程序中已移除原域名专用 IP 提取逻辑。旧域名仅可能存在于私有回退包、旧证书及 Git 历史，均不是生效路由；不为清理代码破坏恢复资料。

## systemd 工具边界

`deploy.py` 支持已有 Ubuntu24.04 x86_64/aarch64 的 Xray/Caddy 安装，不能用作空白主机安装器。Docker 标记存在时，除 show 外拒绝旧维护命令。若确需使用 install/finalize/update-rules/tune-network/decouple-services，先按 Docker 文档回退，确认 systemd 完整健康后执行；不要手动删除 Docker 标记绕过保护。

旧工具使用 `/run/lock/cohost-proxy.lock`、`/etc/cohost-proxy/pending.json` 与 `/var/backups/cohost-proxy/` 保持事务。对应维护命令先恢复未完成事务，不手工清除 pending。tune-network 会显式重启服务，与只修改弱依赖 unit 的 decouple-services 不同。

恢复包含真实秘密，不入 Git；普通容器回退继续使用当前证书，不解压旧证书覆盖续期结果。跨机恢复或状态损坏时，先核对归档摘要、内容、权限、属主和路径，再制定恢复步骤。目录清单见 [001部署文件目录](../001部署文件目录.md)。
