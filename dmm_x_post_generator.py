"""
💰🐦 DMMアフィリエイト → X（Twitter）投稿文ジェネレーター
DMMから商品情報を取得し、X投稿用テキストをデスクトップまたは指定フォルダに保存します。

【v3: 報酬ゼロ対策・サービス新規報酬獲得を重視した改善】

■ 問題の原因分析
- クリック数はあるが報酬ゼロ → FANZAの「ダイレクト報酬」「カテゴリ報酬」が発生していない
- 最も稼ぎやすい「サービス新規報酬」（新規会員登録）が全くゼロ
- 原因: 投稿文がFANZA既存会員向けになっており、未登録者に響いていなかった

■ v3の主な変更点
- FANZA未登録者への「無料会員登録」訴求コピーを追加（サービス新規報酬を狙う）
- ポスト1にアフィリエイトURLも掲載（サンプルURL + 作品ページURL の両立）
- CTAを「FANZAに無料登録してサンプル確認 → 購入」の導線に統一
- ハッシュタグを #AV #FANZAおすすめ から #アダルト動画 #無料サンプルあり 等に変更
  （既存会員ではなく未登録ユーザーへのリーチを優先）
- ヘッダー文に「無料で見られる」「FANZAのサンプル」など発見訴求を追加

AUTO_POST_TO_X=true を設定すると、サンプル動画を実際にXへアップロードして
動画埋め込み付きで自動投稿します。
  - POST_METHOD=browser（デフォルト）: Playwrightでブラウザを直接操作。API課金なし。
                                          要：事前にx_login_setup.pyでログインセッション作成。
  - POST_METHOD=api    : 公式X API（要キー・課金あり）。
未設定時は従来どおりテキストファイル保存のみで完全無料で動作します。
"""

import os
import sys
import json
import datetime
import requests
import random
import re
import tempfile
import time
import base64
from pathlib import Path

try:
    import tweepy
except ImportError:
    tweepy = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

# ================================================================
# ⚙️  設定（環境変数から読み込み）
# ================================================================

DMM_API_ID       = os.environ.get('DMM_API_ID', '')
DMM_AFFILIATE_ID = os.environ.get('DMM_AFFILIATE_ID', '')

if not DMM_API_ID or not DMM_AFFILIATE_ID:
    print('❌ 環境変数 DMM_API_ID / DMM_AFFILIATE_ID が設定されていません。')
    sys.exit(1)

print('✅ 認証情報を読み込みました。')

DMM_FLOOR = os.environ.get('DMM_FLOOR', 'videoa')

# ----------------------------------------------------------------
# 📌 ソートモード設定
#    DMM_SORT_MODE=both（デフォルト）→ 新着15件 ＋ 人気15件 = 計30件を1ファイルに保存
#    DMM_SORT_MODE=date              → 新着順のみ30件
#    DMM_SORT_MODE=rank              → 人気順のみ30件
# ----------------------------------------------------------------
DMM_SORT_MODE = os.environ.get('DMM_SORT_MODE', 'both').lower()

SORT_TARGETS = {
    'both': [('-date', '新着順'), ('-rank', '人気順')],
    'date': [('-date', '新着順')],
    'rank': [('-rank', '人気順')],
}
SORT_LIST = SORT_TARGETS.get(DMM_SORT_MODE, SORT_TARGETS['both'])

# ----------------------------------------------------------------
# 🔢 処理件数の上限（速度優先のため、1回の実行で処理する商品数の合計を制限する）
#    DMM_SORT_MODE=both のように複数ソートを使う場合は、合計でこの件数に収まるよう
#    各ソートの取得件数を自動的に按分する。
# ----------------------------------------------------------------
MAX_PROCESS_COUNT = int(os.environ.get('MAX_PROCESS_COUNT', '30'))
print(f'🔢 処理件数の上限: 合計 {MAX_PROCESS_COUNT} 件（ソート {len(SORT_LIST)} 種類）')

# ----------------------------------------------------------------
# 🔽 価格フィルター使用時の最低保証件数
#    価格フィルターで絞り込んだ後も、この件数に達するまで追加取得を繰り返す。
#    MIN_PROCESS_COUNT=0 で無効化（従来の動作に戻る）
# ----------------------------------------------------------------
MIN_PROCESS_COUNT = int(os.environ.get('MIN_PROCESS_COUNT', '20'))

# ----------------------------------------------------------------
# 🎲 取得開始位置（環境変数未設定時はランダム: 1〜480）
#    ただし新着順（-date）で取得する場合、空欄（未指定）なら「最新のデータ」から
#    検索したいので、その場合は開始位置を1固定にする（POST_START_INDEX_EXPLICITで判定）。
# ----------------------------------------------------------------
_raw_start = os.environ.get('POST_START_INDEX', '')
POST_START_INDEX_EXPLICIT = _raw_start.strip().isdigit()
if POST_START_INDEX_EXPLICIT:
    POST_START_INDEX = int(_raw_start.strip())
    print(f'📌 指定された取得開始番号: {POST_START_INDEX}')
else:
    POST_START_INDEX = random.randint(1, 480)
    print(f'🎲 ランダム取得開始番号: {POST_START_INDEX}（新着順検索では未指定時は1件目＝最新データから検索します）')

