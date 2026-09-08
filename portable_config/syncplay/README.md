# mpv 内置 Syncplay（精简无聊天）

这个目录里的 `mpv_syncplay.py` 是一个纯 Python 标准库客户端，不需要另外安装 Syncplay，也不会启用聊天功能。它通过 mpv 的本地 IPC 管道读取/控制播放状态，协议与官方 Syncplay 服务器兼容。

## 从 GitHub 下载后开始两人观看

请从 GitHub Release 的 `Assets` 下载完整包，不要下载自动生成的
`Source code (zip)`：

- 房主下载并完整解压 `MPV-Syncplay-Host.zip`；
- 观看者下载并完整解压 `MPV-Syncplay-Viewer.zip`。

房主先双击 `WatchParty\房主首次运行.bat`，允许一次 Windows 管理员权限，
再按提示安装并登录 Tailscale。脚本会自动建立仅允许 Tailscale 网段访问
5244 的防火墙规则；向导会配置随包 AList、验证匿名 Range 播放，并显示形如
`http://100.x.x.x:5244` 形式的观看者地址。然后在 Tailscale 管理后台进入
`Machines`，对房主电脑选择 `Share`，把设备共享邀请和该地址发给观看者。

观看者接受邀请后双击 `观看者首次运行.bat`，使用自己的 Tailscale 账号
登录，并确认房主的完整 `http://100.x.x.x:5244` 地址。观看者向导诊断通过
后，日常只需运行 `启动观看.bat`；房主日常运行 `WatchParty\启动.bat`。

两边在 mpv 中按 `Ctrl+Shift+S`，填写完全相同的 Syncplay 服务器和房间名，
再选择“加入 / 连接房间”。建议观看者先连入，房主随后打开
`WatchParty\media` 中的视频；观看者无需选择或下载视频，会在加载完成后
自动进入 READY。

这条路径使用 Tailscale Device Sharing 和房主 `100.x:5244` 直连。不要启用
Funnel，也不需要 Serve、路由器端口映射、AList 账号或房主管理员密码。
`http://100.x...` 只在 Tailscale 网络内可达，传输仍由 Tailscale 加密。

## 最简单的用法

双击 `installer\mpv-syncplay.bat`，按提示输入房间名；也可以把视频文件拖到这个批处理文件上。命令行形式为：

```text
installer\mpv-syncplay.bat "D:\Videos\movie.mkv" 房间名 昵称 syncplay.pl:8995
```

前四个参数保持不变。批处理还接受五个可选的 AList 参数：

```text
installer\mpv-syncplay.bat "视频文件" 房间 昵称 Syncplay服务器 AList服务器 本地根目录 虚拟根目录 就绪超时 HTTP预读
```

例如：

```text
installer\mpv-syncplay.bat "D:\Media\movie.mkv" 电影 小明 syncplay.pl:8995 http://192.168.1.10:5244 "D:\Media" /media 30 20
```

第 5 个参数为空时不会启用 AList。只提供第 5 个参数时，本地根目录默认为
项目下的 `WatchParty\media`，虚拟根目录默认为 `/media`，就绪超时和 HTTP
预读分别默认为 30 秒、20 秒。

仓库内的 `portable_config\mpv.conf` 已启用 `\\.\pipe\mpvpipe`，所以也可以先正常启动 mpv，再在项目根目录执行：

```text
python.exe portable_config\syncplay\mpv_syncplay.py --room 房间名 --name 昵称
```

## 本机中继服务器（可选）

不想使用公共服务器时，可以在一台电脑上启动精简中继：

```text
python.exe portable_config\syncplay\mpv_syncplay.py --host --port 8995
```

其他客户端使用 `--server 电脑IP:8995` 连接。中继同样不保存聊天或播放列表。

## 已实现的同步行为

- 播放/暂停联动
- 手动跳转联动
- 超前时自动减速或回退
- 网络延迟位置补偿
- 断线自动重连

`--virtual` 仅用于开发测试，不需要启动 mpv；`--verbose` 可输出原始协议报文，排查服务器兼容性时使用。

## 内置简洁控制面板

项目已经内置一个不带聊天功能的 Syncplay 风格面板，使用 mpv 自带的 Lua
脚本和随项目提供的 uosc 菜单，不需要安装 Tk、Qt 或其他依赖。启动 mpv
后按 `Ctrl+y`（或 `Ctrl+Shift+S`）即可打开面板。打开后，点击视频空白处或按
`Esc` 都不会关闭；请使用左侧的“关闭面板”选项。面板可以：

- 查看连接状态、服务器、房间、昵称、媒体位置、延迟和房间成员；
- 立即同步到房间位置、暂停/继续、重连和断开；
- 在面板内编辑房间、昵称和服务器地址。

