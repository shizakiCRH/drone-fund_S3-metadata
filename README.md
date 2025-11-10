# S3ファイルメタデータ自動タグ付けシステム

S3バケットに配置されたファイル（PDF、Excel、テキスト等）を自動スキャンし、OpenAI GPT-5-miniを用いてドキュメント種別（doc_type）と日付（doc_date）を抽出し、既存のmetadata.jsonファイルに追記するシステムです。

## 📋 目次

- [システム概要](#システム概要)
- [前提条件](#前提条件)
- [ファイル構成](#ファイル構成)
- [デプロイ手順](#デプロイ手順)
- [実行方法](#実行方法)
- [更新デプロイ手順](#更新デプロイ手順)
- [監視とログ](#監視とログ)
- [トラブルシューティング](#トラブルシューティング)

---

## システム概要

### アーキテクチャ

```
[手動実行]
    ↓
[Step Functions]
    ↓
[Lambda 1: FileScanner] → S3バケット全体をスキャン
    ↓
[Map State (順次実行)]
    ↓
[Lambda 2: MetadataTagger] → 各ファイルをAI解析
    ↓ (成功)
metadata.jsonに追記
    ↓ (エラー)
Slack通知
```

### 主要コンポーネント

| コンポーネント | 役割 |
|--------------|------|
| **FileScanner** (Lambda) | S3バケット内のファイルリストを取得 |
| **MetadataTagger** (Lambda) | 各ファイルをAIで解釈し、メタデータを更新 |
| **Step Functions** | 処理全体のオーケストレーション |
| **OpenAI API (GPT-5-mini)** | ファイル内容の解釈とタグ抽出 |
| **Slack API** | エラー通知 |

---

## 前提条件

### 1. AWSアカウント
- Lambda、S3、Step Functions、Secrets Managerへのアクセス権限

### 2. OpenAI APIキー
- GPT-5-miniが利用可能なAPIキー
- 取得方法: https://platform.openai.com/api-keys

### 3. Slack Webhook URL（オプション）
- エラー通知用のIncoming Webhook URL
- 取得方法: https://api.slack.com/messaging/webhooks

### 4. ローカル環境（Lambda Layerビルド用）
- Python 3.11以上
- pip
- zip コマンド

---

## ファイル構成

```
project/
├── file_scanner/
│   ├── lambda_function.py          # Lambda 1のメインコード
│   └── requirements.txt            # 依存パッケージ（空）
├── metadata_tagger/
│   ├── lambda_function.py          # Lambda 2のメインコード
│   ├── file_processor.py           # ファイル処理ロジック
│   ├── openai_client.py            # OpenAI API呼び出し
│   ├── slack_notifier.py           # Slack通知
│   └── requirements.txt            # openai, requests
├── stepfunctions/
│   └── state_machine.json          # Step Functions ASL定義
├── layer/
│   └── build_layer.sh              # Lambda Layerビルドスクリプト
└── README.md                       # このファイル
```

---

## デプロイ手順

### ステップ1: Secrets Managerの設定

#### 1-1. OpenAI APIキーの登録

1. **AWS Management Console** → **Secrets Manager** を開く
2. **「シークレットを保存」** をクリック
3. シークレットのタイプ: **その他のシークレットのタイプ**
4. キー/値のペア:
   ```
   キー: api_key
   値: sk-proj-...（取得済みのOpenAI APIキー）
   ```
5. シークレット名: `openai-api-key`
6. 「次へ」→「次へ」→「保存」

#### 1-2. Slack Webhook URLの登録

1. 同様の手順で新しいシークレットを作成
2. キー/値のペア:
   ```
   キー: webhook_url
   値: https://hooks.slack.com/services/...
   ```
3. シークレット名: `slack-webhook-url`
4. 「保存」

---

### ステップ2: IAMロールの作成

#### 2-1. FileScanner用ロール

1. **IAM** → **ロール** → **ロールを作成**
2. 信頼されたエンティティ: **AWS のサービス** → **Lambda**
3. 許可ポリシー: **AWSLambdaBasicExecutionRole**
4. ロール名: `FileScannerRole`
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

**※ `your-bucket-name` を実際のバケット名に置き換えてください**

8. ポリシー名: `FileScannerS3Policy`
9. **ポリシーを作成**

#### 2-2. MetadataTagger用ロール

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

**※ `your-bucket-name`, `REGION`, `ACCOUNT_ID` を置き換えてください**

5. ポリシー名: `MetadataTaggerPolicy`

#### 2-3. Step Functions用ロール

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

### ステップ3: Lambda Layerの作成

#### 3-1. ローカル環境でビルド

```bash
cd layer
bash build_layer.sh
```

出力: `python-dependencies.zip` が生成されます

#### 3-2. AWS Consoleでレイヤーを作成

1. **Lambda** → **レイヤー** → **レイヤーを作成**
2. 名前: `python-dependencies`
3. zipファイルをアップロード: `python-dependencies.zip`
4. 互換性のあるランタイム: **Python 3.11**
5. **作成**

または、AWS CLIで:

```bash
aws lambda publish-layer-version \
  --layer-name python-dependencies \
  --zip-file fileb://python-dependencies.zip \
  --compatible-runtimes python3.11
```

---

### ステップ4: Lambda Function 1（FileScanner）の作成

1. **Lambda** → **関数** → **関数の作成**
2. **一から作成**
3. 関数名: `FileScanner`
4. ランタイム: **Python 3.11**
5. 既存のロールを使用: `FileScannerRole`
6. **関数の作成**

#### 設定の変更

7. **設定** → **一般設定** → **編集**
   - タイムアウト: **15分**
   - メモリ: **256MB**
   - **保存**

8. **コード**タブで、`file_scanner/lambda_function.py` の内容を貼り付け
9. **Deploy** をクリック

---

### ステップ5: Lambda Function 2（MetadataTagger）の作成

1. **Lambda** → **関数** → **関数の作成**
2. **一から作成**
3. 関数名: `MetadataTagger`
4. ランタイム: **Python 3.11**
5. 既存のロールを使用: `MetadataTaggerRole`
6. **関数の作成**

#### 設定の変更

7. **設定** → **一般設定** → **編集**
   - タイムアウト: **5分**
   - メモリ: **512MB**
   - **保存**

8. **設定** → **環境変数** → **編集**
   - `OPENAI_API_KEY_SECRET`: `openai-api-key`
   - `SLACK_WEBHOOK_SECRET`: `slack-webhook-url`
   - `MAX_FILE_SIZE`: `10485760`
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

### ステップ6: Step Functionsステートマシンの作成

1. **Step Functions** → **ステートマシン** → **ステートマシンの作成**
2. **コードでワークフローを記述**を選択
3. タイプ: **標準**
4. `stepfunctions/state_machine.json` の内容を貼り付け
5. **以下の箇所を実際の値に置き換え**:
   - `REGION`: AWSリージョン（例: `ap-northeast-1`）
   - `ACCOUNT_ID`: AWSアカウントID（例: `123456789012`）

```json
"Resource": "arn:aws:lambda:REGION:ACCOUNT_ID:function:FileScanner"
```

Lambda関数ARNの確認方法:
- **Lambda** → 関数を開く → 右上の **関数ARN** をコピー

6. ステートマシン名: `S3MetadataTaggingStateMachine`
7. 既存のロールを選択: `StepFunctionsExecutionRole`
8. **ステートマシンの作成**

---

## 実行方法

### AWS Consoleから実行

1. **Step Functions** → `S3MetadataTaggingStateMachine` を開く
2. **実行の開始** をクリック
3. 入力JSONを指定:

```json
{
  "bucket": "your-bucket-name",
  "prefix": ""
}
```

**※ `your-bucket-name` を実際のバケット名に置き換えてください**

4. **実行の開始**
5. 実行状態を確認

### AWS CLIから実行

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:REGION:ACCOUNT_ID:stateMachine:S3MetadataTaggingStateMachine \
  --input '{"bucket":"your-bucket-name","prefix":""}'
```

### 特定のプレフィックス配下のみ処理

```json
{
  "bucket": "your-bucket-name",
  "prefix": "会社A/"
}
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
# file_scanner ディレクトリで実行
cd file_scanner
zip function.zip lambda_function.py
```

**MetadataTagger の場合:**

```bash
# metadata_tagger ディレクトリで実行
cd metadata_tagger
zip -r function.zip .
```

AWS Consoleで:
1. **Lambda** → 対象の関数を開く
2. **コード** → **アップロード元** → **.zipファイル**
3. 作成した `function.zip` を選択
4. **保存**

#### 方法3: AWS CLIを使用

**FileScanner の場合:**

```bash
cd file_scanner
zip function.zip lambda_function.py

aws lambda update-function-code \
  --function-name FileScanner \
  --zip-file fileb://function.zip
```

**MetadataTagger の場合:**

```bash
cd metadata_tagger
zip -r function.zip .

aws lambda update-function-code \
  --function-name MetadataTagger \
  --zip-file fileb://function.zip
```

### Lambda Layerの更新

依存パッケージ（pymupdf, openpyxl, openai, requests）を更新する場合:

```bash
# layer ディレクトリで実行
cd layer
bash build_layer.sh

# 新しいバージョンのレイヤーを作成
aws lambda publish-layer-version \
  --layer-name python-dependencies \
  --zip-file fileb://python-dependencies.zip \
  --compatible-runtimes python3.11
```

レイヤーのバージョンが更新されたら、Lambda関数に新しいバージョンを割り当て:

1. **Lambda** → `MetadataTagger` を開く
2. **レイヤー**セクションで既存のレイヤーを削除
3. **レイヤーの追加** → `python-dependencies` の最新バージョンを選択
4. **追加**

### Step Functions ステートマシンの更新

1. **Step Functions** → `S3MetadataTaggingStateMachine` を開く
2. **編集**をクリック
3. **ワークフロースタジオ**または**コードエディタ**で修正
   - コードエディタを選択すると、JSON定義を直接編集できます
4. `stepfunctions/state_machine.json` の更新内容を貼り付け
5. **保存**

または、AWS CLIで:

```bash
# ステートマシンのARNを取得
STATE_MACHINE_ARN=$(aws stepfunctions list-state-machines \
  --query "stateMachines[?name=='S3MetadataTaggingStateMachine'].stateMachineArn" \
  --output text)

# 定義を更新
aws stepfunctions update-state-machine \
  --state-machine-arn $STATE_MACHINE_ARN \
  --definition file://stepfunctions/state_machine.json
```

### 環境変数の更新

Lambda関数の環境変数を変更する場合:

1. **Lambda** → 対象の関数を開く
2. **設定** → **環境変数** → **編集**
3. 値を変更
4. **保存**

または、AWS CLIで:

```bash
aws lambda update-function-configuration \
  --function-name MetadataTagger \
  --environment "Variables={OPENAI_API_KEY_SECRET=openai-api-key,SLACK_WEBHOOK_SECRET=slack-webhook-url,MAX_FILE_SIZE=10485760}"
```

### Lambda関数の設定変更（タイムアウト、メモリ等）

1. **Lambda** → 対象の関数を開く
2. **設定** → **一般設定** → **編集**
3. タイムアウト、メモリ等を変更
4. **保存**

### 更新後の動作確認

1. **Step Functions** → `S3MetadataTaggingStateMachine` を開く
2. **実行の開始** で小規模なテストを実施

```json
{
  "bucket": "your-bucket-name",
  "prefix": "テスト用フォルダ/"
}
```

3. CloudWatch Logs でエラーがないか確認

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

### 問題: Lambda タイムアウト

**原因**: 大きなPDFファイルの処理

**対処**:
1. Lambda のタイムアウト時間を延長（最大15分）
2. または、`MAX_FILE_SIZE` 環境変数を小さくする

### 問題: OpenAI APIレート制限

**原因**: 短時間に大量リクエスト

**対処**:
- Step Functions の `MaxConcurrency` は既に1に設定済み（順次実行）
- OpenAI アカウントのレート制限を確認

### 問題: metadata.jsonの形式が異なる

**原因**: 既存ファイルの形式が想定外

**対処**:
1. CloudWatch Logs でエラー内容を確認
2. `metadata_tagger/lambda_function.py` の `update_metadata` 関数を調整

### 問題: Secrets Manager からシークレットを取得できない

**原因**: IAMロールの権限不足

**対処**:
1. `MetadataTaggerRole` に `secretsmanager:GetSecretValue` 権限があるか確認
2. シークレットのARNが正しいか確認

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

しずお

## バージョン

1.0 (2025-01-09)