FETCH_COUNT = MAX_PROCESS_COUNT if len(SORT_LIST) == 1 else max(1, -(-MAX_PROCESS_COUNT // len(SORT_LIST)))  # 単一ソートは全件、複数ソートは切り上げ按分（例: 30件÷2ソート=15件ずつ）
DMM_OFFSET  = POST_START_INDEX
DMM_HITS    = FETCH_COUNT

# ----------------------------------------------------------------
# 🔁 FANZA/DMM API リトライ設定
#    通信エラーやAPI側のエラー応答（一時的なレート制限等）が発生した場合、
#    すぐに諦めず、FANZA APIの問い合わせ上限（DMM_MAX_RETRIES回）まで
#    間隔を空けながらリトライする。
# ----------------------------------------------------------------
DMM_MAX_RETRIES    = int(os.environ.get('DMM_MAX_RETRIES', '10'))
DMM_RETRY_WAIT_SEC = float(os.environ.get('DMM_RETRY_WAIT_SEC', '3'))

# ----------------------------------------------------------------
# 💰 価格フィルター設定
#    DMM_PRICE_RANGE=all（デフォルト）→ 価格による絞り込みなし
#    その他の指定例:
#      "0-999"    → 0円〜999円
#      "1000-1999"→ 1000円〜1999円
#      "2000-2999"→ 2000円〜2999円
#      "3000-4999"→ 3000円〜4999円
#      "5000-"    → 5000円以上
# ----------------------------------------------------------------
DMM_PRICE_RANGE = os.environ.get('DMM_PRICE_RANGE', 'all').strip().lower()

def parse_price_range(range_str):
    """価格範囲文字列を (min, max) のタプルに変換する。max=Noneは上限なし。"""
    if not range_str or range_str == 'all':
        return None
    range_str = range_str.replace('円', '').replace(',', '').strip()
    if '-' not in range_str:
        return None
    min_part, max_part = range_str.split('-', 1)
    min_part = min_part.strip()
    max_part = max_part.strip()
    try:
        price_min = int(min_part) if min_part else 0
    except ValueError:
        price_min = 0
    if max_part:
        try:
            price_max = int(max_part)
        except ValueError:
            price_max = None
    else:
        price_max = None
    return (price_min, price_max)

PRICE_RANGE_BOUNDS = parse_price_range(DMM_PRICE_RANGE)
if PRICE_RANGE_BOUNDS:
    _pmin, _pmax = PRICE_RANGE_BOUNDS
    _pmax_label = f'{_pmax:,}円' if _pmax is not None else '上限なし'
    print(f'💰 価格フィルター: {_pmin:,}円 〜 {_pmax_label}')
else:
    print('💰 価格フィルター: なし（すべての価格を対象）')

# ----------------------------------------------------------------
# 🐦 X（Twitter）自動投稿設定
#    AUTO_POST_TO_X=true のときだけ、サンプル動画をXにアップロードして
#    動画埋め込み付きで実際に投稿する。falseならテキスト生成のみ（従来動作）。
#
#    POST_METHOD=browser（デフォルト）: Playwrightでブラウザ操作。API課金なし。
#                                          ただしX利用規約上はグレー〜違反扱い。アカウント凍結リスクあり。
#    POST_METHOD=api                  : 公式X API。課金あり。規約に準拠した正規ルート。
# ----------------------------------------------------------------
AUTO_POST_TO_X = os.environ.get('AUTO_POST_TO_X', 'false').strip().lower() == 'true'
POST_METHOD    = os.environ.get('POST_METHOD', 'browser').strip().lower()

# --- API方式の認証情報 ---
X_API_KEY        = os.environ.get('X_API_KEY', '')
X_API_SECRET      = os.environ.get('X_API_SECRET', '')
X_ACCESS_TOKEN    = os.environ.get('X_ACCESS_TOKEN', '')
X_ACCESS_SECRET   = os.environ.get('X_ACCESS_SECRET', '')

# --- ブラウザ方式のセッション情報 ---
# X_SESSION_FILE: x_login_setup.py で作成したセッションファイルのパス
# X_SESSION_STATE_B64: CI環境用。セッションファイルの中身をBase64化したものを直接渡す場合
X_SESSION_FILE      = os.environ.get('X_SESSION_FILE', 'x_session.json')
X_SESSION_STATE_B64 = os.environ.get('X_SESSION_STATE_B64', '')

# 1回の実行で実際に投稿する最大件数（課金 or 凍結リスクを抑えるため必ず上限を設ける）
X_POST_LIMIT = int(os.environ.get('X_POST_LIMIT', '5'))

# 投稿間隔（秒）。連続投稿でのレート制限・スパム判定・Bot検知を避けるため
X_POST_INTERVAL_SEC = int(os.environ.get('X_POST_INTERVAL_SEC', '30'))

if AUTO_POST_TO_X:
    if POST_METHOD == 'browser':
        if sync_playwright is None:
            print('❌ POST_METHOD=browser ですが playwright がインストールされていません。')
            print('   `pip install playwright && playwright install chromium` を実行してください。')
            sys.exit(1)

        # CI用：Base64で渡されたセッション情報をファイルに復元
        if X_SESSION_STATE_B64 and not os.path.exists(X_SESSION_FILE):
            with open(X_SESSION_FILE, 'wb') as f:
                f.write(base64.b64decode(X_SESSION_STATE_B64))
            print(f'🔑 X_SESSION_STATE_B64 からセッションファイルを復元しました: {X_SESSION_FILE}')

        if not os.path.exists(X_SESSION_FILE):
            print(f'❌ セッションファイルが見つかりません: {X_SESSION_FILE}')
            print('   先に `python x_login_setup.py` を実行してログインセッションを作成してください。')
            sys.exit(1)

        print(f'🐦 自動投稿モード: ON / ブラウザ操作方式（最大 {X_POST_LIMIT} 件・間隔 {X_POST_INTERVAL_SEC} 秒）')
        print('   ⚠️  これはX公式APIを使わない自動操作です。X利用規約違反となりアカウント凍結のリスクがあります。')

    elif POST_METHOD == 'api':
        if tweepy is None:
            print('❌ POST_METHOD=api ですが tweepy がインストールされていません。`pip install tweepy` してください。')
            sys.exit(1)
        if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET]):
            print('❌ POST_METHOD=api ですが X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET が不足しています。')
            sys.exit(1)
        print(f'🐦 自動投稿モード: ON / 公式API方式（最大 {X_POST_LIMIT} 件・間隔 {X_POST_INTERVAL_SEC} 秒）')

    else:
        print(f'❌ POST_METHOD は browser か api を指定してください（指定値: {POST_METHOD}）')
        sys.exit(1)
else:
    print('🐦 自動投稿モード: OFF（テキストファイル保存のみ・無料）')

DMM_API_BASE = 'https://api.dmm.com/affiliate/v3'



FLOOR_SERVICE_MAP = {
    'videoa':  ('digital', 'videoa'),
    'videoc':  ('digital', 'videoc'),
    'anime':   ('digital', 'anime'),
    'doujin':  ('doujin',  'digital_doujin'),
    'comic':   ('ebook',   'comic'),
    'goods':   ('mono',    'goods'),
    'digital': ('digital', 'videoa'),
}

HASHTAG_MAP = {
    # 【v3改善】非会員ユーザーにリーチしやすいタグ構成
    # #AV や #FANZAおすすめ は既存会員ばかりに届く傾向があるため変更
    # 一般エンタメ・動画系タグで間口を広げ、FANZA未登録層にアプローチ
    'videoa': '#アダルト動画 #FANZA #PR',
    'videoc': '#素人動画 #FANZA #個人撮影 #PR',
    'anime':  '#エロアニメ #FANZA #アニメ好き #PR',
    'doujin': '#同人誌 #FANZA #エロ同人 #PR',
    'comic':  '#エロ漫画 #FANZA #電子書籍 #PR',
    'goods':  '#大人グッズ #FANZA #PR',
    'default': '#FANZA #アダルト動画 #PR',
}

# ジャンル別の追加ハッシュタグ（genre_tagsで使うジャンル名に加えて付与する）
GENRE_EXTRA_HASHTAG_MAP = {
    '素人':   '#素人動画',
    '人妻':   '#人妻動画',
    '巨乳':   '#巨乳',
    '美乳':   '#美乳',
    '中出し': '#中出し',
    '企画':   '#企画AV',
    '単体作品': '#単体女優',
}

COPY_TEMPLATES = [
    # ――― v3: 新規会員登録（サービス新規報酬）を狙ったコピーを追加 ―――
    # FANZAを使ったことがない人向けの「入口」として機能する文言を重視
    # 既存会員への購入訴求 + 未登録者への登録訴求 の両立を目指す

    # 【新規会員獲得重視】FANZA未登録者への訴求（サービス新規報酬につながる）
    "FANZAは無料会員登録だけでサンプルが全部見られる。まず登録して確認するのがおすすめ",
    "FANZAって無料で会員登録できて、この手のサンプルが全作品見放題なの地味に神",
    "まだFANZA使ったことない人、登録自体は無料でできるのでまず試してみる価値はある",
    "FANZAは無料会員のまま全サンプル見られる。まず登録してこのサンプルから確認してみて",

    # 【購入後押し型】既存会員向け
    "毎日FANZAチェックしてるけど、これクラスの作品は月に数本しか出ない。見逃すと後悔するやつ",
    "このクオリティでこの価格はさすがに安すぎる。FANZAの値付けがバグってると思う",
    "無料サンプルで十分と思ってたのに、本編が気になって結局ポチった。サンプルが罪すぎる",
    "レビュー評価見てから買ったけど、評価高いのには理由があった。納得の内容だった",
    "FANZAランキング上位をキープしてる理由がわかった。これは実際に見ると納得感がある",
    "サブスクにない作品だから単品購入したけど、それでも全然惜しくない出来だった",
    "FANZAって値段が変わることあるから、気になってるなら今のうちに確認しといたほうがいい",
    "映画1本分の値段で何度でも見返せるって考えたら、コスパ的にもアリだと思う",
    "缶ビール数本分の値段で今夜の時間を最高にできると思ったら安いもんだと思う",
    "動画配信サービス1ヶ月分より安い。それで手元に残るなら買わない理由がない",
    "迷ってる時間がもったいなかった。ポチってから「もっと早く買えばよかった」ってなった",
    "サンプル見て5分悩んで購入した。その5分が惜しかったくらい内容が良かった",
]

# FANZA未登録者向けの登録誘導文（サービス新規報酬の発生を促す）
REGISTRATION_HOOKS = [
    "\n\n💡 FANZAは無料会員登録でサンプル見放題。まず登録してみて👇",
    "\n\n💡 FANZA無料登録でこの作品のサンプルがすぐ確認できます👇",
    "\n\n💡 まだFANZA未登録なら、無料会員登録でサンプルが全部見られます👇",
]

def get_copy():
    return random.choice(COPY_TEMPLATES)


