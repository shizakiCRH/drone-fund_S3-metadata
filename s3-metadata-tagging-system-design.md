# S3ファイルメタデータ自動タグ付けシステム 設計書

## 1. システム概要

### 1.1 目的
S3バケットに配置されたファイル（PDF、Excel、テキスト等）を自動スキャンし、OpenAI GPT-4o-miniを用いてドキュメント種別（doc_type）と日付（doc_date）を抽出し、既存のmetadata.jsonファイルに追記するシステム。

### 1.2 主要コンポーネント
- **AWS Step Functions**: 処理全体のオーケストレーション
- **Lambda Function 1 (File Scanner)**: S3バケット内のファイルリストを取得
- **Lambda Function 2 (Metadata Tagger)**: 各ファイルをAIで解釈し、メタデータを更新
- **OpenAI API (GPT-4o-mini)**: ファイル内容の解釈とタグ抽出
- **Slack API**: エラー通知

---

## 2. システムアーキテクチャ

### 2.1 処理フロー図

```
[手動実行]
    ↓
[Step Functions 開始]
    ↓
[Lambda 1: File Scanner]
  - S3バケット全体をスキャン
  - metadata.json以外のファイルをリストアップ
  - ファイルパスの配列を返却
    ↓
[Map State (並列度1)]
  - 順次実行でファイルを処理
    ↓
  [Lambda 2: Metadata Tagger] (各ファイルごと)
    - ファイルをS3から取得
    - OpenAI APIで解釈
    - doc_type, doc_dateを抽出
    - metadata.jsonを更新
    - エラー時: Slack通知 → Continue
    ↓
[Step Functions 完了]
```

### 2.2 S3ディレクトリ構造

```
s3://bucket-name/
├── 会社A/
│   ├── カテゴリ1/
│   │   ├── document1.pdf
│   │   ├── document1.pdf.metadata.json
│   │   └── 2025年4月/
│   │       ├── report.xlsx
│   │       └── report.xlsx.metadata.json
│   └── document2.txt
│       └── document2.txt.metadata.json
└── 会社B/
    └── ...
```

---

## 3. Lambda Function 1: File Scanner

### 3.1 機能概要
- S3バケット全体を再帰的にスキャン
- `.metadata.json`を除く全ファイルのキーを収集
- Step Functionsに渡すファイルリストを生成

### 3.2 入力（Step Functions入力）
```json
{
  "bucket": "your-bucket-name",
  "prefix": ""
}
```

### 3.3 出力
```json
{
  "files": [
    {
      "key": "会社A/カテゴリ1/document1.pdf",
      "bucket": "your-bucket-name"
    },
    {
      "key": "会社A/document2.txt",
      "bucket": "your-bucket-name"
    }
  ]
}
```

### 3.4 処理ロジック
1. S3 `list_objects_v2` を使用して全オブジェクトを取得
2. ページネーション対応（ContinuationToken）
3. `.metadata.json`で終わるキーを除外
4. ファイルリストを構築して返却

### 3.5 エラーハンドリング
- S3アクセスエラー: Step Functions失敗（リトライ3回、指数バックオフ）
- タイムアウト: Lambda タイムアウト15分設定

### 3.6 IAM権限
- `s3:ListBucket`
- `s3:GetObject` (metadata.jsonの存在確認用)

---

## 4. Lambda Function 2: Metadata Tagger

### 4.1 機能概要
- 1ファイルを処理
- ファイル内容とパスをOpenAI APIに送信
- doc_type, doc_dateを抽出
- 既存metadata.jsonに追記・更新
- エラー時はSlack通知

### 4.2 入力（Map Stateから各アイテム）
```json
{
  "key": "会社A/カテゴリ1/document1.pdf",
  "bucket": "your-bucket-name"
}
```

### 4.3 出力
```json
{
  "key": "会社A/カテゴリ1/document1.pdf",
  "status": "success",
  "doc_type": "投資",
  "doc_date": 20250417
}
```

エラー時:
```json
{
  "key": "会社A/カテゴリ1/document1.pdf",
  "status": "error",
  "error_type": "file_too_large",
  "message": "File size exceeds 10MB limit"
}
```

