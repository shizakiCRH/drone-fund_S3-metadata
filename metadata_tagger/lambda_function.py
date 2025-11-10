"""
Lambda Function 2: Metadata Tagger

S3ファイルを1つ処理し、OpenAI APIでメタデータを抽出して既存のmetadata.jsonに追記します。

機能:
- S3からファイルを取得
- PDF/Excel/テキストファイルからテキストを抽出
- OpenAI API (GPT-5-mini) で doc_type と doc_date を抽出
- 既存の metadata.json に追記・更新
- エラー時は Slack 通知を送信

入力（Step Functions Map Stateから）:
{
    "key": "会社A/カテゴリ1/document1.pdf",
    "bucket": "your-bucket-name"
}

出力:
{
    "key": "会社A/カテゴリ1/document1.pdf",
    "status": "success",
    "doc_type": "投資",
    "doc_date": 20250417
}

エラー時:
{
    "key": "会社A/カテゴリ1/document1.pdf",
    "status": "error",
    "error_type": "file_too_large",
    "message": "File size exceeds 10MB limit"
}
"""

import os
import json
import logging
import boto3
from typing import Dict, Any, Tuple

# 自作モジュールのインポート
from file_processor import (
    extract_text_from_file,
    truncate_text,
    FileTooLargeError,
    FileProcessingError
)
from openai_client import (
    extract_metadata_with_ai,
    validate_metadata,
    OpenAIClientError,
    AIParseError
)
from slack_notifier import send_error_notification

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWSクライアントの初期化
s3_client = boto3.client('s3')

