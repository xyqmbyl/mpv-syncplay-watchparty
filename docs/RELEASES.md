# WatchParty 发布与「快捷上传」流程留痕

本文件写给**下一次接手发布/上传的 AI 或人**。它记录三件事：

1. 版本号在本仓库里到底出现在哪几处、bump 时要同步改什么；
2. 当「多个功能改动合并成一个版本」时的正确操作顺序；
3. 三个 GitHub Actions 工作流各自的定位、触发方式、输入参数约束、附件命名与数量，以及发布后的验证清单。

所有事实都标注了来源文件与行号。凡是**没能亲自核实**的内容，全部集中放在文末「未验证/待确认」一节。正文中凡属推断（而非直接读到的原文或实测结果）的结论，均已就地标注「推断」二字。

---

## 一、版本号出现的位置与 bump 清单

### 1.1 结论先说

本仓库**没有把项目自身版本号写进任何打包脚本、启动器或代码常量**。正文形态的版本号只写在一处（另一处载体是 Git tag，见 1.2）：

| 位置 | 行号 | 当前值 | bump 时怎么改 |
| --- | --- | --- | --- |
| `CHANGELOG.md` | `CHANGELOG.md:3` | `## 0.5.1 - 2026-09-26` | 在文件顶部插一条新的 `## <新版本号> - <YYYY-MM-DD>`（现有 0.5.1 条目整体下移），旧条目原样保留 |

证据：遍历全仓（`git grep` 全量扫描）后，`0.5.1` / `0.5.0` / `v0.5.0` 这类版本字串只命中两处：
- `CHANGELOG.md:3`（`## 0.5.1 - 2026-09-26`）与 `CHANGELOG.md:22`（`## 0.5.0 - 2026-09-26`）；
- `WatchParty/ViewerPackage/ui-baseline/README.md:19`（`## v0.5.1 定制点（在 v0.3.0 基线之上）`）——这是 UI 基线的历史标注，**不是发布版本号，bump 时不要动它**。

`CHANGELOG.md` 格式（照抄现有两条）：一级标题 `# Changelog`（`CHANGELOG.md:1`），版本条目用二级标题 `## <版本号> - <YYYY-MM-DD>`（`CHANGELOG.md:3`、`CHANGELOG.md:22`），条目内容用中文 `-` 列表，每条 1~3 行、面向用户可读（`CHANGELOG.md:5-20` 就是 0.5.1 的写法）。

### 1.2 版本号在别处的真实形态

| 位置 | 实际内容 | 说明（均已在文件中核实） |
| --- | --- | --- |
| Git tag | `v0.5.1`（`git describe --tags --abbrev=0` 的结果） | 版本号的第二处载体；tag 名格式必须匹配 `^v[0-9][A-Za-z0-9._-]*$`，见 `.github/workflows/publish-release-assets.yml:20-23` 与 `.github/workflows/upload-existing-artifacts.yml:13-16` 所描述的正则校验 |
| Release 标题/标签 | 与 tag 同名 | Release 由人工创建（本仓库未内置创建 Release 的工作流，见「未验证/待确认」第 4 条） |
| `.github/workflows/release.yml` | 触发条件 `tags: ['v*']`（`.github/workflows/release.yml:9`） | 只有**推送 `v*` 标签**才会自动构建；工作流名 `构建发布安装包`（`.github/workflows/release.yml:1`） |
| 打包脚本 | `WatchParty/ViewerPackage/build-watchparty-package.ps1` 内**没有项目版本号常量**；只有第三方组件版本常量（AList `v3.64.0`、Tailscale `1.102.3` 等） | 因此 bump 版本号**不需要**改打包脚本 |
| 启动器 `.bat` | `WatchParty/启动.bat`、`WatchParty/首次运行.bat` 内均无版本号 | 不需要改 |

### 1.3 bump 版本号的最小动作集

1. 改 `CHANGELOG.md`：顶部新增一个版本条目（**只加一条**，见第二节）。
2. 提交，然后打 tag 并推送（tag 名必须形如 `v0.5.2`）。
3. 其余地方**不用改**。

> 版本号从 `0.5.1` 起算，下一位是 `0.5.2`；若视为新功能可升 minor 到 `0.6.0`。选哪个由发布者/用户决定，本文件只记录现状，不替你决定。

---

## 二、「两个功能改动合并成一个版本」的正确操作顺序

背景约束来自用户原话（m00112）：

> 「是两个功能改动一起bump 版本号，这次先不改，但下次那快捷上传。」

意思是：**两个功能改动共用一个新的版本号，只 bump 一次**，而不是各自 bump 一次。而且「这次先不改」，即当下不动 `CHANGELOG.md`、不 bump，等下次上传时一起做。

推荐顺序（每一步都有对应证据）：

