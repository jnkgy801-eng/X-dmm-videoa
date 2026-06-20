"""
🔑 X（Twitter）ブラウザ自動投稿のための、初回ログイン・セッション作成スクリプト。

dmm_x_post_generator.py を POST_METHOD=browser で動かす前に、
一度だけこのスクリプトを「自分のPC上」で実行してください。

やること:
1. ブラウザが起動するので、画面上で手動でXにログインする（2段階認証含む）
2. ログインが完了したら、ターミナルに戻って Enter を押す
3. ログイン状態（セッション情報）が x_session.json に保存される

⚠️ 重要な注意:
- x_session.json はログイン状態そのものです。パスワードと同様に厳重に扱ってください。
  - 絶対に Git にコミットしない（.gitignore に追加推奨）
  - 他人に渡さない・公開リポジトリに置かない
- セッションには有効期限があります。投稿が失敗するようになったら、
  このスクリプトを再実行してセッションを作り直してください。
- GitHub Actionsなど自分のPC以外で動かす場合は、このファイルの中身をBase64化して
  X_SESSION_STATE_B64 という名前のSecretとして登録してください。
    例: base64 -i x_session.json | tr -d '\\n'
"""

import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('❌ playwright がインストールされていません。')
    print('   pip install playwright && playwright install chromium')
    sys.exit(1)

SESSION_FILE = "x_session.json"


def main():
    if Path(SESSION_FILE).exists():
        answer = input(f'⚠️ {SESSION_FILE} は既に存在します。上書きしますか？ (y/N): ').strip().lower()
        if answer != 'y':
            print('中止しました。')
            return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://x.com/login")

        print('=' * 60)
        print('ブラウザが開きました。画面上で手動でXにログインしてください。')
        print('（2段階認証や本人確認が出た場合もそのまま手動で進めてください）')
        print('ログインが完了してタイムライン画面が表示されたら、')
        print('このターミナルに戻って Enter キーを押してください。')
        print('=' * 60)
        input()

        context.storage_state(path=SESSION_FILE)
        browser.close()

    print(f'\n✅ セッションを保存しました: {SESSION_FILE}')
    print('⚠️ このファイルは厳重に管理してください（Gitにコミットしない）。')


if __name__ == "__main__":
    main()
