"""
📝 DMMアフィリエイト → はてなブログ 自動投稿スクリプト
DMMから商品情報を取得し、SEOを意識したブログ記事を自動生成してはてなブログに投稿します。

【設計方針】
- 1商品 = 1記事（女優名・作品名をタイトルに入れてロングテールSEOを狙う）
- 記事内にアフィリエイトリンクを自然な形で複数配置
- サービス新規報酬（新規会員登録）を誘導する導線を記事末尾に追加
- はてなブログ AtomPub API で投稿（APIキーのみで完結・OAuth不要）

【必要な環境変数（GitHub Secrets）】
  DMM_API_ID        : DMM API ID
  DMM_AFFILIATE_ID  : DMM アフィリエイトID
  HATENA_ID         : はてなのユーザーID（例: yourname）
  HATENA_BLOG_ID    : ブログID（例: yourname.hatenablog.com）
  HATENA_API_KEY    : はてなブログ AtomPub APIキー
                      （はてなブログ管理画面 → 設定 → 詳細設定 → AtomPub で確認）

【オプション環境変数】
  DMM_FLOOR         : フロア（デフォルト: videoa）
  DMM_SORT_MODE     : ソートモード both/date/rank（デフォルト: rank）
  DMM_PRICE_RANGE   : 価格フィルター（デフォルト: all）
  BLOG_POST_LIMIT   : 1回の実行で投稿する最大記事数（デフォルト: 5）
  POST_START_INDEX  : 取得開始番号（デフォルト: ランダム）
  BLOG_DRAFT        : true にすると下書き保存のみ（確認用・デフォルト: false）
"""

import os
import sys
import re
import time
import random
import datetime
import requests
import base64
from xml.etree import ElementTree as ET

# ================================================================
# ⚙️  設定（環境変数から読み込み）
# ================================================================

DMM_API_ID       = os.environ.get('DMM_API_ID', '')
DMM_AFFILIATE_ID = os.environ.get('DMM_AFFILIATE_ID', '')

if not DMM_API_ID or not DMM_AFFILIATE_ID:
    print('❌ 環境変数 DMM_API_ID / DMM_AFFILIATE_ID が設定されていません。')
    sys.exit(1)

HATENA_ID      = os.environ.get('HATENA_ID', '')
HATENA_BLOG_ID = os.environ.get('HATENA_BLOG_ID', '')
HATENA_API_KEY = os.environ.get('HATENA_API_KEY', '')

if not HATENA_ID or not HATENA_BLOG_ID or not HATENA_API_KEY:
    print('❌ 環境変数 HATENA_ID / HATENA_BLOG_ID / HATENA_API_KEY が設定されていません。')
    print('   はてなブログ管理画面 → 設定 → 詳細設定 → AtomPub でAPIキーを確認してください。')
    sys.exit(1)

print('✅ 認証情報を読み込みました。')

DMM_FLOOR      = os.environ.get('DMM_FLOOR', 'videoa')
DMM_SORT_MODE  = os.environ.get('DMM_SORT_MODE', 'rank').lower()
DMM_PRICE_RANGE = os.environ.get('DMM_PRICE_RANGE', 'all').strip().lower()
BLOG_POST_LIMIT = int(os.environ.get('BLOG_POST_LIMIT', '5'))
BLOG_DRAFT      = os.environ.get('BLOG_DRAFT', 'false').strip().lower() == 'true'

# 取得開始位置（ランダムでページをばらけさせる）
_raw_start = os.environ.get('POST_START_INDEX', '')
if _raw_start.strip().isdigit():
    POST_START_INDEX = int(_raw_start.strip())
else:
    POST_START_INDEX = random.randint(1, 300)

print(f'📌 取得開始番号: {POST_START_INDEX}')
print(f'📝 投稿上限: {BLOG_POST_LIMIT} 件')
print(f'📂 フロア: {DMM_FLOOR} / ソート: {DMM_SORT_MODE}')
print(f'✏️  下書きモード: {"ON（下書き保存のみ）" if BLOG_DRAFT else "OFF（公開投稿）"}')

DMM_API_BASE = 'https://api.dmm.com/affiliate/v3'