| 步骤 | 做什么 | 依据 |
| --- | --- | --- |
| 1 | 两个功能改动的代码全部改完并自测 | `AGENTS.md:152-153`（发布纪律）、`AGENTS.md:89-109`（测试命令） |
| 2 | 跑提交前必须通过的测试 | `AGENTS.md:95` `python -m unittest discover -s portable_config\syncplay -p "test_*.py" -v`；改 UI/打包还要 `AGENTS.md:98` `python WatchParty\ViewerPackage\test_ui_baseline.py -v`；动 macOS 构建器再加 `AGENTS.md:101` `python WatchParty\ViewerPackage\test_macos_launcher.py -v` |
| 3 | 改打包器的话，重建目标包并核对 `output/` 下的 ZIP + `.sha256` + `PACKAGE-CONTENTS.sha256` | `AGENTS.md:108-109` |
| 4 | **在 `CHANGELOG.md` 顶部写「一个」新版本条目**，把两个功能改动都写进这同一个条目 | 用户要求（m00112）+ `AGENTS.md:86-87`「行为变更必须同步 README / CHANGELOG 的 Unreleased 段。发布版本号写入 CHANGELOG 顶层」 |
| 5 | 同步 README（若用户可见行为有变化） | 同上 `AGENTS.md:86-87` |
| 6 | 只 `git add` 自己明确改过的文件后提交（**禁止 `git add -A` / `git add .`**），提交信息用 Conventional Commits（`feat:` / `fix:` / `docs:` / `ci:` …） | `AGENTS.md:126-128`、`AGENTS.md:148-149` |
| 7 | 推 `main`，然后打并推送 tag：`git tag v<新版本号>` + `git push origin v<新版本号>` | `.github/workflows/release.yml:9` `tags: ['v*']` |
| 8 | CI 自动跑 `release.yml`，产出 3 个包（Windows x86、macOS AppleSilicon、macOS Intel） | `.github/workflows/release.yml` 两个 job：`windows-x86` 与 `macos` matrix |
| 9 | **在开发机上本地构建 Windows x64 包**（CI 不产出它） | `.github/workflows/release.yml` 文件头注释明写「Windows x64 合并包携带开发机签名的 64 位运行时，仍由开发机构建后上传到同一个 Release」；`README.md:169-173` 同口径 |
| 10 | 把 CI 的 3 个包用 `publish-release-assets.yml` 发布到 Release，再手动补上 Windows x64 包 | 见第三节 |
| 11 | 发布后按第四节验证 | — |

**关键点：版本号只 bump 一次、`CHANGELOG.md` 只新增一个条目。** 两个功能改动不是两行标题，而是同一个条目下的多条 `-` 列表项。

---

## 三、「快捷上传」到底怎么做

### 3.1 附件全景（一次完整发布应有 8 个附件）

| 附件名 | 谁产出 | 是否在 CI 里 |
| --- | --- | --- |
| `WatchParty-Windows-x86.zip` | `release.yml` 的 `windows-x86` job | ✅ 是 |
| `WatchParty-Windows-x86.zip.sha256` | 同上（构建脚本写 sidecar） | ✅ 是 |
| `WatchParty-macOS-AppleSilicon.zip` | `release.yml` 的 `macos` matrix（`os: macos-14`, `arch: arm64`, `label: AppleSilicon`） | ✅ 是 |
| `WatchParty-macOS-AppleSilicon.zip.sha256` | 同上 | ✅ 是 |
| `WatchParty-macOS-Intel.zip` | `release.yml` 的 `macos` matrix（`os: macos-15-intel`, `arch: intel`, `label: Intel`） | ✅ 是 |
| `WatchParty-macOS-Intel.zip.sha256` | 同上 | ✅ 是 |
| `WatchParty-Windows-x64.zip` | **开发机本地**跑 `build-watchparty-package.ps1` | ❌ **不在 CI 里，必须手动上传** |
| `WatchParty-Windows-x64.zip.sha256` | 同上 | ❌ 同上 |

> 每个 ZIP 都带一个**同名 + `.sha256`** 的校验文件，这是全仓库的硬约定：`AGENTS.md:153`「全部 Release 附件附同名 `.sha256`」、`README.md:169-173`「各带同名 `.sha256`」。

### 3.2 三个工作流的分工

| 工作流文件 | 名称 | 定位 | 触发方式 | 输入参数 |
| --- | --- | --- | --- | --- |
| `.github/workflows/release.yml` | `构建发布安装包`（`:1`） | **构建**。推 tag 时构建 Windows x86 + 两个 macOS 包并上传为 Actions artifact | ① `push` 且 `tags: ['v*']`（`:9`）② 手动 `workflow_dispatch`（`:10`） | `platforms`：`type: choice`，`options: [all, macos]`，`default: all`，描述「构建平台（macos 用于只重建两个 macOS 合并包）」（`:12-16`） |
| `.github/workflows/publish-release-assets.yml` | `发布官方安装包附件`（`:1`） | **首批上传 / 覆盖上传**。从一次成功的 `release.yml` 运行取 3 个官方包，校验 SHA-256 后以正式名称写入 Release | 手动 `workflow_dispatch`（`:10`） | `run_id`、`source_commit`、`release_tag`，三者 `required: true`、`type: string`（`:12-23`） |
| `.github/workflows/upload-existing-artifacts.yml` | `将成功构建的包补传到现有 Release`（`:1`） | **补传缺失附件**。只搬运既有构建产物，**拒绝覆盖任何同名附件** | 手动 `workflow_dispatch`（`:7`） | `run_id`、`release_tag`，两者 `required: true`、`type: string`（`:9-16`）；**没有** `source_commit` |

两个上传工作流的文件头注释已经把定位写清楚了：

- `publish-release-assets.yml` 头注释：从一次成功的 `release.yml` 运行取出 3 个官方合并包（Windows x86、macOS Apple Silicon、Intel），**校验 SHA-256 后以正式名称覆盖** Release 上的同名附件；「Windows x64 合并包由开发机构建后上传，不经过本工作流的 Actions 产物」；「本工作流只处理下列 6 个确定的附件名称（3 个 ZIP + 3 个 `.sha256`），不修改 Release 说明，也不触碰其他附件」。
- `upload-existing-artifacts.yml` 头注释：只搬运既有构建产物（Windows x86、macOS Apple Silicon 与 Intel 三个合并包）；「不会创建标签或 Release，也不会覆盖已有附件」。

### 3.3 输入参数的格式约束（正则原样引用）

三个上传相关正则（`publish-release-assets.yml` 与 `upload-existing-artifacts.yml` 中一致）：

