"""
💰🐦 DMMアフィリエイト → X（Twitter）投稿文ジェネレーター
DMMから商品情報を取得し、X投稿用テキストをデスクトップまたは指定フォルダに保存します。

AUTO_POST_TO_X=true を設定すると、サンプル動画を実際にXへアップロードして
動画埋め込み付きで自動投稿します。
  - POST_METHOD=browser（デフォルト）: Playwrightでブラウザを直接操作。API課金なし。
                                          要：事前にx_login_setup.pyでログインセッション作成。
  - POST_METHOD=api    : 公式X API（要キー・課金あり）。
未設定時は従来どおりテキストファイル保存のみで完全無料で動作します。
"""

import os
import sys
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
#    DMM_SORT_MODE=both（デフォルト）→ 新着20件 ＋ 人気20件 = 計40件を1ファイルに保存
#    DMM_SORT_MODE=date              → 新着順のみ20件
#    DMM_SORT_MODE=rank              → 人気順のみ20件
# ----------------------------------------------------------------
DMM_SORT_MODE = os.environ.get('DMM_SORT_MODE', 'both').lower()

SORT_TARGETS = {
    'both': [('-date', '新着順'), ('-rank', '人気順')],
    'date': [('-date', '新着順')],
    'rank': [('-rank', '人気順')],
}
SORT_LIST = SORT_TARGETS.get(DMM_SORT_MODE, SORT_TARGETS['both'])

# ----------------------------------------------------------------
# 🎲 取得開始位置（環境変数未設定時はランダム: 1〜480）
# ----------------------------------------------------------------
_raw_start = os.environ.get('POST_START_INDEX', '')
if _raw_start.strip().isdigit():
    POST_START_INDEX = int(_raw_start.strip())
    print(f'📌 指定された取得開始番号: {POST_START_INDEX}')
else:
    POST_START_INDEX = random.randint(1, 480)
    print(f'🎲 ランダム取得開始番号: {POST_START_INDEX}')

FETCH_COUNT = 100
DMM_OFFSET  = POST_START_INDEX
DMM_HITS    = FETCH_COUNT

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
    'videoa': '#FANZA #DMM #アダルト #PR #新着',
    'videoc': '#FANZA #DMM #素人 #PR #新着',
    'anime':  '#FANZA #DMM #アニメ #PR #新着',
    'doujin': '#FANZA #DMM #同人 #PR #新着',
    'comic':  '#DMM #電子書籍 #漫画 #PR #新着',
    'goods':  '#DMM #グッズ #PR #新着',
    'default': '#DMM #PR #新着',
}

COPY_TEMPLATES = [
    "今すぐチェック！期間限定で見逃せない作品が登場🔥",
    "ファン待望の最新作がついに配信スタート✨",
    "クオリティに驚くはず…一度見たら止まらない😍",
    "話題沸騰中🔥今だけポイントバックもあるかも！",
    "これは保存確定の神作品👑早めにゲットしておこう！",
    "高画質でいつでも・どこでも楽しめる📱💻",
    "無料試し読み／体験あり！まずはチェックを👀",
    "このクオリティでこの価格はお得すぎる…💸",
    "レビュー高評価続出！納得のクオリティをぜひ体験して✨",
    "見逃す前にダウンロード！ストリーミングにも対応🎬",
    "今日のおすすめはこれ！絶対に後悔しない一本📌",
    "ランキング上位の人気作！話題になる前にチェック🏆",
    "新作キター！気になってた人はこのタイミングで👏",
    "じっくり堪能できるハイクオリティ作品です🎨",
    "気になるあの設定…ぜひ本編で確認してみて👀",
    "深夜のお供にぴったりな一本、見つけました🌙",
    "ファン必見！じっくり集中して楽しみたい作品です🔥",
    "細部までこだわり抜いたクオリティに注目です✨",
    "今だけのお得な価格、チェックは早めがおすすめ⏰",
    "繰り返し見たくなる魅力たっぷりの一本です💕",
    "話題のジャンルが気になる方はこちらをチェック🔍",
    "ハマる人が続出中…まずは詳細を見てみて😳",
    "コレクションに加えたい一本、見つけた方はラッキー🎁",
    "じわじわ人気が広がっている注目作はこちら📈",
    "ここでしか見られない展開、要チェックです👇",
]

def get_copy():
    return random.choice(COPY_TEMPLATES)


# ----------------------------------------------------------------
# ✨ おすすめポイント自動生成（DMM APIのデータから）
#    ジャンル・女優・メーカー・レビュー評価・価格などを組み合わせて、
#    商品ごとに違った訴求文を作る。固定文のランダム抽選より具体的になる。
# ----------------------------------------------------------------