### 4.4 処理ロジック

#### 4.4.1 ファイル取得
```python
# S3からファイルをダウンロード
obj = s3.get_object(Bucket=bucket, Key=key)
file_content = obj['Body'].read()
file_size = obj['ContentLength']

# ファイルサイズチェック
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
if file_size > MAX_FILE_SIZE:
    raise FileTooLargeError()
```

#### 4.4.2 ファイル内容の抽出
- **PDF**: `PyMuPDF (fitz)` でテキスト抽出
- **Excel**: `openpyxl` でシート内容を読み取り
- **テキスト**: 直接読み込み

```python
def extract_text(key, content):
    if key.endswith('.pdf'):
        return extract_pdf_text(content)
    elif key.endswith(('.xlsx', '.xls')):
        return extract_excel_text(content)
    else:
        return content.decode('utf-8')
```

#### 4.4.3 OpenAI API呼び出し

**プロンプト設計**:
```
あなたはドキュメント分析の専門家です。以下のファイルを分析し、JSONで結果を返してください。

ファイルパス: {file_path}
ファイル内容:
{file_content}

以下の情報を抽出してください:
1. doc_type: ドキュメントの種別（例: 投資、契約書、議事録、報告書など）
2. doc_date: ドキュメントの日付（YYYYMMDD形式の数値）
   - ファイル名またはパス内の日付を優先的に使用
   - ファイル内容から日付を推測する場合は、最も重要と思われる日付を選択

返答は以下のJSON形式でお願いします:
{
  "doc_type": "種別名",
  "doc_date": 20250417
}

日付が特定できない場合は null を返してください。
種別が特定できない場合は "unknown" を返してください。
```

**APIパラメータ**:
```python
response = openai.ChatCompletion.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": "You are a document analysis expert. Always respond with valid JSON."},
        {"role": "user", "content": prompt}
    ],
    temperature=0.3,
    max_tokens=150,
    response_format={"type": "json_object"}
)
```

#### 4.4.4 metadata.json の更新

```python
# 既存metadata.jsonを取得
metadata_key = f"{key}.metadata.json"
try:
    obj = s3.get_object(Bucket=bucket, Key=metadata_key)
    metadata = json.loads(obj['Body'].read())
except s3.exceptions.NoSuchKey:
    # metadata.jsonが存在しない場合はスキップ
    raise MetadataNotFoundError()

# doc_type, doc_dateを追記・更新
metadata['metadataAttributes']['doc_type'] = {
    "value": {
        "type": "STRING",
        "stringValue": doc_type or "unknown"
    }
}

if doc_date:
    metadata['metadataAttributes']['doc_date'] = {
        "value": {
            "type": "NUMBER",
            "numberValue": doc_date
        }
    }
else:
    metadata['metadataAttributes']['doc_date'] = {
        "value": {
            "type": "STRING",
            "stringValue": "unknown"
        }
    }

# S3に書き戻し
s3.put_object(
    Bucket=bucket,
    Key=metadata_key,
    Body=json.dumps(metadata, ensure_ascii=False, indent=2),
    ContentType='application/json'
)
```

### 4.5 エラーハンドリング

#### 4.5.1 エラー種別
| エラー種別 | 説明 | 処理 |
|-----------|------|------|
| `file_too_large` | ファイルサイズが10MBを超過 | スキップ + Slack通知 |
| `file_read_error` | ファイル読み込み失敗（破損等） | スキップ + Slack通知 |
| `openai_api_error` | OpenAI API呼び出し失敗 | スキップ + Slack通知 |
| `ai_parse_error` | AIの返答をJSONパースできない | デフォルト値設定 + Slack通知 |
| `metadata_not_found` | metadata.jsonが存在しない | スキップ（通知不要） |
| `s3_write_error` | S3への書き込み失敗 | スキップ + Slack通知 |

#### 4.5.2 Slack通知内容

