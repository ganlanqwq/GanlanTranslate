; 甘岚翻译 — NSIS 安装脚本（多语言）
; 支持：简体中文 / 韩文 / 日文 / 英文。NSIS 会根据系统语言自动选择界面语言。
; 所有自定义字符串均以 LangString 形式定义，保证各语言完整。

Unicode true
!define APP_NAME "甘岚翻译"
!define APP_SHORT_NAME "GanlanTranslate"
!define APP_VERSION "1.0.5"
!define APP_PUBLISHER "甘岚"
!define APP_EXE "GanlanTranslate.exe"
!define VR_EXE "GanlanTranslateVR.exe"
!define APP_DIR "Ganlan Translate"
; 改英文名之前的测试版（2.x）：程序 GanLan.exe / GanLanVR.exe，装在 GanLan 目录，注册表键 GanLan。
; 覆盖安装时要关掉旧程序、把用户数据搬到新目录、删掉旧目录和旧注册表键（见 MigrateOldInstall）。
!define OLD_SHORT_NAME "GanLan"
!define OLD_APP_EXE "GanLan.exe"
!define OLD_VR_EXE "GanLanVR.exe"
; PyInstaller 输出目录（见 GanlanTranslate.spec 顶部的命令；不要用旧的 dist\）
!define BUILD_DIR "build_installer\dist\GanlanTranslate"

Name "${APP_NAME}"
OutFile "build_installer\GanlanTranslate-Setup-${APP_VERSION}.exe"
InstallDir "$PROGRAMFILES\${APP_DIR}"
InstallDirRegKey HKLM "Software\${APP_SHORT_NAME}" "InstallDir"
RequestExecutionLevel admin
ShowInstDetails show
ShowUninstDetails show
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "header.bmp"

; 让 NSIS 把图标嵌入到安装器 exe 头里，避免 Windows 缓存旧图标
!define MUI_SET_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP_RTL "header.bmp"

; 注意：MUI2 要求先声明页面，再 MUI_LANGUAGE，再 LangString；顺序反了会导致向导里部分文字是空的。

; ==========================================================================
; MUI 页面配置
; ==========================================================================
!define MUI_WELCOMEPAGE_TITLE $(WELCOME_TITLE)
!define MUI_WELCOMEPAGE_TEXT $(WELCOME_TEXT)
!define MUI_WELCOMEPAGE_SUBTITLE $(WELCOME_SUBTITLE)
!define MUI_FINISHPAGE_TITLE $(FINISH_TITLE)
!define MUI_FINISHPAGE_TEXT $(FINISH_TEXT)
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!define MUI_FINISHPAGE_NOREBOOT_WARNING

; License 页面
!define MUI_LICENSEPAGE_TEXT_TOP $(LICENSE_TOP)
!define MUI_LICENSEPAGE_TEXT_BOTTOM $(LICENSE_BOTTOM)

; 目录选择页
!define MUI_DIRECTORYPAGE_TEXT_TOP $(DIR_TOP)
!define MUI_DIRECTORYPAGE_TEXT_DOTTOM $(DIR_BOTTOM)

; 安装进行中页
!define MUI_INSTALLING_TEXT $(INSTALLING_TEXT)

; 完成页
!define MUI_FINISHPAGE_SHOWREADME_TEXT $(SHOWREADME_TEXT)
!define MUI_FINISHPAGE_RUN_TEXT $(RUN_TEXT)
; 安装程序是管理员身份，直接启动的话程序也是管理员身份（之后从快捷方式打开的普通实例
; 读不到它的单实例锁）。经 explorer 启动就是普通权限。
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchAsCurrentUser

; 卸载欢迎页
!define MUI_UNWELCOMEPAGE_TITLE $(UNWELCOME_TITLE)
!define MUI_UNWELCOMEPAGE_TEXT $(UNWELCOME_TEXT)
!define MUI_UNWELCOMEPAGE_SUBTITLE $(UNWELCOME_SUBTITLE)

; 卸载确认页
!define MUI_UNCONFIRMPAGE_TEXT_TOP $(UNCONFIRM_TOP)
!define MUI_UNCONFIRMPAGE_TEXT_DOTTOM $(UNCONFIRM_BOTTOM)