# ----------------------------------------------------------------
# ✨ おすすめポイント自動生成（DMM APIのデータから）
#    ジャンル・女優・メーカー・レビュー評価・価格などを組み合わせて、
#    商品ごとに違った訴求文を作る。固定文のランダム抽選より具体的になる。
# ----------------------------------------------------------------

_OPENERS = [
    "正直に購入した理由を言うと、",
    "迷ってる人に一言だけ言うと、",
    "お金を出す価値があると思った理由は、",
    "これを選んだ決め手は、",
]
_CLOSERS = [
    "まず無料サンプルだけ見て、気に入ったらそのまま購入できる👇",
    "サンプル確認 → 気に入ったらポチるだけ。損はしない構造になってる",
    "今の価格で買えるうちにリンクから確認してみて👇",
    "サンプルだけでも見てほしい。それで判断できると思う👇",
]
_FALLBACK_PHRASES = [
    "手元に置いておきたくなる内容だった",
    "購入後に「早く買えばよかった」ってなったやつ",
    "これはレンタルじゃなくて買いだと思う",
]
_FILLER_PHRASES = [
    "無料サンプルで内容確認してから買えるから失敗しにくい",
    "購入者レビューの評価がかなり高くて安心感がある",
    "何度でも見返せるから単品購入でもコスパはいい",
    "FANZAは決済後すぐ視聴できるのが地味に助かる",
    "毎日チェックしてる中でも特に推せると思ったやつ",
]


def build_recommend_points(product, max_len=120):
    """商品データのうち、投稿文の他の行（ジャンルタグ・価格表示）と重複しない
    『レビュー評価・出演者・メーカー』を軸におすすめポイント文を作る。
    データ項目だけで max_len に届かない場合は、商品の事実とは無関係な汎用フレーズ
    （誇張や個別の内容を断定しないもの）を追加し、Xの文字数上限近くまで使い切る。
    """
    segments = []

    if product.get('review_avg'):
        avg = product['review_avg']
        count = product.get('review_count')
        if count:
            segments.append(f"レビュー平均{avg}（{count}件）の高評価")
        else:
            segments.append(f"レビュー評価{avg}の高評価")

    if product.get('actors'):
        as_ = '・'.join(product['actors'][:2])
        segments.append(f"出演は{as_}")

    if product.get('maker'):
        segments.append(f"{product['maker']}制作")

    if not segments:
        segments.append(random.choice(_FALLBACK_PHRASES))

    # 汎用フレーズをランダムな順で末尾に追加候補として用意しておく
    fillers = random.sample(_FILLER_PHRASES, len(_FILLER_PHRASES))
    segments.extend(fillers)

    opener = random.choice(_OPENERS)
    closer = random.choice(_CLOSERS)

    # 入る範囲までセグメントを「、」でつなげて、文字数上限を有効活用する
    # ※ max_len はX（Twitter）の「重み付き文字数」基準（x_text_length）で渡される。
    #    日本語・絵文字は1文字=2カウントなので、ここも len() ではなく
    #    x_text_length() で判定しないと、実際の上限の約2倍も詰め込んでしまう。
    body = ''
    for i, seg in enumerate(segments):
        sep = '' if i == 0 else '、'
        candidate = body + sep + seg
        # opener + candidate + '。' + closer が収まるかチェック
        if x_text_length(opener + candidate + '。' + closer) > max_len:
            continue  # この要素は入らないが、後続のもっと短い要素が入るかもしれないので継続
        body = candidate

    if not body:
        # 1要素も入らない場合は最低限の要約を切り詰めて表示
        return truncate_to_weighted_length(opener + segments[0], max_len)

    text = f"{opener}{body}。{closer}"
    if x_text_length(text) > max_len:
        text = truncate_to_weighted_length(text, max_len)
    return text


def truncate_to_weighted_length(text, max_len):
    """重み付き文字数（x_text_length）がmax_len以下になるよう、末尾に'…'を付けて切り詰める。"""
    if x_text_length(text + '…') <= max_len:
        return text + '…'
    # 1文字ずつ削りながら収まるところまで縮める
    truncated = text
    while truncated and x_text_length(truncated + '…') > max_len:
        truncated = truncated[:-1]
    return truncated + '…' if truncated else '…'


# ----------------------------------------------------------------
# 🔗 URL確認
# ----------------------------------------------------------------

def check_url(url, timeout=8):
    """URLが実際にアクセス可能かHEADリクエストで確認する。結果はTrue/False/None(未確認)。"""
    if not url:
        return None
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True,
                              headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code >= 400:
            # HEADを許可していないサーバーもあるためGETで再確認
            resp = requests.get(url, timeout=timeout, stream=True,
                                 headers={'User-Agent': 'Mozilla/5.0'})
        return resp.status_code < 400
    except Exception:
        return None


def shorten_url(url, timeout=8):
    """TinyURL（APIキー不要）でURLを短縮する。失敗時は元のURLをそのまま返す。
    Xの文字数カウントはURLの自動短縮（t.co）に必ずしも依存できないため、
    実際の文字数を抑えるためにここで短縮しておく。"""
    if not url:
        return url
    try:
        resp = requests.get(
            'https://tinyurl.com/api-create.php',
            params={'url': url},
            timeout=timeout,
        )
        short = resp.text.strip()
        if resp.status_code == 200 and short.startswith('http'):
            return short
    except Exception as e:
        print(f'  ⚠️  URL短縮に失敗（元のURLを使用します）: {e}')
    return url

# ================================================================
# 🔧 DMM API 関数
# ================================================================

def fetch_dmm_products(sort_key, sort_label, offset=None, hits=None):
    service, floor_name = FLOOR_SERVICE_MAP.get(DMM_FLOOR, ('digital', 'videoa'))
    _offset = offset if offset is not None else DMM_OFFSET
    _hits   = hits   if hits   is not None else DMM_HITS
    params = {
        'api_id':       DMM_API_ID,
        'affiliate_id': DMM_AFFILIATE_ID,
        'site':         'FANZA',
        'service':      service,
        'floor':        floor_name,
        'hits':         _hits,
        'offset':       _offset,
        'sort':         sort_key,
        'output':       'json',
    }
    print(f'\n  [{sort_label}] 取得範囲: {_offset}件目〜{_offset + _hits - 1}件目')

    for attempt in range(1, DMM_MAX_RETRIES + 1):
        try:
            resp = requests.get(f'{DMM_API_BASE}/ItemList', params=params, timeout=15)
            data = resp.json()
            result = data.get('result', {})

            # DMM APIがエラー応答を返した場合（レート制限・一時的な問い合わせ上限超過など）
            status = result.get('status')
            if status is not None and str(status) != '200':
                message = result.get('message') or data.get('message') or '不明なエラー'
                raise RuntimeError(f'DMM APIエラー応答 status={status} message={message}')

            items = result.get('items', [])
            if isinstance(items, dict):
                items = items.get('item', [])
            if items:
                url_str = items[0].get('affiliateURL', '')
                print(f"  URLの総文字数: {len(url_str)} / 末尾10文字: {url_str[-10:]}")
            print(f'  ✅ {len(items)} 件取得しました。')
            return items
        except Exception as e:
            if attempt >= DMM_MAX_RETRIES:
                print(f'  ❌ DMM APIエラー（{attempt}/{DMM_MAX_RETRIES}回でリトライ上限に到達）: {e}')
                return []
            wait_sec = DMM_RETRY_WAIT_SEC * attempt  # 試行のたびに待機時間を延ばす（簡易バックオフ）
            print(f'  ⚠️  DMM APIエラー（{attempt}/{DMM_MAX_RETRIES}回目）: {e}')
            print(f'  ⏳ {wait_sec:.1f}秒待機してリトライします...')
            time.sleep(wait_sec)

    return []