_OPENERS = ["注目ポイントは", "イチオシは", "見どころは", "ここが魅力："]
_GENRE_PHRASES = ["「{g}」好きにはたまらない一本", "「{g}」要素がしっかり詰まった内容", "「{g}」ジャンルの中でも完成度の高い一作"]
_ACTOR_PHRASES = ["{a}の魅力を存分に堪能できる", "{a}出演作をお探しの方は必見", "{a}ファンなら見逃せない"]
_REVIEW_PHRASES = ["レビュー平均{avg}（{count}件）の高評価作品", "★{avg}の高評価レビューが多数"]
_MAKER_PHRASES = ["{m}制作ならではのクオリティ"]
_PRICE_PHRASES = ["{p}でこの内容はお得感あり"]
_FALLBACK_PHRASES = [
    "高画質サンプルで雰囲気をチェックしてから購入できる",
    "気になる方はまずサンプル動画から確認を",
    "じっくり本編を楽しみたい一本",
]


def build_recommend_points(product, max_len=60):
    """商品データ（ジャンル・女優・メーカー・レビュー・価格）から
    'おすすめポイント'を要約した一文を作る。情報が無い項目はスキップする。
    """
    parts = []

    if product.get('review_avg'):
        avg = product['review_avg']
        count = product.get('review_count')
        if count:
            parts.append(random.choice(_REVIEW_PHRASES).format(avg=avg, count=count))
        else:
            parts.append(random.choice(_REVIEW_PHRASES).format(avg=avg, count=''))

    if product.get('genres'):
        parts.append(random.choice(_GENRE_PHRASES).format(g=product['genres'][0]))

    if product.get('actors'):
        parts.append(random.choice(_ACTOR_PHRASES).format(a=product['actors'][0]))

    if not parts and product.get('maker'):
        parts.append(random.choice(_MAKER_PHRASES).format(m=product['maker']))

    if not parts:
        parts.append(random.choice(_FALLBACK_PHRASES))

    point = parts[0]
    opener = random.choice(_OPENERS)
    text = f"{opener}{point}🔥" if not point.endswith(('✨', '🔥', '👀')) else f"{opener}{point}"

    if len(text) > max_len:
        text = text[:max_len - 1] + '…'
    return text


# ----------------------------------------------------------------
# 🔗 URL確認・短縮
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
    """TinyURL（APIキー不要）でURLを短縮する。失敗時は元のURLをそのまま返す。"""
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

def fetch_dmm_products(sort_key, sort_label):
    service, floor_name = FLOOR_SERVICE_MAP.get(DMM_FLOOR, ('digital', 'videoa'))
    params = {
        'api_id':       DMM_API_ID,
        'affiliate_id': DMM_AFFILIATE_ID,
        'site':         'FANZA',
        'service':      service,
        'floor':        floor_name,
        'hits':         DMM_HITS,
        'offset':       DMM_OFFSET,
        'sort':         sort_key,
        'output':       'json',
    }
    print(f'\n  [{sort_label}] 取得範囲: {DMM_OFFSET}件目〜{DMM_OFFSET + DMM_HITS - 1}件目')
    try:
        resp = requests.get(f'{DMM_API_BASE}/ItemList', params=params, timeout=15)
        data = resp.json()
        items = data.get('result', {}).get('items', [])
        if isinstance(items, dict):
            items = items.get('item', [])
        if items:
            url_str = items[0].get('affiliateURL', '')
            print(f"  URLの総文字数: {len(url_str)} / 末尾10文字: {url_str[-10:]}")
        print(f'  ✅ {len(items)} 件取得しました。')
        return items
    except Exception as e:
        print(f'  ❌ DMM APIエラー: {e}')
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
    genres = [g.get('name', '') for g in (item.get('iteminfo', {}).get('genre') or [])][:3]
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