| 参数 | 必须匹配 | 不匹配时的报错 |
| --- | --- | --- |
| `run_id` | `^[1-9][0-9]*$` | `run_id 必须是正整数` |
| `source_commit` | `^[0-9a-f]{40}$` | `source_commit 必须是完整的小写提交 SHA` |
| `release_tag` | `^v[0-9][A-Za-z0-9._-]*$` | `release_tag 格式无效` |

要点：`source_commit` 必须是**完整 40 位、全小写**的提交 SHA（缩写、大写都不行）；`release_tag` 必须带 `v` 前缀且以数字开头。

`publish-release-assets.yml` 还会断言（`:50-52`）：

- run 的 `.status == "completed"`
- run 的 `.conclusion == "success"`
- run 的 `.head_sha == $SOURCE_COMMIT`
- run 的 `.workflow_id` 等于 `release.yml` 的工作流 id

任一不满足即报 `构建状态、来源提交或工作流不匹配`。

`upload-existing-artifacts.yml` 的等价断言（`:42-49`）是：`conclusion` 必须为 `success`（否则 `指定的构建运行未成功`）、`workflow_id` 必须是 `release.yml`（否则 `run_id 不是发布包构建工作流`）、`head_sha` 必须等于 tag 指向的提交 SHA（否则 `构建提交与 Release 标签不一致`）。

**区别的关键**：`publish` 让你显式交出构建用的 `source_commit`；`upload-existing` 不问你，而是自己去问 tag 指向哪个提交，然后要求构建提交与它一致。

### 3.4 校验与覆盖行为对比

| 行为 | `publish-release-assets.yml` | `upload-existing-artifacts.yml` |
| --- | --- | --- |
| 下载的 artifact | `macos-AppleSilicon`、`macos-Intel`、`windows-x86` | 同样三个，落地目录名不同（`artifacts/windows-x86`、`artifacts/macos-AppleSilicon`、`artifacts/macos-Intel`） |
| 附件数量断言 | 恰好 **6** 个（`[[ ${#expected[@]} -eq 6 ]]`，否则 `附件数量必须为 6`） | 3 个 ZIP + 3 个 sidecar |
| SHA-256 比对方式 | `digest=$(sha256sum "$archive" | cut -d ' ' -f 1)`，sidecar 先剥 `\r`（`checksum_line=${checksum_line%$'\r'}`），再比 `"$checksum_line" == "$digest *${archive##*/}"` | 相同做法，注释里写明「Windows PowerShell 写出的 sidecar 是 CRLF」 |
| ZIP 结构校验 | `unzip -tq "$archive"`，否则 `ZIP 结构损坏：$archive` | 校验 ZIP 与 sidecar 是否成对存在 |
| 多余文件 | `diff -u` 对比期望清单与 `find artifacts -type f` | 同样 `diff -u` |
| 上传命令 | `gh release upload "$RELEASE_TAG" "$upload_path" --repo "$GITHUB_REPOSITORY" --clobber`（**可覆盖**），失败重试最多 3 次（间隔 `sleep 5`） | `gh release upload "$RELEASE_TAG" "${files[@]}" --repo "$GITHUB_REPOSITORY"`（**无 `--clobber`**） |
| 重名处理 | 远端若已存在且 `size`/`digest` 完全一致则跳过，否则 `--clobber` 覆盖 | **预检 + 上传前双重**检查，命中即 `Release 已有附件，拒绝覆盖：$asset` 并退出 |
| 是否改 Release 说明 | 否（末行 echo 明确「Release 说明和其他附件未修改」） | 否 |

一句话记法：**缺东西 → `upload-existing-artifacts.yml`；要覆盖首批 → `publish-release-assets.yml`。**

### 3.5 Windows x64 包必须本机构建（关键事实）

- `release.yml` 只出 3 个包；它同时支持 `workflow_dispatch` + `platforms=macos`，用于**只重建两个 macOS 包**。
- Windows x64 包不在 CI 里，必须由开发机本地构建后手动上传。依据：`release.yml` 文件头注释、`AGENTS.md:152-153`、`README.md:169-173`。
- 本地构建命令（`README.md:149-152` 与 `AGENTS.md:108-109` 的口径）：

```powershell
# 在仓库根目录 D:\MPV-Duke-Enhanced-Extra\WatchParty\repo-latest 下执行
.\WatchParty\ViewerPackage\build-watchparty-package.ps1 -Arch x64
# 源码检出与本机 mpv 运行目录分开时，用 -NativeRoot 指向已有的 64 位便携目录：
.\WatchParty\ViewerPackage\build-watchparty-package.ps1 -Arch x64 -NativeRoot "D:\MPV-Duke-Enhanced-Extra"
```

脚本参数（`WatchParty/ViewerPackage/build-watchparty-package.ps1` 参数块）：`-OutputDirectory`、`-PackageName`、`-Arch`（`[ValidateSet('x64','x86')]`，默认 `x64`）、`-NativeRoot`。默认包名 `WatchParty-Windows-$Arch`，默认输出目录为构建器同级的 `output`。产物 = `<包名>.zip` + `<包名>.zip.sha256`（sidecar 内容格式 `"<sha256 小写 hex> *<zip 文件名>"`，行尾 CRLF）+ 包内 `PACKAGE-CONTENTS.sha256` 逐文件清单。

macOS 侧（`README.md:162-165`）：`./WatchParty/ViewerPackage/build-macos-package.sh --arch arm64 [--output-dir DIR]` / `--arch intel [--output-dir DIR]`。

### 3.6 触发命令（取自工作流实际参数）