FLOOR_SERVICE_MAP = {
    'videoa':  ('digital', 'videoa'),
    'videoc':  ('digital', 'videoc'),
    'anime':   ('digital', 'anime'),
    'doujin':  ('doujin',  'digital_doujin'),
    'comic':   ('ebook',   'comic'),
    'goods':   ('mono',    'goods'),
}

# ================================================================
# 🔧 DMM API
# ================================================================

def fetch_dmm_products(sort_key='-rank', hits=20, offset=1):
    service, floor_name = FLOOR_SERVICE_MAP.get(DMM_FLOOR, ('digital', 'videoa'))
    params = {
        'api_id':       DMM_API_ID,
        'affiliate_id': DMM_AFFILIATE_ID,
        'site':         'FANZA',
        'service':      service,
        'floor':        floor_name,
        'hits':         hits,
        'offset':       offset,
        'sort':         sort_key,
        'output':       'json',
    }
    try:
        resp = requests.get(f'{DMM_API_BASE}/ItemList', params=params, timeout=15)
        data = resp.json()
        items = data.get('result', {}).get('items', [])
        if isinstance(items, dict):
            items = items.get('item', [])
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
                price_str = f'¥{price_num:,}'

    actors  = [a.get('name', '') for a in (item.get('iteminfo', {}).get('actress') or [])][:5]
    genres  = [g.get('name', '') for g in (item.get('iteminfo', {}).get('genre') or [])][:5]
    series  = ((item.get('iteminfo', {}).get('series') or [{}])[0]).get('name', '')
    maker   = ((item.get('iteminfo', {}).get('maker') or [{}])[0]).get('name', '')
    label   = ((item.get('iteminfo', {}).get('label') or [{}])[0]).get('name', '')
    director = ((item.get('iteminfo', {}).get('director') or [{}])[0]).get('name', '')

    # サムネイル画像
    image_url = ''
    img = item.get('imageURL', {})
    if img:
        image_url = img.get('large', '') or img.get('small', '')

    # サンプル画像
    sample_images = []
    sample_img = item.get('sampleImageURL', {})
    if sample_img:
        imgs = sample_img.get('sample_l', {})
        if isinstance(imgs, dict):
            imgs = imgs.get('image', [])
        sample_images = imgs[:4] if imgs else []

    # サンプル動画
    sample_movie_url = ''
    smv = item.get('sampleMovieURL', {})
    if smv:
        for key in ['size_720_480', 'size_644_414', 'size_560_360']:
            val = smv.get(key, '')
            if val:
                sample_movie_url = val.strip()
                break

    # レビュー
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

    content_id = item.get('content_id', '') or item.get('product_id', '')
    date_str   = item.get('date', '')

    return {
        'title':            title,
        'affiliate_url':    affiliate_url,
        'price':            price_str,
        'price_num':        price_num,
        'actors':           [a for a in actors if a],
        'genres':           [g for g in genres if g],
        'series':           series,
        'maker':            maker,
        'label':            label,
        'director':         director,
        'image_url':        image_url,
        'sample_images':    sample_images,
        'sample_movie_url': sample_movie_url,
        'content_id':       content_id,
        'review_avg':       review_avg,
        'review_count':     review_count,
        'date':             date_str,
    }

# ================================================================
# ✍️  ブログ記事HTML生成
# ================================================================

def star_rating_html(avg):
    """レビュー平均点を星表示に変換する（5段階）"""
    if avg is None:
        return ''
    full  = int(avg)
    half  = 1 if (avg - full) >= 0.5 else 0
    empty = 5 - full - half
    return '★' * full + ('☆' if half else '') + '☆' * empty


def build_article_title(product):
    """SEOを意識した記事タイトルを生成する。
    ロングテールキーワード（女優名 + 作品名 + レビュー）を含める。
    """
    actors = product['actors']
    title  = product['title']

    if actors:
        actor_str = '・'.join(actors[:2])
        return f'【FANZA】{actor_str}「{title[:30]}」レビュー・サンプルあり'
    else:
        return f'【FANZA】{title[:40]}｜レビュー・サンプル動画あり'