面板采用左右二级栏：左侧分类会一直保留，选择“概览”“播放控制”或各项
设置时，对应内容会在右侧展开。状态自动刷新时也会停留在当前右栏。

如果先用 `installer\mpv-syncplay.bat` 启动客户端，面板会自动读取它的状态，
不需要再次启动客户端。默认状态/控制文件位于本目录的
`syncplay_status.json` 和 `syncplay_command.json`，仅用于 mpv 与内置客户端
之间的本机通信。

默认配置使用 `\\.\pipe\mpvpipe`，适合单个 mpv 实例。若同时运行多个 mpv，
请为每个实例使用不同的 `--input-ipc-server` 管道，以及不同的
`--control-file`/`--status-file` 路径，避免实例互相读取状态。

面板的路径和管道也可以通过 `portable_config\script-opts\syncplay_ui.conf`
设置；`pipe` 的值要与该 mpv 实例的 `--input-ipc-server` 保持一致。例如：

```text
pipe=\\.\pipe\mpvpipe-2
command_file=D:\\Temp\\syncplay-command-2.json
status_file=D:\\Temp\\syncplay-status-2.json
```

## AList 媒体共享（可选）

AList 功能在代码默认值中关闭，不会改变原来的本地文件同步行为；本机当前
部署配置已显式开启。它只负责把映射目录中的本地路径转换成房间成员都能
访问的 AList `/d/` 地址，不读取 AList
账号、密码或管理接口。启用前请确保 AList 地址能从其他成员的电脑访问，
并使用会在握手中声明 `isolateRooms=true` 的 Syncplay 服务端；公共
`syncplay.pl` 和本项目 MiniServer 均满足此条件。未声明房间隔离时，客户端
只保留普通播放同步，不会发布或自动加载媒体 URL。

### 运行前提

如果 AList 只监听 `127.0.0.1`，或 `/media` 存储启用了签名，裸
`http://AList地址:5244/d/media/...` 就无法供其他观看者直接播放。正式共享
前需要：

- 如果尚未启动随包 AList，可在项目根目录运行
  `WatchParty\alist\alist.exe server --force-bin-dir`；
- 把 `alist_server` 改为所有观看者都能访问的局域网地址、反向代理地址或
  公网地址，并相应配置 AList 的监听或反向代理；
- 在 AList 管理端为对外公开的 `/media` 存储关闭签名；
- 从另一位观看者的设备直接打开生成的 `/d/media/...` 链接，确认无需登录
  即可播放后再进入房间；
- 不要依赖必须携带 Cookie、Referer 或私密自定义 Header 的防盗链配置；
  公开地址还需要支持 HTTP Range，否则 mpv 的拖动和续播可能失败。

本实现不会读取、保存或发送管理员 Token，也不会调用 AList 的
`/api/fs/get` 接口，所以不能替已开启签名的存储生成临时签名链接。公开
媒体 URL 建议配合本项目的精简中继（MiniServer）和隔离的私有房间使用，
不要把含私密媒体的 URL 发到公共房间。

用内置面板启动客户端时，编辑
`portable_config\script-opts\syncplay_ui.conf`：

```text
alist_enabled=yes
alist_server=http://192.168.1.10:5244
alist_root=~~/../WatchParty/media
alist_virtual_root=/media
alist_map=
media_ready_timeout=30
http_readahead=20
```

`alist_root`（命令行别名 `--alist-local-root`）和 `alist_virtual_root`
组成一条路径映射。`~~` 代表
`portable_config` 目录，因此上例会把项目中的 `WatchParty\media` 映射到
AList 的 `/media`。需要多个目录时，可在 `alist_map` 中用分号分隔，例如：

```text
alist_map=D:\\Movies=/movies;E:\\Shows=/shows
```

直接运行客户端时，`--alist-map` 可以重复使用：

```text
python.exe portable_config\syncplay\mpv_syncplay.py --room 房间 --name 昵称 ^
  --alist-enabled --alist-server http://192.168.1.10:5244 ^
  --alist-map "D:\Movies=/movies" --alist-map "E:\Shows=/shows" ^
  --media-ready-timeout 30 --http-readahead 20
```

### URL 映射确认

路径映射只生成 AList 的标准公开直链。以上默认映射会把：

```text
D:\MPV-Duke-Enhanced-Extra\WatchParty\media\xxx.mp4
```

转换为：

```text
http://AList地址:5244/d/media/xxx.mp4
```

文件名中的空格和非 ASCII 字符会进行 URL 编码。`MediaProvider` 不会在地址
后添加 `?sign=...`，也不会请求临时签名；若 AList 对裸 `/d/media/...`
地址仍返回 `expire missing`，需要在 AList 后台调整公开访问设置，而不是在
客户端填写或发送管理员凭据。

### 部署前连通性诊断

