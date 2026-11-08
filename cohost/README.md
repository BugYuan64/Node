# 配置与维护

本目录提供双协议配置渲染、systemd 事务维护和验证工具。Compose 管理入口位于 [containers](containers/README.md)。服务端命令需要已有 Xray/Caddy 安装、Linux root 权限和完整私有状态；以下说明不代表空白主机可直接运行。

## 配置契约

`deploy.py` 读取已有 `/etc/xray-mihomo-deployer/deploy.env`，渲染器要求以下字段：

| 字段 | 含义 |
| --- | --- |
| `PROXY_DOMAIN`、`SUB_DOMAIN` | Xray 入口与订阅域名，必须不同 |
| `NODE_NAME`、`SUB_FILENAME` | 原节点名称和旧订阅文件名 |
| `XRAY_PORT` | Xray 回环监听端口 |
| `VLESS_UUID` | Xray 认证 UUID，只存放于私有输入 |
| `SUB_TOKEN`、`WS_TOKEN` | 旧订阅与 WebSocket 路由 token，只存放于私有输入 |
| `ROOT_DOMAIN` | 可选的独立根域，提供 HTTPS 404 |
| `SERVER_IP` | 可选的连接覆盖；使用域名连接时不填写 |

`config.example.json` 定义新 Hysteria2/双协议订阅参数：`hy_domain`、`hy_port`、`hy_password`、`sub_token`、`sub_filename`、`hy_node`、`probe_url`、`probe_interval`、`probe_tolerance`。示例凭据为空；首次安装时为空的认证值随机生成，已有部署则复用已保存的值。已有配置不一致时拒绝隐式轮换，域名、凭据及路由变更需要单独迁移。

Hysteria2、Xray、订阅三个域名互不相同，可选根域也必须独立。新订阅 token 和文件名必须不同于旧路由，Hysteria2 密码与新订阅 token 也不能相同。填写自己的域名和非秘密参数后，将输入留在受控位置；不要用模板覆盖运行状态。

默认采用域名连接，并保留 SNI、WebSocket Host 和 `skip-cert-verify: false`。只有显式提供 `SERVER_IP` 时才生成 IP 覆盖和相应直连规则。Hysteria2 的 DIRECT 拨号用于连接入口，不固定物理网卡。

## systemd 部署阶段

从项目根目录调用，`private/config.json` 为自行准备的受控输入：

```sh
sudo python3 -B cohost/deploy.py install --config private/config.json
sudo python3 -B cohost/deploy.py check
# 完成外部客户端验证后再结束 staged 阶段
sudo python3 -B cohost/deploy.py finalize
```

安装流程校验已有 Xray/Caddy 输入、下载并校验固定版本的 Hysteria2/mihomo、渲染候选配置，再以事务方式发布。`staged` 阶段保留旧订阅路由用于切换；`finalize` 完成后移除旧路由并再次检查。

其他命令：

| 命令 | 用途 |
| --- | --- |
| `check` | 校验私有状态、渲染结果、服务依赖、Hysteria2 TCP/UDP/错误认证和订阅 |
| `show` | 在受控终端输出当前订阅地址，输出含秘密 |
| `update-rules` | 检查现有状态后事务更新订阅规则 |
| `decouple-services` | 调整 Hysteria2/Caddy 的 systemd 依赖关系 |
| `tune-network` | 显式调整并持久化 TCP/UDP 参数，会重启相关服务 |

存在 Docker 管理标记时，`deploy.py` 除 `show` 外拒绝上述旧模式命令。规则变更、网络调参或二进制升级需先按容器 README 回退并确认原服务健康，不能删除标记绕过保护。

## 私有状态与事务

下列是程序定义的标准路径，不是某台机器的部署清单：

- `/etc/cohost-proxy/state.json`：阶段、Hysteria2/订阅参数和 Xray 输入摘要。
- `/etc/cohost-proxy/pending.json`：未完成事务及其恢复依据。
- `/run/lock/cohost-proxy.lock`：维护互斥锁。
- `/var/backups/cohost-proxy/`：受控事务快照和回退资料。

私有输入和状态使用 0600，订阅与 Caddy token 片段使用受限属组权限。程序在持锁后保存变更前状态，验证候选、原子写入并检查；失败时恢复。未完成标记及其引用的备份必须保留，不手工删除。

备份可能包含真实认证和证书，不能提交 Git。普通容器回退复用现用证书；跨机或文件损坏恢复需先核对备份摘要、文件内容、权限、属主和目标路径，再实施受控恢复。

## 本地与客户端验证

本地运行方式、项目发布范围和验证边界见 [根 README](../README.md)。`verify.py` 供部署器调用；`verify_client.py` 使用临时 mihomo 验证 DNS、分流、双协议出口、完整下载与故障切换。`measure.py` 提供按需测量，结果默认写入本地 `build/`。

恢复资料、部署现场信息、原始日志和审核记录留在私有位置，不附带到项目发布中。