def build_article_html(product):
    """記事本文のHTMLを生成する。"""
    url         = product['affiliate_url']
    title       = product['title']
    actors      = product['actors']
    genres      = product['genres']
    price       = product['price']
    price_num   = product['price_num']
    maker       = product['maker']
    series      = product['series']
    label       = product['label']
    director    = product['director']
    image_url   = product['image_url']
    sample_imgs = product['sample_images']
    sample_mv   = product['sample_movie_url']
    review_avg  = product['review_avg']
    review_count = product['review_count']
    date_str    = product['date']

    parts = []

    # ── アフィリエイト表記（景品表示法対策）──
    parts.append('<p style="font-size:12px;color:#888;">※本記事にはアフィリエイトリンクが含まれます。</p>')

    # ── 導入文 ──
    actor_str = '・'.join(actors) if actors else '出演者情報なし'
    intro_templates = [
        f'今回はFANZAで注目の作品、<strong>{title}</strong>をご紹介します。出演は{actor_str}。サンプル動画も用意されているので、購入前に内容を確認できます。',
        f'<strong>{title}</strong>のレビューです。{actor_str}が出演するこの作品、FANZAでサンプル動画が公開されています。気になる方はまずサンプルだけでも確認してみてください。',
        f'FANZAで人気の<strong>{title}</strong>をご紹介します。{actor_str}の作品です。無料サンプル動画あり、購入前に内容をチェックできます。',
    ]
    parts.append(f'<p>{random.choice(intro_templates)}</p>')

    # ── 作品情報テーブル ──
    parts.append('<h2>作品情報</h2>')
    parts.append('<table border="1" style="border-collapse:collapse;width:100%;max-width:600px;">')
    parts.append('<tbody>')

    def tr(label_text, value):
        if not value:
            return ''
        return f'<tr><th style="padding:8px;background:#f5f5f5;width:120px;text-align:left;">{label_text}</th><td style="padding:8px;">{value}</td></tr>'

    # ジャケ写画像
    if image_url:
        parts.append(f'<tr><th style="padding:8px;background:#f5f5f5;width:120px;text-align:left;">ジャケット</th><td style="padding:8px;"><a href="{url}" target="_blank" rel="nofollow noopener"><img src="{image_url}" alt="{title}" style="max-width:200px;height:auto;" /></a></td></tr>')

    parts.append(tr('タイトル', title))
    if actors:
        actor_links = '　'.join(actors)
        parts.append(tr('出演', actor_links))
    parts.append(tr('メーカー', maker))
    parts.append(tr('レーベル', label))
    parts.append(tr('シリーズ', series))
    parts.append(tr('監督', director))

    if genres:
        parts.append(tr('ジャンル', '　'.join(genres)))

    if price:
        price_note = ''
        if price_num:
            if price_num <= 500:
                price_note = '（ワンコイン以下！）'
            elif price_num <= 1000:
                price_note = '（缶ビール数本分）'
            elif price_num <= 2000:
                price_note = '（映画1本分以下）'
        parts.append(tr('価格', f'<strong>{price}</strong>{price_note}'))

    parts.append(tr('配信開始', date_str))

    if review_avg is not None:
        stars = star_rating_html(review_avg)
        count_str = f'（{review_count:,}件）' if review_count else ''
        parts.append(tr('レビュー', f'{stars} {review_avg:.1f}{count_str}'))

    parts.append('</tbody></table>')

    # ── 購入ボタン（テーブル直下に1つ目） ──
    parts.append(f'''
<p style="margin:20px 0;">
  <a href="{url}" target="_blank" rel="nofollow noopener"
     style="display:inline-block;padding:14px 28px;background:#e60033;color:#fff;
            font-weight:bold;font-size:16px;border-radius:4px;text-decoration:none;">
    🛒 FANZAで詳細・購入はこちら
  </a>
</p>
''')

    # ── サンプル動画 ──
    if sample_mv:
        parts.append('<h2>無料サンプル動画</h2>')
        parts.append(f'<p>購入前に無料でサンプル動画を確認できます。</p>')
        # litevideo埋め込み
        parts.append(f'<p><a href="{sample_mv}" target="_blank" rel="nofollow noopener">▶ サンプル動画を見る（無料）</a></p>')

    # ── サンプル画像 ──
    if sample_imgs:
        parts.append('<h2>サンプル画像</h2>')
        parts.append('<div style="display:flex;flex-wrap:wrap;gap:8px;">')
        for img in sample_imgs:
            parts.append(f'<a href="{url}" target="_blank" rel="nofollow noopener"><img src="{img}" alt="{title} サンプル" style="max-width:200px;height:auto;" /></a>')
        parts.append('</div>')

    # ── レビュー・おすすめポイント ──
    parts.append('<h2>おすすめポイント</h2>')

    recommend_points = []
    if review_avg and review_avg >= 4.0:
        recommend_points.append(f'レビュー平均<strong>{review_avg:.1f}点</strong>と高評価を獲得している作品です。')
    if actors:
        recommend_points.append(f'<strong>{"・".join(actors[:2])}</strong>が出演しています。')
    if price_num and price_num <= 2000:
        recommend_points.append(f'価格は<strong>{price}</strong>とリーズナブル。映画1本分以下で楽しめます。')
    if sample_mv:
        recommend_points.append('無料サンプル動画あり。購入前に内容を確認できるので安心です。')

    # 汎用ポイントを追加
    generic_points = [
        'FANZAは購入後すぐにダウンロード・ストリーミング視聴が可能です。',
        'FANZAは無料会員登録だけでサンプル動画が全作品見られます。',
        '一度購入すればいつでも何度でも視聴できます。',
    ]
    for pt in generic_points:
        if pt not in recommend_points:
            recommend_points.append(pt)

    parts.append('<ul>')
    for pt in recommend_points[:5]:
        parts.append(f'<li>{pt}</li>')
    parts.append('</ul>')

    # ── 2つ目の購入ボタン ──
    parts.append(f'''
<p style="margin:20px 0;">
  <a href="{url}" target="_blank" rel="nofollow noopener"
     style="display:inline-block;padding:14px 28px;background:#e60033;color:#fff;
            font-weight:bold;font-size:16px;border-radius:4px;text-decoration:none;">
    🛒 FANZAで購入・詳細を見る
  </a>
</p>
''')

    # ── FANZAへの新規登録誘導（サービス新規報酬を狙う重要セクション） ──
    parts.append('<h2>FANZAをまだ使ったことがない方へ</h2>')
    parts.append(f'''
<div style="background:#fff8e1;border:2px solid #ffc107;border-radius:8px;padding:16px;margin:16px 0;">
  <p style="margin:0 0 8px;font-weight:bold;">💡 FANZAは無料会員登録で使えます</p>
  <ul style="margin:0;padding-left:20px;">
    <li>会員登録は<strong>完全無料</strong></li>
    <li>登録するだけで<strong>全作品のサンプル動画が見放題</strong></li>
    <li>購入はクレジットカード・ポイントなど複数の支払い方法に対応</li>
    <li>購入後すぐにダウンロード・ストリーミング視聴できる</li>
  </ul>
  <p style="margin:12px 0 0;">
    <a href="{url}" target="_blank" rel="nofollow noopener"
       style="display:inline-block;padding:12px 24px;background:#ff6f00;color:#fff;
              font-weight:bold;font-size:15px;border-radius:4px;text-decoration:none;">
      🎫 FANZAに無料登録してサンプルを見る
    </a>
  </p>
</div>
''')

    # ── 関連ジャンルタグ ──
    if genres:
        genre_tags = '　'.join([f'<a href="https://www.dmm.co.jp/search/=/searchstr={requests.utils.quote(g)}/" target="_blank" rel="nofollow noopener">{g}</a>' for g in genres])
        parts.append(f'<p>関連ジャンル: {genre_tags}</p>')

    return '\n'.join(parts)


