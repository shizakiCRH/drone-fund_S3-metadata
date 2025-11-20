# S3ファイルメタデータ自動タグ付けシステム

S3バケットに配置されたファイル（PDF、Excel、Word、テキスト等）を自動スキャンし、OpenAI GPTモデルを用いてドキュメント種別（doc_type）と日付（doc_date）を抽出し、既存のmetadata.jsonファイルに追記するシステムです。

**主な特徴**:
- **多様なファイル形式に対応**: PDF、Excel (.xlsx/.xls)、Word (.docx)、テキストファイル
- **ページネーション処理**: 100件ずつバッチ処理で万単位のファイルにも対応
- **並列処理**: 並列度3で処理速度を最適化（設定変更可能）
- **Token最適化**: PDFは3ページ、Excelは15行、全形式とも先頭500文字のみ送信してコスト削減
- **処理ログ記録**: CloudWatch Logsに構造化ログとして記録、後からCSVエクスポート可能
- **エラー通知**: 処理失敗時にSlackへ自動通知、429エラーは自動リトライ

## 📋 目次

- [システム概要](#システム概要)
- [前提条件](#前提条件)
- [ファイル構成](#ファイル構成)
- [デプロイ手順](#デプロイ手順)
- [実行方法](#実行方法)
- [ログの集約方法](#ログの集約方法)
- [更新デプロイ手順](#更新デプロイ手順)
- [監視とログ](#監視とログ)
- [トラブルシューティング](#トラブルシューティング)

---

## システム概要

### アーキテクチャ

```
[手動実行]
    ↓
[Step Functions] ← ページネーションループ
    ↓
[Lambda 1: FileScanner] → S3バケットから100件ずつスキャン
    ↓
[Map State (並列度3)]
    ↓
[Lambda 2: MetadataTagger × 3] → 各ファイルをAI解析（並列）
    ↓ (成功)
metadata.jsonに追記 + CloudWatch Logsに記録
    ↓ (エラー)
Slack通知 + CloudWatch Logsに記録
    ↓
[次の100件へループ or 完了]
```

**ページネーション処理**: 万単位のファイルでも DataLimitExceeded を回避するため、100件ずつバッチ処理します。Step Functionsが自動的にループして全ファイルを処理します。

### 主要コンポーネント

| コンポーネント | 役割 |
|--------------|------|
| **FileScanner** (Lambda) | S3バケットから100件ずつファイルリストを取得 |
| **MetadataTagger** (Lambda) | 各ファイルをAIで解釈し、メタデータを更新 |
| **Step Functions** | 処理全体のオーケストレーション（ページネーションループ） |
| **OpenAI API (GPT-5-mini)** | ファイル内容の解釈とタグ抽出 |
| **CloudWatch Logs** | 処理成功ログを構造化形式で記録 |
| **Slack API** | エラー通知 |

---

## 前提条件

### 1. AWSアカウント
- Lambda、S3、Step Functionsへのアクセス権限

### 2. OpenAI APIキー
- GPT-4o-miniが利用可能なAPIキー（Tier 3推奨）
- 取得方法: https://platform.openai.com/api-keys

### 3. Slack Webhook URL（オプション）
- エラー通知用のIncoming Webhook URL
- 取得方法: https://api.slack.com/messaging/webhooks

### 4. ローカル環境（Lambda Layerビルド用）
- Docker
- インストール方法: https://docs.docker.com/get-docker/

---

## ファイル構成

```
project/
├── file_scanner/
│   ├── lambda_function.py          # Lambda 1のメインコード（ページネーション対応）
│   └── requirements.txt            # 依存パッケージ（空）
├── metadata_tagger/
│   ├── lambda_function.py          # Lambda 2のメインコード
│   ├── file_processor.py           # ファイル処理ロジック
│   ├── openai_client.py            # OpenAI API呼び出し
│   ├── slack_notifier.py           # Slack通知
│   └── requirements.txt            # openai, requests
├── stepfunctions/
│   └── state_machine.json          # Step Functions ASL定義（ページネーション対応）
├── layer/
│   └── build_layer_docker.sh       # Lambda Layerビルドスクリプト（Docker版）
└── README.md                       # このファイル
```

---

## デプロイ手順

### ステップ1: IAMロールの作成

#### 1-1. Lambda関数用ロール（FileScanner & MetadataTagger共通）

1. **IAM** → **ロール** → **ロールを作成**
2. 信頼されたエンティティ: **AWS のサービス** → **Lambda**
3. 許可ポリシー: **AWSLambdaBasicExecutionRole**
4. ロール名: `S3MetadataLambdaRole`
5. **ロールを作成**
6. 作成したロールを開き、**「許可を追加」** → **「インラインポリシーを作成」**
7. JSONタブで以下を貼り付け:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket-name",
        "arn:aws:s3:::your-bucket-name/*"
      ]
    }
  ]
}
```
**※ `your-bucket-name` を実際のバケット名に置き換えてください**

8. ポリシー名: `S3MetadataS3Policy`
9. **ポリシーを作成**

#### 1-2. Step Functions用ロール

1. **IAM** → **ロール** → **ロールを作成**
2. 信頼されたエンティティ: **AWS のサービス** → **Step Functions**
3. ロール名: `StepFunctionsExecutionRole`
4. インラインポリシーを追加:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "lambda:InvokeFunction"
      ],
      "Resource": [
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:FileScanner",
        "arn:aws:lambda:REGION:ACCOUNT_ID:function:MetadataTagger"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogDelivery",
        "logs:GetLogDelivery",
        "logs:UpdateLogDelivery",
        "logs:DeleteLogDelivery",
        "logs:ListLogDeliveries",
        "logs:PutResourcePolicy",
        "logs:DescribeResourcePolicies",
        "logs:DescribeLogGroups"
      ],
      "Resource": "*"
    }
  ]
}
```

**※ `REGION`, `ACCOUNT_ID` を置き換えてください**

5. ポリシー名: `StepFunctionsLambdaInvokePolicy`

---

### ステップ2: Lambda Layerの作成

#### 2-1. ローカル環境でビルド（Docker使用）

**重要**: Lambda環境との互換性のため、**Dockerを使用したビルドが必須**です。

```bash
cd layer
bash build_layer_docker.sh
```

このスクリプトは以下のパッケージをビルドします:
- `pymupdf` (PDF処理用)
- `openpyxl` (Excel .xlsx処理用)
- `xlrd` (Excel .xls処理用)
- `python-docx` (Word .docx処理用)
- `openai` (OpenAI API)
- `requests` (HTTP通信)

出力: `python-dependencies.zip` が生成されます（サイズ: 約50-55MB）

**注意**:
- macOS/WindowsでDockerを使わずにビルドすると、バイナリ互換性の問題でLambdaで動作しません
- ビルドされたzipファイルは50MBを超えるため、S3経由でアップロードする必要があります

#### 2-2. S3にアップロード

Lambda Layerのzipファイルは50MBを超えるため、AWS Consoleから直接アップロードできません。
以下の手順でS3経由でアップロードしてください。

**方法A: AWS CLI（推奨）**

```bash
# 1. S3にアップロード（バケット名を実際のものに置き換えてください）
aws s3 cp layer/python-dependencies.zip s3://your-bucket-name/lambda-layers/python-dependencies.zip

# 2. S3からLambda Layerを作成
aws lambda publish-layer-version \
  --layer-name python-dependencies \
  --content S3Bucket=your-bucket-name,S3Key=lambda-layers/python-dependencies.zip \
  --compatible-runtimes python3.12
```

**方法B: AWS Console（手動）**

1. **S3 Console** を開く
2. 適切なバケットを選択（または新規作成）
3. 「アップロード」をクリック
4. `layer/python-dependencies.zip` を選択してアップロード
5. アップロード先のパス（例: `lambda-layers/python-dependencies.zip`）をメモ

次に、Lambda Layerを作成:

1. **Lambda Console** → **レイヤー** → **レイヤーを作成**
2. 名前: `python-dependencies`
3. 「Amazon S3 からファイルをアップロード」を選択
4. S3 リンク URL を入力: `s3://your-bucket-name/lambda-layers/python-dependencies.zip`
5. 互換性のあるランタイム: **Python 3.12** を選択
6. **作成**

---

### ステップ3: Lambda Function 1（FileScanner）の作成

1. **Lambda** → **関数** → **関数の作成**
2. **一から作成**
3. 関数名: `FileScanner`
4. ランタイム: **Python 3.12**
5. 既存のロールを使用: `S3MetadataLambdaRole`
6. **関数の作成**

#### 設定の変更

7. **設定** → **一般設定** → **編集**
   - タイムアウト: **15分**
   - メモリ: **256MB**
   - **保存**

8. **コード**タブで、`file_scanner/lambda_function.py` の内容を貼り付け
9. **Deploy** をクリック

---

### ステップ4: Lambda Function 2（MetadataTagger）の作成

1. **Lambda** → **関数** → **関数の作成**
2. **一から作成**
3. 関数名: `MetadataTagger`
4. ランタイム: **Python 3.12**
5. 既存のロールを使用: `S3MetadataLambdaRole`
6. **関数の作成**

#### 設定の変更

7. **設定** → **一般設定** → **編集**
   - タイムアウト: **5分**
   - メモリ: **512MB**
   - **保存**

8. **設定** → **環境変数** → **編集**
   - `OPENAI_API_KEY`: `sk-proj-...`（取得済みのOpenAI APIキー）
   - `OPENAI_MODEL`: `gpt-5-mini`（使用するOpenAIモデル名）
   - `SLACK_WEBHOOK_URL`: `https://hooks.slack.com/services/...`（Slack Webhook URL）
   - `MAX_FILE_SIZE`: `10485760`（10MB）
   - **保存**

9. **レイヤー** → **レイヤーの追加**
   - **カスタムレイヤー**を選択
   - `python-dependencies` を選択
   - 最新バージョンを選択
   - **追加**

#### コードのアップロード（zipファイル方式）

10. ローカルで `metadata_tagger/` ディレクトリをzip化:

```bash
cd metadata_tagger
zip -r function.zip .
```

11. Lambda Consoleで:
   - **コード** → **アップロード元** → **.zipファイル**
   - `function.zip` を選択
   - **保存**

---

### ステップ5: Step Functionsステートマシンの作成

1. **Step Functions** → **ステートマシン** → **ステートマシンの作成**
2. **コードでワークフローを記述**を選択
3. タイプ: **標準**
4. `stepfunctions/state_machine.json` の内容を貼り付け
5. **以下の箇所を実際の値に置き換え**:
   - `YOUR_BUCKET_NAME`: 処理対象のS3バケット名
   - `REGION`: AWSリージョン（例: `us-west-2`）
   - `ACCOUNT_ID`: AWSアカウントID（例: `123456789012`）

Lambda関数ARNの確認方法:
- **Lambda** → 関数を開く → 右上の **関数ARN** をコピー

6. ステートマシン名: `S3MetadataTaggingStateMachine`
7. 既存のロールを選択: `StepFunctionsExecutionRole`
8. **ステートマシンの作成**

#### ステートマシンのページネーション処理について

ステートマシンは以下のフローでページネーション（ループ処理）を実現しています:

1. **ScanBatch**: FileScannerが100件ずつファイルをスキャン
   - 出力: `files`（ファイルリスト）、`continuation_token`（次のページのトークン）、`is_truncated`（続きがあるか）

2. **ProcessBatch**: 100件のファイルを並列処理（並列度3）

3. **CheckMoreFiles**: `is_truncated`をチェック
   - `is_truncated = true` の場合: `continuation_token`を使って次の100件をスキャン（ScanBatchへループ）
   - `is_truncated = false` の場合: 全ファイル処理完了

**is_truncatedの意味**:
- `true`: まだ処理すべきファイルが残っている（次のページがある）
- `false`: 全てのファイルをスキャン済み（処理完了）

この仕組みにより、万単位のファイルでもStep Functionsの実行履歴制限（25,000イベント）を超えずに処理できます。

---

## 実行方法

### AWS Consoleから実行

1. **Step Functions** → `S3MetadataTaggingStateMachine` を開く
2. **実行の開始** をクリック
3. 入力JSONは空のオブジェクトで問題ありません:

```json
{}
```

4. **実行の開始**
5. 実行状態を確認

### 処理時間の目安

- **ファイル数**: 10,000ファイル
- **並列度3**: 約9-10時間
- **並列度1（順次）**: 約27時間

### 並列度の変更

処理速度を調整する場合、Step Functions の定義を編集:

1. **Step Functions** → `S3MetadataTaggingStateMachine` → **編集**
2. `ProcessBatch` ステートの `MaxConcurrency` を変更:

```json
"ProcessBatch": {
  "Type": "Map",
  "MaxConcurrency": 3,  // ← ここを変更（1-10推奨）
  ...
}
```

**推奨値**:
- 並列度1: 最も安全、競合なし、処理時間最長
- 並列度3: バランス型（デフォルト）
- 並列度5-10: 高速化（OpenAI Tier 3以上推奨）

---

## ログの集約方法

MetadataTagger は処理結果をCloudWatch Logsに構造化ログとして出力します。処理完了後、CloudWatch Logs Insights でログを集約し、CSV形式でエクスポートできます。

### 手順

#### 1. CloudWatch Logs Insights を開く

1. AWS Management Console → **CloudWatch** を開く
2. 左メニューから **ログ ＞ ログのインサイト** を選択
3. 「ロググループ名」で `/aws/lambda/MetadataTagger` を指定
4. 時間範囲を設定（Step Functions の実行時間をカバーする範囲）

#### 2. クエリを実行

以下のクエリをコピー&ペーストして **「クエリの実行」** をクリック:

```
fields @timestamp, @message
| filter @message like "metadata_updated"
| parse @message '"file_key": "*"' as filepath
| parse @message '"parent_directory": "*"' as parent_dir
| parse @message '"file_name": "*"' as filename
| parse @message '"doc_type": "*"' as doctype
| parse @message '"doc_date": *}' as docdate
| display @timestamp, filepath, parent_dir, filename, doctype, docdate
| sort @timestamp asc
```

#### 3. CSV形式でエクスポート

1. クエリ結果の右上にある **「アクション」** をクリック
2. **「結果をダウンロード (CSV)」** を選択
3. CSVファイルがダウンロードされます

### 出力形式

```csv
処理日時,フルパス,親ディレクトリ,ファイル名,doc_type,doc_date
2025-11-19T13:31:44,1. Test Inc./01 会社概要/株主名簿.pdf,01 会社概要,株主名簿.pdf,会社情報,20200731
```

### エラーログの確認

処理エラーが発生したファイルを確認するクエリ:

```
fields @timestamp, @message
| filter @message like /ERROR/ or @message like /Failed/
| sort @timestamp desc
```

---

## 更新デプロイ手順

初期構築後に、Lambda関数やStep Functionsステートマシンに修正が発生した場合の更新手順です。

### Lambda関数のコード更新

#### 方法1: AWS Consoleで直接編集（小規模な変更）

1. **Lambda** → 対象の関数（`FileScanner` または `MetadataTagger`）を開く
2. **コード**タブで直接編集
3. **Deploy** をクリック

#### 方法2: zipファイルをアップロード（複数ファイルの変更）

**FileScanner の場合:**

```bash
cd file_scanner
zip function.zip lambda_function.py
```

**MetadataTagger の場合:**

```bash
cd metadata_tagger
zip -r function.zip .
```

AWS Consoleで:
1. **Lambda** → 対象の関数を開く
2. **コード** → **アップロード元** → **.zipファイル**
3. 作成した `function.zip` を選択
4. **保存**

### Lambda Layerの更新

依存パッケージ（pymupdf, openpyxl, openai, requests）を更新する場合:

```bash
cd layer
bash build_layer_docker.sh

aws s3 cp python-dependencies.zip s3://your-bucket-name/lambda-layers/python-dependencies.zip

aws lambda publish-layer-version \
  --layer-name python-dependencies \
  --content S3Bucket=your-bucket-name,S3Key=lambda-layers/python-dependencies.zip \
  --compatible-runtimes python3.12
```

レイヤーのバージョンが更新されたら、Lambda関数に新しいバージョンを割り当て。

### Step Functions ステートマシンの更新

1. **Step Functions** → `S3MetadataTaggingStateMachine` を開く
2. **編集**をクリック
3. JSON定義を更新
4. **保存**

---

## 監視とログ

### CloudWatch Logs

以下のロググループで各Lambda関数のログを確認できます:

- `/aws/lambda/FileScanner`
- `/aws/lambda/MetadataTagger`
- `/aws/states/S3MetadataTaggingStateMachine`

### エラー通知

エラーが発生した場合、Slackに以下の情報が通知されます:

- エラー種別
- ファイルパス
- 詳細メッセージ

---

## トラブルシューティング

### 問題: Lambda.TooManyRequestsException (429エラー)

**原因**: Lambda の同時実行数制限

**対処**:
1. Step Functions の `MaxConcurrency` を下げる（5 → 3 → 1）
2. MetadataTagger に予約済み同時実行数を設定（10-20）
3. 429エラーは自動的に5回リトライされます

### 問題: 一部のファイルが処理されていない

**原因**: 
- ページネーション処理のバグ
- Lambda タイムアウト
- 429エラーでリトライ上限到達

**対処**:
1. CloudWatch Logs で FileScanner のログを確認
2. Step Functions の実行イベント履歴を確認
3. エラーログとSlack通知を確認
4. 必要に応じて再実行

### 問題: Lambda タイムアウト

**原因**: 大きなPDFファイルの処理

**対処**:
1. Lambda のタイムアウト時間を延長（最大15分）
2. または、`MAX_FILE_SIZE` 環境変数を小さくする

### 問題: OpenAI APIレート制限

**原因**: 並列度が高すぎる

**対処**:
- 並列度を下げる（3 → 1）
- OpenAI アカウントの Tier を確認（Tier 3推奨）

### 問題: DataLimitExceeded エラー

**原因**: Step Functions のペイロードサイズ制限（256KB）超過

**対処**:
- ページネーション対応版のコードを使用していることを確認
- FileScannerが100件ずつ返していることを確認

### サポートされるファイル形式

#### 対応形式

| ファイル形式 | 拡張子 | ライブラリ | 送信データ |
|------------|--------|----------|----------|
| PDF | `.pdf` | PyMuPDF (fitz) | 先頭3ページ → 500文字 |
| Excel (新) | `.xlsx` | openpyxl | 先頭15行 → 500文字 |
| Excel (旧) | `.xls` | xlrd | 先頭15行 → 500文字 |
| Word (新) | `.docx` | python-docx | 全テキスト → 500文字 |
| テキスト | `.txt`, `.md`, `.csv` 等 | - | 先頭500文字 |

#### 非対応形式

| ファイル形式 | 拡張子 | 理由 | 対処方法 |
|------------|--------|------|---------|
| Word (旧) | `.doc` | バイナリ形式で複雑 | `.docx`形式に変換してください |
| Excel 2003 XML | `.xls` (XML) | XML形式は非対応 | `.xlsx`形式で保存し直してください |
| 画像 | `.jpg`, `.png` 等 | OCR未実装 | - |
| 動画・音声 | `.mp4`, `.mp3` 等 | 対応予定なし | - |

**注意**:
- `.doc`ファイルが検出された場合、エラーとして扱われSlack通知が送信されます
- 非対応ファイルはテキストファイルとして処理を試みますが、正しく抽出できない可能性があります

---

## コスト見積もり（10,000ファイル処理時）

| サービス | コスト |
|---------|--------|
| Lambda実行時間 | $2.50 |
| Lambda呼び出し | $0.002 |
| Step Functions | $0.75 |
| OpenAI API（入力） | $3.00 |
| OpenAI API（出力） | $0.60 |
| S3 リクエスト | $0.058 |
| **合計** | **約$6.91** |

---

## ライセンス

このプロジェクトは内部利用を目的としています。

## 作成者

秋永

## バージョン

2.0 (2025-11-19)
- ページネーション対応追加
- 並列処理対応（並列度設定可能）
- ログ出力をCloudWatch Logsに変更
- 429エラー自動リトライ追加