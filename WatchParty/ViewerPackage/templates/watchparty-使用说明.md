# WatchParty 使用说明（房主 / 观看者共用）

同一个安装包既是房主包，也是观看者包。角色不在下载时决定，而是在 mpv 面板里
选择：按 `Ctrl+Shift+S` 打开面板 → 「联机」→「运行模式」→ 选「房主模式」或
「观看者模式」。随包 AList 只在房主模式下启动，观看者模式不会占用本机任何
端口。

支持 Windows x64 / x86，以及 macOS（Apple Silicon 与 Intel）。

安全边界：媒体只经 Tailscale 加密网络（tailnet）访问，不启用 Tailscale
Funnel，不把 AList 暴露到公网；管理密码只保存在本机，绝不写进包里。

## 第一次使用

1. 完整解压压缩包（Windows 建议解压到非系统盘；macOS 建议放在"文稿"里）。
   - macOS 额外需要去除隔离标记（本包未做付费公证）。打开"终端"执行
     `xattr -cr 把这里替换成解压出来的文件夹路径`；macOS 15 及以上若提示
     "无法验证开发者"，也可在 系统设置 → 隐私与安全性 里点"仍要打开"。
2. 双击首次运行脚本：
   - Windows：`WatchParty\首次运行.bat`
   - macOS：解压文件夹顶层的 `首次设置.command`
   它会检测 Tailscale，缺失时打开包内的官方安装器（签名与哈希已校验），
   然后提示下一步。只看本机影片的话，这一步可以跳过。
3. 安装并登录 Tailscale（房主和观看者都需要，各自使用自己的账号）。
   Windows 在托盘图标登录；macOS 装好后把 Tailscale 拖入"应用程序"，
   在系统设置里允许其系统扩展，再从菜单栏图标登录。
4. 双击日常启动脚本打开 mpv：
   - Windows：`WatchParty\启动.bat`
   - macOS：解压文件夹顶层的 `启动.command`
5. 在 mpv 中按 `Ctrl+Shift+S` 打开面板，进入「联机」→「运行模式」，选择角色。

## 房主模式

1. 首次选择房主模式时会弹出一次管理员授权（仅 Windows）：用于写入一条防火墙
   规则，只放行 Tailscale 网段 `100.64.0.0/10` 访问本机的 AList 端口 5244。
   点"是"即可；拒绝授权则房主模式不会生效，可重新选择并授权。
2. 向导会自动：启动随包 AList → 生成管理员密码（保存在
   `WatchParty/ADMIN_PASSWORD.txt`，请勿外传）→ 开启匿名只读与 HTTP Range →
   自检 → 写入房主配置。
3. 最后一步需要你在网页上手动完成：登录
   <https://login.tailscale.com/admin/machines>，找到本设备，点 Share，
   把共享链接发给观看者。
4. 把视频放进 `WatchParty/media`。外部路径必须已在面板里配置 `alist_map`
   映射，观看者才能自动加载。
5. 在面板里设置房间与昵称，选择"加入 / 连接房间"，再从 `WatchParty/media`
   打开视频。

## 观看者模式

1. 请房主先在 Tailscale 后台把他的设备共享给你的 Tailscale 账号，并接受邀请。
2. 在面板里进入「联机」→「运行模式」→「观看者模式」，按提示输入房主发来的
   完整地址 `http://100.x.x.x:5244`，然后确认。
3. 观看者模式会清除本机的媒体发布映射，并从观看者视角诊断房主地址；诊断失败
   时先解决连接问题，不要强行继续。
4. 在面板左侧依次设置服务器、房间和昵称（必须与房主完全一致；房主使用默认
   服务器时为 `syncplay.pl:8995`），进入"播放控制"选择"加入 / 连接房间"。
5. 等房主从他的媒体目录打开视频。不要在本机选择视频；mpv 会自动载入远程视频，
   并在加载完成后进入 READY。

房主地址不是登录凭据：没有获得房主的 Tailscale 设备共享授权时，地址本身无法
访问视频。房主更换地址后，可在面板里重新选择观看者模式并输入新地址。

## 切换角色

- 「运行模式」随时可以重选。改回「仅本机观看」会关闭随包 AList 与联机共享，
  只播放本地文件。
- 切换角色会重启本机的 Syncplay 客户端进程，切换完成后请在面板里重新选择
  "加入 / 连接房间"。

## 弹幕（不影响同步，和房主互不干扰）

mpv 下方的控制栏里有 `弹幕开关`、`搜索弹幕`、`从源添加弹幕`、`弹幕设置`；
也可以按 `j` 开关弹幕，按 `Ctrl+d` 搜索弹幕。弹幕只在本机显示，不会发送给
房主或其他观看者，并跟随本机播放进度。

## 排查

| 现象 | 处理 |
| --- | --- |
| 面板打不开 | 使用 `Ctrl+Shift+S`。 |
| 房主模式没有生效 / 提示已取消授权 | 重新选择房主模式，并在弹窗中点"是"。 |
| 提示 AList 未能启动 | 确认 `WatchParty/alist/alist.exe`（macOS 为 `alist`）存在；必要时双击首次运行脚本重试。 |
| Tailscale 未连接 | 打开 Tailscale 图标重新登录。 |
| 能进房间但视频不加载 | 确认已接受房主的设备共享邀请，且房主保持 Tailscale、AList 与 mpv 运行。 |
| Tailscale 已连接但诊断不通 | 核对房主当前的 `100.x` 地址；不要让房主开启 Funnel。 |
| macOS 提示"无法验证开发者" | 执行 `xattr -cr 解压目录`，或在 系统设置 → 隐私与安全性 里点"仍要打开"。 |

房主侧诊断命令：

- Windows：`python.exe portable_config\syncplay\watchparty_setup.py doctor --role host`
- macOS：`python/bin/python3 portable_config/syncplay/watchparty_setup.py doctor --role host`