```python
def send_slack_notification(error_type, file_key, message):
    payload = {
        "text": f"⚠️ メタデータタグ付けエラー",
        "attachments": [
            {
                "color": "warning",
                "fields": [
                    {"title": "エラー種別", "value": error_type, "short": True},
                    {"title": "ファイル", "value": file_key, "short": False},
                    {"title": "詳細", "value": message, "short": False}
                ]
            }
        ]
    }
    requests.post(SLACK_WEBHOOK_URL, json=payload)
```

### 4.6 IAM権限
- `s3:GetObject`
- `s3:PutObject`
- `logs:CreateLogGroup`
- `logs:CreateLogStream`
- `logs:PutLogEvents`

### 4.7 環境変数
- `OPENAI_API_KEY`: OpenAI APIキー
- `SLACK_WEBHOOK_URL`: Slack通知用WebhookURL
- `MAX_FILE_SIZE`: 最大ファイルサイズ（バイト）

### 4.8 Lambda設定
- **メモリ**: 512MB
- **タイムアウト**: 5分
- **レイヤー**: Python依存パッケージ（PyMuPDF, openpyxl, openai, requests）

---

## 5. Step Functions ステートマシン定義

### 5.1 ASL（Amazon States Language）

```json
{
  "Comment": "S3ファイルメタデータ自動タグ付けシステム",
  "StartAt": "ScanFiles",
  "States": {
    "ScanFiles": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:FileScanner",
      "Retry": [
        {
          "ErrorEquals": ["States.ALL"],
          "IntervalSeconds": 2,
          "MaxAttempts": 3,
          "BackoffRate": 2.0
        }
      ],
      "Next": "ProcessFiles"
    },
    "ProcessFiles": {
      "Type": "Map",
      "ItemsPath": "$.files",
      "MaxConcurrency": 1,
      "Iterator": {
        "StartAt": "TagMetadata",
        "States": {
          "TagMetadata": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:MetadataTagger",
            "Retry": [
              {
                "ErrorEquals": ["States.TaskFailed"],
                "IntervalSeconds": 1,
                "MaxAttempts": 2,
                "BackoffRate": 2.0
              }
            ],
            "Catch": [
              {
                "ErrorEquals": ["States.ALL"],
                "ResultPath": "$.error",
                "Next": "HandleError"
              }
            ],
            "Next": "Success"
          },
          "HandleError": {
            "Type": "Pass",
            "Result": {
              "status": "error"
            },
            "End": true
          },
          "Success": {
            "Type": "Pass",
            "End": true
          }
        }
      },
      "End": true
    }
  }
}
```

### 5.2 実行パラメータ

**入力**:
```json
{
  "bucket": "your-bucket-name",
  "prefix": ""
}
```

**出力**:
```json
[
  {
    "key": "会社A/document1.pdf",
    "status": "success",
    "doc_type": "投資",
    "doc_date": 20250417
  },
  {
    "key": "会社B/document2.pdf",
    "status": "error",
    "error_type": "file_too_large"
  }
]
```

---

## 6. 手動構築ガイド

### 6.1 必要なAWSリソース

| リソース | 名前 | 説明 |
|---------|------|------|
| Lambda Function | `FileScanner` | ファイルスキャナー |
| Lambda Function | `MetadataTagger` | メタデータタガー |
| Lambda Layer | `python-dependencies` | PyMuPDF, openpyxl等 |
| Step Functions | `S3MetadataTaggingStateMachine` | ステートマシン |
| IAM Role | `FileScannerRole` | Lambda 1用ロール |
| IAM Role | `MetadataTaggerRole` | Lambda 2用ロール |
| IAM Role | `StepFunctionsRole` | Step Functions用ロール |
| Secrets Manager | `openai-api-key` | OpenAI APIキー（シークレット） |
| Secrets Manager | `slack-webhook-url` | Slack Webhook URL（シークレット） |

### 6.2 構築手順

#### ステップ1: Secrets Managerの設定

1. **AWS Management Console** → **Secrets Manager** を開く
2. **「シークレットを保存」**をクリック

**OpenAI APIキーの登録**:
- シークレットのタイプ: 「その他のシークレットのタイプ」
- キー/値のペア:
  - キー: `api_key`
  - 値: `sk-proj-...`（取得済みのOpenAI APIキー）
