/*
 * 甘岚翻译 安装程序的界面。
 *
 * 外壳（installer/*.cs）用 WebView2 的 postMessage 发来状态、进度和日志，这里负责显示，再把用户的操作发回去：
 *   发给外壳：ready、browse、checkDir、install、uninstall、launch、openFolder、openLog、copy、drag、minimize、close
 *   外壳发来：init、dir、progress、log、done、closeRequest
 * 界面语言跟 Windows 显示语言（init 里的 lang），不能手动切。
 * 在浏览器里打开（installer/preview.py）时没有外壳，进演示模式，用假的进度和日志；
 * 地址后面加 ?step=install&lang=ja&mode=uninstall&upgrade=1&fail=1 可以直接看某一步。
 */
(function () {
    'use strict';

    const TEXT = {
        'zh-CN': {
            'app.name': '甘岚翻译',
            'brand.setup': '安装程序',
            'brand.uninstall': '卸载程序',
            'step.welcome': '欢迎', 'step.license': '许可协议', 'step.location': '安装位置', 'step.install': '安装', 'step.finish': '完成',
            'step.confirm': '确认', 'step.uninstall': '卸载',
            'welcome.title': '安装甘岚翻译',
            'welcome.sub': '版本 {version}',
            'welcome.text': '欢迎使用 甘岚翻译，感谢您的支持！',
            'note.upgrade': '已安装 {old}，这次会更新到 {new}。你的设置、插件和下载的模型都会保留。',
            'note.reinstall': '已安装 {old}，这次会重新安装同一个版本。你的设置、插件和下载的模型都会保留。',
            'note.newer': '已安装的 {old} 比这个安装包（{new}）新，继续的话会装回旧版本。',
            'note.running': '甘岚翻译正在运行，安装时会先把它关掉。',
            'note.runningUninstall': '甘岚翻译正在运行，卸载时会先把它关掉。',
            'note.sandbox': '测试模式：注册表和快捷方式写在 {dir}，不会动这台电脑上已经装好的甘岚翻译。',
            'license.title': '许可协议',
            'license.hint': '甘岚翻译是开源软件，使用 MIT 许可',
            'license.agree': '我已阅读并同意上面的许可协议',
            'license.thirdParty': '本安装程序使用了 Microsoft Edge WebView2 SDK，下面是它的许可和声明：',
            'location.title': '安装位置',
            'location.hintNew': '选择甘岚翻译装在哪个文件夹',
            'location.hintLocked': '更新时装在原来的位置，你的设置和数据都在这里',
            'location.browse': '浏览…',
            'location.required': '需要 {size}',
            'location.free': '可用 {size}',
            'location.noSpace': '磁盘空间不够',
            'location.existing': '这里已经装了甘岚翻译，会覆盖更新',
            'dir.empty': '请填写安装位置',
            'dir.relative': '请填写完整路径，比如 C:\\Program Files (x86)\\Ganlan Translate',
            'dir.invalid': '路径里有不能用的字符',
            'dir.root': '不能直接装在磁盘根目录，请选一个文件夹',
            'dir.system': '不能装在 Windows 系统文件夹里',
            'dir.file': '这是一个文件，不是文件夹',
            'shortcuts.desktop': '在桌面创建快捷方式',
            'shortcuts.startMenu': '在开始菜单创建快捷方式',
            'install.log': '安装日志',
            'uninstall.log': '卸载日志',
            'install.lines': '{n} 行',
            'phase.start': '准备中…',
            'phase.verify': '正在校验安装包…',
            'phase.close': '正在关闭甘岚翻译…',
            'phase.extract': '正在解压文件 {done} / {total}',
            'phase.remove': '正在删除文件 {done} / {total}',
            'phase.finish': '正在收尾…',
            'phase.installDone': '安装完成',
            'phase.uninstallDone': '卸载完成',
            'phase.failed': '没有完成',
            'done.title': '甘岚翻译已安装',
            'done.titleUpgrade': '已更新到 {version}',
            'done.text': '装在 {dir}\n可以从桌面或开始菜单打开。',
            'done.launch': '现在启动甘岚翻译',
            'done.openFolder': '打开安装位置',
            'done.openLog': '查看日志',
            'done.uTitle': '甘岚翻译已卸载',
            'done.uText': '已经从这台电脑上移除了。',
            'done.uKept': '你的数据留在 {dir}\n重新安装后还能接着用；不需要的话可以手动删掉这个文件夹。',
            'confirm.title': '卸载甘岚翻译 {version}',
            'confirm.hint': '将从下面的位置卸载',
            'confirm.removeData': '同时删除我的数据',
            'confirm.removeHint': '预设、插件、自定义热词和术语、下载的模型、VR 设置。不勾的话这些会留在原来的位置，重新安装后还能用。',
            'confirm.found': '找到了：{items}',
            'btn.back': '上一步', 'btn.next': '下一步', 'btn.install': '安装', 'btn.update': '更新', 'btn.uninstall': '卸载',
            'btn.finish': '完成', 'btn.retry': '重试', 'btn.copyLog': '复制日志', 'btn.copied': '已复制',
            'footer.sandbox': '测试模式',
            'close.titleInstall': '要退出安装吗？',
            'close.textInstall': '甘岚翻译还没有装好。退出后可以再运行安装程序。',
            'close.titleUninstall': '要退出卸载吗？',
            'close.textUninstall': '还没有开始卸载，甘岚翻译不会有任何变化。',
            'close.quit': '退出', 'close.stay': '继续',
            'window.minimize': '最小化', 'window.close': '关闭',
        },
        en: {
            'app.name': 'Ganlan Translate',
            'brand.setup': 'Setup',
            'brand.uninstall': 'Uninstall',
            'step.welcome': 'Welcome', 'step.license': 'License', 'step.location': 'Location', 'step.install': 'Install', 'step.finish': 'Finish',
            'step.confirm': 'Confirm', 'step.uninstall': 'Uninstall',
            'welcome.title': 'Install Ganlan Translate',
            'welcome.sub': 'Version {version}',
            'welcome.text': 'Welcome to Ganlan Translate, and thank you for your support!',
            'note.upgrade': '{old} is installed and will be updated to {new}. Your settings, plugins and downloaded models are kept.',
            'note.reinstall': '{old} is installed and the same version will be reinstalled. Your settings, plugins and downloaded models are kept.',
            'note.newer': 'The installed {old} is newer than this installer ({new}). Continuing installs the older version.',
            'note.running': 'Ganlan Translate is running and will be closed during setup.',
            'note.runningUninstall': 'Ganlan Translate is running and will be closed before uninstalling.',
            'note.sandbox': 'Test mode: registry entries and shortcuts go to {dir}; the Ganlan Translate installed on this PC is not touched.',
            'license.title': 'License agreement',
            'license.hint': 'Ganlan Translate is open source under the MIT License',
            'license.agree': 'I have read and accept the license agreement above',
            'license.thirdParty': 'This installer uses the Microsoft Edge WebView2 SDK. Its license and notices follow:',
            'location.title': 'Install location',
            'location.hintNew': 'Choose the folder to install Ganlan Translate in',
            'location.hintLocked': 'Updates go to the existing location, where your settings and data are',
            'location.browse': 'Browse…',
            'location.required': '{size} needed',
            'location.free': '{size} free',
            'location.noSpace': 'Not enough disk space',
            'location.existing': 'Ganlan Translate is already here and will be updated',
            'dir.empty': 'Enter an install location',
            'dir.relative': 'Enter a full path, e.g. C:\\Program Files (x86)\\Ganlan Translate',
            'dir.invalid': 'The path contains characters that cannot be used',
            'dir.root': 'Choose a folder, not the root of a drive',
            'dir.system': 'Cannot install into the Windows system folder',
            'dir.file': 'This is a file, not a folder',
            'shortcuts.desktop': 'Create a desktop shortcut',
            'shortcuts.startMenu': 'Create Start menu shortcuts',
            'install.log': 'Setup log',
            'uninstall.log': 'Uninstall log',
            'install.lines': '{n} lines',
            'phase.start': 'Preparing…',
            'phase.verify': 'Checking the installer…',
            'phase.close': 'Closing Ganlan Translate…',
            'phase.extract': 'Extracting files {done} / {total}',
            'phase.remove': 'Removing files {done} / {total}',
            'phase.finish': 'Finishing up…',
            'phase.installDone': 'Installed',
            'phase.uninstallDone': 'Uninstalled',
            'phase.failed': 'Not completed',
            'done.title': 'Ganlan Translate is installed',
            'done.titleUpgrade': 'Updated to {version}',
            'done.text': 'Installed in {dir}\nOpen it from the desktop or the Start menu.',
            'done.launch': 'Launch Ganlan Translate now',
            'done.openFolder': 'Open install folder',
            'done.openLog': 'View log',
            'done.uTitle': 'Ganlan Translate is uninstalled',
            'done.uText': 'It has been removed from this PC.',
            'done.uKept': 'Your data is kept in {dir}\nIt will be picked up again if you reinstall; delete the folder yourself if you no longer need it.',
            'confirm.title': 'Uninstall Ganlan Translate {version}',
            'confirm.hint': 'It will be removed from this location',
            'confirm.removeData': 'Also delete my data',
            'confirm.removeHint': 'Presets, plugins, custom hot words and terms, downloaded models and VR settings. If unchecked they stay where they are and are used again after reinstalling.',
            'confirm.found': 'Found: {items}',
            'btn.back': 'Back', 'btn.next': 'Next', 'btn.install': 'Install', 'btn.update': 'Update', 'btn.uninstall': 'Uninstall',
            'btn.finish': 'Finish', 'btn.retry': 'Retry', 'btn.copyLog': 'Copy log', 'btn.copied': 'Copied',
            'footer.sandbox': 'Test mode',
            'close.titleInstall': 'Quit setup?',
            'close.textInstall': 'Ganlan Translate is not installed yet. You can run setup again later.',
            'close.titleUninstall': 'Quit uninstall?',
            'close.textUninstall': 'Nothing has been removed yet.',
            'close.quit': 'Quit', 'close.stay': 'Continue',
            'window.minimize': 'Minimize', 'window.close': 'Close',
        },
        ja: {
            'app.name': '甘嵐翻訳',
            'brand.setup': 'セットアップ',
            'brand.uninstall': 'アンインストール',
            'step.welcome': 'ようこそ', 'step.license': '使用許諾', 'step.location': 'インストール先', 'step.install': 'インストール', 'step.finish': '完了',
            'step.confirm': '確認', 'step.uninstall': 'アンインストール',
            'welcome.title': '甘嵐翻訳をインストール',
            'welcome.sub': 'バージョン {version}',
            'welcome.text': '甘嵐翻訳へようこそ。ご支援ありがとうございます！',
            'note.upgrade': '{old} がインストール済みです。{new} に更新します。設定・プラグイン・ダウンロードしたモデルはそのまま残ります。',
            'note.reinstall': '{old} がインストール済みです。同じバージョンを入れ直します。設定・プラグイン・ダウンロードしたモデルはそのまま残ります。',
            'note.newer': 'インストール済みの {old} はこのインストーラー（{new}）より新しいため、続けると古いバージョンに戻ります。',
            'note.running': '甘嵐翻訳が起動中です。インストール前に終了します。',
            'note.runningUninstall': '甘嵐翻訳が起動中です。アンインストール前に終了します。',
            'note.sandbox': 'テストモード：レジストリとショートカットは {dir} に書き込み、この PC にインストール済みの甘嵐翻訳には触れません。',
            'license.title': '使用許諾契約',
            'license.hint': '甘嵐翻訳は MIT ライセンスのオープンソースソフトです',
            'license.agree': '上記の使用許諾契約を読み、同意します',
            'license.thirdParty': 'このインストーラーは Microsoft Edge WebView2 SDK を使用しています。そのライセンスと告知は以下のとおりです：',
            'location.title': 'インストール先',
            'location.hintNew': '甘嵐翻訳をインストールするフォルダーを選んでください',
            'location.hintLocked': '更新は元の場所に行います。設定とデータもここにあります',
            'location.browse': '参照…',
            'location.required': '必要：{size}',
            'location.free': '空き：{size}',
            'location.noSpace': 'ディスクの空き容量が足りません',
            'location.existing': 'ここにはすでに甘嵐翻訳があり、上書き更新します',
            'dir.empty': 'インストール先を入力してください',
            'dir.relative': 'フルパスで入力してください（例：C:\\Program Files (x86)\\Ganlan Translate）',
            'dir.invalid': 'パスに使えない文字が含まれています',
            'dir.root': 'ドライブの直下ではなくフォルダーを選んでください',
            'dir.system': 'Windows のシステムフォルダーにはインストールできません',
            'dir.file': 'これはファイルで、フォルダーではありません',
            'shortcuts.desktop': 'デスクトップにショートカットを作成',
            'shortcuts.startMenu': 'スタートメニューにショートカットを作成',
            'install.log': 'インストールログ',
            'uninstall.log': 'アンインストールログ',
            'install.lines': '{n} 行',
            'phase.start': '準備しています…',
            'phase.verify': 'インストーラーを検証しています…',
            'phase.close': '甘嵐翻訳を終了しています…',
            'phase.extract': 'ファイルを展開しています {done} / {total}',
            'phase.remove': 'ファイルを削除しています {done} / {total}',
            'phase.finish': '仕上げています…',
            'phase.installDone': 'インストール完了',
            'phase.uninstallDone': 'アンインストール完了',
            'phase.failed': '完了しませんでした',
            'done.title': '甘嵐翻訳をインストールしました',
            'done.titleUpgrade': '{version} に更新しました',
            'done.text': 'インストール先：{dir}\nデスクトップかスタートメニューから起動できます。',
            'done.launch': '今すぐ甘嵐翻訳を起動する',
            'done.openFolder': 'インストール先を開く',
            'done.openLog': 'ログを見る',
            'done.uTitle': '甘嵐翻訳をアンインストールしました',
            'done.uText': 'この PC から削除しました。',
            'done.uKept': 'あなたのデータは {dir} に残っています。\n再インストールすればそのまま使えます。不要ならフォルダーを手動で削除してください。',
            'confirm.title': '甘嵐翻訳 {version} をアンインストール',
            'confirm.hint': '次の場所から削除します',
            'confirm.removeData': '自分のデータも削除する',
            'confirm.removeHint': 'プリセット、プラグイン、ホットワードと用語、ダウンロードしたモデル、VR 設定。チェックしなければ元の場所に残り、再インストール後も使えます。',
            'confirm.found': '見つかったもの：{items}',
            'btn.back': '戻る', 'btn.next': '次へ', 'btn.install': 'インストール', 'btn.update': '更新', 'btn.uninstall': 'アンインストール',
            'btn.finish': '完了', 'btn.retry': '再試行', 'btn.copyLog': 'ログをコピー', 'btn.copied': 'コピーしました',
            'footer.sandbox': 'テストモード',
            'close.titleInstall': 'セットアップを終了しますか？',
            'close.textInstall': '甘嵐翻訳はまだインストールされていません。あとでもう一度実行できます。',
            'close.titleUninstall': 'アンインストールを終了しますか？',
            'close.textUninstall': 'まだ何も削除していません。',
            'close.quit': '終了', 'close.stay': '続ける',
            'window.minimize': '最小化', 'window.close': '閉じる',
        },
        ko: {
            'app.name': '간란 번역',
            'brand.setup': '설치',
            'brand.uninstall': '제거',
            'step.welcome': '시작', 'step.license': '사용권 계약', 'step.location': '설치 위치', 'step.install': '설치', 'step.finish': '완료',
            'step.confirm': '확인', 'step.uninstall': '제거',
            'welcome.title': '간란 번역 설치',
            'welcome.sub': '버전 {version}',
            'welcome.text': '간란 번역에 오신 것을 환영합니다. 응원해 주셔서 감사합니다!',
            'note.upgrade': '{old}이(가) 설치되어 있어 {new}(으)로 업데이트합니다. 설정, 플러그인, 다운로드한 모델은 그대로 유지됩니다.',
            'note.reinstall': '{old}이(가) 설치되어 있어 같은 버전을 다시 설치합니다. 설정, 플러그인, 다운로드한 모델은 그대로 유지됩니다.',
            'note.newer': '설치된 {old}이(가) 이 설치 파일({new})보다 새 버전입니다. 계속하면 이전 버전으로 돌아갑니다.',
            'note.running': '간란 번역이 실행 중이며 설치 전에 종료됩니다.',
            'note.runningUninstall': '간란 번역이 실행 중이며 제거 전에 종료됩니다.',
            'note.sandbox': '테스트 모드: 레지스트리와 바로 가기를 {dir}에 쓰며, 이 PC에 설치된 간란 번역은 건드리지 않습니다.',
            'license.title': '사용권 계약',
            'license.hint': '간란 번역은 MIT 라이선스의 오픈소스 소프트웨어입니다',
            'license.agree': '위 사용권 계약을 읽었으며 이에 동의합니다',
            'license.thirdParty': '이 설치 프로그램은 Microsoft Edge WebView2 SDK를 사용합니다. 해당 라이선스와 고지는 다음과 같습니다:',
            'location.title': '설치 위치',
            'location.hintNew': '간란 번역을 설치할 폴더를 고르세요',
            'location.hintLocked': '업데이트는 기존 위치에 설치됩니다. 설정과 데이터도 여기에 있습니다',
            'location.browse': '찾아보기…',
            'location.required': '필요 {size}',
            'location.free': '남은 공간 {size}',
            'location.noSpace': '디스크 공간이 부족합니다',
            'location.existing': '여기에 이미 간란 번역이 있어 덮어써서 업데이트합니다',
            'dir.empty': '설치 위치를 입력하세요',
            'dir.relative': '전체 경로를 입력하세요(예: C:\\Program Files (x86)\\Ganlan Translate)',
            'dir.invalid': '경로에 사용할 수 없는 문자가 있습니다',
            'dir.root': '드라이브 루트가 아닌 폴더를 고르세요',
            'dir.system': 'Windows 시스템 폴더에는 설치할 수 없습니다',
            'dir.file': '폴더가 아니라 파일입니다',
            'shortcuts.desktop': '바탕 화면에 바로 가기 만들기',
            'shortcuts.startMenu': '시작 메뉴에 바로 가기 만들기',
            'install.log': '설치 로그',
            'uninstall.log': '제거 로그',
            'install.lines': '{n}줄',
            'phase.start': '준비 중…',
            'phase.verify': '설치 파일 확인 중…',
            'phase.close': '간란 번역을 닫는 중…',
            'phase.extract': '파일 푸는 중 {done} / {total}',
            'phase.remove': '파일 삭제 중 {done} / {total}',
            'phase.finish': '마무리하는 중…',
            'phase.installDone': '설치 완료',
            'phase.uninstallDone': '제거 완료',
            'phase.failed': '완료하지 못했습니다',
            'done.title': '간란 번역을 설치했습니다',
            'done.titleUpgrade': '{version}(으)로 업데이트했습니다',
            'done.text': '설치 위치: {dir}\n바탕 화면이나 시작 메뉴에서 열 수 있습니다.',
            'done.launch': '지금 간란 번역 실행',
            'done.openFolder': '설치 폴더 열기',
            'done.openLog': '로그 보기',
            'done.uTitle': '간란 번역을 제거했습니다',
            'done.uText': '이 PC에서 제거되었습니다.',
            'done.uKept': '내 데이터는 {dir}에 남아 있습니다.\n다시 설치하면 그대로 사용할 수 있고, 필요 없으면 폴더를 직접 삭제하세요.',
            'confirm.title': '간란 번역 {version} 제거',
            'confirm.hint': '다음 위치에서 제거합니다',
            'confirm.removeData': '내 데이터도 함께 삭제',
            'confirm.removeHint': '프리셋, 플러그인, 핫워드와 용어, 다운로드한 모델, VR 설정. 선택하지 않으면 원래 위치에 남아 다시 설치한 뒤에도 사용할 수 있습니다.',
            'confirm.found': '찾은 항목: {items}',
            'btn.back': '이전', 'btn.next': '다음', 'btn.install': '설치', 'btn.update': '업데이트', 'btn.uninstall': '제거',
            'btn.finish': '완료', 'btn.retry': '다시 시도', 'btn.copyLog': '로그 복사', 'btn.copied': '복사했습니다',
            'footer.sandbox': '테스트 모드',
            'close.titleInstall': '설치를 끝낼까요?',
            'close.textInstall': '간란 번역이 아직 설치되지 않았습니다. 나중에 다시 실행할 수 있습니다.',
            'close.titleUninstall': '제거를 끝낼까요?',
            'close.textUninstall': '아직 아무것도 삭제하지 않았습니다.',
            'close.quit': '끝내기', 'close.stay': '계속',
            'window.minimize': '최소화', 'window.close': '닫기',
        },
    };

    const INSTALL_STEPS = ['welcome', 'license', 'location', 'install', 'finish'];
    const UNINSTALL_STEPS = ['confirm', 'install', 'finish'];
    const INFO_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>';
    const WARN_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/></svg>';
    const USER_DATA_LABELS = {
        'presets.json': { 'zh-CN': '预设', en: 'presets', ja: 'プリセット', ko: '프리셋' },
        plugins: { 'zh-CN': '插件', en: 'plugins', ja: 'プラグイン', ko: '플러그인' },
        hot_words_private: { 'zh-CN': '自定义热词', en: 'custom hot words', ja: 'ホットワード', ko: '핫워드' },
        terminology_private: { 'zh-CN': '自定义术语', en: 'custom terms', ja: '用語', ko: '용어' },
        models: { 'zh-CN': '下载的模型', en: 'downloaded models', ja: 'モデル', ko: '모델' },
        local_asr_models: { 'zh-CN': '本地识别模型', en: 'local ASR models', ja: 'ローカル認識モデル', ko: '로컬 인식 모델' },
        'vr_overlay\\settings.json': { 'zh-CN': 'VR 设置', en: 'VR settings', ja: 'VR 設定', ko: 'VR 설정' },
    };

    const host = window.chrome && window.chrome.webview ? window.chrome.webview : null;
    const params = new URLSearchParams(location.search);
    const $ = (id) => document.getElementById(id);

    const state = {
        lang: 'zh-CN',
        info: null,
        steps: INSTALL_STEPS,
        step: null,
        busy: false,
        finished: false,
        failed: false,
        result: null,
        dir: null,
        logLines: 0,
        licenseLoaded: false,
    };

    // ---------------- 文字 ----------------

    function t(key, vars) {
        const table = TEXT[state.lang] || TEXT.en;
        let text = table[key] != null ? table[key] : (TEXT.en[key] != null ? TEXT.en[key] : key);
        if (vars) text = text.replace(/\{(\w+)\}/g, (m, name) => (vars[name] != null ? vars[name] : m));
        return text;
    }

    function normalizeLang(lang) {
        if (!lang) return null;
        const l = String(lang).toLowerCase();
        if (l.startsWith('zh')) return 'zh-CN';
        if (l.startsWith('ja')) return 'ja';
        if (l.startsWith('ko')) return 'ko';
        if (l.startsWith('en')) return 'en';
        return null;
    }

    function setLang(lang) {
        state.lang = normalizeLang(lang) || 'en';
        document.documentElement.lang = state.lang;
        document.querySelectorAll('[data-i18n]').forEach((el) => {
            el.textContent = t(el.dataset.i18n);
        });
        document.querySelectorAll('.setup-win-btn').forEach((el) => {
            const label = t('window.' + el.dataset.action);
            el.title = label;
            el.setAttribute('aria-label', label);
        });
    }

    function formatSize(bytes) {
        if (!(bytes >= 0)) return '';
        if (bytes >= 1024 ** 3) return (bytes / 1024 ** 3).toFixed(1) + ' GB';
        return Math.round(bytes / 1024 ** 2) + ' MB';
    }

    // ---------------- 和外壳通信 ----------------

    function send(type, data) {
        const message = Object.assign({ type }, data || {});
        if (host) host.postMessage(message);
        else Demo.receive(message);
    }

    function onHostMessage(message) {
        if (!message || !message.type) return;
        switch (message.type) {
            case 'init': applyInit(message); break;
            case 'dir': applyDir(message); break;
            case 'progress': setProgress(message); break;
            case 'log': appendLog(message.lines || []); break;
            case 'done': onDone(message); break;
            case 'closeRequest': requestClose(); break;
        }
    }

    // 动画区：岚放的动画想跟着进度变，就监听这几个事件
    function animationEvent(name, detail) {
        const slot = $('install-animation');
        if (slot) slot.dispatchEvent(new CustomEvent(name, { detail: detail || {}, bubbles: true }));
    }

    // ---------------- 状态 ----------------

    function applyInit(info) {
        state.info = info;
        state.steps = info.mode === 'uninstall' ? UNINSTALL_STEPS : INSTALL_STEPS;
        document.body.classList.toggle('mode-uninstall', info.mode === 'uninstall');
        document.documentElement.classList.toggle('frame-region', info.frame === 'region');
        state.dir = info.dir;
        $('install-dir').value = info.dir || '';
        $('install-dir').readOnly = !!info.dirLocked;
        $('browse-btn').hidden = !!info.dirLocked;
        $('opt-desktop').checked = info.desktop !== false;
        $('opt-startmenu').checked = info.startMenu !== false;
        $('opt-remove-data').checked = false;
        setLang(info.lang || state.lang);
        renderAll();

        let start = params.get('step');
        if (host || !state.steps.includes(start)) start = state.steps[0];
        if (!host && start === 'install') return startWork();
        if (!host && start === 'finish') {
            return onDone({ ok: true, dir: info.dir, leftover: info.mode === 'uninstall' ? info.dir : null });
        }
        goTo(start);
    }

    function applyDir(info) {
        if (!state.info) return;
        Object.assign(state.info, info);
        state.dir = info.dir;
        const input = $('install-dir');
        if (document.activeElement !== input || input.value === '') input.value = info.dir || '';
        renderDirInfo();
        updateFooter();
    }

    function dirProblem() {
        const info = state.info;
        if (!info || info.mode === 'uninstall') return null;
        if (info.dirError) return t('dir.' + info.dirError);
        const required = info.requiredBytes || 0;
        if (info.freeBytes >= 0 && info.freeBytes < required && !info.existing) return t('location.noSpace');
        return null;
    }

    // ---------------- 画界面 ----------------

    function renderAll() {
        const info = state.info;
        const uninstall = info.mode === 'uninstall';
        $('brand-sub').textContent = t(uninstall ? 'brand.uninstall' : 'brand.setup');
        document.title = t('app.name') + ' · ' + t(uninstall ? 'brand.uninstall' : 'brand.setup');
        $('welcome-sub').textContent = t('welcome.sub', { version: info.version || '' });
        $('log-title').textContent = t(uninstall ? 'uninstall.log' : 'install.log');
        renderSteps();
        renderWelcome();
        renderLicense();
        renderDirInfo();
        renderConfirm();
    }

    // 底栏左边的步骤点：当前这一步拉长成绿色的条，走过的变浅绿
    function renderSteps() {
        const bar = $('steps');
        if (bar.children.length !== state.steps.length) {
            bar.textContent = '';
            state.steps.forEach((step) => {
                const dot = document.createElement('span');
                dot.className = 'setup-step';
                bar.appendChild(dot);
            });
        }
        const current = state.steps.indexOf(state.step);
        Array.from(bar.children).forEach((dot, index) => {
            const step = state.steps[index];
            dot.classList.toggle('active', index === current);
            dot.classList.toggle('done', index < current || (state.finished && index === current));
            dot.title = state.info && state.info.mode === 'uninstall' && step === 'install' ? t('step.uninstall') : t('step.' + step);
        });
        bar.setAttribute('aria-label', current >= 0 ? (current + 1) + ' / ' + state.steps.length : '');
    }

    function note(kind, text) {
        const el = document.createElement('div');
        el.className = 'setup-note ' + kind;
        el.innerHTML = kind === 'warn' ? WARN_SVG : INFO_SVG;
        const span = document.createElement('span');
        span.textContent = text;
        el.appendChild(span);
        return el;
    }

    function renderWelcome() {
        const info = state.info;
        const box = $('welcome-notes');
        box.textContent = '';
        if (info.mode === 'uninstall') return;
        if (info.sandbox) box.appendChild(note('info', t('note.sandbox', { dir: info.installedDir || info.dir })));
        if (info.upgrade && info.installedVersion) {
            if (info.newer) box.appendChild(note('warn', t('note.newer', { old: info.installedVersion, new: info.version })));
            else if (info.installedVersion === info.version) box.appendChild(note('info', t('note.reinstall', { old: info.installedVersion })));
            else box.appendChild(note('info', t('note.upgrade', { old: info.installedVersion, new: info.version })));
        }
        if (info.running) box.appendChild(note('warn', t('note.running')));
    }

    function renderLicense() {
        if (state.licenseLoaded) return;
        state.licenseLoaded = true;
        const files = ['LICENSE.txt', 'third_party/WebView2-LICENSE.txt', 'third_party/WebView2-NOTICE.txt'];
        Promise.all(files.map((f) => fetch(f).then((r) => (r.ok ? r.text() : '')).catch(() => ''))).then(([main, wv, notice]) => {
            let text = main.replace(/^\uFEFF/, '').trim();
            if (wv || notice) {
                text += '\n\n────────────────────────\n\n' + t('license.thirdParty') + '\n\n'
                    + [wv, notice].filter(Boolean).map((s) => s.trim()).join('\n\n');
            }
            $('license-text').textContent = text;
        });
    }

    function renderDirInfo() {
        const info = state.info;
        if (!info || info.mode === 'uninstall') return;
        $('location-hint').textContent = t(info.dirLocked ? 'location.hintLocked' : 'location.hintNew');
        const box = $('dir-info');
        box.textContent = '';
        const add = (text, cls) => {
            const el = document.createElement('span');
            el.textContent = text;
            if (cls) el.className = cls;
            box.appendChild(el);
        };
        const problem = dirProblem();
        if (problem) add(problem, 'error');
        if (!info.dirError) {
            add(t('location.required', { size: formatSize(info.requiredBytes) }));
            if (info.freeBytes >= 0) add(t('location.free', { size: formatSize(info.freeBytes) }));
            if (info.existing && !info.dirLocked) add(t('location.existing'), 'warn');
        }
    }

    function renderConfirm() {
        const info = state.info;
        if (info.mode !== 'uninstall') return;
        $('confirm-title').textContent = t('confirm.title', { version: info.version || '' }).trim();
        $('uninstall-dir').textContent = info.dir || '';
        const notes = $('confirm-notes');
        notes.textContent = '';
        if (info.sandbox) notes.appendChild(note('info', t('note.sandbox', { dir: info.dir })));
        if (info.running) notes.appendChild(note('warn', t('note.runningUninstall')));
        let hint = t('confirm.removeHint');
        const found = (info.keep || []).map((k) => (USER_DATA_LABELS[k] ? USER_DATA_LABELS[k][state.lang] || USER_DATA_LABELS[k].en : k));
        if (found.length) hint += '\n' + t('confirm.found', { items: found.join(state.lang === 'en' ? ', ' : '、') });
        $('remove-data-hint').textContent = hint;
    }

    // ---------------- 换页 ----------------

    function goTo(step) {
        state.step = step;
        document.querySelectorAll('.setup-main .app-page').forEach((page) => {
            page.hidden = page.dataset.step !== step;
        });
        $('setup-main').scrollTop = 0;
        renderSteps();
        updateFooter();
        if (step === 'location' && !$('install-dir').readOnly) setTimeout(() => $('install-dir').focus({ preventScroll: true }), 50);
    }

    function updateFooter() {
        const info = state.info;
        if (!info) return;
        const back = $('back-btn');
        const next = $('next-btn');
        const uninstall = info.mode === 'uninstall';
        let backLabel = null;
        let nextLabel = null;
        let nextDisabled = false;
        let danger = false;

        switch (state.step) {
            case 'welcome':
                nextLabel = t('btn.next');
                break;
            case 'license':
                backLabel = t('btn.back');
                nextLabel = t('btn.next');
                nextDisabled = !$('license-agree').checked;
                break;
            case 'location':
                backLabel = t('btn.back');
                nextLabel = t(info.upgrade || info.existing ? 'btn.update' : 'btn.install');
                nextDisabled = !!dirProblem() || !$('install-dir').value.trim();
                break;
            case 'confirm':
                nextLabel = t('btn.uninstall');
                danger = true;
                break;
            case 'install':
                if (state.failed) {
                    backLabel = t('btn.copyLog');
                    nextLabel = t('btn.retry');
                } else {
                    nextLabel = t(uninstall ? 'btn.uninstall' : 'btn.install');
                    nextDisabled = true;
                }
                break;
            case 'finish':
                nextLabel = t('btn.finish');
                break;
        }
        back.hidden = !backLabel;
        back.textContent = backLabel || '';
        next.textContent = nextLabel || '';
        next.disabled = nextDisabled;
        next.classList.toggle('danger', danger);
        $('footer-note').textContent = info.sandbox ? t('footer.sandbox') : '';
    }

    function onNext() {
        if ($('next-btn').disabled) return;
        switch (state.step) {
            case 'welcome': return goTo('license');
            case 'license': return goTo('location');
            case 'location':
            case 'confirm':
            case 'install':
                return startWork();
            case 'finish':
                if (state.info.mode !== 'uninstall' && state.result && state.result.ok && $('opt-launch').checked) send('launch');
                return send('close');
        }
    }

    function onBack() {
        switch (state.step) {
            case 'license': return goTo('welcome');
            case 'location': return goTo('license');
            case 'install':
                if (state.failed) copyLog();
                return;
        }
    }

    function copyLog() {
        const text = $('logs-viewer').textContent;
        if (host) send('copy', { text });
        else if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
        $('back-btn').textContent = t('btn.copied');
        setTimeout(() => updateFooter(), 1400);
    }

    // ---------------- 安装 / 卸载 ----------------

    function startWork() {
        if (state.busy) return;
        const uninstall = state.info.mode === 'uninstall';
        state.busy = true;
        state.failed = false;
        state.result = null;
        $('install-error').hidden = true;
        $('progress-bar').classList.remove('failed', 'complete');
        $('logs-viewer').textContent = '';
        state.logLines = 0;
        $('log-count').textContent = '';
        setProgress({ phase: 'start', percent: 0 });
        if (state.step !== 'install') goTo('install');
        else updateFooter();
        // 动图从头播
        document.querySelectorAll('#install-animation img').forEach((img) => { img.src = img.getAttribute('src'); });
        animationEvent('setup:start', { mode: state.info.mode });
        if (uninstall) send('uninstall', { removeData: $('opt-remove-data').checked });
        else send('install', { dir: $('install-dir').value.trim(), desktop: $('opt-desktop').checked, startMenu: $('opt-startmenu').checked });
    }

    let lastProgress = { phase: 'start', percent: 0, done: 0, total: 0 };

    function setStatusText() {
        const p = lastProgress;
        const uninstall = state.info && state.info.mode === 'uninstall';
        let key = 'phase.' + p.phase;
        if (p.phase === 'done') key = uninstall ? 'phase.uninstallDone' : 'phase.installDone';
        if (state.failed) key = 'phase.failed';
        $('progress-status').textContent = t(key, { done: p.done || 0, total: p.total || 0 });
    }

    function setProgress(p) {
        lastProgress = Object.assign({}, lastProgress, p);
        const percent = Math.max(0, Math.min(100, Number(lastProgress.percent) || 0));
        $('progress-fill').style.width = percent + '%';
        $('progress-percent').textContent = Math.floor(percent) + '%';
        $('progress-bar').setAttribute('aria-valuenow', String(Math.floor(percent)));
        $('progress-bar').classList.toggle('complete', percent >= 100);
        setStatusText();
        animationEvent('setup:progress', { percent, phase: lastProgress.phase });
    }

    function appendLog(lines) {
        if (!lines.length) return;
        const viewer = $('logs-viewer');
        // 和主软件的运行日志一样：停在底部时才跟着新内容走，往上翻时不动
        const atBottom = viewer.scrollHeight - viewer.scrollTop - viewer.clientHeight < 24;
        viewer.insertAdjacentText('beforeend', (state.logLines ? '\n' : '') + lines.join('\n'));
        state.logLines += lines.length;
        $('log-count').textContent = t('install.lines', { n: state.logLines });
        if (atBottom) viewer.scrollTop = viewer.scrollHeight;
    }

    function onDone(result) {
        state.busy = false;
        state.result = result;
        if (!result.ok) {
            state.failed = true;
            const error = $('install-error');
            error.textContent = result.error || t('phase.failed');
            error.hidden = false;
            $('progress-bar').classList.add('failed');
            setStatusText();
            if (state.step !== 'install') goTo('install');
            else updateFooter();
            animationEvent('setup:error', { error: result.error || '' });
            return;
        }
        state.finished = true;
        setProgress({ phase: 'done', percent: 100 });
        animationEvent('setup:done', { mode: state.info.mode });
        renderDone(result);
        // 进度条走满再翻页
        setTimeout(() => goTo('finish'), 450);
    }

    function renderDone(result) {
        const info = state.info;
        const uninstall = info.mode === 'uninstall';
        if (uninstall) {
            $('done-title').textContent = t('done.uTitle');
            $('done-text').textContent = result.leftover ? t('done.uKept', { dir: result.leftover }) : t('done.uText');
        } else {
            $('done-title').textContent = info.upgrade ? t('done.titleUpgrade', { version: info.version }) : t('done.title');
            $('done-text').textContent = t('done.text', { dir: result.dir || state.dir || '' });
        }
        $('open-folder-btn').hidden = uninstall && !result.leftover;
        $('open-log-btn').hidden = !host;
        $('launch-row').hidden = uninstall;
    }

    // ---------------- 关窗口 ----------------

    function requestClose() {
        if (state.busy) return;
        if (state.finished || !state.info) return send('close');
        const uninstall = state.info.mode === 'uninstall';
        $('confirm-close-title').textContent = t(uninstall ? 'close.titleUninstall' : 'close.titleInstall');
        $('confirm-close-text').textContent = t(uninstall ? 'close.textUninstall' : 'close.textInstall');
        $('confirm-close').hidden = false;
        $('confirm-close-stay').focus();
    }

    function hideCloseConfirm() {
        $('confirm-close').hidden = true;
    }

    // ---------------- 演示模式（浏览器里预览，没有外壳） ----------------

    const Demo = {
        timer: 0,
        info() {
            const uninstall = params.get('mode') === 'uninstall';
            const upgrade = params.has('upgrade');
            return {
                type: 'init',
                mode: uninstall ? 'uninstall' : 'install',
                lang: normalizeLang(params.get('lang')) || normalizeLang(navigator.language) || 'zh-CN',
                version: '1.1.0',
                installedVersion: upgrade || uninstall ? '1.0.4' : null,
                upgrade,
                newer: false,
                dir: 'C:\\Program Files (x86)\\Ganlan Translate',
                dirLocked: upgrade,
                running: params.has('running'),
                desktop: true,
                startMenu: true,
                requiredBytes: 837400000,
                freeBytes: 182.4 * 1024 ** 3,
                existing: upgrade,
                sandbox: params.has('sandbox'),
                keep: ['presets.json', 'plugins', 'models'],
            };
        },
        receive(message) {
            switch (message.type) {
                case 'ready': return setTimeout(() => onHostMessage(this.info()), 0);
                case 'browse': return setTimeout(() => onHostMessage({ type: 'dir', dir: 'D:\\Games\\Ganlan Translate', dirError: null, requiredBytes: 837400000, freeBytes: 512 * 1024 ** 3, existing: false }), 200);
                case 'checkDir': {
                    const dir = (message.dir || '').trim();
                    const error = !dir ? 'empty' : /^[a-z]:\\?$/i.test(dir) ? 'root' : /^[a-z]:\\/i.test(dir) ? null : 'relative';
                    return onHostMessage({ type: 'dir', dir: message.dir, dirError: error, requiredBytes: 837400000, freeBytes: error ? -1 : 182.4 * 1024 ** 3, existing: false });
                }
                case 'install':
                case 'uninstall':
                    return this.run(message.type === 'uninstall');
                default:
                    return console.log('[演示] 发给外壳：', message);
            }
        },
        run(uninstall) {
            clearInterval(this.timer);
            const total = 3417;
            const fail = params.has('fail');
            const sample = ['GanlanTranslate.exe', 'GanlanTranslateVR.exe', '_internal\\python313.dll', '_internal\\PySide6\\Qt6WebEngineCore.dll',
                '_internal\\ui\\static\\css\\style.css', '_internal\\onnxruntime\\capi\\onnxruntime.dll', '_internal\\terminology\\ja.csv'];
            let percent = 0;
            let done = 0;
            onHostMessage({ type: 'log', lines: [uninstall ? '卸载 甘岚翻译：C:\\Program Files (x86)\\Ganlan Translate' : '甘岚翻译 1.1.0 安装程序', '安装位置：C:\\Program Files (x86)\\Ganlan Translate', '校验安装包…'] });
            this.timer = setInterval(() => {
                percent += uninstall ? 2.2 : 0.9;
                const phase = percent < 4 ? 'verify' : percent < 6 ? 'close' : percent < 90 ? (uninstall ? 'remove' : 'extract') : 'finish';
                if (phase === 'extract' || phase === 'remove') {
                    done = Math.min(total, Math.round((percent - 6) / 84 * total));
                    const lines = [];
                    for (let i = 0; i < 6; i++) lines.push((uninstall ? '删除 ' : '解压 ') + sample[(done + i) % sample.length]);
                    onHostMessage({ type: 'log', lines });
                }
                if (fail && percent > 55) {
                    clearInterval(this.timer);
                    onHostMessage({ type: 'log', lines: ['没有完成：文件被占用，没法写入：C:\\Program Files (x86)\\Ganlan Translate\\_internal\\python313.dll'] });
                    return onHostMessage({ type: 'done', ok: false, error: '文件被占用，没法写入：C:\\Program Files (x86)\\Ganlan Translate\\_internal\\python313.dll\n请关掉甘岚翻译（包括托盘里的）后点「重试」。' });
                }
                if (percent >= 100) {
                    clearInterval(this.timer);
                    onHostMessage({ type: 'log', lines: [uninstall ? '卸载完成' : '安装完成，用时 18 秒'] });
                    return onHostMessage({ type: 'done', ok: true, dir: 'C:\\Program Files (x86)\\Ganlan Translate', leftover: uninstall ? 'C:\\Program Files (x86)\\Ganlan Translate' : null });
                }
                onHostMessage({ type: 'progress', phase, percent, done, total });
            }, 60);
        },
    };

    // ---------------- 启动 ----------------

    function bind() {
        $('next-btn').addEventListener('click', onNext);
        $('back-btn').addEventListener('click', onBack);
        $('license-agree').addEventListener('change', updateFooter);
        $('browse-btn').addEventListener('click', () => send('browse', { dir: $('install-dir').value.trim() }));
        let dirTimer = 0;
        $('install-dir').addEventListener('input', () => {
            updateFooter();
            clearTimeout(dirTimer);
            dirTimer = setTimeout(() => send('checkDir', { dir: $('install-dir').value }), 250);
        });
        $('install-dir').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') onNext();
        });
        $('open-folder-btn').addEventListener('click', () => send('openFolder', { dir: (state.result && (state.result.leftover || state.result.dir)) || state.dir }));
        $('open-log-btn').addEventListener('click', () => send('openLog'));
        $('confirm-close-stay').addEventListener('click', hideCloseConfirm);
        $('confirm-close-quit').addEventListener('click', () => {
            hideCloseConfirm();
            send('close');
        });
        document.querySelectorAll('.setup-win-btn').forEach((el) => {
            el.addEventListener('click', () => {
                if (el.dataset.action === 'minimize') send('minimize');
                else requestClose();
            });
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !$('confirm-close').hidden) hideCloseConfirm();
        });
        // 运行库不支持 app-region 时：按下标题栏空白处让外壳拖动窗口
        document.querySelector('.setup-titlebar').addEventListener('mousedown', (e) => {
            if (!host || !state.info || state.info.nativeDrag || e.button !== 0) return;
            if (e.target.closest('button')) return;
            send('drag');
        });
        if (host) host.addEventListener('message', (e) => onHostMessage(e.data));
    }

    function init() {
        document.body.classList.toggle('preview', !host);
        bind();
        setLang(normalizeLang(params.get('lang')) || normalizeLang(navigator.language) || 'zh-CN');
        send('ready');
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
