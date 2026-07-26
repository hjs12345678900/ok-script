# OK-Script macOS（测试中）

> [!WARNING]
> 这是基于 [`ok-oldking/ok-script`](https://github.com/ok-oldking/ok-script) 修改的非官方 macOS 实验分支，目前用于开发和实机测试，不是原项目的正式 macOS 版本。需要稳定 Windows、模拟器或浏览器支持的用户，请使用[原版 OK-Script](https://github.com/ok-oldking/ok-script)。

本分支在原版纯 Python 图像识别自动化框架上增加了实验性的 macOS ScreenCaptureKit 窗口捕获和 Quartz 前台键鼠输入。Python 包名和导入名仍为 `ok-script` / `ok`，以保持项目兼容性。

## 项目关系

| 用途 | 项目 |
| --- | --- |
| macOS 自动化框架（本测试分支） | [`hjs12345678900/ok-script`](https://github.com/hjs12345678900/ok-script) |
| macOS《鸣潮》脚本（测试中） | [`hjs12345678900/ok-wuthering-waves`](https://github.com/hjs12345678900/ok-wuthering-waves) |
| 原版 Windows / 模拟器框架 | [`ok-oldking/ok-script`](https://github.com/ok-oldking/ok-script) |
| 原版 Windows《鸣潮》脚本 | [`ok-oldking/ok-wuthering-waves`](https://github.com/ok-oldking/ok-wuthering-waves) |

当前 macOS 后端仍在测试：仅支持前台输入，不承诺最小化或后台操作，也尚未提供稳定的二进制发布。

## 优势

1. 纯Python实现, 免费开源, 依赖库均为开源方案
2. 支持pip install任何第三方库, 可以方便整合yolo等框架
3. 一套代码即可支持Windows安卓模拟器/ADB连接的虚拟机, Windows客户端游戏
4. 自适应分辨率
5. 使用coco管理图片匹配素材, 仅需一个分辨率下的截图就, 支持不同分辨率自适应
6. 可打包离线/在线安装setup.exe, 支持通过Pip/Git国内镜像在线增量更新. 在线安装包仅3M
7. 支持Github Action一键构建
8. 支持多语言国际化

## [使用基于ok-script的按键精灵, 快速学习和开始](https://github.com/ok-oldking/ok-py)

**API列表, 脚本录制**
![image_scripting](docs/ok_py/image_scripting.png)

**支持多种截图以及交互方式**
![image_screenshot](docs/ok_py/image_capture.png)

**标注管理 (Template Matching)**
![image_template](docs/ok_py/image_template.png)
![image_markup](docs/ok_py/image_markup.png)

## 安装

推荐使用 **Python 3.12**。本 macOS 分支目前以源码方式安装。

### macOS 首次安装

```bash
git clone https://github.com/hjs12345678900/ok-script.git
cd ok-script
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

作为相邻项目的开发依赖安装时，在目标项目的虚拟环境中执行：

```bash
python -m pip install -e ../ok-script
```

### 开发流程

建议从本 fork 创建独立功能分支，并保留原项目为 `upstream`：

```bash
git remote add upstream https://github.com/ok-oldking/ok-script.git
git fetch upstream
git switch -c feature/your-change

# 完成修改后
python -m pytest
git status
git add path/to/changed-file
git commit -m "Describe the change"
```

提交前请确认测试通过，不要提交 `.venv`、缓存、日志、截图或个人配置。

### macOS 权限与限制

macOS 后端目前面向开发和实机验证，提供 ScreenCaptureKit 窗口捕获及 Quartz 前台键鼠输入。首次使用前：

1. 安装 Xcode Command Line Tools，以便开发模式首次运行时用 `swiftc` 编译原生辅助程序。
2. 在“系统设置 → 隐私与安全性”中，给启动 Python 的终端或应用授予“屏幕与系统音频录制”和“辅助功能”权限。
3. 授权后完全退出并重新启动终端或应用，使权限生效。

macOS 输入仅在目标应用位于前台时发送；目前不承诺最小化或后台输入。

## 许可证与派生开发

本项目沿用原项目的 [GNU AGPL-3.0](LICENSE.txt) 许可证。许可证允许使用、修改、fork 和再发布，但派生版本需要保留许可证和版权声明、明确说明修改，并按 AGPL-3.0 要求向使用者提供对应源代码。具体权利与义务以 `LICENSE.txt` 原文为准。

## 文档和示例代码

* [游戏自动化入门](docs/intro_to_automation/README.md)
  - [1、基本原理：计算机如何“玩”游戏](docs/intro_to_automation/README.md#一基本原理计算机如何玩游戏)
    - [核心循环：三步走](docs/intro_to_automation/README.md#核心循环三步走)
    - [图像分析：从像素到决策](docs/intro_to_automation/README.md#图像分析从像素到决策)
        - [传统图色算法 (OpenCV 库)](docs/intro_to_automation/README.md#1-传统图色算法-opencv-库)
        - [神经网络推理 (Inference)](docs/intro_to_automation/README.md#2-神经网络推理-inference)
    - [2、编程语言选择](docs/intro_to_automation/README.md#二编程语言选择)
        - [常用库概览](docs/intro_to_automation/README.md#常用库概览)
    - [3、开发工具](docs/intro_to_automation/README.md#三开发工具)
* [快速开始](docs/quick_start/README.md)
* [API文档](docs/api_doc/README.md)
  - [Box](docs/api_doc/README.md#box)
  - [BaseTask](docs/api_doc/README.md#basetask)
    - [截图 (Screenshot)](docs/api_doc/README.md#截图-screenshot)
    - [输入 (Input)](docs/api_doc/README.md#输入-input)
    - [OCR](docs/api_doc/README.md#ocr)
    - [找图 (Image finding)](docs/api_doc/README.md#找图-image-finding)
* [进阶使用](docs/after_quick_start/README.md)
  - [1. 模板匹配 (Template Matching)](docs/after_quick_start/README.md#1-模板匹配-template-matching)
  - [2. 多语言国际化 (i18n)](docs/after_quick_start/README.md#2-多语言国际化-i18n)
  - [3. 自动化测试](docs/after_quick_start/README.md#3-自动化测试)
  - [4. 使用 GitHub Action 自动化打包与发布](docs/after_quick_start/README.md#4-使用-github-action-自动化打包与发布)
* 开发者群: 938132715
* pip [https://pypi.org/project/ok-script](https://pypi.org/project/ok-script)


## 使用ok-script的项目：

* 鸣潮 [https://github.com/ok-oldking/ok-wuthering-wave](https://github.com/ok-oldking/ok-wuthering-waves)
* 原神(不在维护,
  但是后台过剧情可用) [https://github.com/ok-oldking/ok-genshin-impact](https://github.com/ok-oldking/ok-genshin-impact)
* 少前2 [https://github.com/ok-oldking/ok-gf2](https://github.com/ok-oldking/ok-gf2)
* 星铁 [https://github.com/Shasnow/ok-starrailassistant](https://github.com/Shasnow/ok-starrailassistant)
* 星痕共鸣 [https://github.com/Sanheiii/ok-star-resonance](https://github.com/Sanheiii/ok-star-resonance)
* 二重螺旋 [https://github.com/BnanZ0/ok-duet-night-abyss](https://github.com/BnanZ0/ok-duet-night-abyss)
* 白荆回廊(停止更新) [https://github.com/ok-oldking/ok-baijing](https://github.com/ok-oldking/ok-baijing)
* 终末地 [https://github.com/AliceJump/ok-end-field](https://github.com/AliceJump/ok-end-field)
* 异环 [https://github.com/BnanZ0/ok-nte](https://github.com/BnanZ0/ok-nte)