# 環境変数から設定を取得
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-5-mini')  # デフォルトはgpt-5-mini
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 10 * 1024 * 1024))  # デフォルト10MB


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda関数のメインハンドラー

    Args:
        event: Step Functions Map Stateからの入力
            - key (str): ファイルのS3キー
            - bucket (str): S3バケット名
        context: Lambda実行コンテキスト

    Returns:
        Dict[str, Any]: 処理結果
            成功時: {"key": "...", "status": "success", "doc_type": "...", "doc_date": ...}
            エラー時: {"key": "...", "status": "error", "error_type": "...", "message": "..."}
    """

    file_key = event.get('key')
    bucket_name = event.get('bucket')

    logger.info(f"Processing file: {file_key} in bucket: {bucket_name}")

    # 入力検証
    if not file_key or not bucket_name:
        error_msg = "Both 'key' and 'bucket' are required in the input event"
        logger.error(error_msg)
        return build_error_response(file_key or "unknown", "invalid_input", error_msg)

    try:
        # メタデータファイルのパスを構築
        metadata_key = f"{file_key}.metadata.json"

        # 1. metadata.jsonの存在確認
        if not metadata_file_exists(bucket_name, metadata_key):
            logger.info(f"metadata.json not found for {file_key}, skipping")
            return build_error_response(file_key, "metadata_not_found", "No metadata.json found for this file")

        # 2. ファイルを取得
        file_content = get_file_from_s3(bucket_name, file_key, MAX_FILE_SIZE)

        # 3. ファイルからテキストを抽出
        text_content = extract_text_from_file(file_key, file_content, MAX_FILE_SIZE)

        # テキストを適切な長さに切り詰める
        text_content = truncate_text(text_content, max_length=10000)

        # 4. OpenAI APIでメタデータを抽出
        doc_type, doc_date = extract_metadata_with_ai(
            api_key=OPENAI_API_KEY,
            file_path=file_key,
            file_content=text_content,
            model=OPENAI_MODEL
        )

        # メタデータの検証
        doc_type, doc_date = validate_metadata(doc_type, doc_date)

        # 5. 既存のmetadata.jsonを取得
        metadata = get_metadata_json(bucket_name, metadata_key)

        # 6. メタデータを更新
        metadata = update_metadata(metadata, doc_type, doc_date)

        # 7. S3に書き戻し
        write_metadata_json(bucket_name, metadata_key, metadata)

        logger.info(f"Successfully processed {file_key}: doc_type={doc_type}, doc_date={doc_date}")

        # 成功レスポンスを返す
        return {
            "key": file_key,
            "status": "success",
            "doc_type": doc_type,
            "doc_date": doc_date
        }

    except FileTooLargeError as e:
        return handle_error(file_key, "file_too_large", str(e))

    except FileProcessingError as e:
        return handle_error(file_key, "file_read_error", str(e))

    except OpenAIClientError as e:
        return handle_error(file_key, "openai_api_error", str(e))

    except AIParseError as e:
        return handle_error(file_key, "ai_parse_error", str(e))

    except Exception as e:
        logger.error(f"Unexpected error processing {file_key}: {str(e)}", exc_info=True)
        return handle_error(file_key, "unknown_error", str(e))


def metadata_file_exists(bucket_name: str, metadata_key: str) -> bool:
    """
    metadata.jsonファイルが存在するかチェックする

    Args:
        bucket_name (str): S3バケット名
        metadata_key (str): metadata.jsonのキー

    Returns:
        bool: ファイルが存在する場合はTrue
    """

    try:
        s3_client.head_object(Bucket=bucket_name, Key=metadata_key)
        return True
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        else:
            raise


def get_file_from_s3(bucket_name: str, file_key: str, max_size: int) -> bytes:
    """
    S3からファイルを取得する

    Args:
        bucket_name (str): S3バケット名
        file_key (str): ファイルのキー
        max_size (int): 最大ファイルサイズ（バイト）

    Returns:
        bytes: ファイルの内容

    Raises:
        FileTooLargeError: ファイルサイズが制限を超えている場合
        FileProcessingError: ファイル取得に失敗した場合
    """

    try:
        # ファイルのメタデータを取得してサイズをチェック
        response = s3_client.head_object(Bucket=bucket_name, Key=file_key)
        file_size = response['ContentLength']

        if file_size > max_size:
            raise FileTooLargeError(f"File size ({file_size} bytes) exceeds limit ({max_size} bytes)")

        # ファイルの内容を取得
        response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        file_content = response['Body'].read()

        logger.info(f"Successfully retrieved file from S3: {file_key} ({file_size} bytes)")

        return file_content

    except s3_client.exceptions.NoSuchKey:
        raise FileProcessingError(f"File not found: {file_key}")

    except Exception as e:
        if isinstance(e, FileTooLargeError):
            raise
        logger.error(f"Error retrieving file from S3: {str(e)}")
        raise FileProcessingError(f"Failed to retrieve file from S3: {str(e)}")


def get_metadata_json(bucket_name: str, metadata_key: str) -> Dict:
    """
    既存のmetadata.jsonを取得する

    Args:
        bucket_name (str): S3バケット名
        metadata_key (str): metadata.jsonのキー

    Returns:
        Dict: metadata.jsonの内容

    Raises:
        FileProcessingError: ファイル取得またはパースに失敗した場合
    """

    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=metadata_key)
        content = response['Body'].read().decode('utf-8')
        metadata = json.loads(content)

        logger.info(f"Successfully loaded metadata.json: {metadata_key}")

        return metadata

    except json.JSONDecodeError as e:
        raise FileProcessingError(f"Invalid JSON in metadata.json: {str(e)}")

    except Exception as e:
        logger.error(f"Error loading metadata.json: {str(e)}")
        raise FileProcessingError(f"Failed to load metadata.json: {str(e)}")


def update_metadata(metadata: Dict, doc_type: str, doc_date: int) -> Dict:
    """
    メタデータにdoc_typeとdoc_dateを追加・更新する

    Args:
        metadata (Dict): 既存のmetadata.json
        doc_type (str): ドキュメント種別
        doc_date (int): ドキュメント日付（YYYYMMDD形式）

    Returns:
        Dict: 更新されたmetadata
    """

    # metadataAttributesキーが存在しない場合は作成
    if 'metadataAttributes' not in metadata:
        metadata['metadataAttributes'] = {}

    # doc_typeを追加・更新
    metadata['metadataAttributes']['doc_type'] = {
        "value": {
            "type": "STRING",
            "stringValue": doc_type
        }
    }

    # doc_dateを追加・更新
    if doc_date is not None:
        metadata['metadataAttributes']['doc_date'] = {
            "value": {
                "type": "NUMBER",
                "numberValue": doc_date
            }
        }
    else:
        # 日付が取得できなかった場合は"unknown"を設定
        metadata['metadataAttributes']['doc_date'] = {
            "value": {
                "type": "STRING",
                "stringValue": "unknown"
            }
        }

    logger.info(f"Updated metadata with doc_type={doc_type}, doc_date={doc_date}")

    return metadata


def write_metadata_json(bucket_name: str, metadata_key: str, metadata: Dict) -> None:
    """
    更新されたmetadata.jsonをS3に書き込む

    Args:
        bucket_name (str): S3バケット名
        metadata_key (str): metadata.jsonのキー
        metadata (Dict): 更新されたmetadata

    Raises:
        FileProcessingError: 書き込みに失敗した場合
    """

    try:
        # JSONを整形して文字列化
        content = json.dumps(metadata, ensure_ascii=False, indent=2)

        # S3に書き込み
        s3_client.put_object(
            Bucket=bucket_name,
            Key=metadata_key,
            Body=content.encode('utf-8'),
            ContentType='application/json'
        )

        logger.info(f"Successfully wrote metadata.json to S3: {metadata_key}")

    except Exception as e:
        logger.error(f"Error writing metadata.json to S3: {str(e)}")
        raise FileProcessingError(f"Failed to write metadata.json to S3: {str(e)}")




def handle_error(file_key: str, error_type: str, message: str) -> Dict[str, Any]:
    """
    エラーを処理し、Slack通知を送信して、エラーレスポンスを返す

    Args:
        file_key (str): ファイルのキー
        error_type (str): エラー種別
        message (str): エラーメッセージ

    Returns:
        Dict[str, Any]: エラーレスポンス
    """

    logger.error(f"Error processing {file_key}: {error_type} - {message}")

    # Slack通知を送信（metadata_not_found以外）
    if error_type != "metadata_not_found":
        try:
            send_error_notification(
                webhook_url=SLACK_WEBHOOK_URL,
                error_type=error_type,
                file_key=file_key,
                message=message
            )
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {str(e)}")

    # エラーレスポンスを返す
    return build_error_response(file_key, error_type, message)


def build_error_response(file_key: str, error_type: str, message: str) -> Dict[str, Any]:
    """
    エラーレスポンスを構築する

    Args:
        file_key (str): ファイルのキー
        error_type (str): エラー種別
        message (str): エラーメッセージ

    Returns:
        Dict[str, Any]: エラーレスポンス
    """

    return {
        "key": file_key,
        "status": "error",
        "error_type": error_type,
        "message": message
    }


# ローカルテスト用のメイン関数
if __name__ == "__main__":
    # ローカルテスト用のイベントデータ
    test_event = {
        "key": "test-folder/document.pdf",
        "bucket": "test-bucket"
    }

    # Lambda関数を実行
    result = lambda_handler(test_event, None)

    print(json.dumps(result, ensure_ascii=False, indent=2))