```bash
REPO=xyqmbyl/mpv-syncplay-watchparty

# 1) 推 tag 后自动触发构建；也可手动重跑（platforms 只能选 all 或 macos）
gh workflow run release.yml --repo $REPO --ref main -f platforms=all

# 2) 找到那次成功的构建运行，拿到 run_id
gh run list --repo $REPO --workflow=release.yml --limit 5

# 3) 首批/覆盖上传 3 个官方包（需完整 40 位小写 SHA）
gh workflow run publish-release-assets.yml --repo $REPO \
  -f run_id=<run_id> \
  -f source_commit=<40位小写commit SHA> \
  -f release_tag=v0.5.2

# 4) 补传缺失附件（只这两个参数；已有同名附件会被拒绝）
gh workflow run upload-existing-artifacts.yml --repo $REPO \
  -f run_id=<run_id> \
  -f release_tag=v0.5.2

# 5) 手动补上 Windows x64 包（CI 不产出它）
gh release upload v0.5.2 \
  "WatchParty\ViewerPackage\output\WatchParty-Windows-x64.zip" \
  "WatchParty\ViewerPackage\output\WatchParty-Windows-x64.zip.sha256" \
  --repo $REPO
```

> 步骤 3 与 5 谁先都行：`publish-release-assets.yml` 只处理它那 6 个确定名称的附件，不会碰你的 Windows x64 包（头注释已声明「也不触碰其他附件」）。

---

## 四、发布后验证清单

| # | 检查项 | 怎么查 | 期望 |
| --- | --- | --- | --- |
| 1 | 附件数量 | Release 页面或 `gh release view v0.5.2 --repo xyqmbyl/mpv-syncplay-watchparty --json assets` | 一次完整发布 = **8 个**：4 个 ZIP（x64 / x86 / macOS-AppleSilicon / macOS-Intel）+ 各自同名 `.sha256` |
| 2 | SHA-256 是否与 sidecar 一致 | 下载 ZIP 与 `.sha256` 后本地重算并比对（CI 侧由 `publish-release-assets.yml` 用 `sha256sum` + sidecar 严格比对完成） | 每对都相等 |
| 3 | ZIP 完整性 | `unzip -tq WatchParty-*.zip`（CI 侧同样步骤，失败即 `ZIP 结构损坏`） | 无错误输出 |
| 4 | Release 说明是否被改动 | 对比发布前后 Release 正文 | **不应被改动**——两个上传工作流都声明不修改 Release 说明 |
| 5 | 是否混入意外附件 | Release 附件列表应只有上述 8 个 | 无多余文件（两个工作流都用 `diff -u` 拒绝未预期文件，但手动上传不受此约束） |
| 6 | Windows x64 包是否真的是本次代码 | 本地重建时间戳/内容与提交对应 | 是（CI 不产出它，只能靠本地重建） |

补充：`CHANGELOG.md:42` 记录过先例——v0.5.0 的 Windows 附件曾**就地替换（未另发版本）**。也就是说附件可以原地替换，但那种情况必须在 `CHANGELOG` 里写清楚。

---

## 五、本次改动对应的下一步

### 5.1 当前状态（截至本文件写作时）

- 本次两项功能改动（日志能力三项 + 相关配套）**已提交并已推送**：`main` 上的提交 `e01c566`（`feat(logs): 三段日志落盘 + 右键「导航 > 日志」菜单（含自动清理）`）。**尚未 bump 版本号**，`CHANGELOG.md` 顶层仍是 `## 0.5.1 - 2026-09-26`（`CHANGELOG.md:3`）。
- 提交之后又做了一次菜单位置调整：日志子菜单从 **导航 → 日志** 移到 **工具 → 日志**（用户要求，菜单逻辑与功能不变）。该调整涉及 `portable_config/input_contextmenu_plus.conf`、`portable_config/script-opts/log_menu.conf`、`README.md`、`CHANGELOG.md`、`docs/RELEASES.md` 五个文件，**已作为提交 `13ce3b5`（`refactor(menu): 日志菜单从「导航」移到「工具」，功能与逻辑不变`）提交并推送**；推送后 `git status -sb` 显示 `## main...origin/main`（无 ahead/behind），工作树干净。注意 `e01c566` 的提交信息里写的是「导航 > 日志」，那是提交当时的位置，不改写历史。
- 用户已明确要求（m00112）：「是两个功能改动一起bump 版本号，这次先不改，但下次那快捷上传。」→ **下次上传时，两项改动合并成一个新版本号，只 bump 一次。**
- 涉及的两个功能面向：
  1. **日志能力**：mpv 本体日志（`portable_config/scripts/contextmenu_plus.lua`）、启动器日志（`WatchParty/启动.bat`、`WatchParty/首次运行.bat`）、联机客户端日志（`portable_config/syncplay/mpv_syncplay.py` 的 `--log-file`）。
  2. 与之配套的菜单/配置/清理能力（`portable_config/input_contextmenu_plus.conf`、`portable_config/script-opts/log_menu.conf`、`portable_config/scripts/syncplay_ui.lua`、`portable_config/syncplay/log_maintenance.py`、`portable_config/syncplay/log_redact.py` 及两个测试文件）。

### 5.2 建议执行的命令序列