; 卸载完成页
!define MUI_UNFINISHPAGE_TITLE $(UNFINISH_TITLE)
!define MUI_UNFINISHPAGE_TEXT $(UNFINISH_TEXT)

; 按钮
!define MUI_BUTTON_NEXT $(BTN_NEXT)
!define MUI_BUTTON_BACK $(BTN_BACK)
!define MUI_BUTTON_CANCEL $(BTN_CANCEL)
!define MUI_BUTTON_CLOSE $(BTN_CLOSE)
!define MUI_BUTTON_INSTALL $(BTN_INSTALL)
!define MUI_BUTTON_UNINSTALL $(BTN_UNINSTALL)

; 卸载按钮
!define MUI_UNBUTTON_NEXT $(UNBTN_NEXT)
!define MUI_UNBUTTON_BACK $(UNBTN_BACK)
!define MUI_UNBUTTON_CANCEL $(UNBTN_CANCEL)
!define MUI_UNBUTTON_CLOSE $(UNBTN_CLOSE)

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

; ==========================================================================
; 语言（NSIS 按系统语言自动选择；韩文/日文/英文/简体中文）
; 必须先加载语言，LangString 才能使用 LANG_* 常量
; ==========================================================================
!insertmacro MUI_LANGUAGE "Korean"
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ==========================================================================
; 语言字符串定义
; ==========================================================================
; ---- 欢迎页 ----
LangString WELCOME_TITLE ${LANG_SIMPCHINESE} "${APP_NAME} 安装向导"
LangString WELCOME_TITLE ${LANG_KOREAN} "${APP_NAME} 설치 마법사"
LangString WELCOME_TITLE ${LANG_JAPANESE} "${APP_NAME} インストール ウィザード"
LangString WELCOME_TITLE ${LANG_ENGLISH} "${APP_NAME} Setup Wizard"

LangString WELCOME_TEXT ${LANG_SIMPCHINESE} "本安装程序将引导您把 ${APP_NAME} 安装到您的电脑上。$\r$\n$\r$\n本软件包含语音识别、翻译、VRChat OSC 桥接、SteamVR 手腕字幕等功能。$\r$\n$\r$\n点击下一步继续。"
LangString WELCOME_TEXT ${LANG_KOREAN} "이 설치 프로그램은 ${APP_NAME}를(을) 컴퓨터에 설치하도록 안내합니다.$\r$\n$\r$\n이 소프트웨어는 음성 인식, 번역, VRChat OSC 브리지, SteamVR 손목 자막 등의 기능을 포함합니다.$\r$\n$\r$\n다음을 클릭하여 계속합니다."
LangString WELCOME_TEXT ${LANG_JAPANESE} "このインストーラーは ${APP_NAME} をコンピューターにインストールします。$\r$\n$\r$\n本ソフトウェアは音声認識・翻訳・VRChat OSC ブリッジ・SteamVR 手首字幕などの機能を備えています。$\r$\n$\r$\n「次へ」をクリックして続行してください。"
LangString WELCOME_TEXT ${LANG_ENGLISH} "This wizard will guide you through installing ${APP_NAME} on your computer.$\r$\n$\r$\nThis software includes speech recognition, translation, VRChat OSC bridge, SteamVR wrist subtitles and more.$\r$\n$\r$\nClick Next to continue."

LangString WELCOME_SUBTITLE ${LANG_SIMPCHINESE} "在开始安装前，请先关闭其他正在运行的程序。"
LangString WELCOME_SUBTITLE ${LANG_KOREAN} "설치를 시작하기 전에 다른 실행 중인 프로그램을 종료하십시오."
LangString WELCOME_SUBTITLE ${LANG_JAPANESE} "インストールを開始する前に、他の実行中のプログラムを閉じてください。"
LangString WELCOME_SUBTITLE ${LANG_ENGLISH} "Please close any other running programs before starting the installation."