在让观看者加入房间前，先对实际媒体 URL 执行匿名检测：

```text
python.exe portable_config\syncplay\mpv_syncplay.py --alist-test-url "http://192.168.1.10:5244/d/media/xxx.mp4"
```

也可以直接运行独立诊断模块：

```text
python.exe portable_config\syncplay\alist_diagnostics.py "http://192.168.1.10:5244/d/media/xxx.mp4"
```

诊断只读取很少量响应数据，并用 `Range: bytes=0-0` 验证拖动播放所需的
Range 行为，不会下载完整视频。输出格式固定为：

```text
[AList Media Test]

URL: http://192.168.1.10:5244/d/media/xxx.mp4
Status: HTTP 状态及 AList 应用状态
Auth: 匿名访问结果，以及是否疑似依赖 Cookie 或 Referer
Range: 是否返回 206 和有效的 Content-Range
Content-Type: 服务端返回的媒体类型
Result: 是否可供 mpv 直接播放，或具体的配置错误
```

只有匿名请求能够取得媒体且 Range 请求得到 `206 Partial Content` 和有效的
`Content-Range`，才表示适合 mpv HTTP 播放。仅返回
`Accept-Ranges: bytes`、但实际 Range 请求仍返回完整的 `200`，不能视为已
支持 Range。AList 有时会用 HTTP 200 返回 JSON 错误，因此诊断也会检查少量
响应正文；例如正文为 `{"code":401,"message":"expire missing"}` 时会输出：

```text
Result: 媒体源不可访问，请检查 AList 签名或权限
```

客户端遇到这个错误时不会把远程媒体标记为就绪，只会上报 `ready=false`，
绝不会发送 `ready=true`。首次匿名诊断请求和实际远程播放都不使用管理员
浏览器的登录状态，不读取或转发登录 Cookie、Referer、管理员 Token 或私密
Header，也不调用 `/api/fs/get`。只有首次请求疑似 Referer 防盗链时，诊断器
才会额外发送一次仅含站点根地址、不含媒体路径或查询串的同源 Referer 对照
请求，用来明确报告该依赖；这个对照结果不会让地址通过共享检查。因此，已登录
的后台页面能打开文件不能证明直链可共享；修改设置后还要在无痕窗口或另一台
未登录 AList 的设备上重新运行诊断。

### AList v3.64 后台检查

随包版本使用 AList v3.64。菜单翻译可能因语言包略有不同，可按下面的层级
和相邻英文名称核对：

1. 打开 `设置 -> 全局`，关闭“签名所有”（Sign all）及同组中所有强制
   `/d/` 链接签名的选项。全局签名会覆盖单个存储的公开设置。
2. 打开 `存储 -> /media -> 编辑`，关闭“启用签名”（Enable signing）。保存
   后重新生成裸 `/d/media/xxx.mp4` 地址验证，不要把旧的 `?sign=...` 地址
   写入配置。
3. 打开 `元信息`，分别检查 `/` 和 `/media`。清除访问密码，并检查“应用到
   子文件夹”设置，确保 `/media` 没有继承上级密码或其他只允许登录用户访问
   的限制。
4. 打开 `用户 -> guest -> 编辑`，取消“停用”并检查它所属的角色；再到该角色
   的路径范围（部分语言包显示在用户编辑页）确认覆盖 `/media`。登录后的
   “基本路径”只决定默认打开位置，不等于访问授权。仅授予读取、列表和下载
   媒体所需的只读权限，不要授予上传、删除、重命名或管理权限。
5. 在 `存储 -> /media -> 编辑` 检查“Web 代理”（Web proxy）及该云盘驱动的
   Header 设置。若底层云盘直链必须携带私密 Header，应由 AList 在服务端代理
   并保管这些信息；观看者得到的 `/d/media/...` 地址本身必须无需 Cookie、
   Referer 或私密 Header。不要把底层云盘凭据放进客户端配置。
6. 若 AList 位于 Nginx、Caddy、IIS 等反向代理后，确认代理会转发请求中的
   `Range` 和 `If-Range`，保留响应中的 `206`、`Content-Range`、
   `Content-Length` 和 `Accept-Ranges`，且不会把 Range 请求改成完整文件的
   `200` 响应。
7. `127.0.0.1` 只能从 AList 所在电脑访问。将 AList 监听地址及防火墙配置为
   允许所需网络访问，并把 `alist_server` 设置为观看者实际能访问的局域网 IP、
   域名或 HTTPS 反向代理地址。

最终验收应在未登录设备上进行：裸 `/d/media/xxx.mp4` 不含 `sign` 参数，
不要求 Cookie 或 Referer，普通请求返回真实媒体 `Content-Type`，Range 请求
返回 `206` 和有效 `Content-Range`。满足这几项后再启动房间共享。

