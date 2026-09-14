# DJI SN 批量激活查询助手

本项目提供 **Windows EXE 版**、**Python 源码版**和 **Chrome / Edge 扩展版**，用于逐条填入 SN、尝试完成条形滑块验证、记录激活结果并导出 CSV。

## Windows EXE 版（无需安装 Python）

从 [GitHub Releases](https://github.com/Jonxinxin/dji-sn-batch-checker/releases/latest) 下载 **`DJI-SN-Checker-Windows-x64.zip`**，解压后双击 `DJI-SN-Checker.exe` 即可运行。发行页也提供完整源码 ZIP，可用于 Python 版或加载 Chrome / Edge 扩展。

发行文件位于 `dist`：

- `DJI-SN-Checker.exe`：单文件程序，可直接复制到其他电脑运行。
- `DJI-SN-Checker-Windows-x64.zip`：包含 EXE、使用说明及开源组件许可，适合分发。

运行环境为 **Windows 10 / 11 64 位**，电脑上需要已安装 **Edge 或 Chrome**。双击 EXE 后点击“打开浏览器 / 登录”，在查询窗口登录 DJI 账号，然后导入 SN 开始查询。程序内置 Python、Tk、NumPy 及 Playwright 驱动。

EXE 版的队列、设置、浏览器登录状态与诊断文件保存在 `%LOCALAPPDATA%\DJI-SN-Checker`。更新时关闭程序后替换 EXE 即可；更换电脑需要重新登录。发行包不包含构建电脑的 SN 队列或登录资料。完整操作说明见 [windows/使用说明.txt](windows/使用说明.txt)。

### 重新打包

在已有 Python 构建环境的 Windows 电脑上运行 `打包EXE.cmd`。也可先安装 `requirements-build.txt`，再执行：

```powershell
.\.venv\Scripts\python.exe scripts\build_exe.py
```

构建脚本使用固定的 PyInstaller 版本生成单文件 EXE，检查资源与依赖，并将 EXE 复制到含中文和空格的独立目录中运行离线自检。测试时移除 Python 环境变量和 PATH 中的 Python，验证界面、轨迹生成、模拟页面中的查询/重试、完成收尾和登录保留；测试不会向大疆官网提交设备查询。通过后生成 ZIP、`build-info.json` 和 `SHA256SUMS.txt`。

## Python 本地版

适用于扩展滑块不动、调试连接不可用的情况。Python 版会打开独立的 Chrome / Edge 查询窗口，默认使用 Windows 系统鼠标拖动，不需要安装扩展。**首次需要在这个查询窗口登录 DJI 账号**；它不会继承日常浏览器的登录状态。登录后会复用同一份浏览器配置，直到官网会话过期或你退出登录。

### 启动与使用

1. 双击本目录的 **`启动Python版.cmd`**。本机的 Python 环境已配置好；在新电脑上需先安装 Python 3.11 或更新版本、Chrome 或 Edge，再运行 `install-python.cmd`。
2. 点击 **“打开浏览器 / 登录”**，在新窗口点击官网的“登录 DJI 账号”，自行完成登录并返回设备查询页。界面显示登录状态；程序不填写账号、密码或短信验证码。
3. 粘贴 SN，点击“加入队列”，再点击“开始 / 继续”。
4. 默认开启自动滑块和连续查询。Python 版已接入本地 `slider-captcha-lab-main` 的 `human_track.py`（`feedback` 轨迹），并适配大疆条形滑块。每次拖动动态选择约 **0.9–3.1 秒**的移动时长，变速阶段、停顿和移动间隔也会变化；日志显示本次时长。系统鼠标模式会移动真实光标，拖动期间请保持查询窗口在前台；按 **Esc** 或点击“暂停”可以停止。
5. 程序确认已登录后，会切换到 SN 查询页签、填写当前 SN 并验证。官网确认验证通过后再点击查询、保存结果。处理下一条时，程序会在**同一个标签页**返回设备查询网址，等待表单加载后再填写 SN；重试也使用当前标签页。未登录或会话过期时，当前条目会显示 **“等待登录”**；已开始的队列会在你登录返回后继续，不会把登录后才能查看的激活信息记成“未激活”。点击“暂停”可停止等待和自动继续。
6. 全部条目处理完毕后，程序会先保存结果、自动关闭查询浏览器，再将主界面恢复到前台并弹出完成提示，显示已激活、未激活及失败/跳过数量。主界面保留结果，可点击“导出 CSV”；再次加入 SN 并开始时会重新打开浏览器，复用本机登录状态。暂停、等待登录、等待人工验证或仍有待查询条目时不会触发完成提示。

**“自动尝试上限”** 默认 3 次，可设置 1–10 次，**包含首次拖动**。失败后会等待约 2–5 秒，在当前标签页重新加载查询网址、填回同一个 SN，再使用新轨迹重试。达到上限后，只刷新一次让滑块可以重新操作，然后进入 **“待人工验证”**；此后不再自动刷新或拖动，手动验证通过后继续查询。若在等待重试期间已经验证通过，会直接继续查询。拼图或鼠标定位问题直接转人工。

次数设置和当前 SN 已用的尝试次数都会保存；暂停、重新登录、重开程序不重置上限。点击 **“重试当前 / 选中项”** 才会为这条 SN 开始新一轮尝试。关闭自动滑块后进入人工模式。

可点击 **“测试滑块”** 单独测试一次官网滑块；它不填写 SN、不提交设备查询，**不参与上述自动重试**。如系统鼠标无法定位窗口，可选择“浏览器鼠标（兼容模式）”再点重试。两种模式都以官网返回的验证结果为准；轨迹变化不保证官网接受验证。[上游来源与适配说明](third_party/slider-captcha-lab/README.md)。

Python 队列保存在 `.python-data/queue.json`，浏览器配置及登录状态保存在 `.python-data/browser-profile/`，失败截图与操作轨迹在 `.python-data/diagnostics/`。这些文件只保存在本机。退出程序会保存浏览器配置；再次双击启动仍使用同一目录。Python 队列与扩展队列相互独立，已有 SN 可复制粘贴到 Python 版。

### Python 诊断与测试

```powershell
.\.venv\Scripts\python.exe python_app.py --diagnose --headless
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_python_*.py" -v
.\.venv\Scripts\python.exe tests\python_browser_smoke.py
.\.venv\Scripts\python.exe tests\python_login_smoke.py
.\.venv\Scripts\python.exe tests\python_navigation_smoke.py
.\.venv\Scripts\python.exe tests\python_retry_smoke.py
.\.venv\Scripts\python.exe tests\python_gui_smoke.py
```

`--diagnose` 只检查官网状态，不拖动、不提交查询。`python_login_smoke.py` 用本地页面验证等待登录、登录后切换 SN 页签、关闭重开保留会话以及会话过期；不登录真实账号、不查询真实设备。`python_navigation_smoke.py` 模拟官网“查询其他设备”按钮打开新标签的行为，验证程序可在同一标签连续处理 3 条结果及重试。`python_retry_smoke.py` 验证失败刷新后成功、达到上限后转人工、查询时验证过期后的重试，并检查登录与标签页复用。`python_gui_smoke.py` 验证次数设置保存及重启恢复。以下本地测试会打开一个可见的隔离浏览器，并移动真实鼠标；所有网页请求都由本地测试页提供，不访问官网：

```powershell
.\.venv\Scripts\python.exe tests\python_browser_smoke.py --native
```

也可直接测试一次官网滑块（不查询设备）：

```powershell
.\.venv\Scripts\python.exe python_app.py --test-slider --mouse system
```

## 扩展版

扩展会在大疆设备信息查询页右侧增加批量队列。

## 安装

1. 打开 Chrome 的 `chrome://extensions`，或 Edge 的 `edge://extensions`。
2. 开启右上角的“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择本文件夹 `dji-sn-batch-checker`。
5. 打开 [大疆设备信息查询页](https://repair.dji.com/device/detail?re=cn&lang=zh-CN)。

更新已有安装时，在扩展管理页点击本扩展的“重新加载”，再刷新大疆查询页。自动滑块需要 `debugger` 权限；浏览器可能显示“正在调试此浏览器”的提示，单次操作结束后扩展会断开调试连接。

## 使用

1. 将一批 SN 粘贴到右侧面板，一行一条，也可以用空格、逗号或分号分隔。
2. 点击“加入队列”，再点击“开始查询”。
3. 扩展会自动把当前 SN 填入大疆官网输入框。
4. 默认开启“自动完成滑块验证”和连续模式。扩展会尝试拖动条形滑块，只有官网显示验证通过后才自动点击查询。
5. 扩展会记录官网结果，并自动返回查询页、填入下一条 SN。
6. 全部完成后点击“导出 CSV”。

每条 SN 自动尝试一次滑块。失败时可点击“重试滑块”，也可手动拖动后继续；不会无限重试。出现拼图验证时需人工完成。点击“暂停”、删除当前条目或切换 SN 会取消正在进行的自动操作。

关闭“自动完成滑块验证”后，可手动拖动官网滑块；关闭“验证后自动查询、记录并进入下一条”后，需要手动点击官网“查询”，结果记录后再点击“下一条”。

如需修正已记录的结果，可使用“标记已激活 / 标记未激活 / 待确认”。

## 常见问题

- **自动滑块连接不可用**：重新加载扩展并刷新官网页。若提示调试连接被占用，关闭该页面的开发者工具后重试。
- **滑块被遮挡**：收起右侧面板或调整窗口、滚动位置，使滑块完整显示，再重试。拖动过程中请保持窗口位置和页面布局稳定。
- **官网验证失败或未确认通过**：点击“重试滑块”，或手动验证；自动操作不能保证官网接受验证。
- **官网查询报错**：队列会暂停并保留错误信息，可使用条目右侧的重新查询按钮。
- **刷新页面**：队列、结果及开关设置保存在本地；运行中的队列会在表单加载后继续。

## 数据与限制

- SN 队列和结果只保存在浏览器扩展的本地存储中。
- 查询仍由大疆官网处理，扩展没有额外的数据上传服务。
- 自动滑块仅操作大疆设备查询页内已显示的条形滑块，验证是否成功以官网实际结果为准。
- 同一时间只运行一个自动滑块任务；多标签页同时使用时，其他页面会提示稍后重试。
- SN 长度按大疆当前页面规则限制为 14–20 位字母或数字。
- 官网页面结构变更后，可能需要更新结果识别规则。

## 开发测试

安装依赖及测试用 Chromium，然后在本目录执行：

```powershell
npm ci
npx playwright install chromium
npm test
npm run test:browser
```

单元测试覆盖结果解析、轨迹边界、取消及调试连接清理。浏览器测试在隔离的 Chromium 配置中加载真实扩展，通过本地模拟页面验证队列、拖动事件、失败重试、人工模式、刷新恢复和 CSV 导出，不会向官网提交测试 SN，也不代表真实验证码通过率。截图写入 `test-results/`。

轨迹生成器改编自 MIT 许可的 [slider-captcha-lab](https://github.com/kangleyao/slider-captcha-lab)，来源与改动说明见 [third_party/slider-captcha-lab/README.md](third_party/slider-captcha-lab/README.md)。