```bash
cd D:\MPV-Duke-Enhanced-Extra\WatchParty\repo-latest

# 0) 先看清楚现状（多 Agent 可能在同一工作区，动手前必查）
git status
git log --oneline -5
git describe --tags --abbrev=0          # 预期 v0.5.1

# 1) 上传前复核：log_redact.py 已在两个打包脚本的白名单里（见第六节，已修复，复核即可）

# 1.5) 「工具 → 日志」菜单位置调整已于 13ce3b5 提交并推送（见 5.1 第二条），此处只需确认：
git log --oneline -3        # 预期能看到 13ce3b5 与 e01c566
git status -sb              # 预期 ## main...origin/main，无 ahead/behind

# 2) 提交前必须通过的测试（AGENTS.md:95 / :98 / :101）
python -m unittest discover -s portable_config\syncplay -p "test_*.py" -v
python WatchParty\ViewerPackage\test_ui_baseline.py -v
python WatchParty\ViewerPackage\test_macos_launcher.py -v

# 3) 在 CHANGELOG.md 顶部写「一个」新版本条目，把两项改动都写进同一条

# 4) 只 add 自己改过的文件（禁止 git add -A / git add .）
git add CHANGELOG.md portable_config/... WatchParty/...
git commit -m "feat(logging): ..."      # Conventional Commits

# 5) 推 main，打并推 tag（tag 名必须形如 v0.5.2，供 workflow 正则校验）
git push origin main
git tag v0.5.2
git push origin v0.5.2

# 6) 本地构建 Windows x64 包（CI 不产出）
.\WatchParty\ViewerPackage\build-watchparty-package.ps1 -Arch x64 -NativeRoot "D:\MPV-Duke-Enhanced-Extra"

# 7) 等 release.yml 跑完，拿 run_id
gh run list --repo xyqmbyl/mpv-syncplay-watchparty --workflow=release.yml --limit 5

# 8) 首批上传 3 个官方包（6 个附件），再手动补 Windows x64
gh workflow run publish-release-assets.yml --repo xyqmbyl/mpv-syncplay-watchparty `
  -f run_id=<run_id> -f source_commit=<40位小写SHA> -f release_tag=v0.5.2
gh release upload v0.5.2 "WatchParty\ViewerPackage\output\WatchParty-Windows-x64.zip" "WatchParty\ViewerPackage\output\WatchParty-Windows-x64.zip.sha256" --repo xyqmbyl/mpv-syncplay-watchparty

# 9) 按第四节验证
```

> **推送通道备注（实测）**：本机到 `github.com:443` 的 HTTPS 会被重置/超时
> （`fatal: unable to access ...: Recv failure: Connection was reset`、
> `Failed to connect to github.com:443 after 21098 ms`），但 `ssh.github.com:443` 可用。
> 若 `git push origin main` 报上述错误，改用：
>
> ```bash
> git push ssh://git@ssh.github.com:443/xyqmbyl/mpv-syncplay-watchparty.git main
> ```

（第 8 步在 PowerShell 下用反引号续行，在 bash 下改成 `\`。）

---

## 六、`log_redact.py` 打包白名单：曾经的缺陷与当前状态

### 6.1 曾经的缺陷（HEAD 真实存在）

本仓库 HEAD 版本的两个打包脚本**都没有**把 `log_redact.py` 复制进包，而联机客户端在模块顶层就要导入它：

- `portable_config/syncplay/mpv_syncplay.py:52` `from .log_redact import redact`，回退分支 `portable_config/syncplay/mpv_syncplay.py:55` `from log_redact import redact`。
- 实测证据：`git grep -n 'log_redact' HEAD` **零命中**（HEAD 里既没有该模块，也没有任何脚本提到它）——当时的 `log_redact.py` 只存在于**未跟踪的工作树**上（`git status --porcelain` 显示 `?? portable_config/syncplay/log_redact.py`）。
- 后果（推断，未实际构建复现）：包内联机客户端执行到顶层 `from log_redact import redact` 会 `ImportError`，**在包内直接崩**。

### 6.2 为何单测暴露不了

单测是在源码目录里跑的，`log_redact.py` 就在 `mpv_syncplay.py` 旁边，两份导入路径都能成功；测试断言的是「脱敏逻辑对不对」，而不是「发布包里有没有这个文件」。全仓也没有任何测试或构建断言打包白名单的条目集合，所以只有真正构建一次包才会暴露。——「没有白名单断言」这一点由 `grep` 全仓打包脚本与 `test_*.py` 均无白名单相关断言得出（事实）；「只有构建才会暴露」为**推断**。

### 6.3 当前状态：已修复并已随 `e01c566` 提交推送

两个打包脚本的白名单现在都显式包含 `log_redact.py`，以及本次日志能力同批新增的 `log_maintenance.py`、`log_menu.conf`：

| 脚本 | 复制白名单（已含修复） | 构建后审计列表 | 包内自测 |
| --- | --- | --- | --- |
| `WatchParty/ViewerPackage/build-watchparty-package.ps1` | `:217` `'syncplay\log_maintenance.py',`、`:218` `'syncplay\log_redact.py',`；界面层 `:232` `'script-opts\log_menu.conf',` | `:448` `'portable_config\syncplay\log_maintenance.py',`、`:449` `'portable_config\syncplay\log_redact.py',`、`:451` `'portable_config\script-opts\log_menu.conf',` | `:652` 用包内 Python 跑 `log_maintenance.py --help` |
| `WatchParty/ViewerPackage/build-macos-package.sh` | `:222` `tailscale_integration.py watchparty_setup.py log_maintenance.py \`、`:223` `log_redact.py; do`；`:242-243` `cp … log_menu.conf` | 无同构审计列表（改为自检后重跑归档审计，见 `:462`） | `:454`-`:456`，见 6.4 |

工作树实测（本次改写时重新测量，均为工作树当前状态，非 HEAD）：

- `WatchParty/ViewerPackage/build-watchparty-package.ps1`：42974 字节，sha256 `e9f2d59bbf914fe4506a9d9f4be529fcc720f5ce1113e002fa8062b6fae28280`，行尾全 LF（939 LF / 0 CRLF）。
- `WatchParty/ViewerPackage/build-macos-package.sh`：22980 字节，sha256 `cbf2296cd252c7b551ad2481af2cd2d27ddd2615147fd03bc76fc603b7e778e8`，512 LF / 0 CRLF。
- 工作树 `git grep -n 'log_redact'` 命中 5 处（只统计**已跟踪**文件）：`WatchParty/ViewerPackage/build-macos-package.sh:223`、`WatchParty/ViewerPackage/build-watchparty-package.ps1:218`、`WatchParty/ViewerPackage/build-watchparty-package.ps1:449`、`portable_config/syncplay/mpv_syncplay.py:52`、`portable_config/syncplay/mpv_syncplay.py:55`。未跟踪的 `portable_config/syncplay/log_redact.py`、`portable_config/syncplay/test_mpv_syncplay.py` 不出现在 `git grep` 结果里，这是该命令只扫已跟踪文件的预期行为。

### 6.4 残留缺口

- **macOS 包的运行时自检不含 `log_maintenance.py` 的 `--help`**（Windows 侧 `build-watchparty-package.ps1:652` 有）。实测 `WatchParty/ViewerPackage/build-macos-package.sh:451` 注释为 `# 7. 运行时自检：包内 python、AList、mpv 都要能启动；Tailscale 安装包哈希核对。`，其后只有 `:454` `watchparty_setup.py --help`、`:455` `mpv_syncplay.py --help`、`:456` `tailscale_integration.py verify-installer`——**确实没有** `log_maintenance.py --help`。
- **两个脚本都没有单独针对 `log_redact.py` 的自检项**。它只被间接覆盖：`mpv_syncplay.py:52` 的导入在模块顶层，所以两个脚本各自的 `mpv_syncplay.py --help` 自检一旦执行到该导入语句就会连带验证它。也就是说白名单再漏掉 `log_redact.py` 时，构建会在自检阶段失败而不是静默产出坏包——但这条是**推断**，本次未实际构建验证（见第八节）。
- **本节所有「已修复」证据当时取自未提交的工作树；这些改动现已随 `e01c566` 提交并推送**（实测 `git status --porcelain` 对两个打包脚本已无 ` M`，`main` 与 `origin/main` 同步）。下文按改写时的实际行号记录，供复核使用。