只配置 `--alist-enabled` 和 AList 服务器时，客户端可作为观看者自动接收
远程媒体，不需要本地文件或路径映射。房主要自动发布本地文件时，还必须
提供至少一条有效映射。`--alist-root PATH --alist-virtual-root PATH` 等同于
一条 `--alist-map LOCAL=VIRTUAL`。接收远程媒体时，控制面板会显示 AList
来源、加载状态和仍在等待就绪的成员；超时后错误也会显示在概览中。

扩展文件信息包含 `media_url`、`source_type=alist` 和可选的
`media_owner`。最后一项只记录最初发布者，避免观看者加载后回传相同 URL
时被误认成新的媒体源；它不是认证凭据。Syncplay 协议本身没有可验证的
“房主”身份，因此请优先使用精简中继和不公开的房间名。

观看者断线重连后会保留已经加载的远程媒体，但不会立即重新发布旧 URL；
只有服务器返回的房间成员列表再次确认原发布者仍在同一房间、且发布的是
同一 URL 后，客户端才会恢复扩展媒体信息。切换服务器或房间会直接丢弃旧
URL 的发布和加载状态，避免把上一个房间的地址带入新房间。

官方旧版 Syncplay 客户端仍可加入同一房间并参与原有播放同步，但不会识别
这里扩展的媒体 URL，也不会自动加载远程文件，需要手动打开相同媒体。新版
客户端等待所有成员就绪的时间默认最多 30 秒；旧客户端未回报匹配的媒体
就绪状态时，等待会在超时后自动解除并继续播放。若房间只使用本扩展客户端
并要求严格“全员就绪才播放”，把 `media_ready_timeout` 设为 `0` 即可禁用
兼容超时；此时需要未就绪成员加载成功或离开房间后才会继续。

本项目 MiniServer 会给没有加载媒体的空闲成员持续发送仅含 ping 的状态，
也会先让新成员确认当前房间时间轴，再把其位置纳入“最慢成员”聚合，避免
刚加入时上报的 0 秒把正在播放的房间整体拉回开头。

## 使用 Tailscale 连接外地观看者

项目内的 `WatchParty\Tailscale` 已包含经过签名和 SHA-256 校验的官方
Windows x64 安装包，以及房主/观看者配置入口。详细步骤见该目录的
`README.md`。

推荐流程是通过 Tailscale 管理后台的 `Machines -> 房主电脑 -> Share` 只
共享房主设备。观看者使用自己的账号接受邀请，不要共用房主登录。房主首次
向导会读取本机 Tailscale `100.x` IPv4，并把 AList 媒体地址写为
`http://100.x.x.x:5244`；观看者配置同一个完整地址即可。

当前流程不使用旧的 `.ts.net + Tailscale Serve` 方式，也不要启用 Funnel。
Funnel 会把服务暴露到公网；设备共享则只授权观看者经 Tailscale 加密网络
访问房主设备。无需在路由器上开放 5244 端口。

按 `Ctrl+Shift+S` 打开控制面板后，左侧 `Tailscale` 二级栏可查看安装、登录
和本机 Tailscale IP 状态。首次部署以 `WatchParty\房主首次运行.bat` 和观看者
包内的 `观看者首次运行.bat` 为准，它们还会完成 AList 共享与可达性诊断。

面板和辅助模块不会读取或保存 Tailscale auth key。媒体地址改变后，正在运行
的 Syncplay 客户端会停止，重新选择“加入 / 连接房间”即可让严格的 AList
来源校验使用新地址。Tailscale 只解决网络可达性，AList 的匿名只读权限和
HTTP Range 要求仍然保持不变。

## 简洁控制面板接口（可选）

客户端启动后会在本目录原子写入 `syncplay_status.json`，并轮询
`syncplay_command.json`。控制面板应使用 UTF-8 JSON 原子替换命令文件；命令
成功执行后客户端会校验并清空旧内容，若同时换入了新文件则保留新命令。
也可以用参数指定每个实例的
文件位置：

```text
python.exe portable_config\syncplay\mpv_syncplay.py --room 房间 --name 昵称 ^
  --control-file D:\\Temp\\syncplay-command.json ^
  --status-file D:\\Temp\\syncplay-status.json
```

支持的命令示例：

```json
{"command":"stop"}
{"command":"reconnect"}
{"command":"set", "server":"syncplay.pl:8995", "room":"电影", "name":"小明"}
{"command":"sync"}
{"command":"pause"}
{"command":"play"}
{"command":"toggle_pause"}
{"command":"seek", "position": 123.5}
```

状态文件至少包含 `logged`、`server`、`room`、`name`、`filename`、`position`、
`paused`、`speed_changed`、`users` 和 `last_error`，另附 `rtt`、房间进度等
信息，适合 Lua/Tk 等标准库界面直接读取。
