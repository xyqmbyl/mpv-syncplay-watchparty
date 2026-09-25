function trim(s)
   return (s:gsub("^%s*(%S+)%s*", "%1"))
end

-- 剪贴板读取命令按平台选择：Windows 用 powershell，macOS 用 pbpaste。
-- 两侧都把结果写到 stdout，因此后续逻辑完全一致。
local clip_args
if mp.get_property_native("platform") == "darwin" then
   clip_args = { "/usr/bin/pbpaste" }
else
   clip_args = { "powershell", "-Command", "Get-Clipboard", "-Raw" }
end

function openURL()
   
   subprocess = {
      name = "subprocess",
      args = clip_args,
      playback_only = false,
      capture_stdout = true,
      capture_stderr = true
   }
   
   mp.osd_message("正在加载剪贴板中的URL...")
   
   r = mp.command_native(subprocess)
   
   --failed getting clipboard data for some reason
   if r == nil or r.status == nil or r.status < 0 then
      mp.osd_message("获取剪贴板数据失败！")
      if r == nil then
         print("Error: 无法启动剪贴板读取命令")
      else
         print("Error(string): "..tostring(r.error_string))
         print("Error(stderr): "..tostring(r.stderr))
      end
      return
   end
   
   url = r.stdout
   
   if not url then
      return
   end
   
   --trim whitespace from string
   url=trim(url)

   if not url then
      mp.osd_message("剪贴板为空")
      return
   end
   
   --immediately resume playback after loading URL
   if mp.get_property_bool("core-idle") then
      if not mp.get_property_bool("idle-active") then
         mp.command("keypress space")
      end
   end

   --try opening url
   --will fail if url is not valid
   mp.osd_message("尝试打开URL:\n"..url)
   mp.commandv("loadfile", url, "replace")
end

mp.add_key_binding("ctrl+v", openURL)