def parse_product(item):
    title         = item.get('title', '')
    affiliate_url = item.get('affiliateURL', '') or item.get('URL', '')
    prices        = item.get('prices', {})
    price_str     = ''
    price_num     = None
    if prices:
        price_val = prices.get('price') or prices.get('list_price') or ''
        if price_val:
            digits = ''.join(c for c in str(price_val) if c.isdigit())
            if digits:
                price_num = int(digits)
                price_str = f'\u00a5{price_num:,}'
    actors = [a.get('name', '') for a in (item.get('iteminfo', {}).get('actress') or [])][:3]
    # 【表示除外】「ハイビジョン」「無料サンプルあり」はジャンルタグとして意味が薄いため非表示にする
    EXCLUDED_GENRES = {'ハイビジョン', '無料サンプルあり'}
    genres = [
        g.get('name', '')
        for g in (item.get('iteminfo', {}).get('genre') or [])
        if g.get('name', '') not in EXCLUDED_GENRES
    ][:3]
    maker  = ((item.get('iteminfo', {}).get('maker') or [{}])[0]).get('name', '')

    sample_movie_url = ''
    smv = item.get('sampleMovieURL', {})
    if smv:
        for key in ['size_720_480', 'size_644_414', 'size_560_360', 'size_476_306']:
            val = smv.get(key, '')
            if val:
                sample_movie_url = val.strip()
                break

    content_id = item.get('content_id', '') or item.get('product_id', '')

    # レビュー情報（平均評価・件数）。商品によっては存在しない。
    review_info  = item.get('review', {}) or {}
    review_avg   = review_info.get('average', '')
    review_count = review_info.get('count', '')
    try:
        review_avg = float(review_avg) if review_avg not in ('', None) else None
    except (TypeError, ValueError):
        review_avg = None
    try:
        review_count = int(review_count) if review_count not in ('', None) else None
    except (TypeError, ValueError):
        review_count = None

    # 配信開始日（新着訴求に使う）
    date_str = item.get('date', '')

    return {
        'title':            title,
        'affiliate_url':    affiliate_url,
        'price':            price_str,
        'price_num':        price_num,
        'actors':           actors,
        'genres':           genres,
        'maker':            maker,
        'sample_movie_url': sample_movie_url,
        'content_id':       content_id,
        'review_avg':       review_avg,
        'review_count':     review_count,
        'date':             date_str,
    }

def clean_url(url):
    if not url:
        return ''
    url = url.strip().replace('\n', '').replace('\r', '').replace('　', '')
    if not url.startswith('http'):
        return ''
    return url


def actor_tags(actors):
    return '　'.join('#' + a.replace(' ', '').replace('　', '') for a in actors if a)


def genre_tags(genres):
    """ジャンル名（人妻・主婦、巨乳など）をハッシュタグ形式に変換する。"""
    return '　'.join('#' + g.replace(' ', '').replace('　', '') for g in genres if g)


def price_in_range(product):
    """価格フィルターが設定されている場合、商品の価格が範囲内かどうかを判定する。"""
    if not PRICE_RANGE_BOUNDS:
        return True
    price_num = product.get('price_num')
    if price_num is None:
        return False
    price_min, price_max = PRICE_RANGE_BOUNDS
    if price_num < price_min:
        return False
    if price_max is not None and price_num > price_max:
        return False
    return True


# ----------------------------------------------------------------
# 📏 X（Twitter）の文字数カウント
#    旧実装は len(text) をそのまま使っていたが、これはバグだった。
#    Xは公式の重み付きカウント方式（twitter-text）を採用しており、
#    日本語・絵文字・全角記号などは「2文字分」としてカウントされる。
#    例えば見た目140文字の日本語投稿でも、Xの実カウントでは280文字相当となり
#    上限ギリギリ〜超過になる。これを反映しないと
#    「上限内のはずなのに実際は超過していた」事象が起きる。
#
#    重み付けルール（X公式 twitter-text の config を反映）:
#      - 半角英数字・一般的な記号など（コードポイント 0-4351、
#        および一部の句読点範囲）は 1文字としてカウント
#      - それ以外（ひらがな・カタカナ・漢字・絵文字・全角記号など）は
#        2文字としてカウント
#      - URL（http/https〜）は実際の文字数に関わらず、Xが自動でt.co形式に
#        短縮するため「23文字」固定としてカウント
# ----------------------------------------------------------------

# 1文字としてカウントする（重み1）コードポイント範囲
_X_LOW_WEIGHT_RANGES = [
    (0, 4351),       # 基本ラテン文字、各種記号、ギリシャ文字、キリル文字 など
    (8192, 8205),    # 一般句読点（スペース類）
    (8208, 8223),    # 一般句読点（ハイフン・ダッシュ類）
    (8242, 8247),    # プライム記号など
]

_X_URL_PATTERN = re.compile(r'https?://\S+')


def _x_char_weight(ch):
    """1文字あたりの重みを返す（半角=1、それ以外（CJK・絵文字等）=2）。"""
    cp = ord(ch)
    for lo, hi in _X_LOW_WEIGHT_RANGES:
        if lo <= cp <= hi:
            return 1
    return 2


def x_text_length(text):
    """X（Twitter）公式の重み付き文字数カウントを再現する。

    - URLはt.co短縮を見込んで23文字固定で計算
    - それ以外は文字ごとに重み（半角=1、日本語・絵文字等=2）を合計
    """
    urls = _X_URL_PATTERN.findall(text)
    text_without_urls = _X_URL_PATTERN.sub('', text)

    weighted = sum(_x_char_weight(c) for c in text_without_urls)
    weighted += len(urls) * 23

    return weighted


def build_x_post(product, char_limit=280):
    """後方互換用ラッパー。スレッドの1ポスト目テキストを返す。"""
    return build_x_thread(product, char_limit)[0]


