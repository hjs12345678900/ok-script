# ok-script
* ok-script 是基于图像识别技术, 纯Python实现的, 支持Windows窗口和模拟器的自动化测试框架。
* 框架包含UI, 截图, 输入, 设备控制, OCR, 模板匹配, 框框Debug浮层, 基于Github Action的测试, 打包, 升级/降级。
* 基于开发一个工业级的自动化软件仅需几百行代码。

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

推荐使用 **Python 3.12**。`ok-script` 支持 Python 3.11 及以上版本。

### 作为项目依赖安装

```bash
python -m pip install --upgrade pip
python -m pip install ok-script
```

建议在虚拟环境中安装，避免与系统 Python 的依赖冲突：

```bash
python3.12 -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install ok-script
```

### 从源码安装（开发者）

```bash
git clone https://github.com/hjs12345678900/ok-script.git
cd ok-script
python3.12 -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

运行测试：

```bash
python -m pytest
```

编译国际化文件（Windows）：

```bat
compile_i18n.cmd
```

### macOS 支持

macOS 后端目前面向开发和实机验证，提供 ScreenCaptureKit 窗口捕获及 Quartz 前台键鼠输入。首次使用前：

1. 安装 Xcode Command Line Tools，以便开发模式首次运行时用 `swiftc` 编译原生辅助程序。
2. 在“系统设置 → 隐私与安全性”中，给启动 Python 的终端或应用授予“屏幕与系统音频录制”和“辅助功能”权限。
3. 授权后完全退出并重新启动终端或应用，使权限生效。

macOS 输入仅在目标应用位于前台时发送；目前不承诺最小化或后台输入。

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