; ---- 完成页 ----
LangString FINISH_TITLE ${LANG_SIMPCHINESE} "${APP_NAME} 已安装"
LangString FINISH_TITLE ${LANG_KOREAN} "${APP_NAME} 설치 완료"
LangString FINISH_TITLE ${LANG_JAPANESE} "${APP_NAME} のインストールが完了しました"
LangString FINISH_TITLE ${LANG_ENGLISH} "${APP_NAME} Installed"

LangString FINISH_TEXT ${LANG_SIMPCHINESE} "${APP_NAME} 已成功安装到您的电脑。$\r$\n首次运行时会自动打开控制面板。$\r$\n$\r$\n点击完成关闭本向导。"
LangString FINISH_TEXT ${LANG_KOREAN} "${APP_NAME}가(이) 컴퓨터에 성공적으로 설치되었습니다.$\r$\n첫 실행 시 제어판이 자동으로 열립니다.$\r$\n$\r$\n완료를 클릭하여 마법사를 닫습니다."
LangString FINISH_TEXT ${LANG_JAPANESE} "${APP_NAME} がコンピューターに正常にインストールされました。$\r$\n初回起動時にコントロールパネルが自動的に開きます。$\r$\n$\r$\n「完了」をクリックしてウィザードを閉じます。"
LangString FINISH_TEXT ${LANG_ENGLISH} "${APP_NAME} has been successfully installed on your computer.$\r$\nThe control panel will open automatically on first run.$\r$\n$\r$\nClick Finish to close this wizard."


LangString ABORTWARNING_TEXT ${LANG_SIMPCHINESE} "您确定要退出 ${APP_NAME} 安装程序吗？"
LangString ABORTWARNING_TEXT ${LANG_KOREAN} "${APP_NAME} 설치 프로그램을 종료하시겠습니까？"
LangString ABORTWARNING_TEXT ${LANG_JAPANESE} "${APP_NAME} インストーラーを終了しますか？"
LangString ABORTWARNING_TEXT ${LANG_ENGLISH} "Are you sure you want to quit the ${APP_NAME} installer?"

; ---- License 页面 ----
LangString LICENSE_TOP ${LANG_SIMPCHINESE} "请阅读以下许可协议。继续安装即表示您同意本协议的所有条款。"
LangString LICENSE_TOP ${LANG_KOREAN} "다음 사용권 계약을 읽어 주십시오. 설치를 계속하면 이 계약의 모든 조건에 동의하는 것입니다."
LangString LICENSE_TOP ${LANG_JAPANESE} "以下の使用許諾契約をお読みください。インストールを続行すると、本契約のすべての条項に同意したことになります。"
LangString LICENSE_TOP ${LANG_ENGLISH} "Please read the following license agreement. Continuing the installation means you agree to all its terms."

LangString LICENSE_BOTTOM ${LANG_SIMPCHINESE} "如果您同意协议中的所有条款，请选中下方复选框继续安装。"
LangString LICENSE_BOTTOM ${LANG_KOREAN} "계약의 모든 조건에 동의하면 아래의 확인란을 선택하여 설치를 계속하십시오."
LangString LICENSE_BOTTOM ${LANG_JAPANESE} "契約のすべての条項に同意する場合は、下のチェックボックスを選択して続行してください。"
LangString LICENSE_BOTTOM ${LANG_ENGLISH} "If you accept all terms of the agreement, check the box below to continue the installation."

; ---- 目录选择页 ----
LangString DIR_TOP ${LANG_SIMPCHINESE} "安装程序将把 ${APP_NAME} 安装到下面的文件夹中。$\r$\n$\r$\n如需安装到其他文件夹，请点击浏览选择。"
LangString DIR_TOP ${LANG_KOREAN} "설치 프로그램은 ${APP_NAME}를(을) 아래 폴더에 설치합니다.$\r$\n$\r$\n다른 폴더에 설치하려면 찾아보기를 클릭하십시오."
LangString DIR_TOP ${LANG_JAPANESE} "インストーラーは ${APP_NAME} を以下のフォルダーにインストールします。$\r$\n$\r$\n別のフォルダーにインストールする場合は、参照をクリックしてください。"
LangString DIR_TOP ${LANG_ENGLISH} "The installer will install ${APP_NAME} into the following folder.$\r$\n$\r$\nTo install to a different folder, click Browse and select another folder."