def build_x_thread(product, char_limit=280):
    """スレッド投稿用に2ポスト分のテキストをリストで返す。
    [0] 1ポスト目：引き一言 ＋ タイトル ＋ アフィリエイトURL
    [1] 2ポスト目：おすすめポイント詳細 ＋ 価格・出演者 ＋ ハッシュタグ（＋サンプルURL）
    """
    hashtags  = HASHTAG_MAP.get(DMM_FLOOR, HASHTAG_MAP['default'])
    url_full    = clean_url(product['affiliate_url'])
    sample_full = clean_url(product.get('sample_movie_url', ''))

    # --- URL確認（元のURLに対して行う） ---
    url_ok    = check_url(url_full) if url_full else None
    sample_ok = check_url(sample_full) if sample_full else None
    if url_full and url_ok is False:
        print(f"    ⚠️  アフィリエイトURLにアクセスできませんでした: {url_full}")
    if sample_full and sample_ok is False:
        print(f"    ⚠️  サンプル動画URLにアクセスできませんでした: {sample_full}")

    # --- URL短縮 ---
    url    = shorten_url(url_full) if url_full else ''
    sample = shorten_url(sample_full) if sample_full else ''

    product['url_check']    = url_ok
    product['sample_check'] = sample_ok

    act_tags = actor_tags(product['actors'])

    title = product['title']
    title_short = (title[:35] + '…') if len(title) > 35 else title

    def extra_genre_hashtags(genre_list):
        extras = [GENRE_EXTRA_HASHTAG_MAP[g] for g in genre_list if g in GENRE_EXTRA_HASHTAG_MAP]
        return '　'.join(extras)

    # ================================================================
    # ── 1ポスト目：引き＋タイトル＋サンプルURL（無料なので踏みやすい）
    # ================================================================
    # 【v3改善】ヘッダー文を「FANZA未登録層」にも届く表現に刷新
    # FANZAを知らない人が「何これ？」と気になるフックを増やす
    HEADERS = [
        "今夜これ正解だった👇",
        "FANZAのサンプル、これは必見👇",
        "無料で見られるやつ教える👇",
        "これは損しない作品だった👇",
        "FANZAで話題になってた作品👇",
        "無料サンプルだけでも見て👇",
        "今夜のためにこれ置いとく👇",
    ]
    hook = random.choice(COPY_TEMPLATES)
    header = random.choice(HEADERS)

    # 【v3改善】ポスト1にアフィリエイトURLも含める
    # サービス新規報酬はFANZAへの新規会員登録で発生する。
    # ポスト1にアフィリエイトURLを置くことで、1クリックで登録ページまで誘導できる。
    # サンプルURLは「無料で見られる」訴求、アフィURL直後に「登録はこちら」を添える構成。
    # 【v4改善】ポスト1にURLを2つ入れるとXのリンクカード（画像）が表示されなくなるため、
    # ポスト1には無料サンプルURLのみを掲載し、アフィリエイトURLはポスト2にまとめる。
    post1_url = sample if sample else url
    post1_lines = [header, f"📽 {title_short}", '', hook, '', f"▶ 無料サンプル: {post1_url}" if sample else post1_url]

    post1 = '\n'.join(post1_lines)

    # 1ポスト目が280字を超える場合はhookを切り詰める
    if x_text_length(post1) > char_limit:
        over = x_text_length(post1) - char_limit
        hook_budget = max(10, x_text_length(hook) - over)
        hook = truncate_to_weighted_length(hook, hook_budget)
        post1_lines = [header, f"📽 {title_short}", '', hook, '', f"▶ 無料サンプル: {post1_url}" if sample else post1_url]
        post1 = '\n'.join(post1_lines)

    assert x_text_length(post1) <= char_limit, (
        f"⚠️ 1ポスト目文字数超過: {x_text_length(post1)} > {char_limit}\n{post1}"
    )

    # ================================================================
    # ── 2ポスト目：詳細情報＋アフィリエイトURL＋ハッシュタグ
    # ================================================================
    extra = extra_genre_hashtags(product['genres'])
    full_hashtags = hashtags + ('　' + extra if extra else '')

    def build_post2_lines(genre_list, copy_text):
        # videoa の場合はポスト2がアフィリエイトリンク専用であることを明示
        # 【v3改善】FANZA未登録者への登録誘導CTAを追加（サービス新規報酬を狙う）
        BUY_CTAS = [
            "🛒 FANZAに無料登録してサンプル確認 → そのまま購入できます👇",
            "🛒 無料会員登録でサンプル見放題。気に入ったらそのまま購入👇",
            "🛒 FANZA無料登録 → サンプル確認 → 購入の流れです👇",
            "🛒 無料サンプルあり・FANZAに登録すればすぐ視聴できます👇",
            "🛒 今の価格で購入できます（FANZAは値段変動あり・無料登録で確認）👇",
        ]
        OTHER_CTAS = [
            "📌 FANZA無料登録 → サンプル確認 → 購入はこちら👇",
            "📌 無料会員登録でサンプルが見られます。購入はこちら👇",
        ]
        if DMM_FLOOR == 'videoa':
            lines = [random.choice(BUY_CTAS)]
        else:
            lines = [random.choice(OTHER_CTAS)]
        if copy_text:
            lines += ['', copy_text]
        lines.append('')
        if product['price']:
            price_num = product.get('price_num')
            if price_num and price_num <= 500:
                price_label = f"💰 {product['price']}（ワンコイン以下）"
            elif price_num and price_num <= 1000:
                price_label = f"💰 {product['price']}（缶ビール数本分）"
            elif price_num and price_num <= 2000:
                price_label = f"💰 {product['price']}（映画1本分以下）"
            else:
                price_label = f"💰 {product['price']}"
            lines.append(price_label)
        if act_tags:
            lines.append(f"👤 {act_tags}")
        if genre_list:
            lines.append(f"🏷 {genre_tags(genre_list)}")
        lines += ['', url, '', full_hashtags]
        return lines

    # おすすめポイントに使える文字数を逆算
    skeleton2 = '\n'.join(build_post2_lines(product['genres'], ''))
    available = char_limit - x_text_length(skeleton2)
    copy = build_recommend_points(product, max_len=max(available, 15))

    post2 = '\n'.join(build_post2_lines(product['genres'], copy))

    # 超過時の段階的フォールバック
    if x_text_length(post2) > char_limit:
        post2 = '\n'.join(build_post2_lines(product['genres'][:2], copy))
    if x_text_length(post2) > char_limit:
        over = x_text_length(post2) - char_limit
        copy = truncate_to_weighted_length(copy, max(10, x_text_length(copy) - over))
        post2 = '\n'.join(build_post2_lines(product['genres'][:2], copy))
    if x_text_length(post2) > char_limit:
        post2 = '\n'.join(build_post2_lines([], ''))
    if x_text_length(post2) > char_limit:
        minimal_tags = '#FANZA #PR'
        post2 = re.sub(r'#FANZA.*$', minimal_tags, post2, flags=re.DOTALL)
    if x_text_length(post2) > char_limit:
        post2 = truncate_to_weighted_length(post2, char_limit)

    assert x_text_length(post2) <= char_limit, (
        f"⚠️ 2ポスト目文字数超過: {x_text_length(post2)} > {char_limit}\n{post2}"
    )

    return [post1, post2]

# ================================================================
# 🐦 X（Twitter）動画埋め込み投稿
# ================================================================

def get_x_clients():
    """v1.1（チャンクアップロード用）とv2（投稿用）の両方のクライアントを返す。"""
    auth = tweepy.OAuth1UserHandler(
        X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
    )
    api_v1 = tweepy.API(auth)

    client_v2 = tweepy.Client(
        consumer_key=X_API_KEY,
        consumer_secret=X_API_SECRET,
        access_token=X_ACCESS_TOKEN,
        access_token_secret=X_ACCESS_SECRET,
    )
    return api_v1, client_v2


def resolve_and_download_via_browser(context, page_url, content_id='', max_bytes=200 * 1024 * 1024):
    """
    litevideoページを「requestsライブラリ」ではなく、実際のChromiumブラウザ(context)で
    開いて動画の実体URLを取得し、ダウンロードする。
    cc3001.dmm.co.jp 等のCDNは単純なrequestsアクセスをbot判定でブロックすることがあるため、
    既にXログイン用に開いている本物のブラウザコンテキストを流用することで通過させる狙い。
    """
    if not page_url:
        return None

    mp4_url = page_url if page_url.lower().endswith('.mp4') else None

    if not mp4_url:
        tab = context.new_page()
        captured = {}

        def on_response(resp):
            if '.mp4' in resp.url and resp.status == 200 and 'mp4_url' not in captured:
                captured['mp4_url'] = resp.url

        try:
            tab.on('response', on_response)
            tab.goto(page_url, timeout=20000)

            # <video>タグに直接srcが入っているケース
            try:
                tab.wait_for_selector('video', timeout=8000)
                src = tab.eval_on_selector('video', 'el => el.currentSrc || el.src || ""')
                if src and '.mp4' in src:
                    mp4_url = src
            except Exception:
                pass

            # 再生開始まで動画srcがセットされないプレイヤーもあるため、
            # 再生ボタンらしき要素をクリックしてみてネットワークから.mp4を拾う
            if not mp4_url:
                for sel in ['video', '.play-button', '[class*="play"]', 'button']:
                    try:
                        tab.click(sel, timeout=1500)
                        break
                    except Exception:
                        continue
                tab.wait_for_timeout(3000)
                mp4_url = captured.get('mp4_url')
        except Exception as e:
            print(f'  ❌ litevideoページの読み込みに失敗: {e}')
        finally:
            tab.close()

    if not mp4_url:
        print('  ⚠️  ブラウザ経由でも.mp4のURLを取得できませんでした'
              '（この商品にサンプル動画が無い可能性があります）。')
        return None

    try:
        resp = context.request.get(mp4_url, timeout=30000)
        if resp.status != 200:
            print(f'  ❌ 動画ダウンロード失敗（HTTP {resp.status}）')
            return None
        body = resp.body()
        if len(body) > max_bytes:
            print('  ⚠️  動画サイズが大きすぎます。スキップします。')
            return None
        if len(body) < 1024 or b'ftyp' not in body[:64]:
            print('  ⚠️  ダウンロードした内容が動画ファイルではないようです。スキップします。')
            return None

        tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        tmp.write(body)
        tmp.close()
        print(f'  ✅ 動画ダウンロード完了（{len(body) / 1024 / 1024:.1f}MB・ブラウザ経由）')
        return tmp.name
    except Exception as e:
        print(f'  ❌ 動画ダウンロード失敗: {e}')
        return None


_SAMPLE_HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://www.dmm.co.jp/',
}
# DMM/FANZAは年齢確認を済ませていないとサンプル動画ページが
# 年齢確認画面にリダイレクトされ、動画情報が一切含まれなくなる。
_SAMPLE_HTTP_COOKIES = {
    'age_check_done': '1',
}


