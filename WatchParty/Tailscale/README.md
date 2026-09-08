# Tailscale 远程联机

这个目录把 Tailscale 接入随包 AList 与 mpv Syncplay 面板。Tailscale 只负责
建立加密网络；视频仍由 AList 提供，播放同步仍由现有 Syncplay 客户端完成。
集成脚本不会保存账号、密码、OAuth 信息或 auth key。

## 房主首次设置

1. 运行 `install-tailscale.bat`。脚本会先校验下载包的 SHA-256 与
   Authenticode 签名，再显示官方安装界面。
2. 在 Tailscale 托盘程序中登录。不要把自己的账号密码交给观看者。
3. 运行 `configure-host.bat`。脚本会读取本机完整 MagicDNS 名称，执行
   `tailscale serve --bg 5244`，并把面板的 `alist_server` 更新为
   `https://主机名.tailnet.ts.net`。
4. 将生成的 `connection.json` 中的 `tailscale_host` 或 `alist_server` 发给
   观看者。该文件不含登录凭据。
5. 重启 mpv，在 Syncplay 面板中正常进入房间。

Tailscale Serve 只在 tailnet 内代理 `http://127.0.0.1:5244`。不要启用
Tailscale Funnel；Funnel 会把站点公开到整个互联网。

## 观看者首次设置

1. 在自己的电脑安装 Tailscale，并用自己的账号登录。
2. 接受房主发送的设备共享邀请。推荐房主在 Tailscale 管理后台使用
   `Machines -> 房主电脑 -> Share`，不要多人共用一个账号。
3. 使用同一套 mpv 项目，运行 `configure-viewer.bat`。
4. 输入房主提供的完整 `.ts.net` 名称或完整 `https://...` 地址。
5. 重启 mpv，进入与房主相同的 Syncplay 服务器和房间。

完整 `.ts.net` 名称比固定 `100.x` 地址更适合共享设备。Tailscale 在不同
tailnet 间共享机器时，接收方看到的 `100.x` 地址可能不同；完整 MagicDNS
名称仍可保持客户端所需的严格媒体来源校验。

## mpv 面板

按 `Ctrl+Shift+S` 打开面板，再选择左侧 `Tailscale`：

- `安装 Tailscale`：启动本目录中已校验的官方 MSI；
- `打开 / 登录 Tailscale`：打开托盘登录程序；
- `房主：启用安全共享`：配置 Tailscale Serve 和房主媒体地址；
- 观看者可在右侧输入框粘贴房主完整地址并提交；
- `刷新状态`：重新读取安装、登录、IP 和 MagicDNS 状态。

更改媒体地址时，已运行的 Syncplay 客户端会先断开。配置完成后重新选择
`播放控制 -> 加入 / 连接房间`。

## 安全边界

- Tailscale 不会绕过 AList 的应用层权限。`/media` 仍需允许匿名只读访问，
  并保持全局签名、存储签名和元信息密码关闭。
- 只把 `WatchParty\media` 暴露给 guest，不要挂载整个磁盘。
- Tailscale Serve 会代理 AList 的网站入口；管理员密码必须保持强且唯一。
- 推荐只共享房主设备，并把访问策略限制到该设备的 TCP 443。
- 观影结束后可在 Tailscale 管理后台撤销设备共享。

## 排查

运行 `status.bat` 可查看本机状态。房主配置成功后，应看到：

```text
状态：Running
完整名称：主机名.tailnet.ts.net
媒体地址：https://主机名.tailnet.ts.net
```

观看者最终仍需用项目中的 `[AList Media Test]` 验证实际视频地址返回
`HTTP 206` 和有效 `Content-Range`。若 Tailscale 显示 `relay`，视频可能经
DERP 中继而速度较慢；这不是 Syncplay 同步故障。
