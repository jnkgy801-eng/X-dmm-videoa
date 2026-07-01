# DMM & FANZA X投稿文ジェネレーター

DMMアフィリエイトAPI（v3）を使って商品情報を自動取得し、X（Twitter）向けの**スレッド形式投稿文**を自動生成するツールです。  
生成した投稿文はテキストファイルに保存されます。`AUTO_POST_TO_X=true` を設定すると、サンプル動画をXに直接アップロードして自動投稿することも可能です。

---

## 📌 投稿の構成（スレッド2ポスト形式）

1商品につき、2ポストのスレッドとして生成します。

```
【ポスト1】引き + タイトル + 無料サンプルURL
━━━━━━━━━━━━━━━━━━━━━━━━━
これ絶対見て👇
📽 タイトル

正直期待してなかったのに、気づいたら最後まで一気見してた

▶ 無料サンプル: https://tinyurl.com/xxxxx

【ポスト2】詳細 + アフィリエイトURL + ハッシュタグ  ← スレッド続き
━━━━━━━━━━━━━━━━━━━━━━━━━
📌 気に入ったら本編はこちら👇

当たりだと思った理由を正直に言うと、レビュー平均4.5（32件）の高評価、〇〇制作。
今夜暇な人はとりあえずリンク踏んでみて

💰 ¥990
👤 出演者名
🏷 #人妻　#巨乳

https://tinyurl.com/yyyyy

#FANZA #FANZAおすすめ #AV #PR
```

- ポスト1に**無料サンプル**を置くことで、コストなしにまず興味を引く
- サンプルを見て気に入った人がスレッドを辿り、ポスト2の**アフィリエイトURL**から購入する導線
- サンプル動画がない商品は、ポスト1にアフィリエイトURLを代わりに表示

---

## 🚀 セットアップ