def _clean_content_id(content_id):
    return re.sub(r'[^0-9a-zA-Z]', '', content_id or '')


def build_direct_cdn_candidates(content_id):
    """
    content_id から、FANZA動画でよく使われる直リンクの命名規則を組み立てる。
    例: https://cc3001.dmm.co.jp/litevideo/freepv/r/rb/rbd00185/rbd00185_mhb_w.mp4
    実在しない場合もあるため、複数パターン・複数サフィックスを候補として返す。
    """
    cid = _clean_content_id(content_id)
    if not cid:
        return []

    suffixes = ['mhb_w', 'dmb_w', 'sm_w', 'mhb_s', 'dmb_s', 'sm_s']
    hosts = ['cc3001.dmm.co.jp', 'cc3001.dmm.com']
    candidates = []
    for host in hosts:
        for suf in suffixes:
            candidates.append(
                f'https://{host}/litevideo/freepv/{cid[0]}/{cid[0:3]}/{cid}/{cid}_{suf}.mp4'
            )
    return candidates


def resolve_litevideo_mp4_url(page_url, content_id=''):
    """
    DMM/FANZAの 'litevideo' URL (.../litevideo/-/part/=/cid=.../size=.../)は
    動画ファイルそのものではなく、プレイヤーを埋め込んだHTMLページのURL。
    1. まずcontent_idから直リンクの命名規則を推測して存在確認（速くて確実）
    2. ダメならHTMLページを取得して中から実際の.mp4 URLを抜き出す
    既に.mp4で終わるURLが渡された場合はそのまま返す。
    """
    if not page_url:
        return ''
    if page_url.lower().endswith('.mp4'):
        return page_url

    # --- 方式1: 命名規則からの直接推測 ---
    for cand in build_direct_cdn_candidates(content_id):
        try:
            r = requests.head(
                cand, timeout=8, allow_redirects=True,
                headers=_SAMPLE_HTTP_HEADERS, cookies=_SAMPLE_HTTP_COOKIES,
            )
            if r.status_code == 200 and int(r.headers.get('Content-Length', '0') or 0) > 10000:
                return cand
        except Exception:
            continue

    # --- 方式2: litevideoページをスクレイピングして中の.mp4 URLを抜く ---
    try:
        resp = requests.get(
            page_url, timeout=15,
            headers=_SAMPLE_HTTP_HEADERS, cookies=_SAMPLE_HTTP_COOKIES,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f'  ❌ litevideoページの取得に失敗: {e}')
        return ''

    # JSON内などでは "https:\/\/..." のようにスラッシュがエスケープされているため、
    # 正規表現にかける前に元に戻しておく。
    body = resp.text.replace('\\/', '/')
    candidates = re.findall(r'https?://[^\s\'"<>]+\.mp4', body)
    if not candidates:
        print('  ⚠️  litevideoページ内に.mp4のURLが見つかりませんでした'
              '（年齢確認画面にリダイレクトされたか、この商品にサンプル動画が無い可能性があります）。')
        return ''
    return candidates[0]


def download_sample_video(url, content_id='', max_bytes=200 * 1024 * 1024):
    """サンプル動画をダウンロードして一時ファイルに保存し、パスを返す。失敗時はNone。"""
    if not url:
        return None

    mp4_url = resolve_litevideo_mp4_url(url, content_id)
    if not mp4_url:
        return None

    try:
        resp = requests.get(
            mp4_url, stream=True, timeout=30,
            headers=_SAMPLE_HTTP_HEADERS, cookies=_SAMPLE_HTTP_COOKIES,
        )
        resp.raise_for_status()

        tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        total = 0
        with tmp:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    print('  ⚠️  動画サイズが大きすぎます。スキップします。')
                    os.unlink(tmp.name)
                    return None
                tmp.write(chunk)

        # Content-Typeヘッダーは信頼できないことがあるため、
        # 実ファイルの先頭バイト（mp4のftypボックス等）で簡易検証する。
        with open(tmp.name, 'rb') as f:
            head = f.read(32)
        if total < 1024 or (b'ftyp' not in head and not head.startswith(b'\x00\x00\x00')):
            print('  ⚠️  ダウンロードした内容が動画ファイルではないようです。スキップします。')
            os.unlink(tmp.name)
            return None

        print(f'  ✅ 動画ダウンロード完了（{total / 1024 / 1024:.1f}MB）')
        return tmp.name
    except Exception as e:
        print(f'  ❌ 動画ダウンロード失敗: {e}')
        return None


def upload_video_to_x(api_v1, filepath):
    """動画をXにチャンクアップロードし、処理完了を待ってmedia_idを返す。失敗時はNone。"""
    try:
        media = api_v1.media_upload(
            filename=filepath,
            media_category='tweet_video',
            chunked=True,
        )
        media_id = media.media_id

        # 動画は非同期処理されるため、processing_info が無くなるまで待つ
        processing_info = getattr(media, 'processing_info', None)
        while processing_info:
            state = processing_info.get('state')
            if state == 'succeeded':
                break
            if state == 'failed':
                print(f'  ❌ X側の動画処理に失敗: {processing_info}')
                return None
            wait_sec = processing_info.get('check_after_secs', 3)
            time.sleep(wait_sec)
            status = api_v1.get_media_upload_status(media_id)
            processing_info = getattr(status, 'processing_info', None)

        print(f'  ✅ X側へのアップロード完了（media_id={media_id}）')
        return media_id
    except Exception as e:
        print(f'  ❌ Xへの動画アップロード失敗: {e}')
        return None


def post_tweet_with_video(client_v2, text, media_id, reply_to_tweet_id=None):
    """media_idを添付して投稿する。reply_to_tweet_idを指定するとスレッド返信になる。成功時はtweet_idを返す。"""
    try:
        kwargs = dict(text=text, media_ids=[media_id])
        if reply_to_tweet_id:
            kwargs['in_reply_to_tweet_id'] = reply_to_tweet_id
        resp = client_v2.create_tweet(**kwargs)
        tweet_id = resp.data.get('id')
        print(f'  ✅ 投稿完了: https://x.com/i/web/status/{tweet_id}')
        return tweet_id
    except Exception as e:
        print(f'  ❌ 投稿失敗: {e}')
        return None


def post_to_x_api(api_v1, client_v2, product, thread_texts):
    """【API方式】スレッド投稿。1ポスト目に動画を添付し、2ポスト目を返信チェーンで続ける。"""
    sample_url = clean_url(product.get('sample_movie_url', ''))
    if not sample_url:
        print('  ⚠️  サンプル動画URLが無いためスキップします。')
        return False

    video_path = download_sample_video(sample_url, product.get('content_id', ''))
    if not video_path:
        return False

    try:
        media_id = upload_video_to_x(api_v1, video_path)
        if not media_id:
            return False

        post1_text = thread_texts[0]
        # 動画添付時はサンプルURLのテキスト行を除去（動画プレビューで代替される）
        post1_text = '\n'.join(
            line for line in post1_text.split('\n')
            if not line.startswith('▶ 無料サンプル:')
        )

        tweet_id = post_tweet_with_video(client_v2, post1_text, media_id)
        if not tweet_id:
            return False

        # 2ポスト目をスレッド返信として投稿
        if len(thread_texts) > 1:
            print('  📎 スレッド2ポスト目を投稿中...')
            try:
                resp2 = client_v2.create_tweet(
                    text=thread_texts[1],
                    in_reply_to_tweet_id=tweet_id,
                )
                tweet_id2 = resp2.data.get('id')
                print(f'  ✅ スレッド2ポスト目完了: https://x.com/i/web/status/{tweet_id2}')
            except Exception as e:
                print(f'  ⚠️  スレッド2ポスト目の投稿に失敗（1ポスト目は成功）: {e}')

        return True
    finally:
        try:
            os.unlink(video_path)
        except OSError:
            pass

# ================================================================
# 🌐 X（Twitter）ブラウザ自動操作投稿（API課金なし／要事前ログイン）
# ================================================================
#
# ⚠️ 公式APIを経由しない自動投稿は、X利用規約上「自動化されたアカウント操作」に
#    該当しうる行為です。アカウント凍結リスクを理解した上で利用してください。
# ⚠️ X側のHTML構造（data-testid等）は予告なく変更されるため、突然動かなくなる
#    可能性があります。動かなくなった場合はセレクタの見直しが必要です。

