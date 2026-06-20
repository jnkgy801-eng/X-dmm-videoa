# DMM & FANZA X投稿文ジェネレーター

DMMアフィリエイトAPI（v3）を使用して、商品の情報を自動取得し、X（Twitter）の投稿用に最適化されたテキストを自動生成するツールです。

## 🌟 今回の修正点
- **FANZA同人 (`doujin`)** に完全対応しました。
- GitHub ActionsのインターフェースおよびPythonスクリプト側で、フロア種別に `doujin` を選択した際に、自動的にFANZAの同人データ（サークル名や同人特有のハッシュタグ、絵文字など）を引っ張れるように最適化しています。
- **サンプル動画をポストに埋め込んで自動投稿する機能を追加**しました（`AUTO_POST_TO_X=true`）。従来どおりURLをテキストに貼るだけのモードがデフォルトで、動画埋め込み自動投稿は完全にオプトインです。
- 投稿方式を **2種類** から選べます（`POST_METHOD`）。
  - `browser`（デフォルト）: ブラウザ自動操作。**API課金なし**。ただしX利用規約上はグレー〜違反の自動化行為で、アカウント凍結リスクがあります。
  - `api`: 公式X API。課金あり。規約に準拠した正規ルート。

## 🐦 動画埋め込み自動投稿（AUTO_POST_TO_X）

サンプル動画URLをテキストとして貼るのではなく、Xに動画ファイルを直接アップロードして、ポスト上で再生できる形で投稿します。

---

### 方式A: `POST_METHOD=browser`（API課金なし）

Playwrightで実際のブラウザを操作し、ログイン済みアカウントから手動投稿と同じ操作を自動で行います。X公式APIは一切使わないため料金はかかりませんが、**X利用規約違反となりアカウント凍結のリスクがあります**。

#### 事前準備（自分のPCで一度だけ）
```bash
pip install playwright
playwright install chromium

python x_login_setup.py
```
ブラウザが開くので、画面上で手動でXにログイン（2段階認証含む）→ターミナルでEnter。
`x_session.json` というファイルにログイン状態が保存されます。

⚠️ **`x_session.json` はパスワードと同等の機密情報です。**
- 絶対にGitにコミットしない（`.gitignore` に追加済み）
- 他人に渡さない、公開しない
- 有効期限が切れたら `x_login_setup.py` を再実行

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
2. 中身をBase64化してSecretに登録
   ```bash
   base64 -i x_session.json | tr -d '\n'
   ```
3. リポジトリの Settings → Secrets に `X_SESSION_STATE_B64` として登録
4. ワークフロー実行時に `auto_post_to_x: true`、`post_method: browser` を指定

セッションには有効期限があります。投稿が失敗するようになったら手順1〜3をやり直してください。

---

### 方式B: `POST_METHOD=api`（公式API・課金あり・規約準拠）

#### 必要なもの
1. **X Developer Portal**（developer.x.com）でアプリを作成し、権限を **Read and Write** に設定
2. OAuth 1.0aの4点セット（API Key/Secret、Access Token/Secret）を取得
3. 2026年時点、X APIは新規開発者は従量課金制です（URLを含む投稿は1件あたり約$0.20）。実行前に必ずX側の請求ダッシュボードで料金体系を確認してください。

#### ローカル実行
```bash
pip install requests tweepy

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

### 共通の環境変数

| 変数名 | 説明 |
|---|---|
| `AUTO_POST_TO_X` | `true` で自動投稿モードON（未設定/`false`なら従来どおりテキスト生成のみ・完全無料） |
| `POST_METHOD` | `browser`（デフォルト・無料）または `api`（課金あり） |
| `X_POST_LIMIT` | 1回の実行で実際に投稿する最大件数（デフォルト5。凍結/課金リスクを抑えるため必ず指定） |
| `X_POST_INTERVAL_SEC` | 投稿間隔・秒（デフォルト30。連続投稿によるBot検知・スパム判定対策） |

### 注意事項
- アダルトコンテンツを動画付きで投稿するため、Xアカウント側で **「メディアにセンシティブな内容を含めることがある」設定をON** にしておいてください。
- DMMアフィリエイト規約上、サンプル動画の再配布（自社サーバーや他サービスへのアップロード）が許可される範囲かどうかは、ご自身でDMMアフィリエイトの利用規約を確認してください。本機能は規約の許諾範囲内での利用を前提としています。
- `browser`方式はX側のHTML構造（data-testid等）に依存しています。X側の仕様変更で突然動かなくなる可能性があります。
- `X_POST_LIMIT` は凍結リスク・課金額に直結するため、テストは小さい値（1〜2件）から始めることを推奨します。