LangString DIR_BOTTOM ${LANG_SIMPCHINESE} "必须选择安装目录后才能继续。"
LangString DIR_BOTTOM ${LANG_KOREAN} "설치 디렉터리를 선택해야 계속할 수 있습니다."
LangString DIR_BOTTOM ${LANG_JAPANESE} "インストール先フォルダーを選択してから続行してください。"
LangString DIR_BOTTOM ${LANG_ENGLISH} "You must select an installation directory to continue."

; ---- 安装进行中页 ----
LangString INSTALLING_TEXT ${LANG_SIMPCHINESE} "正在安装 ${APP_NAME}，请稍候…"
LangString INSTALLING_TEXT ${LANG_KOREAN} "${APP_NAME}를(을) 설치하는 중입니다. 잠시 기다려 주십시오…"
LangString INSTALLING_TEXT ${LANG_JAPANESE} "${APP_NAME} をインストールしています。お待ちください…"
LangString INSTALLING_TEXT ${LANG_ENGLISH} "Installing ${APP_NAME}, please wait…"

; ---- 完成页附加 ----
LangString SHOWREADME_TEXT ${LANG_SIMPCHINESE} "打开使用说明"
LangString SHOWREADME_TEXT ${LANG_KOREAN} "사용 설명서 열기"
LangString SHOWREADME_TEXT ${LANG_JAPANESE} "取扱説明書を開く"
LangString SHOWREADME_TEXT ${LANG_ENGLISH} "Open the user manual"

LangString RUN_TEXT ${LANG_SIMPCHINESE} "启动 ${APP_NAME}"
LangString RUN_TEXT ${LANG_KOREAN} "${APP_NAME} 시작"
LangString RUN_TEXT ${LANG_JAPANESE} "${APP_NAME} を起動"
LangString RUN_TEXT ${LANG_ENGLISH} "Launch ${APP_NAME}"

; ---- 卸载欢迎页 ----
LangString UNWELCOME_TITLE ${LANG_SIMPCHINESE} "卸载 ${APP_NAME}"
LangString UNWELCOME_TITLE ${LANG_KOREAN} "${APP_NAME} 제거"
LangString UNWELCOME_TITLE ${LANG_JAPANESE} "${APP_NAME} のアンインストール"
LangString UNWELCOME_TITLE ${LANG_ENGLISH} "Uninstall ${APP_NAME}"

LangString UNWELCOME_TEXT ${LANG_SIMPCHINESE} "本卸载程序将把 ${APP_NAME} 从您的电脑中移除。$\r$\n$\r$\n点击下一步继续。"
LangString UNWELCOME_TEXT ${LANG_KOREAN} "이 제거 프로그램은 ${APP_NAME}를(을) 컴퓨터에서 제거합니다.$\r$\n$\r$\n다음을 클릭하여 계속합니다."
LangString UNWELCOME_TEXT ${LANG_JAPANESE} "このアンインストーラーは ${APP_NAME} をコンピューターから削除します。$\r$\n$\r$\n「次へ」をクリックして続行してください。"
LangString UNWELCOME_TEXT ${LANG_ENGLISH} "This uninstaller will remove ${APP_NAME} from your computer.$\r$\n$\r$\nClick Next to continue."

LangString UNWELCOME_SUBTITLE ${LANG_SIMPCHINESE} "卸载前请先关闭正在运行的 ${APP_NAME}。"
LangString UNWELCOME_SUBTITLE ${LANG_KOREAN} "제거하기 전에 실행 중인 ${APP_NAME}를(을) 종료하십시오."
LangString UNWELCOME_SUBTITLE ${LANG_JAPANESE} "アンインストールする前に、実行中の ${APP_NAME} を閉じてください。"
LangString UNWELCOME_SUBTITLE ${LANG_ENGLISH} "Please close any running ${APP_NAME} before uninstalling."