def get_browser_page(playwright):
    """保存済みセッションでログイン済み状態のページを開く。"""
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=X_SESSION_FILE)
    page = context.new_page()
    return browser, context, page


def post_to_x_browser(context, page, product, thread_texts):
    """【ブラウザ方式】スレッド投稿。1ポスト目に動画を添付し、2ポスト目を返信チェーンで続ける。"""
    sample_url = clean_url(product.get('sample_movie_url', ''))
    if not sample_url:
        print('  ⚠️  サンプル動画URLが無いためスキップします。')
        return False

    video_path = resolve_and_download_via_browser(context, sample_url, product.get('content_id', ''))
    if not video_path:
        video_path = download_sample_video(sample_url, product.get('content_id', ''))
    if not video_path:
        return False

    post1_text = '\n'.join(
        line for line in thread_texts[0].split('\n')
        if not line.startswith('▶ 無料サンプル:')
    )

    try:
        page.goto('https://x.com/compose/post', timeout=30000)
        page.wait_for_selector('div[data-testid="tweetTextarea_0"]', timeout=20000)

        if 'login' in page.url:
            print('  ❌ セッションが切れています。x_login_setup.py を再実行してください。')
            return False

        # 1ポスト目を入力
        page.click('div[data-testid="tweetTextarea_0"]')
        page.keyboard.type(post1_text, delay=10)

        # 動画添付
        file_input = page.locator('input[data-testid="fileInput"]')
        file_input.set_input_files(video_path)

        page.wait_for_selector('div[data-testid="attachments"] video', timeout=120000)

        # スレッドの2ポスト目を追加
        if len(thread_texts) > 1:
            print('  📎 スレッド2ポスト目を追加中...')
            try:
                # 「さらに追加」ボタンをクリックして2ポスト目の入力欄を出す
                add_btn = page.locator('[data-testid="addButton"]')
                add_btn.click(timeout=10000)
                page.wait_for_selector('div[data-testid="tweetTextarea_1"]', timeout=10000)
                page.click('div[data-testid="tweetTextarea_1"]')
                page.keyboard.type(thread_texts[1], delay=10)
            except Exception as e:
                print(f'  ⚠️  スレッド2ポスト目の入力に失敗（1ポスト目のみ投稿します）: {e}')

        # 投稿ボタンが有効になるまで待つ
        page.wait_for_function(
            """() => {
                const btn = document.querySelector('[data-testid="tweetButton"]')
                         || document.querySelector('[data-testid="tweetButtonInline"]');
                return btn && btn.getAttribute('aria-disabled') !== 'true';
            }""",
            timeout=120000
        )

        post_button = page.locator(
            '[data-testid="tweetButton"], [data-testid="tweetButtonInline"]'
        ).first
        post_button.click()

        page.wait_for_timeout(4000)
        print('  ✅ ブラウザ経由でスレッド投稿完了')
        return True

    except Exception as e:
        print(f'  ❌ ブラウザ投稿失敗: {e}')
        return False
    finally:
        try:
            os.unlink(video_path)
        except OSError:
            pass

# ================================================================
# 💾 保存先を決定
# ================================================================

def get_history_file():
    """過去に生成済みの品番（content_id）を記録するJSONファイルのパスを返す。
    HISTORY_FILE環境変数で明示指定可能。未指定時は保存先フォルダ（get_save_dirと同じ場所）に置く。
    """
    explicit = os.environ.get('HISTORY_FILE', '').strip()
    if explicit:
        return explicit
    return os.path.join(get_save_dir(), 'dmm_posted_history.json')


def load_posted_history():
    """過去に生成済みの品番一覧を読み込む。ファイルがなければ空集合を返す。"""
    path = get_history_file()
    if not os.path.exists(path):
        return set()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        if isinstance(data, dict):
            return set(data.get('content_ids', []))
    except Exception as e:
        print(f'⚠️  履歴ファイルの読み込みに失敗しました（{path}）: {e}')
    return set()


def save_posted_history(content_ids):
    """品番一覧を履歴ファイルに保存する（既存の履歴に追記）。"""
    path = get_history_file()
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(sorted(content_ids), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'⚠️  履歴ファイルの保存に失敗しました（{path}）: {e}')


def get_save_dir():
    """
    保存先の優先順位:
    1. 環境変数 SAVE_DIR で明示指定されたパス
    2. GitHub Actions 環境 (SAVE_TO_REPO=true) → カレントディレクトリ（後でoutputsへ移動）
    3. デスクトップ（ローカル実行時）
       - ~/Desktop
       - ~/OneDrive/Desktop
       - ~/OneDrive/デスクトップ
       - ~/デスクトップ
    4. カレントディレクトリ（フォールバック）
    """
    # 環境変数で明示指定
    explicit = os.environ.get('SAVE_DIR', '').strip()
    if explicit:
        Path(explicit).mkdir(parents=True, exist_ok=True)
        return explicit

    # GitHub Actions上での実行（outputs/フォルダに保存）
    if os.environ.get('SAVE_TO_REPO', '').lower() == 'true':
        out = Path('outputs')
        out.mkdir(exist_ok=True)
        return str(out)

    # ローカル実行時はデスクトップを探す
    try:
        home = Path.home()
        for path in [
            home / "Desktop",
            home / "OneDrive" / "Desktop",
            home / "OneDrive" / "デスクトップ",
            home / "デスクトップ",
        ]:
            if path.exists():
                return str(path)
    except Exception:
        pass

    return '.'


def save_posts(all_sections):
    save_dir  = get_save_dir()
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename  = f'dmm_x_posts_{timestamp}.txt'
    filepath  = os.path.join(save_dir, filename)

    total = sum(len(posts) for _, posts in all_sections)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"# DMMアフィリエイト X投稿文（スレッド形式）\n")
        f.write(f"# 生成日時: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# フロア: {DMM_FLOOR} / モード: {DMM_SORT_MODE}\n")
        f.write(f"# 価格フィルター: {DMM_PRICE_RANGE}\n")
        f.write(f"# 取得開始: {DMM_OFFSET}件目 / 各ソート{FETCH_COUNT}件\n")
        f.write(f"# 総投稿数: {total}件（各商品スレッド2ポスト構成）\n")
        f.write("=" * 60 + "\n\n")

        for sort_label, posts in all_sections:
            f.write(f"{'=' * 60}\n")
            f.write(f"【{sort_label}】{len(posts)}件\n")
            f.write(f"{'=' * 60}\n\n")

            for i, (product, thread) in enumerate(posts, 1):
                f.write(f"--- {sort_label} {i}/{len(posts)} ---\n")
                f.write(f"商品名: {product['title']}\n")
                f.write(f"文字数: ポスト1={x_text_length(thread[0])} / ポスト2={x_text_length(thread[1])}（上限各280文字）\n")

                url_status = {True: 'OK', False: 'NG（要確認）', None: '未確認'}.get(product.get('url_check'))
                f.write(f"URL確認: {product['affiliate_url']} [{url_status}]\n")

                if product.get('sample_movie_url'):
                    sample_status = {True: 'OK', False: 'NG（要確認）', None: '未確認'}.get(product.get('sample_check'))
                    f.write(f"サンプル動画: {product['sample_movie_url']} [{sample_status}]\n")

                f.write("-" * 40 + "\n")
                f.write("【ポスト1】\n")
                f.write(thread[0])
                f.write("\n\n")
                f.write("【ポスト2（スレッド続き）】\n")
                f.write(thread[1])
                f.write("\n\n")

    print(f'\n💾 保存完了！')
    print(f'📄 ファイル: {filepath}')
    return filepath

# ================================================================
# 🚀 メイン実行
# ================================================================

print(f'🛍️  DMMから商品情報を取得中（フロア: {DMM_FLOOR} / モード: {DMM_SORT_MODE}）...')

POSTED_HISTORY = load_posted_history()
print(f'📚 過去に生成済みの品番: {len(POSTED_HISTORY)}件（重複はスキップします）')
newly_posted_ids = set()