---

## 七、编码规范的已决议变更：两个 `.bat` 改为 GBK 无 BOM

工作树中的两个启动批处理已从「UTF-8 无 BOM + `chcp 65001`」改为「GBK/ANSI 无 BOM、不 `chcp`」。本节只记录事实与出处，不替用户做判断。

### 7.1 实测文件状态

| 文件 | 字节数 | sha256 | 行尾 | BOM | `chcp` 出现次数 |
| --- | --- | --- | --- | --- | --- |
| `WatchParty/启动.bat` | 1138 | `227f4cfd8f586f1377ee1b87005495786b32e1ce18acc119a20b51f4724b91e5` | CRLF=26 / LF=26（无裸 LF） | 无（首 3 字节 `40 65 63`，即 `@ec`） | 0 |
| `WatchParty/首次运行.bat` | 2000 | `9b3d2619b26840b08e8735c2bf44970fcffc11bd4888a2c948fe85b47c9d0d3c` | CRLF=47 / LF=47（无裸 LF） | 无（首 3 字节 `40 65 63`） | 2（均在 `:4` 的 `rem` 注释里，不是 `chcp` 命令；全文 grep `chcp` 的 2 处命中都在注释中） |

两份文件按 GBK（cp936）解码后中文正常；按 UTF-8 解码会乱码。

### 7.2 文件内的 rem 说明原文

`WatchParty/启动.bat:2-3`：

```
rem 注意：本文件必须保存为 GBK/ANSI（zh-CN）编码，不要改存 UTF-8！
rem Win11 24H2+ 上 cmd 解析含多字节 UTF-8 的批处理会错位（详见 首次运行.bat）。
```

`WatchParty/首次运行.bat:2-6`：

```
rem 注意：本文件必须保存为 GBK/ANSI（zh-CN）编码，不要改存 UTF-8！
rem Win11 24H2+ 上 cmd 解析含多字节 UTF-8 的批处理会错位（表现为“语法错误”
rem 后窗口一闪而过），UTF-8+chcp 65001 方案已不可用，故本文件用 GBK 且不 chcp。
rem 控制台默认就是 cp936，中文正常显示；Python 输出走 WriteConsoleW 不受影响，
rem 管道里的 --json 输出仍由 PYTHONIOENCODING=utf-8 保证为 UTF-8。
```

### 7.3 与 `AGENTS.md` 的关系（含变更前原文）

- **变更前的规范原文**（`git show HEAD:AGENTS.md` 第 2 条，原 `AGENTS.md:72-73`）：```.bat` 一律 UTF-8 无 BOM + CRLF，开头 `chcp 65001`，注意 `!` 延迟展开兼容；Python 输出显式 UTF-8；shell 脚本 LF。``
- **当前规范原文**（工作树 `AGENTS.md:72-78`）：`` `.bat` 一律 **GBK/ANSI（zh-CN）无 BOM + CRLF，不写 `chcp 65001`**，注意 `!` 延迟展开兼容。原因：Win11 24H2+ 的 cmd 解析含多字节 UTF-8 的批处理会错位（表现为 `语法错误` 后窗口一闪而过），UTF-8 + `chcp 65001` 方案已不可用；控制台默认 cp936，中文正常显示。Python 输出显式 UTF-8；管道里的 `--json` 输出由 `PYTHONIOENCODING=utf-8` 保证为 UTF-8；shell 脚本 LF。（本条于 2026-10 按用户裁决由「.bat 用 UTF-8 无 BOM + `chcp 65001`」改为现状，详见 `docs/RELEASES.md`。）``
- **状态**：`AGENTS.md` 的编码条目已按**用户裁决**同步改写为 GBK/ANSI 口径；工作树里两个 `.bat` 的实际实现与该新口径一致（7.1 实测），因此**规范与实现一致，不是未决冲突**。（来源：用户裁决；`AGENTS.md:77-78` 亦自述「本条于 2026-10 按用户裁决…改为现状」。）
- 该改动**已随 `e01c566` 提交并推送**（连同 `AGENTS.md`、两个 `.bat` 与两个打包脚本）；实测当前 `git status --porcelain` 对这三个文件已无 ` M`。