- シークレット名: `openai-api-key`
- 「次へ」→「次へ」→「保存」

**Slack Webhook URLの登録**:
- 同様の手順で作成
- キー/値のペア:
  - キー: `webhook_url`
  - 値: `https://hooks.slack.com/services/...`
- シークレット名: `slack-webhook-url`

#### ステップ2: IAMロールの作成

**2-1. FileScanner用ロールの作成**

1. **IAM** → **ロール** → **ロールを作成**
2. 信頼されたエンティティタイプ: **AWS のサービス**
3. ユースケース: **Lambda**
4. 許可ポリシー: **AWSLambdaBasicExecutionRole**（検索して選択）
5. ロール名: `FileScannerRole`
6. **ロールを作成**

7. 作成したロールを開き、**「許可を追加」** → **「インラインポリシーを作成」**
8. JSON タブで以下を貼り付け:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetObject"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket-name",
        "arn:aws:s3:::your-bucket-name/*"
      ]
    }
  ]
}
```

9. ポリシー名: `FileScannerS3Policy`
10. **ポリシーを作成**

**2-2. MetadataTagger用ロールの作成**

1. 同様の手順で新しいロールを作成
2. 基本ポリシー: **AWSLambdaBasicExecutionRole**
3. ロール名: `MetadataTaggerRole`
4. インラインポリシーを追加:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::your-bucket-name/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:openai-api-key-*",
        "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:slack-webhook-url-*"
      ]
    }
  ]
}
```

5. ポリシー名: `MetadataTaggerPolicy`

**2-3. Step Functions用ロールの作成**

1. **IAM** → **ロール** → **ロールを作成**
2. 信頼されたエンティティタイプ: **AWS のサービス**
3. ユースケース: **Step Functions**
4. ロール名: `StepFunctionsExecutionRole`
5. インラインポリシーを追加:

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

6. ポリシー名: `StepFunctionsLambdaInvokePolicy`

#### ステップ3: Lambda Layerの作成

ローカル環境（またはCloud9）で実行:

```bash
# 作業ディレクトリの作成
mkdir lambda-layer
cd lambda-layer

# pythonディレクトリを作成（必須の構造）
mkdir python

# 依存パッケージのインストール
pip install pymupdf openpyxl openai requests -t python/

# zipファイルの作成
zip -r python-dependencies.zip python/
```

**AWS Management Consoleでの登録**:

1. **Lambda** → **レイヤー** → **レイヤーを作成**
2. 名前: `python-dependencies`
3. zipファイルをアップロード: `python-dependencies.zip`
4. 互換性のあるランタイム: **Python 3.11** を選択
5. **作成**

#### ステップ4: Lambda Function 1（FileScanner）の作成

1. **Lambda** → **関数** → **関数の作成**
2. **一から作成**を選択
3. 関数名: `FileScanner`
4. ランタイム: **Python 3.11**
5. 既存のロールを使用: `FileScannerRole`
6. **関数の作成**

**設定の変更**:

7. **設定** → **一般設定** → **編集**
   - タイムアウト: **15分**
   - メモリ: **256MB**

8. **コード**タブで実装（後述のコードを貼り付け）

#### ステップ5: Lambda Function 2（MetadataTagger）の作成

1. **Lambda** → **関数** → **関数の作成**
2. **一から作成**を選択
3. 関数名: `MetadataTagger`
4. ランタイム: **Python 3.11**
5. 既存のロールを使用: `MetadataTaggerRole`
6. **関数の作成**

**設定の変更**:

7. **設定** → **一般設定** → **編集**
   - タイムアウト: **5分**
   - メモリ: **512MB**

8. **設定** → **環境変数** → **編集**
   - `OPENAI_API_KEY_SECRET`: `openai-api-key`
   - `SLACK_WEBHOOK_SECRET`: `slack-webhook-url`
   - `MAX_FILE_SIZE`: `10485760`

9. **レイヤー** → **レイヤーの追加**
   - **カスタムレイヤー**を選択
   - `python-dependencies` レイヤーを選択
   - 最新バージョンを選択
   - **追加**

10. **コード**タブで実装（後述のコードを貼り付け）