all_sections = []
processed_total = 0

for sort_key, sort_label in SORT_LIST:
    if processed_total >= MAX_PROCESS_COUNT:
        print(f'  ⏹  処理件数の上限（{MAX_PROCESS_COUNT}件）に達したため、以降のソートはスキップします。')
        break

    # ----------------------------------------------------------------
    # 価格フィルター使用時: MIN_PROCESS_COUNT に達するまで追加取得を繰り返す
    # ----------------------------------------------------------------
    remaining_quota = MAX_PROCESS_COUNT - processed_total
    # このソートで目指す件数（上限 and 最低保証の両方を考慮）
    sort_target = min(remaining_quota, MAX_PROCESS_COUNT // len(SORT_LIST) if len(SORT_LIST) > 1 else MAX_PROCESS_COUNT)
    # 最低保証件数：価格フィルターの有無にかかわらず常に適用
    # （サンプルURLなしスキップも含め、フィルターで大きく件数が落ちることに対応）
    min_target = min(MIN_PROCESS_COUNT, remaining_quota) if MIN_PROCESS_COUNT > 0 else 0

    products      = []
    # 新着順（-date）で開始番号が未指定（空欄）の場合は、ランダム開始位置ではなく
    # 1件目（＝最新データ）から検索する
    if sort_key == '-date' and not POST_START_INDEX_EXPLICIT:
        fetch_offset = 1
        print(f'  🆕 [{sort_label}] 開始番号が未指定のため、最新データ（1件目）から検索します。')
    else:
        fetch_offset = DMM_OFFSET
    fetch_hits    = DMM_HITS
    seen_ids      = set()
    MAX_FETCH_ROUNDS = 20  # 無限ループ防止: 最大20回まで追加取得（20件確保のため増量）

    # 価格フィルター・サンプルフィルター両方を考慮して最低件数まで追加取得を続ける
    effective_min = min_target if MIN_PROCESS_COUNT > 0 else 0

    for _round in range(MAX_FETCH_ROUNDS):
        raw_items = fetch_dmm_products(sort_key, sort_label, offset=fetch_offset, hits=fetch_hits)
        if not raw_items:
            print(f'  ⚠️  [{sort_label}] 商品が取得できませんでした。')
            break

        for item in raw_items:
            cid = item.get('content_id') or item.get('product_id') or item.get('affiliateURL', '')
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            p = parse_product(item)
            if p.get('content_id') and p['content_id'] in POSTED_HISTORY:
                print(f"    ⏭ 過去に生成済みのためスキップ: [{p['content_id']}] {p['title'][:30]}")
                continue
            if PRICE_RANGE_BOUNDS and not price_in_range(p):
                continue
            # サンプル動画URLがない商品はポスト1にURLを付けられないためスキップ
            if not p.get('sample_movie_url'):
                print(f"    ⏭ サンプルURLなしのためスキップ: {p['title'][:30]}")
                continue
            products.append(p)
            # 価格フィルターなし or min_target未達の場合は remaining_quota で止める
            # 価格フィルターあり & min_target 未達の場合は、まず min_target まで収集を続ける
            hard_limit = max(remaining_quota, effective_min) if effective_min > 0 else remaining_quota
            if len(products) >= hard_limit:
                break

        collected = len(products)
        # 価格フィルター・サンプルフィルターどちらでスキップされた場合も追加取得を継続する
        need_more = MIN_PROCESS_COUNT > 0 and collected < min_target
        print(f'  📦 [{sort_label}] 累計確保: {collected}件 / 目標最低: {min_target}件')

        if not need_more or len(raw_items) < fetch_hits:
            # 目標達成 or これ以上取得できるデータがない
            if len(raw_items) < fetch_hits and need_more:
                print(f'  ⚠️  [{sort_label}] DMM APIの取得可能件数の上限に達しました（{collected}件で終了）。')
            break

        # 取得範囲をずらして追加取得
        fetch_offset += fetch_hits
        print(f'  🔄 [{sort_label}] {min_target}件未満（{collected}件）のため、offset={fetch_offset} で追加取得します...')

    if PRICE_RANGE_BOUNDS:
        print(f'  💰 価格フィルター適用済み: 合計 {len(products)} 件確保')

    # 合計処理件数の上限を適用
    # min_target を優先：フィルターで件数が落ちた場合は min_target まで確保した分を守る
    if MIN_PROCESS_COUNT > 0:
        effective_cap = max(remaining_quota, min(min_target, len(products)))
    else:
        effective_cap = remaining_quota
    if len(products) > effective_cap:
        products = products[:effective_cap]

    if not products:
        print(f'  ⚠️  [{sort_label}] 価格条件に合う商品がありませんでした。スキップします。')
        continue

    print(f'  📝 [{sort_label}] 投稿文を生成中...')

    posts = []
    for p in products:
        thread = build_x_thread(p)
        posts.append((p, thread))
        if p.get('content_id'):
            newly_posted_ids.add(p['content_id'])
        total_chars = x_text_length(thread[0]) + x_text_length(thread[1])
        print(f"    ✅ [スレッド {x_text_length(thread[0])}+{x_text_length(thread[1])}文字] {p['title'][:30]}...")

    processed_total += len(posts)
    all_sections.append((sort_label, posts))

if not all_sections:
    print('❌ 商品が1件も取得できませんでした。')
    sys.exit(1)

first_label, first_posts = all_sections[0]
print('\n' + '=' * 60)
print(f'📋 投稿文プレビュー（{first_label} 1件目）')
print('=' * 60)
print('--- ポスト1 ---')
print(first_posts[0][1][0])
print('--- ポスト2（スレッド続き）---')
print(first_posts[0][1][1])
print('=' * 60)

save_posts(all_sections)

if newly_posted_ids:
    save_posted_history(POSTED_HISTORY | newly_posted_ids)
    print(f'📚 履歴を更新しました（今回 +{len(newly_posted_ids)}件 / 累計 {len(POSTED_HISTORY | newly_posted_ids)}件）: {get_history_file()}')

total = sum(len(p) for _, p in all_sections)
print(f'\n✅ 完了！合計 {total} 件の投稿文を保存しました。')

if AUTO_POST_TO_X:
    print('\n' + '=' * 60)
    print(f'🐦 X自動投稿を開始します（方式: {POST_METHOD} / 最大 {X_POST_LIMIT} 件）')
    print('=' * 60)

    # 全セクションを1本のリストにまとめ、サンプル動画があるものだけ対象にする
    flat_posts = [
        (product, thread)
        for _, posts in all_sections
        for product, thread in posts
        if product.get('sample_movie_url')
    ]

    posted_count = 0

    if POST_METHOD == 'api':
        api_v1, client_v2 = get_x_clients()
        for product, thread in flat_posts:
            if posted_count >= X_POST_LIMIT:
                break
            print(f"\n--- 投稿 {posted_count + 1}/{X_POST_LIMIT} ---")
            print(f"商品名: {product['title'][:40]}")
            success = post_to_x_api(api_v1, client_v2, product, thread)
            if success:
                posted_count += 1
                if posted_count < X_POST_LIMIT:
                    time.sleep(X_POST_INTERVAL_SEC)

    elif POST_METHOD == 'browser':
        with sync_playwright() as pw:
            browser, context, page = get_browser_page(pw)
            try:
                for product, thread in flat_posts:
                    if posted_count >= X_POST_LIMIT:
                        break
                    print(f"\n--- 投稿 {posted_count + 1}/{X_POST_LIMIT} ---")
                    print(f"商品名: {product['title'][:40]}")
                    success = post_to_x_browser(context, page, product, thread)
                    if success:
                        posted_count += 1
                        if posted_count < X_POST_LIMIT:
                            time.sleep(X_POST_INTERVAL_SEC)
            finally:
                browser.close()

    print(f'\n🐦 自動投稿完了: {posted_count}/{X_POST_LIMIT} 件成功')
else:
    print('テキストファイルを開いてXに手動投稿してください。')
    print('（動画埋め込みの自動投稿を行うには AUTO_POST_TO_X=true を設定してください）')