---

## 八、已确认但未修复的缺口：`log_redact.py` 的注释声称 vs 代码未实现

`portable_config/syncplay/log_redact.py:15-17` 的模块 docstring 声称面板动作会先调用它一次：

```
 命令行（面板「打开日志文件夹」会在打开前调用一次，把当前会话文件
 尚未脱敏的缺口关掉）：
  python log_redact.py --dir <日志目录>
```

但实测该调用点**从未实现**：

- `git grep -n 'redact' -- portable_config/scripts/contextmenu_plus.lua` **零命中**（退出码 1，无任何匹配）。
- 面板菜单项与消息入口确实存在：`portable_config/scripts/contextmenu_plus.lua:691` `-- 动作：打开日志文件夹`、`:692` `local function log_open_dir()`、`:1129` `mp.register_script_message('log-menu-open-dir', function()`。`log_open_dir()` 直接打开目录，未经过 `log_redact.py`。
- `log_redact.py` 的 CLI 本身是可用的、且**被单测覆盖**：`portable_config/syncplay/test_mpv_syncplay.py:161`（docstring「log_redact 的 --dir 落地能力：原地脱敏已有日志」）、`:191`、`:194` 都调用 `log_redact.main(["--dir", …])`。也就是说「CLI 能用」有测试保证，「面板会在打开前调用它」**没有实现、也没有测试会失败**。

结论只到此：这是**文档/注释声称 vs 代码未实现**的缺口，**尚未修复**，不要写成已修复。（该缺口由用户/Lead 指出，本次由我复核了上述行号与 grep 结果。）

---

## 九、未验证/待确认

以下内容**未经亲自核实**，不要当事实使用：

1. **没有实际运行过任何打包脚本**。本次只做了只读检查、文档写作与单测执行；`build-watchparty-package.ps1` / `build-macos-package.sh` 均未执行，所以「包内冒烟自检是否通过」「产物 ZIP 的字节数与结构」「`PACKAGE-CONTENTS.sha256` 是否正确」仍未验证。
2. **测试命令的实测结果（本次实测）**——注意解释器不同结果不同：
   - `python -m unittest discover -s portable_config\syncplay -p "test_*.py" -v`：用 PATH 上的 `python`（本机解析到 `C:\Users\xyqmbyl\.local\libreoffice\program\python.exe`，Python 3.12.14）时**不是全绿**：`Ran 279 tests in 1.268s` → `FAILED (errors=2, skipped=1)`，两处错误都是 `portable_config/syncplay/test_watchparty_setup.py:1312`（`_run_cli` 里的 `subprocess.run`）在 `:1333` 用例上抛 `PermissionError: [WinError 5] 拒绝访问`，即子进程创建被拒，与该解释器环境有关，与本次 `log_redact` 改动无关。
   - 换用 `C:\Users\xyqmbyl\AppData\Local\Programs\Python\Python313\python.exe`：`Ran 279 tests in 4.217s` → `OK (skipped=1)`；换用运行目录的 `D:\MPV-Duke-Enhanced-Extra\python.exe`（Python 3.14.2）：`Ran 279 tests in 1.401s` → `OK (skipped=1)`。
   - Lead 本轮实测：`Ran 279 tests in 4.330s` → `OK (skipped=1)`（来源：Lead 实测）。
   - `python WatchParty\ViewerPackage\test_ui_baseline.py`：**2 处失败**，`Ran 14 tests in 0.016s` → `FAILED (failures=2, skipped=1)`；两处都在 `WatchParty/ViewerPackage/test_ui_baseline.py:355` 的 `test_main_menu_drops_only_the_two_groups`，参数化分组 `VF 滤镜` 与 `着色器`，断言 `AssertionError: True is not false : … must not appear in the shipped main menu`。**已归因为预存失败，与本批改动无关**：测试期望的主菜单分组口径与上游已推送包不一致（来源：Lead 实测）。
   - `python WatchParty\ViewerPackage\test_macos_launcher.py`：`Ran 8 tests in 0.000s` → `OK (skipped=8)`（8 个用例全部 skip）。
3. **CI 侧的校验步骤无法在 Windows 本地复现**。`sha256sum`、`unzip -tq`、`diff -u` 这些步骤依赖 Linux runner，本机没有等价实测。
4. **tag / Release 由谁创建未在仓库中固化**。三个 workflow 里没有任何创建 tag 或 Release 的步骤，推 tag 的动作与建 Release 的动作看起来都需人工完成。
5. **`release.yml` 的 `workflow_dispatch` 是否能在 tag 已存在时重复触发、以及是否会覆盖已有附件**，未实测。
6. **本地 Windows x64 构建的实际耗时、产物字节数**未实测（旧产物存在但是改动前的版本，不能作为参考）。
7. **`graphflow-out/` 未跟踪目录的来源未查**（疑似 graphflow MCP 产物），不应提交。原文提到的 `docs/_tmp_b18.md`、`docs/_tmp_b24.md` 现已不存在：实测 `docs/` 目录下**只有 `RELEASES.md` 一个文件**。
8. **正文里指向 `AGENTS.md` 的行号引用可能已过期**。工作树对 `AGENTS.md` 第 2 条做了扩写（原 `:72-73` 两行 → 现 `:72-78` 七行），其后所有内容整体下移约 5 行，因此本文里形如 `AGENTS.md:90` / `:93` / `:96` / `:147-148` 的引用是改动前的行号，**未逐一重测**。