; ---- 卸载确认页 ----
LangString UNCONFIRM_TOP ${LANG_SIMPCHINESE} "${APP_NAME} 将从以下目录中卸载："
LangString UNCONFIRM_TOP ${LANG_KOREAN} "${APP_NAME}가(이) 다음 디렉터리에서 제거됩니다："
LangString UNCONFIRM_TOP ${LANG_JAPANESE} "${APP_NAME} は以下のフォルダーからアンインストールされます："
LangString UNCONFIRM_TOP ${LANG_ENGLISH} "${APP_NAME} will be uninstalled from the following folder:"

LangString UNCONFIRM_BOTTOM ${LANG_SIMPCHINESE} "继续前请保存所有未保存的工作。"
LangString UNCONFIRM_BOTTOM ${LANG_KOREAN} "계속하기 전에 저장하지 않은 작업을 저장하십시오."
LangString UNCONFIRM_BOTTOM ${LANG_JAPANESE} "続行する前に、保存していない作業を保存してください。"
LangString UNCONFIRM_BOTTOM ${LANG_ENGLISH} "Please save any unsaved work before continuing."

; ---- 卸载完成页 ----
LangString UNFINISH_TITLE ${LANG_SIMPCHINESE} "卸载完成"
LangString UNFINISH_TITLE ${LANG_KOREAN} "제거 완료"
LangString UNFINISH_TITLE ${LANG_JAPANESE} "アンインストール完了"
LangString UNFINISH_TITLE ${LANG_ENGLISH} "Uninstall Complete"

LangString UNFINISH_TEXT ${LANG_SIMPCHINESE} "${APP_NAME} 已从您的电脑中移除。"
LangString UNFINISH_TEXT ${LANG_KOREAN} "${APP_NAME}가(이) 컴퓨터에서 제거되었습니다."
LangString UNFINISH_TEXT ${LANG_JAPANESE} "${APP_NAME} はコンピューターから削除されました。"
LangString UNFINISH_TEXT ${LANG_ENGLISH} "${APP_NAME} has been removed from your computer."

; ---- 状态文本 ----
LangString INSTALLING_TITLE ${LANG_SIMPCHINESE} "正在安装 ${APP_NAME}"
LangString INSTALLING_TITLE ${LANG_KOREAN} "${APP_NAME} 설치 중"
LangString INSTALLING_TITLE ${LANG_JAPANESE} "${APP_NAME} をインストール中"
LangString INSTALLING_TITLE ${LANG_ENGLISH} "Installing ${APP_NAME}"

LangString INSTALLING_SUBTITLE ${LANG_SIMPCHINESE} "安装程序正在准备安装，请稍候。"
LangString INSTALLING_SUBTITLE ${LANG_KOREAN} "설치 프로그램이 설치를 준비하고 있습니다. 잠시 기다려 주십시오."
LangString INSTALLING_SUBTITLE ${LANG_JAPANESE} "インストーラーがインストールを準備しています。お待ちください。"
LangString INSTALLING_SUBTITLE ${LANG_ENGLISH} "The installer is preparing to install, please wait."

LangString FINISH_SUBTITLE ${LANG_SIMPCHINESE} "安装程序已完成安装。"
LangString FINISH_SUBTITLE ${LANG_KOREAN} "설치 프로그램이 설치를 완료했습니다."
LangString FINISH_SUBTITLE ${LANG_JAPANESE} "インストーラーはインストールを完了しました。"
LangString FINISH_SUBTITLE ${LANG_ENGLISH} "The installer has completed the installation."

LangString ABORT_TITLE ${LANG_SIMPCHINESE} "安装已取消"
LangString ABORT_TITLE ${LANG_KOREAN} "설치가 취소되었습니다"
LangString ABORT_TITLE ${LANG_JAPANESE} "インストールがキャンセルされました"
LangString ABORT_TITLE ${LANG_ENGLISH} "Installation Cancelled"

LangString ABORT_SUBTITLE ${LANG_SIMPCHINESE} "安装程序未完成安装。"
LangString ABORT_SUBTITLE ${LANG_KOREAN} "설치 프로그램이 설치를 완료하지 못했습니다."
LangString ABORT_SUBTITLE ${LANG_JAPANESE} "インストーラーはインストールを完了しませんでした。"
LangString ABORT_SUBTITLE ${LANG_ENGLISH} "The installer did not complete the installation."