def build_x_post(product):
    hashtags  = HASHTAG_MAP.get(DMM_FLOOR, HASHTAG_MAP['default'])
    url       = clean_url(product['affiliate_url'])
    sample    = clean_url(product.get('sample_movie_url', ''))

    # --- URL確認 ---
    url_ok    = check_url(url) if url else None
    sample_ok = check_url(sample) if sample else None
    if url and url_ok is False:
        print(f"    ⚠️  アフィリエイトURLにアクセスできませんでした: {url}")
    if sample and sample_ok is False:
        print(f"    ⚠️  サンプル動画URLにアクセスできませんでした: {sample}")

    # --- サンプル動画URLを短縮 ---
    sample_short = shorten_url(sample) if sample else ''

    # save_posts()で出力するため商品データに確認結果を残しておく
    product['url_check']        = url_ok
    product['sample_url_short'] = sample_short
    product['sample_check']     = sample_ok

    # --- おすすめポイントを生成（DMMデータから） ---
    copy      = build_recommend_points(product)
    act_tags  = actor_tags(product['actors'])

    title = product['title']
    if len(title) > 35:
        title = title[:35] + '…'

    lines = []
    lines.append(f"🎬 {title}")
    lines.append('')
    lines.append(copy)
    lines.append('')
    if product['price']:
        lines.append(f"💰 価格: {product['price']}")
    if act_tags:
        lines.append(f"👤 {act_tags}")
    if product['genres']:
        lines.append(f"🎞 {genre_tags(product['genres'])}")
    lines.append('')
    lines.append(url)
    if sample_short:
        lines.append(f"▶ サンプル動画: {sample_short}")
    lines.append(hashtags)

    text = '\n'.join(lines)

    if len(text) > 280:
        lines2 = []
        lines2.append(f"🎬 {title}")
        lines2.append('')
        lines2.append(copy)
        lines2.append('')
        if product['price']:
            lines2.append(f"💰 {product['price']}")
        if act_tags:
            lines2.append(f"👤 {act_tags}")
        if product['genres']:
            lines2.append(f"🎞 {genre_tags(product['genres'][:2])}")
        lines2.append('')
        lines2.append(url)
        if sample_short:
            lines2.append(f"▶ サンプル: {sample_short}")
        lines2.append(hashtags)
        text = '\n'.join(lines2)

    # それでも280文字を超える場合は、おすすめポイントを短くして再調整
    if len(text) > 280:
        over = len(text) - 280
        short_copy = copy[:max(10, len(copy) - over - 1)] + '…'
        text = text.replace(copy, short_copy, 1)

    return text

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


def post_tweet_with_video(client_v2, text, media_id):
    """media_idを添付して投稿する。成功時はtweet_idを返す。"""
    try:
        resp = client_v2.create_tweet(text=text, media_ids=[media_id])
        tweet_id = resp.data.get('id')
        print(f'  ✅ 投稿完了: https://x.com/i/web/status/{tweet_id}')
        return tweet_id
    except Exception as e:
        print(f'  ❌ 投稿失敗: {e}')
        return None


def post_to_x_api(api_v1, client_v2, product, text):
    """【API方式】1商品ぶんをダウンロード→アップロード→投稿まで一気に行う。"""
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

        # 動画をポストに埋め込む場合、本文中のURL羅列が二重表示にならないよう
        # 「▶ サンプル動画: URL」の行は外す（動画自体がプレビュー表示されるため）
        text_for_post = '\n'.join(
            line for line in text.split('\n')
            if not line.startswith('▶ サンプル動画:') and not line.startswith('▶ サンプル:')
        )

        return post_tweet_with_video(client_v2, text_for_post, media_id) is not None
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


def post_to_x_browser(context, page, product, text):
    """【ブラウザ方式】動画をダウンロードし、Xの投稿画面を直接操作して投稿する。"""
    sample_url = clean_url(product.get('sample_movie_url', ''))
    if not sample_url:
        print('  ⚠️  サンプル動画URLが無いためスキップします。')
        return False

    # まず実ブラウザ(同じcontext)経由で動画を取りに行く。
    # requestsライブラリ単体だとCDN側のbot対策で弾かれることがあるため。
    video_path = resolve_and_download_via_browser(context, sample_url, product.get('content_id', ''))
    if not video_path:
        # 念のためrequestsベースの方式もフォールバックとして試す
        video_path = download_sample_video(sample_url, product.get('content_id', ''))
    if not video_path:
        return False

    text_for_post = '\n'.join(
        line for line in text.split('\n')
        if not line.startswith('▶ サンプル動画:') and not line.startswith('▶ サンプル:')
    )

    try:
        page.goto('https://x.com/compose/post', timeout=30000)
        page.wait_for_selector('div[data-testid="tweetTextarea_0"]', timeout=20000)

        # ログインが切れている場合はここで弾く
        if 'login' in page.url:
            print('  ❌ セッションが切れています。x_login_setup.py を再実行してください。')
            return False

        # 本文入力
        page.click('div[data-testid="tweetTextarea_0"]')
        page.keyboard.type(text_for_post, delay=10)

        # 動画ファイルを添付（隠しinput[type=file]に直接ファイルパスを渡す）
        file_input = page.locator('input[data-testid="fileInput"]')
        file_input.set_input_files(video_path)

        # アップロード・X側のエンコード処理完了を待つ
        page.wait_for_selector('div[data-testid="attachments"] video', timeout=120000)
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

        # 投稿完了の反映を待つ（コンポーズ画面が閉じる/タイムラインに戻る）
        page.wait_for_timeout(4000)
        print('  ✅ ブラウザ経由で投稿完了')
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
        f.write(f"# DMMアフィリエイト X投稿文\n")
        f.write(f"# 生成日時: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# フロア: {DMM_FLOOR} / モード: {DMM_SORT_MODE}\n")
        f.write(f"# 価格フィルター: {DMM_PRICE_RANGE}\n")
        f.write(f"# 取得開始: {DMM_OFFSET}件目 / 各ソート{FETCH_COUNT}件\n")
        f.write(f"# 総投稿数: {total}件\n")
        f.write("=" * 60 + "\n\n")

        for sort_label, posts in all_sections:
            f.write(f"{'=' * 60}\n")
            f.write(f"【{sort_label}】{len(posts)}件\n")
            f.write(f"{'=' * 60}\n\n")

            for i, (product, text) in enumerate(posts, 1):
                f.write(f"--- {sort_label} {i}/{len(posts)} ---\n")
                f.write(f"商品名: {product['title']}\n")
                f.write(f"文字数: {len(text)}文字\n")

                url_status = {True: 'OK', False: 'NG（要確認）', None: '未確認'}.get(product.get('url_check'))
                f.write(f"URL確認: {product['affiliate_url']} [{url_status}]\n")

                if product.get('sample_movie_url'):
                    sample_status = {True: 'OK', False: 'NG（要確認）', None: '未確認'}.get(product.get('sample_check'))
                    f.write(f"サンプル動画(元URL): {product['sample_movie_url']} [{sample_status}]\n")
                    if product.get('sample_url_short'):
                        f.write(f"サンプル動画(短縮URL): {product['sample_url_short']}\n")
                f.write("-" * 40 + "\n")
                f.write(text)
                f.write("\n\n")

    print(f'\n💾 保存完了！')
    print(f'📄 ファイル: {filepath}')
    return filepath