---

## 十、2026-10-24 修复：日志菜单在 uosc 右键主菜单中不可见

用户反馈（m01213，附右键截图）：桌面 mpv 右键主菜单里没有日志入口。

### 10.1 根因（实测）

- 右键主菜单由 **uosc** 生成，数据源是 `portable_config/mpv.conf:4` 的
  `input-conf = "~~/input_uosc.conf"`。uosc 的解析器（`scripts/uosc/lib/menus.lua`
  的 `get_all_user_bindings()`）逐行读该文件，以 `#!` 注释里 `A > B > C` 形式的
  标题建分级菜单（`#` 键位 = 纯菜单项），**不认识 `#@关键字` 动态子菜单**
  （uosc 源码全文无 `#@` 处理）。
- 而日志菜单此前只写在 `portable_config/input_contextmenu_plus.conf:28`
  （`#! 工具 > ⇲ 日志 > #@log`）。该文件只被 contextmenu_plus 脚本消费
  （`script-opts.conf:45` 的 `contextmenu_plus-input_conf=...`），生成的是另一套
  menu-data；mpv 日志里虽能看到 `load menu: ⇲ 日志, keyword: log`，但默认右键
  打开的是 uosc 菜单，用户永远看不到它。
- 用户截图的顶级分组（加载/导航/VF 滤镜/着色器/截屏/视频/工具/关于）与
  `input_uosc.conf` 的分组一致，佐证以上结论。

### 10.2 修复方式

- `portable_config/input_uosc.conf`「工具」组末尾静态加入「日志」子菜单共 8 行：
  📂 打开日志文件夹 / 🧹 自动清理（开/关切换）/ 🗓 清理周期（三级子菜单，
  静态列出 7/14/30/60/90 天）/ 🧹 立即清理一次。命令直接调用
  `contextmenu_plus.lua:1129-1143` 已注册的 `log-menu-open-dir` /
  `log-menu-toggle-auto` / `log-menu-set-days N` / `log-menu-clean-now` 消息，
  与 `#@log` 动态子菜单的动作一一对应。uosc 静态条目显示不了动态勾选态
  （当前开关状态/当前周期值），这是唯一差异；功能与 `log_menu.conf` 的
  持久化逻辑完全不变。
- `input_contextmenu_plus.conf:28` 的动态入口**保留**：它是另一条菜单路径
  （contextmenu_plus 侧），分组同样挂在「工具」下，与 uosc 侧功能一致，
  不构成矛盾；删掉反而削弱该路径的能力。
- 部署：本次代码改动只有 `input_uosc.conf` 一个文件，已复制到运行目录
  `D:\MPV-Duke-Enhanced-Extra\portable_config\` 并 sha256 比对一致；同批核对的
  `input_contextmenu_plus.conf`、`scripts/contextmenu_plus.lua`、
  `script-opts/log_menu.conf` 仓库与运行目录也一致。

### 10.3 验证（全部实测）

- **解析器等价复刻**：按 `menus.lua` 的逐行解析逻辑写 Python 校验脚本跑新
  `input_uosc.conf`：顶级分组与用户截图一致；「工具」组末尾为「日志」子菜单，
  4 个动作 + 5 档周期条目与命令一一对应。
- **单测**：`D:\MPV-Duke-Enhanced-Extra\python.exe -m unittest discover -s
  portable_config\syncplay -p "test_*.py" -v` → `Ran 279 tests / OK (skipped=1)`，
  与基线一致；`test_ui_baseline.py` 仍为既有 2 处失败（`VF 滤镜`/`着色器` 分组
  口径，见第九节），无新增失败。
- **端到端**（临时 config 目录实跑 `D:\MPV-Duke-Enhanced-Extra\mpv.exe`，
  同版 uosc 5.12.0 + contextmenu_plus，不触碰用户运行目录）：
  - IPC `script-binding uosc/menu` 打开菜单，`user-data/uosc/menu/type`
    由 `property not found` 变为 `"menu"`；
  - 逐级键导航：主菜单第 7 项「工具」→ 工具组第 11 项「日志」→ 日志组
    第 3 项「🗓 清理周期」→ 第 4 项「60 天」→ 回车，`log_menu.conf`
    **精确写入 `retention_days=60`**（菜单内容、条目顺序、命令绑定三重证明；
    uosc 菜单打开时默认选中第 1 项，DOWN 计数按此校准）；
  - 窗口截图逐级确认：「工具」子菜单末尾高亮项为「日志 ›」；「日志」子菜单
    四项 📂 打开日志文件夹 / 🧹 自动清理（开/关切换）/ 🗓 清理周期 › /
    🧹 立即清理一次全部可见；「清理周期」子菜单 7/14/30/60/90 天五项齐全、
    60 天高亮；
  - 另直发 `script-message log-menu-set-days 14` → conf 写入
    `retention_days=14`，消息回路与持久化均可用。

### 10.4 涉及文件

`portable_config/input_uosc.conf`（+12 行）、`README.md`（「日志与诊断」节
措辞）、`CHANGELOG.md`（Unreleased 新增修复条目）、`docs/RELEASES.md`（本节）。
**未 bump 版本号**；提交后经 `ssh://git@ssh.github.com:443/` 通道推送 main。

<!-- 生成于 2026-10-23，WatchParty 日志功能交付时留痕 -->