; ---- 按钮 ----
LangString BTN_NEXT ${LANG_SIMPCHINESE} "&下一步 >"
LangString BTN_NEXT ${LANG_KOREAN} "&다음 >"
LangString BTN_NEXT ${LANG_JAPANESE} "&次へ >"
LangString BTN_NEXT ${LANG_ENGLISH} "&Next >"

LangString BTN_BACK ${LANG_SIMPCHINESE} "< &上一步"
LangString BTN_BACK ${LANG_KOREAN} "< &뒤로"
LangString BTN_BACK ${LANG_JAPANESE} "< &戻る"
LangString BTN_BACK ${LANG_ENGLISH} "< &Back"

LangString BTN_CANCEL ${LANG_SIMPCHINESE} "取消"
LangString BTN_CANCEL ${LANG_KOREAN} "취소"
LangString BTN_CANCEL ${LANG_JAPANESE} "キャンセル"
LangString BTN_CANCEL ${LANG_ENGLISH} "Cancel"

LangString BTN_CLOSE ${LANG_SIMPCHINESE} "关闭"
LangString BTN_CLOSE ${LANG_KOREAN} "닫기"
LangString BTN_CLOSE ${LANG_JAPANESE} "閉じる"
LangString BTN_CLOSE ${LANG_ENGLISH} "Close"

LangString BTN_INSTALL ${LANG_SIMPCHINESE} "安装"
LangString BTN_INSTALL ${LANG_KOREAN} "설치"
LangString BTN_INSTALL ${LANG_JAPANESE} "インストール"
LangString BTN_INSTALL ${LANG_ENGLISH} "Install"

LangString BTN_UNINSTALL ${LANG_SIMPCHINESE} "卸载"
LangString BTN_UNINSTALL ${LANG_KOREAN} "제거"
LangString BTN_UNINSTALL ${LANG_JAPANESE} "アンインストール"
LangString BTN_UNINSTALL ${LANG_ENGLISH} "Uninstall"

; ---- 卸载按钮 ----
LangString UNBTN_NEXT ${LANG_SIMPCHINESE} "&卸载 >"
LangString UNBTN_NEXT ${LANG_KOREAN} "&제거 >"
LangString UNBTN_NEXT ${LANG_JAPANESE} "&アンインストール >"
LangString UNBTN_NEXT ${LANG_ENGLISH} "&Uninstall >"

LangString UNBTN_BACK ${LANG_SIMPCHINESE} "< &上一步"
LangString UNBTN_BACK ${LANG_KOREAN} "< &뒤로"
LangString UNBTN_BACK ${LANG_JAPANESE} "< &戻る"
LangString UNBTN_BACK ${LANG_ENGLISH} "< &Back"

LangString UNBTN_CANCEL ${LANG_SIMPCHINESE} "取消"
LangString UNBTN_CANCEL ${LANG_KOREAN} "취소"
LangString UNBTN_CANCEL ${LANG_JAPANESE} "キャンセル"
LangString UNBTN_CANCEL ${LANG_ENGLISH} "Cancel"

LangString UNBTN_CLOSE ${LANG_SIMPCHINESE} "关闭"
LangString UNBTN_CLOSE ${LANG_KOREAN} "닫기"
LangString UNBTN_CLOSE ${LANG_JAPANESE} "閉じる"
LangString UNBTN_CLOSE ${LANG_ENGLISH} "Close"

; ---- 段描述 ----
LangString DESC_SecMain ${LANG_SIMPCHINESE} "${APP_NAME} 主程序，所有功能必需。"
LangString DESC_SecMain ${LANG_KOREAN} "${APP_NAME} 메인 프로그램. 모든 기능에 필요합니다."
LangString DESC_SecMain ${LANG_JAPANESE} "${APP_NAME} メインプログラム。すべての機能に必要です。"
LangString DESC_SecMain ${LANG_ENGLISH} "${APP_NAME} main program. Required for all features."