# ================================================================
# 🚀 メイン実行
# ================================================================

print(f'🛍️  DMMから商品情報を取得中（フロア: {DMM_FLOOR} / モード: {DMM_SORT_MODE}）...')

all_sections = []

for sort_key, sort_label in SORT_LIST:
    raw_items = fetch_dmm_products(sort_key, sort_label)
    if not raw_items:
        print(f'  ⚠️  [{sort_label}] 商品が取得できませんでした。スキップします。')
        continue

    products = [parse_product(item) for item in raw_items]

    if PRICE_RANGE_BOUNDS:
        before_count = len(products)
        products = [p for p in products if price_in_range(p)]
        print(f'  💰 価格フィルター適用: {before_count}件 → {len(products)}件')

    if not products:
        print(f'  ⚠️  [{sort_label}] 価格条件に合う商品がありませんでした。スキップします。')
        continue

    print(f'  📝 [{sort_label}] 投稿文を生成中...')

    posts = []
    for p in products:
        text = build_x_post(p)
        posts.append((p, text))
        print(f"    ✅ [{len(text)}文字] {p['title'][:30]}...")

    all_sections.append((sort_label, posts))

if not all_sections:
    print('❌ 商品が1件も取得できませんでした。')
    sys.exit(1)

first_label, first_posts = all_sections[0]
print('\n' + '=' * 60)
print(f'📋 投稿文プレビュー（{first_label} 1件目）')
print('=' * 60)
print(first_posts[0][1])
print('=' * 60)

save_posts(all_sections)

total = sum(len(p) for _, p in all_sections)
print(f'\n✅ 完了！合計 {total} 件の投稿文を保存しました。')

if AUTO_POST_TO_X:
    print('\n' + '=' * 60)
    print(f'🐦 X自動投稿を開始します（方式: {POST_METHOD} / 最大 {X_POST_LIMIT} 件）')
    print('=' * 60)

    # 全セクションを1本のリストにまとめ、サンプル動画があるものだけ対象にする
    flat_posts = [
        (product, text)
        for _, posts in all_sections
        for product, text in posts
        if product.get('sample_movie_url')
    ]

    posted_count = 0

    if POST_METHOD == 'api':
        api_v1, client_v2 = get_x_clients()
        for product, text in flat_posts:
            if posted_count >= X_POST_LIMIT:
                break
            print(f"\n--- 投稿 {posted_count + 1}/{X_POST_LIMIT} ---")
            print(f"商品名: {product['title'][:40]}")
            success = post_to_x_api(api_v1, client_v2, product, text)
            if success:
                posted_count += 1
                if posted_count < X_POST_LIMIT:
                    time.sleep(X_POST_INTERVAL_SEC)

    elif POST_METHOD == 'browser':
        with sync_playwright() as pw:
            browser, context, page = get_browser_page(pw)
            try:
                for product, text in flat_posts:
                    if posted_count >= X_POST_LIMIT:
                        break
                    print(f"\n--- 投稿 {posted_count + 1}/{X_POST_LIMIT} ---")
                    print(f"商品名: {product['title'][:40]}")
                    success = post_to_x_browser(context, page, product, text)
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