#### ステップ6: Step Functionsステートマシンの作成

1. **Step Functions** → **ステートマシン** → **ステートマシンの作成**
2. **コードでワークフローを記述**を選択
3. タイプ: **標準**
4. 定義に以下のASLを貼り付け（後述）
5. ステートマシン名: `S3MetadataTaggingStateMachine`
6. 既存のロールを選択: `StepFunctionsExecutionRole`
7. **ステートマシンの作成**

#### ステップ7: Lambda関数ARNの更新

Step Functionsの定義内の以下の部分を、実際のLambda関数ARNに置き換え:

```json
"Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:FileScanner"
```

Lambda関数ARNの確認方法:
1. **Lambda** → **関数** → `FileScanner`を開く
2. 右上に表示されている **関数ARN**をコピー
3. Step Functionsの定義に貼り付け

`MetadataTagger`も同様に更新。

#### ステップ8: 動作確認

1. **Step Functions** → `S3MetadataTaggingStateMachine`を開く
2. **実行の開始**をクリック
3. 入力JSONを指定:

```json
{
  "bucket": "your-bucket-name",
  "prefix": ""
}
```

4. **実行の開始**
5. 実行状態を確認

### 6.3 デプロイ用ファイル構成

Claude Codeに実装してもらう際、以下の構成でコードを生成してもらいます:

```
project/
├── file_scanner/
│   ├── lambda_function.py          # Lambda 1のメインコード
│   └── requirements.txt            # 依存パッケージ（空でOK）
├── metadata_tagger/
│   ├── lambda_function.py          # Lambda 2のメインコード
│   ├── file_processor.py           # ファイル処理ロジック
│   ├── openai_client.py            # OpenAI API呼び出し
│   ├── slack_notifier.py           # Slack通知
│   └── requirements.txt            # boto3のみ（Layerで他は対応）
├── stepfunctions/
│   └── state_machine.json          # Step Functions ASL定義
└── README.md                       # デプロイ手順書
```

### 6.4 Lambda関数のデプロイ方法

**方法1: Management Consoleで直接編集**（推奨）
- 関数のコードが小さい場合（FileScanner）
- コードエディタに直接貼り付け

**方法2: zipファイルのアップロード**
- 関数のコードが複数ファイルの場合（MetadataTagger）

```bash
cd metadata_tagger
zip -r function.zip .
```

Lambda Consoleで:
1. **コード** → **アップロード元** → **.zipファイル**
2. `function.zip`を選択
3. **保存**

---

## 7. 運用

### 7.1 実行方法

**AWS CLIから実行**:
```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:REGION:ACCOUNT_ID:stateMachine:S3MetadataTaggingStateMachine \
  --input '{"bucket":"your-bucket-name","prefix":""}'
```

**Management Consoleから実行**:
1. Step Functions コンソールを開く
2. `S3MetadataTaggingStateMachine` を選択
3. 「実行の開始」をクリック
4. 入力JSONを指定して実行

### 7.2 監視

**CloudWatch Logs**:
- `/aws/lambda/FileScanner`
- `/aws/lambda/MetadataTagger`
- `/aws/states/S3MetadataTaggingStateMachine`

**CloudWatch Metrics**:
- Lambda実行時間
- Lambda エラー率
- Step Functions 実行時間

### 7.3 コスト見積もり（万単位ファイル処理時）

**前提**:
- ファイル数: 10,000ファイル
- 平均ファイルサイズ: 1MB
- Lambda 2実行時間: 平均30秒/ファイル

| サービス | 単価 | 使用量 | コスト |
|---------|------|--------|--------|
| Lambda実行時間 | $0.0000166667/GB秒 | 10,000 × 30秒 × 0.5GB | $2.50 |
| Lambda呼び出し | $0.20/100万リクエスト | 10,000回 | $0.002 |
| Step Functions | $0.025/1000遷移 | 約30,000遷移 | $0.75 |
| OpenAI API | $0.150/1M入力token | 10,000 × 2,000 token | $3.00 |
| OpenAI API | $0.600/1M出力token | 10,000 × 100 token | $0.60 |
| S3 GET | $0.0004/1000リクエスト | 20,000回 | $0.008 |
| S3 PUT | $0.005/1000リクエスト | 10,000回 | $0.05 |
| **合計** | | | **約$6.91** |

