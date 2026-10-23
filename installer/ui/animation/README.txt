安装程序里的动画（安装、卸载那一步中间那块）。

- install.gif：安装时播（甘岚跳舞）
- uninstall.gif：卸载时播（甘岚敲头）

换动画：直接用同名文件替换这两个。想换成别的格式（APNG、WebP、视频、Lottie），改 installer/ui/setup.html 里
id="install-animation" 那两行，注释里有写法。

预览：.venv\Scripts\python.exe installer\preview.py，浏览器打开
  http://127.0.0.1:5098/?step=install（安装）
  http://127.0.0.1:5098/?step=install&mode=uninstall（卸载）
打包：.venv\Scripts\python.exe installer\build.py（这个文件夹会整个打进安装程序）

动画区高 150px、宽度占满，动画按比例缩放居中；高度在 installer/ui/setup.css 里改 --setup-animation-height。