LangString DESC_SecDesktop ${LANG_SIMPCHINESE} "在桌面创建 ${APP_NAME} 的快捷方式。"
LangString DESC_SecDesktop ${LANG_KOREAN} "바탕 화면에 ${APP_NAME} 바로 가기를 만듭니다."
LangString DESC_SecDesktop ${LANG_JAPANESE} "デスクトップに ${APP_NAME} のショートカットを作成します。"
LangString DESC_SecDesktop ${LANG_ENGLISH} "Create a ${APP_NAME} shortcut on the desktop."

LangString DESC_SecStartMenu ${LANG_SIMPCHINESE} "在开始菜单创建 ${APP_NAME} 与卸载的快捷方式。"
LangString DESC_SecStartMenu ${LANG_KOREAN} "시작 메뉴에 ${APP_NAME} 및 제거 바로 가기를 만듭니다."
LangString DESC_SecStartMenu ${LANG_JAPANESE} "スタートメニューに ${APP_NAME} とアンインストールのショートカットを作成します。"
LangString DESC_SecStartMenu ${LANG_ENGLISH} "Create ${APP_NAME} and uninstall shortcuts in the Start menu."

; ---- 卸载时其他提示 ----

LangString UNINSTALL_UNINSTALL ${LANG_SIMPCHINESE} "卸载"
LangString UNINSTALL_UNINSTALL ${LANG_KOREAN} "제거"
LangString UNINSTALL_UNINSTALL ${LANG_JAPANESE} "アンインストール"
LangString UNINSTALL_UNINSTALL ${LANG_ENGLISH} "Uninstall"

LangString PRESETS_PROMPT ${LANG_SIMPCHINESE} "是否同时删除预设文件 presets.json？$\r$\n选否会保留您保存的预设。"
LangString PRESETS_PROMPT ${LANG_KOREAN} "프리셋 파일 presets.json도 삭제하시겠습니까？$\r$\n아니요를 선택하면 저장된 프리셋이 유지됩니다."
LangString PRESETS_PROMPT ${LANG_JAPANESE} "プリセットファイル presets.json も削除しますか？$\r$\n「いいえ」を選択すると、保存したプリセットが保持されます。"
LangString PRESETS_PROMPT ${LANG_ENGLISH} "Also delete the preset file presets.json?$\r$\nChoosing No will keep your saved presets."

LangString PRESETS_YES ${LANG_SIMPCHINESE} "是"
LangString PRESETS_YES ${LANG_KOREAN} "예"
LangString PRESETS_YES ${LANG_JAPANESE} "はい"
LangString PRESETS_YES ${LANG_ENGLISH} "Yes"

LangString PRESETS_NO ${LANG_SIMPCHINESE} "否"
LangString PRESETS_NO ${LANG_KOREAN} "아니요"
LangString PRESETS_NO ${LANG_JAPANESE} "いいえ"
LangString PRESETS_NO ${LANG_ENGLISH} "No"

; -------- 节定义 --------
Section $(DESC_SecMain) SecMain
    SectionIn RO

    SetOutPath "$INSTDIR"

    ; 覆盖升级前关掉正在运行的程序（VR 附属程序可能被 SteamVR 在后台拉起着），否则文件覆盖不了
    nsExec::Exec 'taskkill /F /IM ${VR_EXE}'
    nsExec::Exec 'taskkill /F /T /IM ${APP_EXE}'
    nsExec::Exec 'taskkill /F /IM ${OLD_VR_EXE}'
    nsExec::Exec 'taskkill /F /T /IM ${OLD_APP_EXE}'
    Sleep 800

    ; 复制 PyInstaller --onedir 输出的全部文件（含 GanlanTranslateVR.exe）
    File /r "${BUILD_DIR}\*.*"

    Call MigrateOldInstall

    ; 程序会在安装目录里写 presets.json、VR 设置、下载的模型等，给普通用户写权限
    ; （S-1-5-32-545 = Users 组，不受系统语言影响）
    nsExec::Exec 'icacls "$INSTDIR" /grant *S-1-5-32-545:(OI)(CI)M /T /Q'

    ; 写入卸载信息
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT_NAME}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT_NAME}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT_NAME}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT_NAME}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT_NAME}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT_NAME}" "DisplayIcon" "$INSTDIR\${APP_EXE}"

    ; 注册安装目录
    WriteRegStr HKLM "Software\${APP_SHORT_NAME}" "InstallDir" "$INSTDIR"
