using System.Collections.Generic;
using System.Globalization;

namespace GanlanSetup
{
    /// <summary>
    /// 外壳这边的文字：安装日志、出错原因、提示框。网页界面上的文字在 ui/setup.js 里。
    /// 每条按 中文 / English / 日本語 / 한국어 的顺序。
    /// </summary>
    static class Strings
    {
        public static readonly string[] Languages = { "zh-CN", "en", "ja", "ko" };
        static int _index;

        public static string Lang
        {
            get => Languages[_index];
            set
            {
                int i = System.Array.IndexOf(Languages, Normalize(value));
                _index = i < 0 ? 1 : i;
            }
        }

        public static string Normalize(string lang)
        {
            if (string.IsNullOrEmpty(lang)) return null;
            string l = lang.ToLowerInvariant();
            if (l.StartsWith("zh")) return "zh-CN";
            if (l.StartsWith("ja")) return "ja";
            if (l.StartsWith("ko")) return "ko";
            if (l.StartsWith("en")) return "en";
            return null;
        }

        /// <summary>Windows 显示语言（和主程序 ui_locale.system_ui_language 一样：不是中日韩就用英语）。</summary>
        public static string SystemLanguage()
        {
            switch (Native.GetUserDefaultUILanguage() & 0x3FF)
            {
                case 0x04: return "zh-CN";
                case 0x11: return "ja";
                case 0x12: return "ko";
                default: return "en";
            }
        }

        public static string T(string key, params object[] args)
        {
            string text = Table.TryGetValue(key, out string[] row) ? row[_index] : key;
            return args.Length == 0 ? text : string.Format(CultureInfo.InvariantCulture, text, args);
        }

        public static string AppName => T("app.name");