def build_hatena_categories(product):
    """はてなブログのカテゴリ（タグ）を生成する"""
    cats = ['FANZA', 'アダルト動画', 'アフィリエイト']
    if product['actors']:
        cats.extend(product['actors'][:2])
    if product['genres']:
        cats.extend(product['genres'][:2])
    return cats

# ================================================================
# 📮 はてなブログ AtomPub API
# ================================================================

HATENA_ENDPOINT = f'https://blog.hatena.ne.jp/{HATENA_ID}/{HATENA_BLOG_ID}/atom/entry'


def post_to_hatena(title_text, body_html, categories, is_draft=False):
    """はてなブログに記事を投稿する。成功時は投稿URLを返す。"""

    # AtomPub XMLを構築
    cat_xml = '\n'.join([f'    <category term="{c}" />' for c in categories])
    draft_xml = '<app:draft>yes</app:draft>' if is_draft else '<app:draft>no</app:draft>'

    xml_body = f'''<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{_xml_escape(title_text)}</title>
  <content type="text/html">
    {_xml_escape(body_html)}
  </content>
{cat_xml}
  <app:control>
    {draft_xml}
  </app:control>
</entry>'''

    # Basic認証（はてなID:APIキー）
    credentials = base64.b64encode(f'{HATENA_ID}:{HATENA_API_KEY}'.encode()).decode()

    try:
        resp = requests.post(
            HATENA_ENDPOINT,
            data=xml_body.encode('utf-8'),
            headers={
                'Content-Type': 'application/atom+xml; charset=utf-8',
                'Authorization': f'Basic {credentials}',
            },
            timeout=30,
        )
        if resp.status_code in (200, 201):
            # レスポンスXMLから投稿URLを取得
            root = ET.fromstring(resp.content)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            link = root.find('atom:link[@rel="alternate"]', ns)
            post_url = link.attrib.get('href', '') if link is not None else ''
            status = '下書き保存' if is_draft else '公開投稿'
            print(f'  ✅ {status}成功: {post_url}')
            return post_url
        else:
            print(f'  ❌ 投稿失敗 (HTTP {resp.status_code}): {resp.text[:200]}')
            return None
    except Exception as e:
        print(f'  ❌ 投稿エラー: {e}')
        return None