### 必要なもの
- Python 3.10以上
- DMMアフィリエイトAPI ID・アフィリエイトID（[DMM アフィリエイト](https://affiliate.dmm.com/) で取得）

### インストール
```bash
pip install requests tweepy playwright
playwright install chromium  # 自動投稿（browser方式）を使う場合のみ
```

---

## ⚙️ 環境変数一覧

### 必須

| 変数名 | 説明 |
|---|---|
| `DMM_API_ID` | DMMアフィリエイトのAPI ID |
| `DMM_AFFILIATE_ID` | DMMアフィリエイトのアフィリエイトID |

### 投稿内容の調整（任意）

| 変数名 | デフォルト | 説明 |
|---|---|---|
| `DMM_FLOOR` | `videoa` | 取得するフロア。`videoa` / `videoc` / `anime` / `doujin` / `comic` / `goods` |
| `DMM_SORT_MODE` | `both` | `both`（新着＋人気）/ `date`（新着のみ）/ `rank`（人気のみ） |
| `DMM_PRICE_RANGE` | `all` | 価格フィルター。例: `0-999` / `1000-1999` / `5000-`（上限なし）/ `all` |
| `POST_START_INDEX` | ランダム（1〜480） | 取得開始番号。空欄でランダム（ただし新着順「-date」で検索する場合は、空欄なら1件目＝最新データから検索します） |
| `MAX_PROCESS_COUNT` | `30` | 1回の実行で処理する商品数の上限 |
| `DMM_MAX_RETRIES` | `10` | FANZA/DMM APIへの問い合わせが失敗した場合のリトライ回数の上限 |
| `DMM_RETRY_WAIT_SEC` | `3` | リトライ時の待機秒数（試行のたびに延びる簡易バックオフ） |

### 自動投稿（任意）

| 変数名 | デフォルト | 説明 |
|---|---|---|
| `AUTO_POST_TO_X` | `false` | `true` で自動投稿モードON。未設定ならテキスト生成のみ（完全無料） |
| `POST_METHOD` | `browser` | `browser`（API課金なし・規約リスクあり）/ `api`（公式API・課金あり） |
| `X_POST_LIMIT` | `5` | 1回の実行で投稿する最大件数。凍結・課金リスクに直結するため必ず指定 |
| `X_POST_INTERVAL_SEC` | `30` | 投稿間隔（秒）。Bot検知・スパム判定対策 |

---

## 🐦 自動投稿モード（AUTO_POST_TO_X）

`AUTO_POST_TO_X=true` にすると、サンプル動画をDMMからダウンロードしてXに直接アップロードし、動画埋め込み付きでスレッド投稿します（テキストにURLを貼るのではなく、ポスト上で動画が再生される形）。

### 方式A: `POST_METHOD=browser`（API課金なし）

PlaywrightでChromiumを操作し、ログイン済みアカウントから自動投稿します。X公式APIを使わないため料金はかかりませんが、**X利用規約上の自動化行為に該当し、アカウント凍結リスクがあります**。

#### 事前準備（自分のPCで一度だけ）
```bash
python x_login_setup.py
```
ブラウザが開くので、画面上で手動でXにログイン（2段階認証含む）→ターミナルでEnter。  
`x_session.json` にログイン状態が保存されます。

> ⚠️ `x_session.json` はパスワードと同等の機密情報です。Gitにコミットしない（`.gitignore` 済み）、他人に渡さない。有効期限が切れたら再実行してください。

#### ローカル実行
```bash
export DMM_API_ID=xxxx
export DMM_AFFILIATE_ID=xxxx
export AUTO_POST_TO_X=true
export POST_METHOD=browser
export X_POST_LIMIT=3

python dmm_x_post_generator.py
```

#### GitHub Actionsで実行する場合
1. ローカルで `x_login_setup.py` を実行してセッションを作成
2. セッションファイルをBase64化してコピー
   ```bash
   base64 -i x_session.json | tr -d '\n'
   ```
3. リポジトリの Settings → Secrets に `X_SESSION_STATE_B64` として登録
4. ワークフロー実行時に `auto_post_to_x: true`・`post_method: browser` を指定

セッションには有効期限があります。投稿が失敗するようになったら手順1〜3をやり直してください。

---

### 方式B: `POST_METHOD=api`（公式API・課金あり・規約準拠）

#### 必要なもの
1. [X Developer Portal](https://developer.x.com/) でアプリを作成し、権限を **Read and Write** に設定
2. OAuth 1.0aの4点セット（API Key / API Secret / Access Token / Access Token Secret）を取得
3. 料金体系を確認（2026年時点、投稿1件あたり従量課金制）

#### ローカル実行
```bash
export DMM_API_ID=xxxx
export DMM_AFFILIATE_ID=xxxx
export AUTO_POST_TO_X=true
export POST_METHOD=api
export X_API_KEY=xxxx
export X_API_SECRET=xxxx
export X_ACCESS_TOKEN=xxxx
export X_ACCESS_SECRET=xxxx
export X_POST_LIMIT=3

python dmm_x_post_generator.py
```

---

## 💾 出力ファイル

生成した投稿文は `outputs/` フォルダに `dmm_x_posts_YYYYMMDD_HHMMSS.txt` という名前で保存されます。  
各商品につき【ポスト1】【ポスト2（スレッド続き）】の2ポスト分が記録されます。

ローカル実行時はデスクトップ（`~/Desktop`）への保存を優先します。環境変数 `SAVE_DIR` で保存先を明示指定することもできます。

---

## ⚠️ 注意事項

- アダルトコンテンツを動画付きで投稿するため、Xアカウントの設定で **「メディアにセンシティブな内容を含める」をON** にしてください。
- DMMアフィリエイト規約上、サンプル動画の他サービスへのアップロードが許可される範囲かどうかは、ご自身で[DMMアフィリエイト利用規約](https://affiliate.dmm.com/)をご確認ください。
- `browser` 方式はX側のHTML構造（`data-testid` 等）に依存しており、X側の仕様変更で突然動かなくなる場合があります。
- `X_POST_LIMIT` は凍結リスク・課金額に直結するため、テスト時は `1〜2` から始めることを推奨します。
