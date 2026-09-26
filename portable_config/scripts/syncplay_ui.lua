-- Syncplay 风格的轻量控制面板。
--
-- 面板本身是 mpv Lua 脚本，不依赖 Tk/Qt；它通过 uosc 菜单显示，
-- 与 mpv_syncplay.py 使用 portable_config/syncplay 下的 JSON 桥接。

local msg = require("mp.msg")
local utils = require("mp.utils")
local options = {
    server = "syncplay.pl:8995",
    room = "mpv",
    name = "",
    pipe = "\\\\.\\pipe\\mpvpipe",
    status_file = "",
    command_file = "",
    auto_start = false,
    alist_enabled = false,
    alist_server = "http://127.0.0.1:5244",
    alist_root = "~~/../WatchParty/media",
    alist_virtual_root = "/media",
    alist_map = "",
    media_ready_timeout = 30,
    http_readahead = 20,
    tailscale_mode = "off",
    tailscale_host = "",
    tailscale_helper = "~~/syncplay/tailscale_integration.py",
    setup_helper = "~~/syncplay/watchparty_setup.py",
    tailscale_installer = "",
}
pcall(function() require("mp.options").read_options(options, "syncplay_ui") end)

local script_name = mp.get_script_name()
local config_dir = mp.command_native({"expand-path", "~~/"})
-- Windows 官方构建里 Python 与 mpv.exe 同级；macOS 包使用
-- python-build-standalone 的 python/bin/python3。
local is_windows = mp.get_property("platform") == "windows"
local python_binary = is_windows and "~~/../python.exe" or "~~/../python/bin/python3"

local function resolve_path(value, fallback)
    if value and value ~= "" then
        return mp.command_native({"expand-path", value})
    end
    return config_dir .. fallback
end

local function file_exists(path)
    local handle = io.open(path, "rb")
    if handle then
        handle:close()
        return true
    end
    return false
end

-- Windows x64 与 x86 包携带的 MSI 文件名不同；按包内实际存在的文件选择。
local tailscale_installer_default
if is_windows then
    for _, name in ipairs({
        "tailscale-setup-1.102.3-amd64.msi",
        "tailscale-setup-1.102.3-x86.msi",
    }) do
        if file_exists(mp.command_native({"expand-path", "~~/../WatchParty/Tailscale/" .. name})) then
            tailscale_installer_default = "/../WatchParty/Tailscale/" .. name
            break
        end
    end
    tailscale_installer_default = tailscale_installer_default
        or "/../WatchParty/Tailscale/tailscale-setup-1.102.3-amd64.msi"
else
    tailscale_installer_default = "/../WatchParty/Tailscale/Tailscale-1.102.4-macos.pkg"
end

local status_file = resolve_path(options.status_file, "/syncplay/syncplay_status.json")
local command_file = resolve_path(options.command_file, "/syncplay/syncplay_command.json")
local tailscale_helper = resolve_path(options.tailscale_helper, "/syncplay/tailscale_integration.py")
local setup_helper = resolve_path(options.setup_helper, "/syncplay/watchparty_setup.py")
local tailscale_installer = resolve_path(options.tailscale_installer, tailscale_installer_default)

local state = {
    logged = false,
    connecting = false,
    server = options.server,
    room = options.room,
    name = options.name ~= "" and options.name or "未设置",
    filename = "",
    position = nil,
    paused = nil,
    global_position = nil,
    global_paused = nil,
    speed_changed = false,
    rtt = nil,
    users = {},
    last_error = "",
    media_url = nil,
    source_type = nil,
    media_loading = false,
    waiting_for_ready = false,
    waiting_users = nil,
}

local tailscale_state = {
    installed = false,
    backend_state = "Unknown",
    online = false,
    ipv4 = "",
    dns_name = "",
    error = "",
}

-- 当前便携配置自带 uosc；先按可用处理，之后收到版本消息时再确认。
-- 这样即使 uosc 比本脚本先加载，也不会错过首次 uosc-version 广播。
local uosc_available = true
local panel_open = false
local menu_kind = nil
local client_handle = nil
-- 面板主动结束客户端进程时递增；旧进程的回调据此判断自己已经作废，
-- 不会把这次中止报告成“客户端启动失败”，也不会覆盖新进程的句柄。
local client_generation = 0
-- Forward declaration: the Join action can launch the bundled client before
-- the function's implementation appears below.
local launch_client
local command_id = 0
local last_status_raw = nil
local status_stale = false
local tailscale_busy = false
local tailscale_last_refresh = 0
-- 用户在面板中修改过的连接参数不应在后续状态轮询时被旧快照覆盖；
-- 未修改的参数则会从状态文件同步，以支持启动器传入房间/昵称/服务器。
local options_user_changed = {server = false, room = false, name = false}

local function trim(value)
    return tostring(value or ""):gsub("^%s*(.-)%s*$", "%1")
end

local function option_enabled(value)
    if value == true then return true end
    local normalized = tostring(value or ""):lower()
    return normalized == "yes" or normalized == "true"
        or normalized == "1" or normalized == "on"
end

local function expand_local_path(value)
    local path = trim(value)
    if path == "" then return "" end
    local ok, expanded = pcall(mp.command_native, {"expand-path", path})
    if ok and type(expanded) == "string" and expanded ~= "" then return expanded end
    return path
end

local function format_time(value)
    local n = tonumber(value)
    if not n then return "--:--" end
    n = math.max(0, math.floor(n + 0.5))
    return string.format("%d:%02d:%02d", math.floor(n / 3600), math.floor(n / 60) % 60, n % 60)
end

