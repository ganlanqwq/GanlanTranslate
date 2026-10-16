<div align="center">

# Ganlan Translate · 甘岚翻译

**Real-time speech translation, built for VRChat**

An AI-powered, highly customizable, high-performance, all-in-one open-source translator.
What you say is translated and sent to the VRChat chatbox in real time. What others say, and the text on your screen, can be translated for you too.

[简体中文](README.md) · **English** · [日本語](README.ja.md)

[Download the latest version](https://github.com/ganlanqwq/GanlanTranslate/releases/latest) · [MIT License](LICENSE.md)

<img src="images/screenshot.jpg" alt="Ganlan Translate main window" width="860">

</div>

## What it does

Ganlan Translate has three translation modes. Run any one of them, or all at once:

| Mode | Listens to / looks at | Where the translation goes |
|---|---|---|
| **Self translation** | Your microphone | The VRChat chatbox (OSC) and the self overlay |
| **Translate others** | Other players' voices in VRChat | The others overlay, with speakers labeled A, B, C… |
| **Translate screen** | Text in the VRChat window (one capture per hotkey press) | The screen overlay, with each translation under its original text |

Also included:

- **VR wrist subtitles**: read translations with your headset on. Raise your wrist in SteamVR to see them, and switch modes or languages from the SteamVR dashboard.
- **Words**: add your own recognition hot words (friends' names, world names, community slang) and decide how specific words should be translated.
- **Four UI languages**: Simplified Chinese, English, Japanese and Korean. Follows your Windows display language by default.

## Features

### Self translation

- **Translates while you speak**: streaming LLM translation lets others see the meaning before you finish the sentence. This uses more tokens and can be turned off.
- **Choice of speech recognition**: Qwen-Audio-3 on Alibaba Cloud Model Studio by default. You can also choose Qwen3 ASR, Fun-ASR, Soniox or Doubao, or use any OpenAI-compatible speech endpoint (a multimodal LLM that can listen, or a Whisper-compatible transcription API).
- **Translation by LLM**: any OpenAI-compatible endpoint works, for example Model Studio's compatible mode, DeepSeek or OpenRouter. You can set the formality (chatting with friends / first meeting / very polite) and sentence style.
- **Two languages at once**: set a second output language to show two translations in the chatbox. You can also set a fallback language that is used when you are already speaking the target language.
- **Follows your VRChat mic**: nothing is recognized (and nothing is billed) while you are muted in VRChat; recognition starts when you unmute. Toggling the mic twice within 0.8 seconds clears the chatbox (this can be turned off in Advanced).
- **Understands context**: recent sentences are included. With VRCX it also includes the current world and the player names in the instance, so names are less likely to be mistranslated.
- **Chatbox details**: automatic length trimming, typing indicator, Arabic / Hebrew reshaping, furigana for Japanese, pinyin for Chinese, Unicode fancy fonts and more.

### Translate others

- Records only the VRChat process, so your music, voice chat apps and system sounds are left out.
- Tells speakers apart by voice and labels them A–Z in the overlay.
- Detects the speaker's language automatically (about 176 languages), shows it next to the original text, then translates into the language you choose.
- The translation language for others is set separately from self translation, with quick-switch buttons on the overlay. Others are still translated even when self translation is set to "Recognition only".

### Translate screen

- Press the global hotkey (default `Ctrl+Alt+F8`) or the camera button on the screen overlay to capture the VRChat window once. It works even if VRChat is covered by other windows (but not when it is minimized).
- A vision model reads the text in the capture (Qwen OCR `qwen-vl-ocr` by default, or any OpenAI-compatible model that can see images). Each block is labeled with its language and translated.
- Handy for signs, menus and instructions inside worlds.

### VR wrist subtitles (SteamVR)

- In the main window, set **Display** to **VR** and click **Launch** to start the VR companion. After that it starts automatically every time you open SteamVR.
- One subtitle card on each wrist, shown only when you raise your wrist. Under **Position & angle** in the SteamVR dashboard you can move it along three axes, rotate it freely in any direction (a full turn), and change its size, opacity and text size. Both hands mirror each other.
- To look back through history, use the page buttons of **Others** or **Screen** on the Ganlan Translate page of the SteamVR dashboard; the wrist card follows. The wrist cards themselves can't be clicked, so turning your wrist to read them won't put VRChat into AFK.
- Ganlan Translate has its own page in the SteamVR dashboard: start and stop the three modes, change each mode's translation language, and choose what the wrists show.

### Words

- **Recognition hot words**: one list each for Chinese, English and Japanese, one word per line, shared by self translation and translate others.
- **Translation terms**: tell the model how to translate a keyword when it appears. Saved separately for each target language and used from the next sentence.
- The built-in lists are shown read-only. Your own entries are saved separately and survive upgrades.

### More

- **Presets**: save a whole set of settings as a preset, switch between presets at any time, or export them to a file to share with friends. Presets never contain API keys.
- **Overlays**: the self, others and screen overlays open separately and can float on top of other windows.
- **Tray**: closing the main window only minimizes it to the tray. Opening the program again brings back the existing window instead of starting a second copy.

## Download and install

1. Download `GanlanTranslate-Setup-<version>.exe` from [Releases](https://github.com/ganlanqwq/GanlanTranslate/releases/latest).
2. Run the installer. The default location is `C:\Program Files (x86)\Ganlan Translate`.
3. If Windows SmartScreen blocks the first run, click "More info" → "Run anyway".

Requirements: Windows 10 / 11 (64-bit). VR wrist subtitles need SteamVR.

When you install over a beta version (program name `GanLan.exe`), your presets, custom hot words and terms, VR companion settings and downloaded models are moved to the new folder automatically, and the old folder is removed.

## Quick start

1. **Get your keys**
   - An Alibaba Cloud Model Studio (DashScope) key: used by the default speech recognition and screen text recognition. Create one in the [Model Studio console](https://modelstudio.console.aliyun.com/ap-southeast-1?tab=doc#/api-key); new accounts usually get a free quota. For keys from the international site, tick **International**.
   - A key for the translation LLM: any OpenAI-compatible endpoint works. For Model Studio (international), use the base URL `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` and a model such as `qwen-plus`.
2. **Enter them in the app**: open Ganlan Translate and fill in the keys, the LLM base URL and the model name on the **Core** page. Settings are saved automatically.
3. **Turn on OSC in VRChat**: Action Menu → `Options` → `OSC` → `Enable`.
4. **Set your languages**: set the target language on the **Self translation** page (for example `ja` or `en`), and the language you want to read on the **Translate others** and **Translate screen** pages (for example `en`).
5. **Start**: start the modes you need from the service card in the top right, or click **Start all**. Click **Overlay** on the right of each row when you want to see the translations.

Stop the service when you are not using it, so it doesn't keep using your recognition quota.

## Data and privacy

- API keys are stored only in the program on your PC. They are never written into preset files and never sent anywhere except to the matching service provider.
- Your voice, screenshots and text are only sent to the recognition / translation / image recognition services you configure (plus Google Translate when **Reverse Translation** is enabled).
- Presets, hot words, terms and VR settings are stored in the install folder and are kept when you upgrade.

## FAQ

<details>
<summary><b>Self translation doesn't respond</b></summary>

- Self translation only listens while VRChat is running and your mic is on. Check that VRChat is running, OSC is enabled, and you are not muted in the game.
- Check that the right microphone is selected on the **Self translation** page.
- Look at **Run Logs** at the bottom of the **Core** page. Errors are shown there.

</details>

<details>
<summary><b>Nothing appears in the VRChat chatbox</b></summary>

- Make sure `Options → OSC` is enabled in VRChat. The default port is UDP 9000.
- Check that no other program is using port 9000. If you use a game accelerator / VPN, don't use its "process mode".
- **Compatibility mode** in **Advanced** is for other games such as Resonite. Leave it off for VRChat.

</details>

<details>
<summary><b>401 / "api-key is invalid" in the logs</b></summary>

The key and the **International** checkbox don't match: leave **International** unticked for keys from the China site, and tick it for keys from the international site.

</details>

<details>
<summary><b>No subtitles for others</b></summary>

- Start VRChat first. Ganlan Translate only records the VRChat process.
- Two people with very similar voices may get the same letter. This is a limit of telling speakers apart from mixed audio.

</details>

<details>
<summary><b>Screen capture fails / the hotkey does nothing</b></summary>

- The capture doesn't work while VRChat is minimized or in exclusive fullscreen. Use windowed or borderless mode.
- Another program may be using the hotkey. Choose a different one on the **Translate screen** page, then restart the screen mode. Without a hotkey you can still use the camera button on the overlay.

</details>

<details>
<summary><b>No subtitles on my wrist in VR</b></summary>

- **Display** in the main window must be set to VR, and the top right must show "VR connected".
- By default the wrist cards only appear when you raise your wrist: palm down, then look at the back of your hand.
- On the Ganlan Translate page of the SteamVR dashboard, make sure **Wrist** isn't set to **Off**.

</details>

## Run from source

You need Python 3.13 (the version used for development and packaging) and Git (one dependency is installed from GitHub).

```bash
git clone https://github.com/ganlanqwq/GanlanTranslate.git
cd GanlanTranslate
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# Desktop window + tray (same as the installed version)
.venv\Scripts\python run_ui.py

# Or only the web console, then open http://127.0.0.1:5001 in a browser
.venv\Scripts\python ui\app.py
```

Don't run the installed version and the source version at the same time. They fight over the same port and microphone.

### Build the installer

You need [NSIS](https://nsis.sourceforge.io/).

```bash
.venv\Scripts\python -m PyInstaller GanlanTranslate.spec --noconfirm --distpath build_installer\dist --workpath build_installer\work
"C:\Program Files (x86)\NSIS\makensis.exe" installer.nsi
```

The installer is written to `build_installer\GanlanTranslate-Setup-<version>.exe`. The version number lives in both `installer.nsi` and `ui/app.py`; change them together.

### Project layout

| Path | Contents |
|---|---|
| `ui/` | Web console (Flask backend, pages, styles, scripts) |
| `qt_ui/`, `run_ui.py`, `tray_app.py` | Desktop window and tray |
| `main.py`, `recognition_handler.py` | Self translation |
| `others_pipeline.py`, `speaker_id.py` | Translate others, speaker labels |
| `screen_translation.py` | Translate screen |
| `vr_overlay/`, `vr_bridge.py` | SteamVR wrist subtitles and dashboard page |
| `speech_recognizers/`, `streaming_translation/` | Speech recognition and translation providers |
| `hot_words/`, `terminology/` | Built-in hot words and terms |
| `tailwind_build/` | Tailwind setup for rebuilding the UI styles |

## Acknowledgements

The self-translation flow is based on [Yakutan](https://github.com/febilly/Yakutan) by febilly. Thanks for the ideas and the code.

Open-source projects used or referenced:

- Alibaba Cloud Fun-ASR sample code
- [LiveCaptions-Translator](https://github.com/SakiRinn/LiveCaptions-Translator) (Google Translate API)
- [PuriPuly-heart](https://github.com/kapitalismho/PuriPuly-heart) (some prompts)
- [LiveTranslate](https://github.com/TheDeathDragon/LiveTranslate), [SenseVoice-python](https://github.com/lovemefan/SenseVoice-python), [SenseVoice](https://github.com/FunAudioLLM/SenseVoice), [Qwen3-ASR-GGUF](https://github.com/HaujetZhao/Qwen3-ASR-GGUF), [llama.cpp](https://github.com/ggml-org/llama.cpp) (local recognition)
- [Silero VAD](https://github.com/snakers4/silero-vad) (voice activity detection)
- [vrchat_oscquery](https://github.com/theepicsnail/vrchat_oscquery), [pyopenvr](https://github.com/cmbruns/pyopenvr), [PySide6](https://doc.qt.io/qtforpython-6/), [Flask](https://flask.palletsprojects.com/), [fast-langdetect](https://github.com/LlmKira/fast-langdetect)

Special thanks (in no particular order): nino, wood, 苌梦, febilly. Their stories are on the **Info → Contributors** page in the app.

## Feedback

Questions and suggestions are welcome in [Issues](https://github.com/ganlanqwq/GanlanTranslate/issues), or reach me through the contact on the **Info → Contributors** page in the app.

## License

[MIT](LICENSE.md)