        static readonly Dictionary<string, string[]> Table = new Dictionary<string, string[]>
        {
            ["app.name"] = new[] { "甘岚翻译", "Ganlan Translate", "甘嵐翻訳", "간란 번역" },
            ["title.setup"] = new[] { "{0} 安装程序", "{0} Setup", "{0} セットアップ", "{0} 설치" },
            ["title.uninstall"] = new[] { "卸载 {0}", "Uninstall {0}", "{0} のアンインストール", "{0} 제거" },
            ["shortcut.uninstall"] = new[] { "卸载", "Uninstall", "アンインストール", "제거" },

            // ---- 提示框 ----
            ["dlg.needAdmin"] = new[]
            {
                "需要管理员权限才能继续（要写入 Program Files 和注册表）。",
                "Administrator permission is needed to continue (Program Files and the registry are written).",
                "続行するには管理者権限が必要です（Program Files とレジストリに書き込みます）。",
                "계속하려면 관리자 권한이 필요합니다(Program Files와 레지스트리에 씁니다).",
            },
            ["dlg.noWebView2"] = new[]
            {
                "这台电脑没有 Microsoft Edge WebView2 运行库，安装界面显示不出来。\n\n「是」：打开微软的下载页，装好后再运行安装程序。\n「否」：不显示界面，用默认设置直接安装。\n「取消」：退出。",
                "Microsoft Edge WebView2 Runtime is not installed, so the setup window cannot be shown.\n\nYes: open Microsoft's download page; run setup again afterwards.\nNo: install now with the default settings, without the window.\nCancel: quit.",
                "Microsoft Edge WebView2 ランタイムが入っていないため、セットアップ画面を表示できません。\n\nはい：Microsoft のダウンロードページを開きます。入れてからもう一度実行してください。\nいいえ：画面を出さずに既定の設定でインストールします。\nキャンセル：終了します。",
                "Microsoft Edge WebView2 런타임이 없어 설치 화면을 표시할 수 없습니다.\n\n예: Microsoft 다운로드 페이지를 엽니다. 설치 후 다시 실행하세요.\n아니요: 화면 없이 기본 설정으로 바로 설치합니다.\n취소: 종료합니다.",
            },
            ["dlg.noWebView2Uninstall"] = new[]
            {
                "这台电脑没有 Microsoft Edge WebView2 运行库，卸载界面显示不出来。\n\n要直接卸载吗？你的预设、插件、自定义词语和下载的模型会保留。",
                "Microsoft Edge WebView2 Runtime is not installed, so the uninstall window cannot be shown.\n\nUninstall now? Your presets, plugins, custom words and downloaded models will be kept.",
                "Microsoft Edge WebView2 ランタイムが入っていないため、アンインストール画面を表示できません。\n\nこのままアンインストールしますか？プリセット・プラグイン・用語・ダウンロードしたモデルは残ります。",
                "Microsoft Edge WebView2 런타임이 없어 제거 화면을 표시할 수 없습니다.\n\n바로 제거할까요? 프리셋, 플러그인, 사용자 단어, 다운로드한 모델은 남겨 둡니다.",
            },
            ["dlg.installed"] = new[] { "{0} 已安装到：\n{1}", "{0} has been installed to:\n{1}", "{0} をインストールしました：\n{1}", "{0}을(를) 설치했습니다:\n{1}" },
            ["dlg.uninstalled"] = new[] { "{0} 已卸载。", "{0} has been uninstalled.", "{0} をアンインストールしました。", "{0}을(를) 제거했습니다." },
            ["dlg.failed"] = new[] { "没有完成：{0}\n\n日志：{1}", "Not completed: {0}\n\nLog: {1}", "完了しませんでした：{0}\n\nログ：{1}", "완료하지 못했습니다: {0}\n\n로그: {1}" },
            ["dlg.notInstalled"] = new[]
            {
                "没有找到已安装的 {0}。", "No installed {0} was found.", "インストールされた {0} が見つかりません。", "설치된 {0}을(를) 찾지 못했습니다.",
            },

            // ---- 出错原因 ----
            ["err.corrupt"] = new[]
            {
                "安装包不完整或已损坏，请重新下载。",
                "The installer is incomplete or damaged. Please download it again.",
                "インストーラーが不完全か壊れています。もう一度ダウンロードしてください。",
                "설치 파일이 불완전하거나 손상되었습니다. 다시 다운로드하세요.",
            },
            ["err.inUse"] = new[]
            {
                "文件被占用，没法写入：{0}\n请关掉甘岚翻译（包括托盘里的）后点「重试」。",
                "This file is in use and cannot be written: {0}\nClose Ganlan Translate (including the tray icon) and click Retry.",
                "ファイルが使用中で書き込めません：{0}\n甘嵐翻訳（トレイのものも）を閉じてから「再試行」を押してください。",
                "파일이 사용 중이라 쓸 수 없습니다: {0}\n간란 번역(트레이 포함)을 닫고 '다시 시도'를 누르세요.",
            },
            ["err.inUseBy"] = new[]
            {
                "文件被「{1}」占用，没法写入：{0}\n请关掉它后点「重试」（游戏的反作弊扫描过甘岚翻译后会一直占着，要退出游戏）。",
                "This file is held by \"{1}\" and cannot be written: {0}\nClose it and click Retry (a game's anti-cheat may keep it open after scanning Ganlan Translate until you quit the game).",
                "ファイルが「{1}」に使われていて書き込めません：{0}\nそれを閉じてから「再試行」を押してください（ゲームのチート対策が甘嵐翻訳をスキャンすると、ゲームを終了するまで開いたままになることがあります）。",
                "파일을 '{1}'이(가) 사용 중이라 쓸 수 없습니다: {0}\n그 프로그램을 닫고 '다시 시도'를 누르세요(게임의 안티치트가 간란 번역을 검사한 뒤 게임을 끌 때까지 파일을 열어 둘 수 있습니다).",
            },
            ["err.access"] = new[] { "没有权限写入：{0}", "No permission to write: {0}", "書き込む権限がありません：{0}", "쓸 권한이 없습니다: {0}" },
            ["err.diskFull"] = new[] { "磁盘空间不够了。", "The disk is full.", "ディスクの空き容量が足りません。", "디스크 공간이 부족합니다." },
            ["err.badDir"] = new[] { "安装位置不能用：{0}", "This location cannot be used: {0}", "このインストール先は使えません：{0}", "이 위치는 사용할 수 없습니다: {0}" },

            // ---- 安装日志 ----
            ["log.start"] = new[] { "{0} {1} 安装程序", "{0} {1} setup", "{0} {1} セットアップ", "{0} {1} 설치" },
            ["log.target"] = new[] { "安装位置：{0}", "Install location: {0}", "インストール先：{0}", "설치 위치: {0}" },
            ["log.upgrade"] = new[] { "已安装 {0}，覆盖更新，保留设置和数据", "{0} is installed; updating it and keeping your settings and data", "{0} がインストール済み。設定とデータを残して更新します", "{0}이(가) 설치되어 있어 설정과 데이터를 유지한 채 업데이트합니다" },
            ["log.verify"] = new[] { "校验安装包…", "Checking the installer…", "インストーラーを検証しています…", "설치 파일 확인 중…" },
            ["log.verifyOk"] = new[] { "安装包完好：{0} 个文件，共 {1}", "Installer OK: {0} files, {1} in total", "インストーラーは正常です：{0} 個のファイル、合計 {1}", "설치 파일 정상: 파일 {0}개, 총 {1}" },
            ["log.closing"] = new[] { "关闭正在运行的甘岚翻译…", "Closing the running Ganlan Translate…", "起動中の甘嵐翻訳を終了しています…", "실행 중인 간란 번역을 닫는 중…" },
            ["log.askedQuit"] = new[] { "已请甘岚翻译自己退出（让设置保存完）", "Asked Ganlan Translate to quit by itself (so settings get saved)", "甘嵐翻訳に終了を依頼しました（設定を保存させるため）", "간란 번역에 스스로 종료하도록 요청했습니다(설정 저장을 위해)" },
            ["log.exited"] = new[] { "甘岚翻译已退出", "Ganlan Translate has exited", "甘嵐翻訳が終了しました", "간란 번역이 종료되었습니다" },
            ["log.killed"] = new[] { "强制结束 {0}", "Force-closed {0}", "{0} を強制終了しました", "{0}을(를) 강제로 종료했습니다" },
            ["log.extracting"] = new[] { "解压文件到 {0}", "Extracting files to {0}", "{0} にファイルを展開しています", "{0}에 파일을 푸는 중" },
            ["log.file"] = new[] { "解压 {0}", "Extract {0}", "展開 {0}", "풀기 {0}" },
            ["log.extracted"] = new[] { "解压完成：{0} 个文件，用时 {1:0.0} 秒", "Extracted {0} files in {1:0.0} s", "展開完了：{0} 個のファイル、{1:0.0} 秒", "풀기 완료: 파일 {0}개, {1:0.0}초" },
            ["log.retry"] = new[] { "{0} 被占用，稍后重试", "{0} is in use, retrying", "{0} が使用中のため再試行します", "{0}이(가) 사용 중이라 다시 시도합니다" },
            ["log.lockedSame"] = new[] { "{0} 被「{1}」占用，但和新版一模一样，不用换", "{0} is held by \"{1}\" but is identical to the new version, so it stays", "{0} は「{1}」に使われていますが、新しい版と同じなのでそのままにします", "{0}은(는) '{1}'이(가) 사용 중이지만 새 버전과 같아서 그대로 둡니다" },
            ["log.lockedMoved"] = new[] { "{0} 被「{1}」占用，旧文件已改名挪开（重启后删除），换上了新版", "{0} is held by \"{1}\"; the old file was renamed (deleted after a restart) and the new one put in place", "{0} は「{1}」に使われているため、古いファイルの名前を変えて（再起動後に削除）新しいものに置き換えました", "{0}을(를) '{1}'이(가) 사용 중이라 이전 파일 이름을 바꾸고(재시작 후 삭제) 새 파일로 바꿨습니다" },
            ["log.stale"] = new[] { "删除旧版留下的文件 {0}", "Remove leftover file from the old version: {0}", "旧バージョンの不要なファイルを削除：{0}", "이전 버전의 남은 파일 삭제: {0}" },
            ["log.staleCount"] = new[] { "删除了 {0} 个旧版留下的文件", "Removed {0} leftover files from the old version", "旧バージョンの不要なファイルを {0} 個削除しました", "이전 버전의 남은 파일 {0}개를 삭제했습니다" },
            ["log.migrate"] = new[] { "从旧版目录搬数据：{0}", "Moving data from the old install: {0}", "旧バージョンのフォルダーからデータを移動：{0}", "이전 설치 폴더에서 데이터 이동: {0}" },
            ["log.migrateRemoved"] = new[] { "删除旧版目录 {0}", "Removed the old install folder {0}", "旧バージョンのフォルダー {0} を削除しました", "이전 설치 폴더 {0}을(를) 삭제했습니다" },
            ["log.plugins"] = new[] { "建插件文件夹 plugins", "Create the plugins folder", "plugins フォルダーを作成", "plugins 폴더 만들기" },
            ["log.acl"] = new[] { "给 Users 组修改权限（程序要在安装目录里保存设置）", "Give the Users group modify permission (the app saves settings in its folder)", "Users グループに変更権限を付与（設定をインストール先に保存するため）", "Users 그룹에 수정 권한 부여(설정을 설치 폴더에 저장하기 때문)" },
            ["log.aclFailed"] = new[] { "改权限没有成功（{0}），不用管理员身份运行时可能存不了设置", "Could not change permissions ({0}); settings may not save without admin rights", "権限を変更できませんでした（{0}）。管理者でないと設定を保存できない場合があります", "권한을 바꾸지 못했습니다({0}). 관리자 권한 없이는 설정이 저장되지 않을 수 있습니다" },
            ["log.uninstaller"] = new[] { "写入卸载程序 Uninstall.exe", "Write the uninstaller Uninstall.exe", "アンインストーラー Uninstall.exe を作成", "제거 프로그램 Uninstall.exe 쓰기" },
            ["log.registry"] = new[] { "写入注册表（「应用和功能」里的卸载项）", "Write registry entries (the Apps & features entry)", "レジストリに登録（「アプリと機能」の項目）", "레지스트리 기록('앱 및 기능' 항목)" },
            ["log.shortcut"] = new[] { "创建快捷方式 {0}", "Create shortcut {0}", "ショートカットを作成 {0}", "바로 가기 만들기 {0}" },
            ["log.shortcutRemoved"] = new[] { "删除快捷方式 {0}", "Remove shortcut {0}", "ショートカットを削除 {0}", "바로 가기 삭제 {0}" },
            ["log.done"] = new[] { "安装完成，用时 {0:0} 秒", "Installed in {0:0} s", "インストール完了（{0:0} 秒）", "설치 완료({0:0}초)" },
            ["log.failed"] = new[] { "没有完成：{0}", "Not completed: {0}", "完了しませんでした：{0}", "완료하지 못했습니다: {0}" },
            ["log.launch"] = new[] { "启动甘岚翻译", "Launch Ganlan Translate", "甘嵐翻訳を起動", "간란 번역 실행" },
            ["log.sandbox"] = new[] { "沙盒模式：注册表和快捷方式写在 {0}", "Sandbox mode: registry and shortcuts go to {0}", "サンドボックス：レジストリとショートカットは {0} に書きます", "샌드박스 모드: 레지스트리와 바로 가기를 {0}에 씁니다" },

            // ---- 卸载日志 ----
            ["log.uStart"] = new[] { "卸载 {0}：{1}", "Uninstall {0}: {1}", "{0} をアンインストール：{1}", "{0} 제거: {1}" },
            ["log.uFiles"] = new[] { "删除程序文件…", "Removing program files…", "プログラムファイルを削除しています…", "프로그램 파일 삭제 중…" },
            ["log.uAll"] = new[] { "删除整个安装目录（包括你的设置、插件和模型）…", "Removing the whole install folder (including your settings, plugins and models)…", "インストール先を丸ごと削除しています（設定・プラグイン・モデルを含む）…", "설치 폴더 전체 삭제 중(설정, 플러그인, 모델 포함)…" },
            ["log.uRemoved"] = new[] { "删除了 {0} 个文件", "Removed {0} files", "{0} 個のファイルを削除しました", "파일 {0}개를 삭제했습니다" },
            ["log.uKept"] = new[] { "保留：{0}", "Kept: {0}", "残しました：{0}", "남김: {0}" },
            ["log.uRegistry"] = new[] { "删除注册表项", "Remove registry entries", "レジストリの項目を削除", "레지스트리 항목 삭제" },
            ["log.uDone"] = new[] { "卸载完成", "Uninstalled", "アンインストール完了", "제거 완료" },
            ["log.uLocked"] = new[] { "有文件删不掉，重启后会自动删除：{0}", "Some files are locked and will be removed after a restart: {0}", "削除できないファイルは再起動後に削除されます：{0}", "삭제하지 못한 파일은 다시 시작한 뒤 삭제됩니다: {0}" },
        };
    }
}