local function expire_stale_status()
    local timestamp = tonumber(state.timestamp)
    if not timestamp or os.time() - timestamp <= 3 then return false end
    if status_stale then return false end
    status_stale = true
    state.logged = false
    state.connected = false
    state.connecting = false
    state.phase = "stopped"
    state.position = nil
    state.paused = nil
    state.global_position = nil
    state.global_paused = nil
    state.speed_changed = false
    state.users = {}
    state.media_url = nil
    state.source_type = nil
    state.media_loading = false
    state.waiting_for_ready = false
    state.waiting_users = nil
    return true
end

local function read_status()
    local file = io.open(status_file, "rb")
    if not file then return expire_stale_status() end
    local raw = file:read("*a")
    file:close()
    if not raw or raw == "" or raw == last_status_raw then return expire_stale_status() end
    local ok, parsed = pcall(utils.parse_json, raw)
    if ok and type(parsed) == "table" then
        -- JSON null values are omitted by mp.utils.parse_json. Clear nullable
        -- snapshot fields first so recovered errors and unloaded media do not
        -- leave stale values in the panel.
        for _, key in ipairs({
            "filename", "position", "paused", "last_error",
            "global_position", "global_paused", "media_url", "source_type",
            "waiting_users",
        }) do
            state[key] = nil
        end
        state.media_loading = false
        state.waiting_for_ready = false
        for key, value in pairs(parsed) do state[key] = value end
        -- FileControlBridge publishes a phase string; mirror it into the
        -- boolean used by the menu renderer so “连接中” is not shown as
        -- “未连接”.
        state.connecting = parsed.phase == "connecting"
        -- When Python was started by the launcher, copy its effective
        -- connection parameters into the form defaults.  Keep values the
        -- user has edited in this panel authoritative for the rest of the
        -- session.
        for _, key in ipairs({"server", "room", "name"}) do
            if not options_user_changed[key] and parsed[key] ~= nil and tostring(parsed[key]) ~= "" then
                options[key] = tostring(parsed[key])
            end
        end
        last_status_raw = raw
        status_stale = false
        expire_stale_status()
        return true
    end
    return false
end

local function write_command(command, fields)
    command_id = command_id + 1
    local payload = {id = tostring(os.time()) .. "-" .. tostring(command_id), command = command}
    for key, value in pairs(fields or {}) do payload[key] = value end

    local encoded = utils.format_json(payload)
    if not encoded or encoded == "" then
        msg.warn("无法编码 Syncplay 控制命令")
        return false
    end

    -- 先写同目录临时文件，再替换目标，避免 Python 轮询线程读到半个
    -- JSON。Windows 在目标已存在时不能直接 rename 覆盖；重试前只在
    -- 目标仍是我们写入前看到的旧内容（或已被桥清空）时删除，避免把
    -- 并发写入的下一条命令误删。
    local previous_content = nil
    local previous = io.open(command_file, "rb")
    if previous then
        previous_content = previous:read("*a")
        previous:close()
    end
    local temp_path = command_file .. ".tmp." .. tostring(os.time()) .. "." .. tostring(command_id)
    local file = io.open(temp_path, "wb")
    if not file then
        msg.warn("无法写入 Syncplay 控制文件: " .. temp_path)
        return false
    end
    file:write(encoded)
    file:flush()
    file:close()

    local renamed = os.rename(temp_path, command_file)
    if not renamed then
        local current_content = nil
        local current = io.open(command_file, "rb")
        if current then
            current_content = current:read("*a")
            current:close()
        end
        if current_content == nil or current_content == "" or current_content == previous_content then
            os.remove(command_file)
            renamed = os.rename(temp_path, command_file)
        end
    end
    if not renamed then
        os.remove(temp_path)
        msg.warn("无法替换 Syncplay 控制文件: " .. command_file)
        return false
    end
    return true
end

local function notify(text, seconds)
    mp.osd_message("Syncplay: " .. text, seconds or 2.5)
end

-- mpv 对脚本消息、定时器和异步回调里未捕获的 Lua 错误的处置是终止整个
-- 脚本（"Destroying client handle"）。面板由 uosc 独立绘制，所以表现恰恰
-- 是"面板还在、点了没反应"：所有点击事件仍在发给一个已经不存在的脚本。
-- 所有外部入口（菜单事件、输入提交、轮询、辅助进程回调）一律经 safe_call
-- 调用，单个动作出错只记日志并提示，绝不让它带走整个面板。
local function safe_call(name, fn, ...)
    local ok, err = pcall(fn, ...)
    if not ok then
        msg.error("操作「" .. tostring(name) .. "」出错: " .. tostring(err))
        notify("操作失败，请重试；详情见日志", 6)
    end
    return ok
end

local function close_menu()
    if uosc_available then
        mp.commandv("script-message-to", "uosc", "close-menu", menu_kind or "syncplay")
    end
    panel_open = false
    menu_kind = nil
end

local function setting_value(key)
    -- State files produced by an externally launched client are authoritative
    -- until the user edits that field in this panel.
    if options_user_changed[key] then return options[key] end
    local value = state[key]
    if value ~= nil and tostring(value) ~= "" then return tostring(value) end
    return options[key]
end

local function refresh_panel_soon()
    if panel_open then
        mp.add_timeout(0.05, function() if panel_open then safe_call("刷新面板", open_panel) end end)
    end
end

local function apply_tailscale_payload(payload)
    if type(payload) ~= "table" then return end
    for _, key in ipairs({
        "installed", "backend_state", "online", "ipv4", "ipv6",
        "dns_name", "error", "alist_server", "tailscale_mode", "tailscale_host",
    }) do
        if payload[key] ~= nil then tailscale_state[key] = payload[key] end
    end
    -- Viewer configuration intentionally clears publishing mappings.  Apply
    -- empty values too, otherwise this mpv process could keep stale host paths
    -- in memory until it is restarted.  alist_enabled must be applied as well:
    -- a merged install starts with sharing off and only starts publishing once
    -- a role has been selected in this panel.
    for _, key in ipairs({
        "alist_enabled", "alist_server", "alist_root", "alist_virtual_root",
        "alist_map", "tailscale_mode", "tailscale_host",
    }) do
        if payload[key] ~= nil then options[key] = tostring(payload[key]) end
    end