def _xml_escape(text):
    """XML特殊文字をエスケープする"""
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))

# ================================================================
# 💰 価格フィルター
# ================================================================

def parse_price_range(range_str):
    if not range_str or range_str == 'all':
        return None
    range_str = range_str.replace('円', '').replace(',', '').strip()
    if '-' not in range_str:
        return None
    min_part, max_part = range_str.split('-', 1)
    try:
        price_min = int(min_part.strip()) if min_part.strip() else 0
        price_max = int(max_part.strip()) if max_part.strip() else None
    except ValueError:
        return None
    return (price_min, price_max)


PRICE_RANGE_BOUNDS = parse_price_range(DMM_PRICE_RANGE)


def price_in_range(product):
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

# ================================================================
# 🚀 メイン実行
# ================================================================

SORT_MAP = {
    'rank': '-rank',
    'date': '-date',
    'both': '-rank',  # both の場合は人気順を優先
}
sort_key = SORT_MAP.get(DMM_SORT_MODE, '-rank')

print(f'\n🛍️  DMMから商品情報を取得中...')
raw_items = fetch_dmm_products(sort_key=sort_key, hits=BLOG_POST_LIMIT * 3, offset=POST_START_INDEX)

if not raw_items:
    print('❌ 商品が1件も取得できませんでした。')
    sys.exit(1)

# 価格フィルター適用・重複除去
products = []
seen_ids = set()
for item in raw_items:
    cid = item.get('content_id') or item.get('product_id') or ''
    if cid and cid in seen_ids:
        continue
    if cid:
        seen_ids.add(cid)
    p = parse_product(item)
    if not price_in_range(p):
        continue
    products.append(p)
    if len(products) >= BLOG_POST_LIMIT:
        break

print(f'\n📝 {len(products)} 件の記事を{"下書き保存" if BLOG_DRAFT else "公開投稿"}します...\n')

posted_urls = []
for i, product in enumerate(products, 1):
    print(f'--- {i}/{len(products)}: {product["title"][:40]} ---')

    article_title = build_article_title(product)
    article_html  = build_article_html(product)
    categories    = build_hatena_categories(product)

    print(f'  タイトル: {article_title}')
    print(f'  カテゴリ: {", ".join(categories)}')

    post_url = post_to_hatena(
        title_text=article_title,
        body_html=article_html,
        categories=categories,
        is_draft=BLOG_DRAFT,
    )
    if post_url:
        posted_urls.append(post_url)

    # 連続投稿によるレート制限を避けるため少し待つ
    if i < len(products):
        time.sleep(3)

print(f'\n✅ 完了！ {len(posted_urls)}/{len(products)} 件を投稿しました。')
if posted_urls:
    print('\n投稿済みURL一覧:')
    for url in posted_urls:
        print(f'  - {url}')