---

## 8. セキュリティ考慮事項

### 8.1 APIキー管理
- OpenAI APIキーとSlack Webhook URLはSecrets Managerで管理
- Lambdaは実行時に動的に取得
- キーのローテーション: 90日ごと

### 8.2 S3アクセス制御
- Lambda実行ロールは最小権限の原則
- 特定バケットのみアクセス許可
- 暗号化: S3デフォルト暗号化（SSE-S3）

### 8.3 ログ
- CloudWatch Logsに全ログを記録
- 保持期間: 30日
- エラーログは別途フィルタリングしてアラート設定

---

## 9. 今後の拡張案

### 9.1 追加機能
- 処理進捗のリアルタイム表示（DynamoDB + WebSocket）
- ファイル種別ごとの専用プロンプト
- 処理履歴の記録（DynamoDB）
- 再処理機能（特定ファイルのみ再実行）

### 9.2 パフォーマンス改善
- Map Stateの並列度を上げる（現状は順次実行）
- Lambda Powertuningで最適メモリサイズを特定
- ファイル内容のキャッシュ（ElastiCache）

### 9.3 コスト最適化
- S3 Intelligent-Tieringの活用
- OpenAI APIのバッチ処理API利用
- Lambda Reserved Concurrency設定

---

## 10. 実装チェックリスト

### 構築フェーズ
- [ ] Secrets Managerにキーを登録（OpenAI API、Slack Webhook）
- [ ] IAMロール3つを作成（FileScanner、MetadataTagger、StepFunctions）
- [ ] Lambda Layerを作成してアップロード
- [ ] Lambda Function 1 (FileScanner) を作成
- [ ] Lambda Function 2 (MetadataTagger) を作成・Layer追加
- [ ] Step Functions ステートマシンを作成
- [ ] Step FunctionsのASL定義内のARNを実際の値に更新

### テストフェーズ
- [ ] FileScannerの単体テスト（テストイベント実行）
- [ ] MetadataTaggerの単体テスト（テストイベント実行）
- [ ] Step Functions全体の結合テスト（少数ファイル）
- [ ] Slack通知の動作確認
- [ ] エラーハンドリングのテスト（大きすぎるファイル等）

### 本番実行フェーズ
- [ ] CloudWatch Logsの確認準備
- [ ] 本番バケットでの小規模テスト（10-100ファイル）
- [ ] 本番実行（全ファイル）
- [ ] 実行結果の検証（metadata.jsonの更新確認）

---

## 付録A: サンプルコード構成

```
project/
├── file_scanner/
│   ├── lambda_function.py          # Lambda 1のメインコード
│   └── requirements.txt            # 依存パッケージ（空でOK）
├── metadata_tagger/
│   ├── lambda_function.py          # Lambda 2のメインコード
│   ├── file_processor.py           # ファイル処理ロジック
│   ├── openai_client.py            # OpenAI API呼び出し
│   ├── slack_notifier.py           # Slack通知
│   └── requirements.txt            # boto3等
├── stepfunctions/
│   └── state_machine.json          # Step Functions ASL定義
├── layer/
│   └── build_layer.sh              # Lambda Layerビルドスクリプト
└── README.md                       # 手動構築手順とデプロイガイド
```

---

## 付録B: トラブルシューティング

### B.1 よくある問題

**問題**: Lambda タイムアウト
- **原因**: 大きなPDFファイルの処理
- **対処**: タイムアウト時間を延長、またはファイルサイズ制限を厳格化

**問題**: OpenAI APIレート制限
- **原因**: 短時間に大量リクエスト
- **対処**: Map Stateの並列度を1に設定済み（順次実行）

**問題**: metadata.jsonの形式が異なる
- **原因**: 既存ファイルの形式が想定外
- **対処**: エラーログを確認し、柔軟なパーサーを実装

---

**作成日**: 2025-11-09  
**バージョン**: 1.0  
**作成者**: しずお  
**レビュー**: 未実施