end

-- 串行锁没有免费的兜底：命令异步回调若永远不来，tailscale_busy 会卡死
-- 后续所有网络操作。看门狗在超时后中止命令（subprocess 会被 mpv 杀掉并
-- 以失败调用回调），busy 由回调复位，面板保持可用。超时上限取 mac 上
-- 最慢路径（AList 首启 + 全包隔离清理 + Tailscale 检查）的数倍。
local HELPER_TIMEOUT = 300

local function run_helper(script, arguments, callback, silent)
    if tailscale_busy then
        if not silent then notify("上一项网络配置还在进行") end
        return false
    end
    local python = mp.command_native({"expand-path", python_binary})
    local args = {python, script, "--json"}
    for _, value in ipairs(arguments or {}) do args[#args + 1] = tostring(value) end
    tailscale_busy = true
    local watchdog
    local handle = mp.command_native_async({
        name = "subprocess",
        args = args,
        playback_only = false,
        capture_stdout = true,
        capture_stderr = true,
    }, function(success, result, error)
        if watchdog then watchdog:kill() end
        tailscale_busy = false
        tailscale_last_refresh = os.time()
        safe_call("辅助进程回调", function()
            local raw = type(result) == "table" and result.stdout or ""
            local parsed_ok, payload = pcall(utils.parse_json, raw or "")
            if not parsed_ok or type(payload) ~= "table" then payload = nil end
            apply_tailscale_payload(payload)
            local process_ok = success and (not result or tonumber(result.status or 0) == 0)
            local operation_ok = process_ok and payload ~= nil and payload.ok ~= false
            local detail = (payload and payload.error)
                or (type(result) == "table" and result.stderr)
                or error or "配置操作失败"
            detail = trim(detail)
            if not operation_ok and detail == "" then detail = "配置操作失败" end
            if callback then callback(operation_ok, payload, detail) end
            refresh_panel_soon()
        end)
    end)
    watchdog = mp.add_timeout(HELPER_TIMEOUT, function()
        if not tailscale_busy then return end
        msg.warn("辅助进程超过 " .. HELPER_TIMEOUT .. " 秒未返回，已中止")
        pcall(mp.abort_async_command, handle)
        notify("网络配置长时间无响应，已中止；请稍后重试", 6)
    end)
    return true
end

local function run_tailscale_helper(arguments, callback, silent)
    return run_helper(tailscale_helper, arguments, callback, silent)
end

local function run_setup_helper(arguments, callback, silent)
    return run_helper(setup_helper, arguments, callback, silent)
end

local function refresh_tailscale_status(silent)
    return run_tailscale_helper({"status"}, function(ok, _payload, detail)
        if not ok and not silent then notify(detail) end
    end, silent)
end

local function stop_for_tailscale_change()
    if state.logged or state.connecting or client_handle then
        if write_command("stop") then
            notify("媒体地址已保存；客户端停止后请重新加入房间")
        end
        return true
    end
    return false
end

-- 媒体地址只在 mpv_syncplay 启动时读取一次。运行模式改变后必须结束当前
-- 客户端进程，否则它会继续使用旧的 --alist-* 参数（房主不发布、观看者
-- 指向旧地址），只有重新启动进程才会生效。
local function stop_client_process()
    if not client_handle then return false end
    local handle = client_handle
    client_handle = nil
    client_generation = client_generation + 1
    pcall(mp.abort_async_command, handle)
    return true
end

local function role_change_notice(headline)
    local message = headline
    if stop_client_process() then
        message = message .. " 请点击「加入 / 连接房间」应用新地址。"
    elseif state.logged or state.connecting then
        write_command("stop")
        message = message .. " 媒体地址已保存，请重新运行启动脚本后再加入房间。"
    end
    notify(message, 8)
end

local function activate_host_mode()
    -- 配置包含一次管理员授权（首次）加 AList/Tailscale 检查，通常几秒，
    -- 等待 UAC 时可能更久。提示必须留够时间，否则用户会以为"点了没反应"。
    notify("正在配置房主模式…如出现“用户账户控制”请点“是”", 12)
    local started = run_setup_helper({"mode-host"}, function(ok, payload, detail)
        if not ok then
            tailscale_state.error = detail
            notify(detail, 12)
            return
        end
        apply_tailscale_payload(payload)
        role_change_notice("房主模式已切换。请在 Tailscale 后台对本设备点 Share。")
    end)
    if not started then
        notify("正在配置其它网络项目，请稍候再点一次「切换为房主模式」", 6)
    end
end

local function activate_local_mode()
    run_tailscale_helper({"configure-local"}, function(ok, payload, detail)
        if not ok then
            tailscale_state.error = detail
            notify(detail)
            return
        end
        apply_tailscale_payload(payload)
        role_change_notice("已切换为仅本机观看。")
    end)
end

local function configure_tailscale_host()
    if trim(options.tailscale_mode):lower() ~= "host" then
        notify("当前不是房主配置，已拒绝改写房主媒体地址")
        return
    end
    run_tailscale_helper({"configure-host"}, function(ok, payload, detail)
        if not ok then
            tailscale_state.error = detail
            notify(detail)
            return
        end
        if not stop_for_tailscale_change() then
            notify("房主 100.x 媒体地址已配置；请在后台手动 Share 设备")
        end
        apply_tailscale_payload(payload)
    end)
end

-- 观看者模式既可以从「运行模式」选择，也可以在观看者模式下用面板输入的
-- 房主地址重新指定；因此这里不再判断当前模式，调用方负责限制入口。
local function configure_tailscale_viewer(host)
    host = trim(host)
    if host == "" then
        notify("请输入房主的 Tailscale 100.x 地址")
        return
    end
    run_tailscale_helper({"configure-viewer", host}, function(ok, payload, detail)
        if not ok then
            tailscale_state.error = detail
            notify(detail)
            return
        end
        apply_tailscale_payload(payload)
        role_change_notice("观看者模式已切换。现在可以「加入 / 连接房间」。")
    end)
end

local function open_tailscale()
    run_tailscale_helper({"open"}, function(ok, _payload, detail)
        if not ok then notify(detail) end
    end)
end

local function install_tailscale()
    local file = io.open(tailscale_installer, "rb")
    if not file then
        notify("找不到项目内的 Tailscale 安装包")
        return
    end
    file:close()
    run_tailscale_helper({"verify-installer"}, function(ok, _payload, detail)
        if not ok then
            notify(detail)
            return
        end
        local install_args = is_windows
            and {"msiexec.exe", "/i", tailscale_installer}
            or {"open", tailscale_installer}
        mp.command_native_async({
            name = "subprocess",
            args = install_args,
            playback_only = false,
            detach = true,
        }, function(success, _result, error)
            if success then
                notify("已打开 Tailscale 官方安装界面")
            else
                notify(tostring(error or "无法启动安装程序"))
            end
        end)
    end)
end

-- 合并安装包在首次运行前不预设角色：tailscale_mode=off 表示"还没选"。
-- 必须声明在 run_action 之前：run_action 的「切换为观看者模式」分支要读它，
-- 而 Lua 的 local 只在声明语句之后可见，放在后面会变成调用一个不存在的
-- 全局函数，点击后只会抛 "attempt to call global 'current_mode'"。
local function current_mode()
    return trim(options.tailscale_mode):lower()
end

local function run_action(action)
    if action == "refresh" then
        read_status()
    elseif action == "sync" then
        if write_command("sync") then notify("正在对齐到房间位置") end
    elseif action == "toggle-pause" then
        -- 由 mpv 自己切换，Python 监视线程会把这次变化同步给房间。
        mp.command("cycle pause")
    elseif action == "stop" then
        if write_command("stop") then notify("正在断开连接") end
    elseif action == "reconnect" then
        if write_command("reconnect", {
            server = setting_value("server"),
            room = setting_value("room"),
            name = setting_value("name"),
        }) then
            notify("正在重新连接")
        end
    elseif action == "join" or action == "join-room" then
        -- No client process means the panel is being used as the launcher.
        -- Pass the current values as process arguments in that case; when a
        -- client was started externally, only send a set command to it.
        if state.logged or state.connecting or client_handle then
            if write_command("set", {
                server = setting_value("server"),
                room = setting_value("room"),
                name = setting_value("name"),
            }) then
                notify("正在加入房间「" .. tostring(setting_value("room") or "mpv") .. "」")
            end
        else
            launch_client()
            notify("正在启动 Syncplay 客户端")
        end
    elseif action == "tailscale-refresh" then
        refresh_tailscale_status(false)
    elseif action == "tailscale-install" then
        install_tailscale()
    elseif action == "tailscale-open" then
        open_tailscale()
    elseif action == "tailscale-host" then
        configure_tailscale_host()
    elseif action == "mode-host" then
        activate_host_mode()
    elseif action == "mode-viewer" then
        if current_mode() == "host" then
            -- 房主模式下 tailscale_host 保存的是本机地址，直接复用会指向自己。
            notify("请在「运行模式」顶部输入框填入对方的 100.x.x.x 后回车")
        else
            configure_tailscale_viewer(trim(options.tailscale_host))
        end
    elseif action == "mode-local" then
        activate_local_mode()
    elseif action == "copy-host" then
        -- 共享地址是观看者加入的入口，提供一键复制（mpv 的 clipboard 属性在
        -- Windows/macOS 都可用）。
        local host = trim(options.tailscale_host)
        if host == "" then
            notify("共享地址为空，请先完成房主模式配置")
        else
            local ok = mp.set_property("clipboard/text", host)
            notify(ok and ("已复制共享地址：" .. host) or "复制失败，请手动选择地址")
        end
    elseif action == "close" then
        close_menu()
    end
end

local function append_alist_arguments(args)
    if not option_enabled(options.alist_enabled) then return false end

    local server = trim(options.alist_server)
    local root = expand_local_path(options.alist_root)
    local virtual_root = trim(options.alist_virtual_root)
    local mappings = {}

    -- Extra mappings are separated by semicolons. Split each one at its last
    -- equals sign so a Windows local path may itself contain an equals sign.
    for entry in (tostring(options.alist_map or "") .. ";"):gmatch("(.-);") do
        entry = trim(entry)
        if entry ~= "" then
            local separator = nil
            for index = 1, #entry do
                if entry:sub(index, index) == "=" then separator = index end
            end
            local local_root = separator and trim(entry:sub(1, separator - 1)) or ""
            local remote_root = separator and trim(entry:sub(separator + 1)) or ""
            if local_root ~= "" and remote_root ~= "" then
                mappings[#mappings + 1] = expand_local_path(local_root) .. "=" .. remote_root
            else
                msg.warn("忽略无效的 AList 映射: " .. entry)
            end
        end
    end

    local has_root_mapping = root ~= "" and virtual_root ~= ""
    if server == "" then
        return false, "AList 已开启，但服务器地址为空；已按本地媒体模式启动"
    end

    args[#args + 1] = "--alist-enabled"
    args[#args + 1] = "--alist-server"
    args[#args + 1] = server
    if has_root_mapping then
        args[#args + 1] = "--alist-root"
        args[#args + 1] = root
        args[#args + 1] = "--alist-virtual-root"
        args[#args + 1] = virtual_root
    end
    for _, mapping in ipairs(mappings) do
        args[#args + 1] = "--alist-map"
        args[#args + 1] = mapping
    end
    args[#args + 1] = "--media-ready-timeout"
    args[#args + 1] = tostring(tonumber(options.media_ready_timeout) or 30)
    args[#args + 1] = "--http-readahead"
    args[#args + 1] = tostring(tonumber(options.http_readahead) or 20)
    return true
end

launch_client = function()
    if client_handle then
        notify("客户端已经在运行")
        return
    end

    local python = mp.command_native({"expand-path", python_binary})
    local script = mp.command_native({"expand-path", "~~/syncplay/mpv_syncplay.py"})
    -- Read once more so a client started by the batch launcher can provide
    -- its effective settings before this panel launches a replacement.
    read_status()
    local server = setting_value("server") or "syncplay.pl:8995"
    local room = setting_value("room") or "mpv"
    local name = setting_value("name")
    if not name or name == "" or name == "未设置" then name = "MPV用户" end
    -- `~~` 前缀由客户端解析（Windows 命名管道原样保留，macOS socket 展开）。
    local pipe_arg = mp.command_native({"expand-path", options.pipe})
    local args = {
        python, script,
        "--pipe", pipe_arg,
        "--server", server,
        "--room", room,
        "--name", name,
        "--control-file", command_file,
        "--status-file", status_file,
    }
    local _, alist_error = append_alist_arguments(args)
    if alist_error then notify(alist_error) end
    local generation = client_generation
    client_handle = mp.command_native_async({
        name = "subprocess",
        args = args,
        playback_only = false,
        capture_stdout = false,
        capture_stderr = false,
    }, function(success, result, error)
        -- 面板在切换运行模式时主动结束了这个进程：它已经作废，不要再报错，
        -- 也不要把 client_handle 置空（可能已经属于新启动的客户端）。
        if generation ~= client_generation then return end
        client_handle = nil
        if not success then
            state.last_error = tostring(error or (result and result.stderr) or "客户端启动失败")
            notify(state.last_error)
        end
        read_status()
        if panel_open then
            mp.add_timeout(0.05, function() if panel_open then safe_call("刷新面板", open_panel) end end)
        end
    end)
end

local function start_or_reconnect()
    if state.logged or state.connecting or client_handle then
        safe_call("重连房间", run_action, "reconnect")
    else
        launch_client()
    end
end

local function apply_text(kind, value)
    value = trim(value)
    if value == "" then
        notify("输入不能为空")
        return
    end
    options[kind] = value
    if options_user_changed[kind] ~= nil then options_user_changed[kind] = true end
    if kind == "room" then
        start_or_reconnect()
    elseif kind == "name" or kind == "server" then
        if state.logged or state.connecting or client_handle then
            safe_call("重连房间", run_action, "reconnect")
        end
    end
    mp.add_timeout(0.05, function() if panel_open then safe_call("刷新面板", open_panel) end end)
end

local function action_value(action)
    return "syncplay-action:" .. action
end

local function user_entries()
    local users = state.users
    local result = {}
    if type(users) ~= "table" then return result end
    local current_room = tostring(setting_value("room") or "")
    local own_name = tostring(setting_value("name") or "")
    local function same_room(user)
        -- The official List response contains every room on the server.  Do
        -- not leak unrelated rooms into this compact room-member panel;
        -- incremental user events may omit room, in which case retain them.
        return user.room == nil or tostring(user.room) == current_room
    end
    if #users > 0 then
        for _, user in ipairs(users) do
            if type(user) == "table" and same_room(user)
                and tostring(user.name or user.username or "") ~= own_name then
                result[#result + 1] = user
            end
        end
    else
        for name, user in pairs(users) do
            if type(user) == "table" and same_room(user) then
                local copy = {}
                for key, value in pairs(user) do copy[key] = value end
                copy.name = copy.name or copy.username or name
                if tostring(copy.name or "") ~= own_name then result[#result + 1] = copy end
            end
        end
    end
    table.sort(result, function(a, b) return tostring(a.name or "") < tostring(b.name or "") end)
    return result
end

local function connection_status()
    local logged = state.logged == true
    local status = logged and "已连接" or (state.connecting and "连接中" or "未连接")
    local status_icon = logged and "cloud_done" or (state.connecting and "sync" or "cloud_off")
    return logged, status, status_icon
end

local function waiting_users_hint()
    local names = {}
    local seen = {}
    local explicit_count = nil

    local function add_name(value)
        if type(value) == "table" then value = value.name or value.username end
        local name = trim(value)
        if name ~= "" and not seen[name] then
            seen[name] = true
            names[#names + 1] = name
        end
    end

    local waiting = state.waiting_users
    if type(waiting) == "number" then
        explicit_count = math.max(0, math.floor(waiting))
    elseif type(waiting) == "string" then
        add_name(waiting)
    elseif type(waiting) == "table" then
        explicit_count = tonumber(waiting.count)
        if type(waiting.names) == "table" then
            for _, value in ipairs(waiting.names) do add_name(value) end
        elseif #waiting > 0 then
            for _, value in ipairs(waiting) do add_name(value) end
        else
            for key, value in pairs(waiting) do
                if key ~= "count" and key ~= "names" then
                    if type(value) == "table" then
                        add_name(value.name or value.username or key)
                    elseif value == true or value == false then
                        add_name(key)
                    elseif type(value) == "string" or type(value) == "number" then
                        add_name(value)
                    end
                end
            end
        end
    end

    if #names == 0 then
        for _, user in ipairs(user_entries()) do
            if user.ready == false then add_name(user) end
        end
    end

    table.sort(names)
    local count = math.max(#names, math.floor(tonumber(explicit_count) or 0))
    if #names > 3 then
        return table.concat({names[1], names[2], names[3]}, "、") .. " 等 " .. tostring(count) .. " 人"
    elseif #names > 0 then
        local label = table.concat(names, "、")
        if count > #names then label = label .. "（共 " .. tostring(count) .. " 人）" end
        return label
    elseif count > 0 then
        return tostring(count) .. " 人尚未就绪"
    end
    return "等待其他成员完成加载"
end

local function build_overview_items()
    local logged, status, status_icon = connection_status()
    local items = {
        {title = status, hint = tostring(setting_value("server") or "未设置"), icon = status_icon,
            selectable = false, bold = logged},
        {title = "房间", hint = tostring(setting_value("room") or "未设置"), icon = "meeting_room", selectable = false},
        {title = "昵称", hint = tostring(setting_value("name") or "未设置"), icon = "person", selectable = false},
    }

    local filename = tostring(state.filename or "")
    if filename == "" then filename = "未加载媒体" end
    local pos = state.position or state.global_position
    local play = state.paused == true and "暂停" or (state.paused == false and "播放中" or "--")
    items[#items + 1] = {title = filename, hint = format_time(pos) .. "  ·  " .. play,
        icon = "movie", selectable = false}

    local source_type = trim(state.source_type):lower()
    local media_url = trim(state.media_url)
    if source_type == "alist" then
        items[#items + 1] = {title = "AList 远程媒体",
            hint = media_url ~= "" and media_url or "由房间共享",
            icon = "cloud", selectable = false}
    elseif source_type == "local" or (source_type == "" and filename ~= "未加载媒体") then
        items[#items + 1] = {title = "本地媒体", hint = "直接从此电脑播放",
            icon = "folder", selectable = false}
    elseif source_type ~= "" then
        items[#items + 1] = {title = "媒体来源", hint = tostring(state.source_type),
            icon = "link", selectable = false}
    end
    if state.media_loading == true then
        items[#items + 1] = {title = "正在加载远程媒体",
            hint = media_url ~= "" and media_url or "等待 mpv 建立时间轴",
            icon = "hourglass_top", selectable = false}
    end
    if state.waiting_for_ready == true then
        items[#items + 1] = {title = "等待成员就绪", hint = waiting_users_hint(),
            icon = "groups", selectable = false}
    end
    local rtt = tonumber(state.rtt)
    if rtt then
        items[#items + 1] = {title = "网络延迟", hint = string.format("%.0f ms", rtt * 1000),
            icon = "network_check", selectable = false}
    end
    if state.speed_changed then
        items[#items + 1] = {title = "同步调整", hint = "正在减速等待", icon = "slow_motion_video", selectable = false}
    end

    if state.last_error and state.last_error ~= "" then
        items[#items + 1] = {title = "错误", hint = tostring(state.last_error), icon = "error",
            selectable = false, muted = true}
    end

    return items
end


local function build_playback_items()
    local logged = state.logged == true
    local items = {}

    if logged then
        items[#items + 1] = {title = "立即同步", hint = "对齐到房间位置", icon = "sync", value = action_value("sync")}
        items[#items + 1] = {title = state.paused and "继续播放" or "暂停播放", icon = state.paused and "play_arrow" or "pause", value = action_value("toggle-pause")}
        items[#items + 1] = {title = "重新连接", icon = "refresh", value = action_value("reconnect")}
        items[#items + 1] = {title = "断开连接", icon = "link_off", value = action_value("stop")}
    elseif state.connecting or client_handle then
        items[#items + 1] = {title = "正在连接", hint = tostring(setting_value("server") or ""),
            icon = "sync", selectable = false}
        items[#items + 1] = {title = "重新连接", icon = "refresh", value = action_value("reconnect")}
        items[#items + 1] = {title = "断开连接", icon = "link_off", value = action_value("stop")}
    else
        items[#items + 1] = {title = "加入 / 连接房间", icon = "login", value = action_value("join-room")}
    end
    items[#items + 1] = {title = "刷新状态", icon = "refresh", value = action_value("refresh")}
    return items
end

local function build_member_items()
    local items = {}
    local users = user_entries()

    if state.logged == true then
        items[#items + 1] = {
            title = tostring(setting_value("name") or "我") .. "（你）",
            hint = state.paused == true and "暂停" or "在线",
            icon = "account_circle",
            selectable = false,
            bold = true,
        }
    end

    for _, user in ipairs(users) do
        local ready = user.ready
        local ready_text = ready == true and "就绪" or (ready == false and "暂离" or "在线")
        items[#items + 1] = {
            title = tostring(user.name or "匿名"),
            hint = ready_text,
            icon = ready == false and "pause_circle" or "person",
            selectable = false,
        }
    end

    if #items == 0 then
        items[1] = {title = "暂无在线成员", icon = "person_off", selectable = false, muted = true}
    end
    return items
end

-- current_mode 已在上文 run_action 之前声明，这里不再重复定义。
local function mode_label()
    local mode = current_mode()
    if mode == "host" then return "房主", "home_work" end
    if mode == "viewer" then return "观看者", "visibility" end
    return "未选择", "help"
end

local function tailscale_status_label()
    if tailscale_state.installed ~= true then return "未安装", "download" end
    if tailscale_state.online == true then return "已连接", "vpn_lock" end
    local backend = trim(tailscale_state.backend_state):lower()
    if backend == "needslogin" then return "等待登录", "login" end
    if backend == "stopped" then return "已停止", "link_off" end
    return "未连接", "vpn_lock" end

local function build_tailscale_items()
    local status, status_icon = tailscale_status_label()
    local status_hint = trim(tailscale_state.error)
    if status_hint == "" then
        status_hint = trim(tailscale_state.dns_name)
        if status_hint == "" then status_hint = trim(tailscale_state.ipv4) end
    end
    local items = {
        {title = "Tailscale " .. status, hint = status_hint, icon = status_icon,
            selectable = false, bold = tailscale_state.online == true},
        {title = "本机 Tailscale 地址", hint = trim(tailscale_state.ipv4) ~= "" and
            tostring(tailscale_state.ipv4) or "--", icon = "lan", selectable = false},
        {title = "AList 媒体地址", hint = tostring(options.alist_server or "未设置"),
            icon = "cloud", selectable = false},
    }
    if tailscale_state.installed == true then
        items[#items + 1] = {title = "打开 / 登录 Tailscale", icon = "login",
            value = action_value("tailscale-open")}
    else
        items[#items + 1] = {title = "安装 Tailscale", icon = "download",
            value = action_value("tailscale-install")}
    end
    if current_mode() == "host" then
        items[#items + 1] = {title = "房主：写入共享地址",
            hint = "Device Sharing · 100.x:5244",
            icon = "security", value = action_value("tailscale-host")}
    end
    items[#items + 1] = {title = "刷新状态", icon = "refresh",
        value = action_value("tailscale-refresh")}
    return items
end

-- 「运行模式」二级菜单：一台电脑只选一次，之后由配置文件记住。
-- 菜单本身是 palettes（可输入房主 100.x 地址），下面的条目负责点选。
local function build_mode_items()
    local mode = current_mode()
    local label, icon = mode_label()
    local hint = trim(options.alist_server)
    if mode == "off" then
        hint = "首次使用请选择一种模式"
    elseif mode == "host" then
        hint = trim(options.tailscale_host) ~= ""
            and ("本机共享地址：" .. tostring(options.tailscale_host)) or "尚未写入共享地址"
    end
    local items = {
        {title = "当前模式：" .. label, hint = hint, icon = icon,
            selectable = false, bold = true},
    }
    if mode == "host" then
        items[#items + 1] = {title = "房主模式已启用",
            hint = "别忘了在 Tailscale 后台对本设备点 Share",
            icon = "home_work", selectable = false, muted = true}
        if trim(options.tailscale_host) ~= "" then
            items[#items + 1] = {title = "复制共享地址",
                hint = "把 " .. tostring(options.tailscale_host) .. " 复制给观看者",
                icon = "content_copy", value = action_value("copy-host")}
        end
    else
        items[#items + 1] = {title = "切换为房主模式",
            hint = tailscale_busy and "正在配置网络，请稍候再点"
                or "发布本机 WatchParty\\media 并共享给观看者",
            icon = "home_work", value = action_value("mode-host")}
    end
    if mode == "viewer" then
        items[#items + 1] = {title = "观看者模式已启用",
            hint = tostring(options.alist_server or "未设置"),
            icon = "visibility", selectable = false, muted = true}
    else
        items[#items + 1] = {title = "切换为观看者模式",
            hint = trim(options.tailscale_host) ~= ""
                and ("房主地址：" .. tostring(options.tailscale_host))
                or "先在上方输入房主的 100.x.x.x 再回车",
            icon = "visibility", value = action_value("mode-viewer")}
    end
    if mode == "off" then
        items[#items + 1] = {title = "只在本机观看（不共享）",
            hint = "维持现状即可；随时可以再选模式",
            icon = "movie", selectable = false, muted = true}
    else
        items[#items + 1] = {title = "改回仅本机观看",
            hint = "关闭共享；已保存的地址会保留",
            icon = "movie", value = action_value("mode-local")}
    end
    if tailscale_state.installed ~= true then
        items[#items + 1] = {title = "提示：两种模式都需要先安装 Tailscale",
            hint = "请在下面的「Tailscale」条目里安装并登录",
            icon = "info", selectable = false, muted = true}
    end
    return items
end

local function build_input_items(kind, title, icon)
    return {
        {
            title = title,
            hint = tostring(setting_value(kind) or "未设置"),
            icon = icon,
            selectable = false,
        },
    }
end

local function build_items()
    local logged, status = connection_status()
    local members = user_entries()
    local playback_hint = logged and "已联机" or (state.connecting and "连接中" or "未连接")
    local function input_menu(kind, title, icon, current_title)
        return {
            id = "syncplay." .. kind,
            title = title,
            hint = tostring(setting_value(kind) or "未设置"),
            icon = icon,
            search_style = "palette",
            search_debounce = "submit",
            search_suggestion = tostring(setting_value(kind) or ""),
            on_search = {"script-message-to", script_name, "syncplay-input-submit", kind},
            items = build_input_items(kind, current_title, icon),
        }
    end

    return {
        {
            id = "syncplay.overview",
            title = "概览",
            hint = status,
            icon = "dashboard",
            search_style = "disabled",
            items = build_overview_items(),
        },
        {
            id = "syncplay.mode",
            title = "运行模式",
            hint = select(1, mode_label()),
            icon = select(2, mode_label()),
            search_style = "palette",
            search_debounce = "submit",
            -- 观看者模式下预填房主地址方便修改；其余模式（尤其房主）预填的
            -- 是本机地址，回车会指向自己，必须留空让用户填对方的 100.x.x.x。
            search_suggestion = current_mode() == "viewer"
                and tostring(options.tailscale_host or "") or "",
            on_search = {"script-message-to", script_name, "syncplay-mode-submit"},
            items = build_mode_items(),
        },
        {
            id = "syncplay.playback",
            title = "播放控制",
            hint = playback_hint,
            icon = "play_circle",
            search_style = "disabled",
            items = build_playback_items(),
        },
        {
            id = "syncplay.members",
            title = "房间成员",
            hint = tostring(#members + (logged and 1 or 0)),
            icon = "group",
            search_style = "disabled",
            items = build_member_items(),
        },
        input_menu("room", "房间设置", "meeting_room", "当前房间"),
        input_menu("name", "昵称设置", "person", "当前昵称"),
        input_menu("server", "服务器设置", "dns", "当前服务器"),
        {
            id = "syncplay.tailscale",
            title = "Tailscale",
            hint = select(1, tailscale_status_label()),
            icon = "vpn_lock",
            search_style = current_mode() == "viewer" and
                "palette" or "disabled",
            search_debounce = "submit",
            search_suggestion = tostring(options.tailscale_host or ""),
            on_search = current_mode() == "viewer" and
                {"script-message-to", script_name, "syncplay-tailscale-host-submit"} or nil,
            items = build_tailscale_items(),
        },
        {separator = true, selectable = false},
        {title = "关闭面板", icon = "close", value = action_value("close")},
    }
end

function open_panel()
    read_status()
    if not tailscale_busy and os.time() - tailscale_last_refresh >= 10 then
        refresh_tailscale_status(true)
    end
    if not uosc_available then
        mp.osd_message("Syncplay 控制面板需要 uosc（当前配置已启用）", 3)
        return
    end
    local data = {
        type = "syncplay",
        title = "Syncplay",
        footnote = tostring(setting_value("room") or "未设置") .. "  ·  " .. select(2, connection_status()),
        search_style = "disabled",
        callback = {script_name, "syncplay-menu-event"},
        -- 还没选过运行模式时把光标停在「运行模式」上（概览之后的第一项）。
        selected_index = current_mode() == "off" and 2 or 1,
        keep_open = true,
        persistent = true,
        items = build_items(),
    }
    local active_menu = mp.get_property("user-data/uosc/menu/type")
    if panel_open or active_menu == "syncplay" then
        mp.commandv("script-message-to", "uosc", "update-menu", utils.format_json(data))
    else
        mp.commandv("script-message-to", "uosc", "open-menu", utils.format_json(data))
    end
    -- Opening a menu can synchronously emit the replaced menu's close event.
    -- Record our state after the uosc command so that event cannot stale it.
    menu_kind = "syncplay"
    panel_open = true
end

function toggle_panel()
    safe_call("打开面板", open_panel)
end

mp.register_script_message("uosc-version", function()
    uosc_available = true
end)

mp.register_script_message("toggle", toggle_panel)

mp.register_script_message("syncplay-menu-event", function(json)
    local ok, event = pcall(utils.parse_json, json or "")
    if not ok or type(event) ~= "table" then return end
    if event.type == "close" then
        panel_open = false
        menu_kind = nil
        return
    end
    if event.type ~= "activate" then return end
    local value = event.value
    if type(value) ~= "string" then return end
    local action = value:match("^syncplay%-action:(.+)$")
    if action then
        safe_call("菜单操作 " .. action, run_action, action)
        -- 出错也不能跳过重开：否则面板停留在旧状态，看起来像"点了没反应"。
        if action ~= "close" then
            mp.add_timeout(0.15, function() if panel_open then safe_call("刷新面板", open_panel) end end)
        end
    end
end)

-- `on_search` command arrays receive their fixed arguments first, followed
-- by uosc's dynamic `(query, menu_id)` pair.  Keep this order in sync with
-- the command array above.
mp.register_script_message("syncplay-input-submit", function(kind, query, _menu_id)
    safe_call("输入提交", apply_text, kind or "room", query)
end)

mp.register_script_message("syncplay-tailscale-host-submit", function(query, _menu_id)
    if current_mode() ~= "viewer" then
        notify("当前不是观看者配置，已忽略房主地址输入")
        return
    end
    safe_call("写入共享地址", configure_tailscale_viewer, query)
end)

-- 「运行模式」的 palette 输入：既能填房主的 100.x 地址（切到观看者），
-- 也能直接输入"房主"两个字（切到房主模式）。
mp.register_script_message("syncplay-mode-submit", function(query, _menu_id)
    local text = trim(query)
    if text == "" then
        notify("请输入房主的 100.x.x.x 地址，或输入“房主”两个字")
        return
    end
    local lowered = text:lower()
    if lowered == "房主" or lowered == "房主模式" or lowered == "host" then
        safe_call("切换房主模式", activate_host_mode)
        return
    end
    safe_call("切换观看者模式", configure_tailscale_viewer, text)
end)

local function poll_status()
    safe_call("状态轮询", function()
        local changed = read_status()
        if changed and panel_open and menu_kind == "syncplay" then open_panel() end
        if panel_open and not tailscale_busy and os.time() - tailscale_last_refresh >= 10 then
            refresh_tailscale_status(true)
        end
    end)
end

mp.add_periodic_timer(0.5, poll_status)
mp.add_key_binding("ctrl+shift+s", "syncplay-ui-toggle", toggle_panel)
mp.add_timeout(0.7, function() refresh_tailscale_status(true) end)

-- 房主模式下随包 AList 必须常驻（观看者要从这里取流）。启动 mpv 时静默
-- 确认一次：已在运行就复用，没运行就拉起。启动阶段的状态查询可能还在
-- 运行，helper 是串行的，所以失败（被占用）时退避重试几次，最后才提示。
local function ensure_host_alist(attempt)
    if current_mode() ~= "host" then return end
    local started = run_setup_helper({"ensure-alist"}, function(ok, _payload, detail)
        if ok then return end
        if attempt < 3 then
            mp.add_timeout(2.0, function() ensure_host_alist(attempt + 1) end)
        else
            notify("AList 未能启动：" .. detail)
        end
    end, true)
    if not started and attempt < 3 then
        mp.add_timeout(1.5, function() ensure_host_alist(attempt + 1) end)
    end
end
mp.add_timeout(1.5, function() ensure_host_alist(1) end)

-- 合并包首次运行尚未选择角色：给一次明确指引，选过之后不再出现。
mp.add_timeout(2.5, function()
    if current_mode() ~= "off" then return end
    mp.osd_message(
        "WatchParty 首次使用：按 Ctrl+Shift+S 打开面板，在「运行模式」中选择房主或观看者",
        8)
end)

mp.register_event("shutdown", function()
    if panel_open then close_menu() end
end)

if options.auto_start == true or tostring(options.auto_start):lower() == "yes" then
    mp.add_timeout(0.5, launch_client)
end