SectionEnd

Section $(DESC_SecDesktop) SecDesktop
    CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
SectionEnd

Section $(DESC_SecStartMenu) SecStartMenu
    CreateDirectory "$SMPROGRAMS\${APP_DIR}"
    CreateShortcut "$SMPROGRAMS\${APP_DIR}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
    CreateShortcut "$SMPROGRAMS\${APP_DIR}\$(BTN_UNINSTALL).lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\Uninstall.exe" 0
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} $(DESC_SecMain)
    !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} $(DESC_SecDesktop)
    !insertmacro MUI_DESCRIPTION_TEXT ${SecStartMenu} $(DESC_SecStartMenu)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; -------- 改名前的旧版（GanLan）：搬数据、删旧目录 --------
Var OldDir

!macro CopyOldData NAME
    ${If} ${FileExists} "$OldDir\${NAME}\*.*"
        CreateDirectory "$INSTDIR\${NAME}"
        CopyFiles /SILENT "$OldDir\${NAME}\*.*" "$INSTDIR\${NAME}"
    ${EndIf}
!macroend

Function MigrateOldInstall
    ReadRegStr $OldDir HKLM "Software\${OLD_SHORT_NAME}" "InstallDir"
    ${If} $OldDir == ""
        Return
    ${EndIf}

    ${If} $OldDir == $INSTDIR
        ; 自己选回了旧目录：数据已经在这里，只删旧名字的程序
        Delete "$INSTDIR\${OLD_APP_EXE}"
        Delete "$INSTDIR\${OLD_VR_EXE}"
    ${ElseIf} ${FileExists} "$OldDir\${OLD_APP_EXE}"
        ; 预设、自定义热词 / 术语、VR 附属程序设置、下载过的模型
        ${If} ${FileExists} "$OldDir\presets.json"
            CopyFiles /SILENT "$OldDir\presets.json" "$INSTDIR"
        ${EndIf}
        !insertmacro CopyOldData "hot_words_private"
        !insertmacro CopyOldData "terminology_private"
        !insertmacro CopyOldData "models"
        !insertmacro CopyOldData "local_asr_models"
        ${If} ${FileExists} "$OldDir\vr_overlay\settings.json"
            CreateDirectory "$INSTDIR\vr_overlay"
            CopyFiles /SILENT "$OldDir\vr_overlay\settings.json" "$INSTDIR\vr_overlay"
        ${EndIf}
        ; 只删确实是旧版安装目录的文件夹（里面有 GanLan.exe）
        RMDir /r "$OldDir"
    ${EndIf}

    Delete "$DESKTOP\${APP_NAME}.lnk"
    RMDir /r "$SMPROGRAMS\${OLD_SHORT_NAME}"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${OLD_SHORT_NAME}"
    DeleteRegKey HKLM "Software\${OLD_SHORT_NAME}"
FunctionEnd

; -------- 安装前检查 --------
Function .onInit
    ; 已装过甘岚翻译：直接覆盖升级（安装目录沿用注册表里的），保留 presets.json 等数据，不提示卸载。
FunctionEnd

; -------- 卸载段 --------
Section "Uninstall"
    nsExec::Exec 'taskkill /F /IM ${VR_EXE}'
    nsExec::Exec 'taskkill /F /T /IM ${APP_EXE}'
    Sleep 800
    RMDir /r "$INSTDIR"

    Delete "$DESKTOP\${APP_NAME}.lnk"
    RMDir /r "$SMPROGRAMS\${APP_DIR}"

    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT_NAME}"
    DeleteRegKey HKLM "Software\${APP_SHORT_NAME}"

    ${If} ${FileExists} "$INSTDIR\presets.json"
        MessageBox MB_YESNO|MB_ICONQUESTION "$(PRESETS_PROMPT)" IDYES +2 IDNO +1
        Delete "$INSTDIR\presets.json"
    ${EndIf}
SectionEnd

Function LaunchAsCurrentUser
    Exec '"$WINDIR\explorer.exe" "$INSTDIR\${APP_EXE}"'
FunctionEnd
