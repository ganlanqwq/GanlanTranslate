"""示例插件：聊天框前后缀。

把这个文件夹里的文件（plugin.json、main.py、icon.png）一起压成 zip，拖进甘岚翻译的「插件」页即可导入。
可用的函数和 ctx 见 docs/插件开发.md。
"""
import os
import time

ctx = None


def setup(context):
    global ctx
    ctx = context
    ctx.log('已加载，当前设置', ctx.settings)


def teardown():
    if ctx is not None:
        ctx.log('已停止')


def on_settings_changed(settings):
    ctx.log('设置已更新')


def decorate(text):
    """前后缀和正文之间不留空格；都没填时不改。"""
    settings = ctx.settings
    limit = int(settings['max_length'])
    prefix = settings['prefix'].strip()[:limit]
    suffix = settings['suffix'].strip()[:limit]
    if not prefix and not suffix:
        return None
    return f'{prefix}{text.strip()}{suffix}'


def filter_source(text):
    # 整句识别出来、还没翻译时就加上：翻译的时候前后缀会跟着一起翻译
    return decorate(text)


def filter_chatbox(text, ongoing):
    # 说话中的半句不翻译，只在发到聊天框时加；整句在 filter_source 里已经加过了
    if ongoing and ctx.settings['partial']:
        return decorate(text)
    return None


def on_self_result(event):
    if not ctx.settings['save_log']:
        return
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{event['source']}\t{event['translation']}\n"
    with open(os.path.join(ctx.data_dir, 'log.txt'), 'a', encoding='utf-8') as f:
        f.write(line)
