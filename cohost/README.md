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

## 出口纯净度审计

从项目根目录对生成 mihomo profile 执行离线审计。以下 `private/` 路径只是受控本地输入示例，其中报告可省略：

```sh
python -B cohost/egress_audit.py \
  --profile private/mihomo.yaml \
  --report private/egress-report.json
python -B cohost/egress_audit.py --profile private/mihomo.yaml --json
```

工具使用项目本地检查已安装的 PyYAML 读取完整 profile，不访问网络。无报告时仍检查 profile；报告缺少 `categories` 等字段时给出警告而不是伪造结论。

项目可控护栏包括：根级和 `dns` 的 IPv6 必须关闭；`dns.nameserver` 非空、每项使用精确的 `#PROXY` 且避开 `8.8.8.8`/`8.8.4.4`/`dns.google` 等易跨区归属或带 ECS 的公共解析器；`dns.proxy-server-nameserver` 与 `dns.direct-nameserver` 非空且不使用 `#PROXY`；最后一条规则为 `MATCH,PROXY`；`PROXY` 组及其子组不可达 `DIRECT`；至少存在两个具名代理节点和预期的 `自动选择`、`PROXY` 分组。代码不接受任意 `#<group>` 别名，避免未经验证的分组路径掩盖直连。

报告中带 `pts` 的问题项按 ID、标签及分类归因为三类，并分别汇总原始分类权重；该合计不等于报告最终得分，也不能证明问题由当前 profile 引起。

- `project`：DNS、IPv6、分流/路由类配置问题；只有报告问题确实来自本 profile 时，重新渲染并正确加载才可能缓解。
- `client`：时区、语言、Turnstile、WebRTC 本机候选等浏览器/系统环境问题。
- `egress`：机房属性、ASN 机器流量、风险/黑名单、注册地/广播定位等出口自身问题。

退出码为：配置护栏通过 `0`；输入或解析错误 `1`；项目可控护栏失败 `2`；配合 `--fail-on-external` 且报告存在客户端/出口问题时 `3`。外部问题默认不使命令失败。

该审计只能减少配置造成的 DNS、IPv6 和分流泄漏，不能改变 IP 类型、ASN 信誉、风险记录、广播定位、Turnstile、浏览器时区/语言或 WebRTC 本机地址，也不声明评分提升。客户端覆写 DNS、端口或 IPv6 后仍需重新验证实际行为。

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

存在 Docker 管理标记时，`deploy.py` 除 `show` 外拒绝上述旧模式命令。Compose 模式下的订阅规则更新使用 `containers/manage.py update-rules`；网络调参或二进制升级需先按容器 README 回退并确认原服务健康，不能删除标记绕过保护。

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
