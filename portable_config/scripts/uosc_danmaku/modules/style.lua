-- 弹幕样式持久化
-- 需求：在弹幕样式菜单里修改一次样式后，该值就变成新的默认值，不需要每次播放都重新设置。
-- 实现：把修改过的样式写入 script-opts/uosc_danmaku.conf，
--       下次启动时由 modules/options.lua 里的 read_options 读回并覆盖内置默认值。
--       文件里已有的其它配置项和注释不会被破坏，只增删对应的样式行。
local msg = require('mp.msg')
local utils = require("mp.utils")

-- 允许持久化的样式项（与弹幕样式菜单一一对应）
local STYLE_KEYS = {
    "bold", "fontsize", "outline", "shadow", "scrolltime", "opacity", "displayarea",
}
local STYLE_KEY_SET = {}
for _, key in ipairs(STYLE_KEYS) do
    STYLE_KEY_SET[key] = true
end

local conf_path = nil

local function get_conf_path()
    if conf_path == nil then
        conf_path = mp.command_native({ "expand-path", "~~/script-opts/uosc_danmaku.conf" })
    end
    return conf_path
end

local function read_lines(path)
    local lines = {}
    local file = io.open(path, "r")
    if not file then
        return lines
    end
    for line in file:lines() do
        lines[#lines + 1] = line
    end
    file:close()
    return lines
end

local function ensure_directory(path)
    local info = utils.file_info(path)
    if info ~= nil then
        return true
    end
    local platform = mp.get_property("platform")
    local command
    if platform == "windows" or os.getenv("windir") ~= nil then
        command = string.format('mkdir "%s" >NUL 2>NUL', path)
    else
        command = string.format('mkdir -p "%s" 2>/dev/null', path)
    end
    os.execute(command)
    return utils.file_info(path) ~= nil
end

local function write_lines(path, lines)
    local directory = get_parent_directory(path)
    if directory and directory ~= "" then
        ensure_directory(directory)
    end
    local file, err = io.open(path, "w")
    if not file then
        return false, err
    end
    local text = table.concat(lines, "\n")
    if text ~= "" then
        text = text .. "\n"
    end
    file:write(text)
    file:close()
    return true
end

-- 转换成 mp.options 能正确解析的写法
local function format_value(key, value)
    if key == "bold" then
        return value and "yes" or "no"
    end
    if type(value) == "number" then
        if value == math.floor(value) and math.abs(value) < 1e15 then
            return string.format("%d", value)
        end
        return tostring(value)
    end
    return tostring(value)
end

-- 写入或更新一项样式（重复的同名配置会合并成一条）
function set_style_override(key, value)
    if not STYLE_KEY_SET[key] then
        return false
    end
    local path = get_conf_path()
    if not path then
        msg.warn("弹幕样式保存失败：无法定位 uosc_danmaku.conf 路径")
        return false
    end

    local pattern = "^%s*" .. key .. "%s*="
    local replacement = string.format("%s=%s", key, format_value(key, value))
    local kept, found = {}, false
    for _, line in ipairs(read_lines(path)) do
        if line:match(pattern) then
            if not found then
                kept[#kept + 1] = replacement
                found = true
            end
        else
            kept[#kept + 1] = line
        end
    end
    if not found then
        kept[#kept + 1] = replacement
    end

    local ok, err = write_lines(path, kept)
    if not ok then
        msg.warn("弹幕样式保存失败：" .. tostring(err))
    else
        msg.verbose(string.format("弹幕样式已保存：%s", replacement))
    end
    return ok
end

-- 删除某项样式覆盖，使其回到内置默认值
function clear_style_override(key)
    if not STYLE_KEY_SET[key] then
        return false
    end
    local path = get_conf_path()
    if not path then
        return false
    end

    local pattern = "^%s*" .. key .. "%s*="
    local kept, removed = {}, false
    for _, line in ipairs(read_lines(path)) do
        if line:match(pattern) then
            removed = true
        else
            kept[#kept + 1] = line
        end
    end
    if not removed then
        return true
    end

    local ok, err = write_lines(path, kept)
    if not ok then
        msg.warn("弹幕样式恢复失败：" .. tostring(err))
    else
        msg.verbose(string.format("弹幕样式已恢复默认：%s", key))
    end
    return ok
end

return {
    set = set_style_override,
    clear = clear_style_override,
    keys = STYLE_KEYS,
}
